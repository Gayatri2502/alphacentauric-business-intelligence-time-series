#!/usr/bin/env python3
"""Alphacentauric Business Intelligence: entirely local market forecasting."""
import argparse
import secrets
import time
import logging
import multiprocessing
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote,urlsplit

APP_DIR=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('BI_DATA_DIR',str(Path.cwd()/'var'))).expanduser().resolve()
ROOT.mkdir(parents=True,exist_ok=True)
from .security import Accounts
AUTH_REQUIRED=os.environ.get('BI_AUTH_REQUIRED','0')=='1'
ACCOUNTS=Accounts(ROOT) if AUTH_REQUIRED else None
from .bi_engine import load_table,profile
from .bi_plus import analyze,export_report,review,MODELS
import pandas as pd
import zipfile
import io
from datetime import datetime,timezone

DATASETS={}; JOBS={};LOCK=threading.Lock()
LOG=logging.getLogger('alphacentauric')

def persist_job(job_id):
    """Atomic status snapshots retain failures and interrupted work across restarts."""
    job=JOBS[job_id]
    folder=ROOT/'reports'/job_id;folder.mkdir(parents=True,exist_ok=True)
    value={k:v for k,v in job.items() if k not in ('folder','report','forecast','batch')}
    temp=folder/'status.tmp'
    temp.write_text(json.dumps(value,allow_nan=False));temp.replace(folder/'status.json')

def worker_entry(batch,job_id,dataset,config,root):
    global ROOT
    ROOT=Path(root)
    JOBS[job_id]={'id':job_id,'status':'running','stage':'Fitting models in isolated worker'}
    try:
        (run_batch if batch else run_job)(job_id,dataset,config)
    except Exception:
        JOBS[job_id].update(status='failed',error='Analysis worker failed',stage='Failed')
    persist_job(job_id)

def guarded_run(target,job_id,dataset,config):
    started=time.monotonic()
    persist_job(job_id)
    worker=multiprocessing.get_context('spawn').Process(target=worker_entry,args=(target is run_batch,job_id,dataset,config,str(ROOT)))
    try:
        worker.start();worker.join(float(os.environ.get('BI_JOB_TIMEOUT','300')))
        if worker.is_alive():
            worker.terminate();worker.join(5)
            if worker.is_alive():worker.kill();worker.join()
            JOBS[job_id].update(status='failed',stage='Timed out',error='Analysis exceeded its processing time limit. Reduce the model comparison or batch size.')
        elif worker.exitcode!=0:
            JOBS[job_id].update(status='failed',stage='Worker stopped',error='Analysis process exited unexpectedly. Review settings and retry.')
        else:
            JOBS.pop(job_id,None)
            job=get_job(job_id)
            if not job:JOBS[job_id]={'id':job_id,'status':'failed','error':'Worker produced no readable report','stage':'Failed'}
    except Exception:
        LOG.exception('worker_failed')
        JOBS[job_id]={'id':job_id,'status':'failed','error':'Could not start analysis worker','stage':'Failed'}
    finally:
        JOBS[job_id]['duration_seconds']=round(time.monotonic()-started,3)
        persist_job(job_id)
        LOG.info(json.dumps({'event':'analysis_finished','job_id':job_id,'status':JOBS[job_id]['status'],'duration_seconds':JOBS[job_id]['duration_seconds']}))

def validate_upload(data,filename):
    if not data or len(data)>10*1024*1024:raise ValueError('Upload must be nonempty and at most 10 MiB.')
    suffix=Path(filename).suffix.lower()
    if suffix not in ('.csv','.xlsx','.xls'):raise ValueError('Use CSV, XLSX or XLS files.')
    if suffix=='.xlsx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist()
            if len(entries)>10000 or sum(x.file_size for x in entries)>100*1024*1024:
                raise ValueError('Expanded workbook exceeds the safe processing limit.')
            if any(x.flag_bits & 1 for x in entries):raise ValueError('Encrypted workbooks are unsupported.')
    return data


def valid_id(value):
    return len(value)==32 and all(c in '0123456789abcdef' for c in value)

def get_job(job_id):
    if job_id in JOBS:return JOBS[job_id]
    if not valid_id(job_id):return None
    folder=ROOT/'reports'/job_id
    try:
        status=json.loads((folder/'status.json').read_text())
        if status.get('status')=='failed':
            JOBS[job_id]=status;return status
    except (ValueError,OSError):pass
    if (folder/'batch.json').is_file():
        try:
            JOBS[job_id]={'id':job_id,'status':'done','batch':json.loads((folder/'batch.json').read_text()),'files':['market_comparison.csv','batch.json','comparison.html','comparison.zip'],'folder':folder,'stage':'Saved batch'}
            return JOBS[job_id]
        except Exception:return None
    if (folder/'findings.json').is_file():
        try:
            report=json.loads((folder/'findings.json').read_text());forecast=pd.read_csv(folder/'forecast.csv',float_precision='round_trip').to_dict('records')
            JOBS[job_id]={'id':job_id,'status':'done','report':report,'forecast':forecast,'files':sorted(p.name for p in folder.iterdir() if p.is_file()),'folder':folder,'stage':'Saved analysis'}
            return JOBS[job_id]
        except Exception:return None
    status=folder/'status.json'
    if status.is_file():
        try:
            job=json.loads(status.read_text())
            if job.get('status')=='running':
                job.update(status='failed',stage='Interrupted',error='Server restarted before completion. Review and submit the saved project again.')
            JOBS[job_id]=job
            return job
        except (ValueError,OSError):return None
    return None

def project_list():
    results=[]
    for path in (ROOT/'projects').glob('*/project.json'):
        try:
            m=json.loads(path.read_text());results.append({k:m[k] for k in ('id','name','saved_at','filename')})
        except Exception:pass
    return sorted(results,key=lambda x:x['saved_at'],reverse=True)

def library_list():
    results=[]
    for path in (ROOT/'reports').glob('*/findings.json'):
        try:
            r=json.loads(path.read_text())
            if 'final_test' in r:results.append({'id':path.parent.name,'model':r['model'],'created_at':r['created_at'],'filename':r['source']['filename'],'filters':r['configuration'].get('filters',{})})
        except Exception:pass
    for path in (ROOT/'reports').glob('*/batch.json'):
        try:
            b=json.loads(path.read_text());results.append({'id':path.parent.name,'model':'Batch comparison','created_at':b.get('created_at',datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()),'filename':b.get('filename',b['column']),'filters':{'dimension':b['column'],'groups':len(b['rows'])}})
        except Exception:pass
    return sorted(results,key=lambda x:x['created_at'],reverse=True)[:100]

def run_batch(job_id,dataset,config):
    folder=ROOT/'reports'/job_id;folder.mkdir(parents=True,exist_ok=True)
    column=config['batch_column'];frame=dataset['frame'];selected=frame
    try:
        if column not in frame:raise ValueError('Choose an existing batch dimension.')
        for c,v in config.get('filters',{}).items():
            if c!=column:selected=selected[selected[c].astype(str).eq(v)]
        groups=sorted(set(selected[column].dropna().astype(str)))
        if not groups or len(groups)>12:raise ValueError('Batch supports 1–12 groups. Filter the dataset to reduce the selection.')
        rows=[];failures=[]
        for i,value in enumerate(groups):
            with LOCK:JOBS[job_id]['stage']=f'Forecasting {i+1}/{len(groups)}: {value}'
            group_config=dict(config,filters={**config.get('filters',{}),column:value},batch_column='')
            group_id=uuid.uuid4().hex
            with LOCK:JOBS[group_id]={'id':group_id,'status':'running','stage':'Batch member'}
            run_job(group_id,dataset,group_config);member=JOBS[group_id]
            if member['status']=='done':
                report=member['report'];summary=report['summary'];test=report['final_test']
                rows.append({'group':value,'job_id':group_id,'model':report['model'],'latest_observed':summary['latest_observed'],'forecast_endpoint':summary['forecast_endpoint'],'historical_cagr_percent':summary['historical_cagr_percent'],'forecast_cagr_percent':summary['forecast_cagr_percent'],'final_test_rmse':test['rmse'],'coverage_percent':test['coverage'],'unit':config.get('unit','')})
            else:failures.append({'group':value,'error':member['error']})
        if not rows:raise ValueError('All groups failed: '+json.dumps(failures))
        pd.DataFrame(rows).to_csv(folder/'market_comparison.csv',index=False)
        batch={'id':job_id,'created_at':datetime.now(timezone.utc).isoformat(),'filename':dataset['filename'],'column':column,'rows':rows,'failures':failures,'note':'Independent forecasts; groups may have different model choices. Check source comparability and overlapping totals before interpreting rankings.'}
        (folder/'batch.json').write_text(json.dumps(batch,allow_nan=False))
        table=pd.DataFrame([{k:v for k,v in r.items() if k!='job_id'} for r in rows]).to_html(index=False,escape=True)
        (folder/'comparison.html').write_text('<!doctype html><meta charset="utf-8"><title>Market comparison</title><h1>Alphacentauric market comparison</h1><p>'+batch['note']+'</p>'+table+'<h2>Failed segments</h2><pre>'+__import__('html').escape(json.dumps(failures,indent=2))+'</pre>')
        with zipfile.ZipFile(folder/'comparison.zip','w',zipfile.ZIP_DEFLATED) as z:
            for name in ('market_comparison.csv','batch.json','comparison.html'):z.write(folder/name,name)
            for r in rows:z.write(ROOT/'reports'/r['job_id']/'report.zip',r['job_id']+'/report.zip')
        with LOCK:JOBS[job_id].update(status='done',batch=batch,folder=folder,files=['market_comparison.csv','batch.json','comparison.html','comparison.zip'],stage='Batch complete')
    except Exception as exc:
        with LOCK:JOBS[job_id].update(status='failed',error=str(exc))

def run_job(job_id,dataset,config):
    folder=ROOT/'reports'/job_id
    try:
        with LOCK: JOBS[job_id]['stage']='Preparing the selected market series'
        source={'filename':dataset['filename'],'sha256':dataset['sha256'],'sheet':dataset['sheet'],'note':'Uploaded data; external source credibility is not verified.'}
        config=dict(config,sheet=dataset['sheet'],orientation=dataset['orientation'])
        with LOCK: JOBS[job_id]['stage']='Fitting models and running chronological backtests'
        result=analyze(dataset['frame'],config,source)
        with LOCK: JOBS[job_id]['stage']='Writing the report and reproducibility evidence'
        files=export_report(folder,result)
        report=result[0]
        with LOCK: JOBS[job_id].update(status='done',report=report,files=files,folder=folder,forecast=result[2].to_dict('records'),stage='Complete')
    except Exception as exc:
        with LOCK: JOBS[job_id].update(status='failed',error=str(exc),stage='Analysis stopped')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,value,status=200,kind='application/json',download=None):
        data=json.dumps(value,allow_nan=False).encode() if kind=='application/json' else value
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if download:self.send_header('Content-Disposition','attachment; filename="'+download+'"')
        self.end_headers();self.wfile.write(data)
    def principal(self):
        return ACCOUNTS.authenticate(self.headers.get('Cookie')) if ACCOUNTS else {'name':'local','role':'owner'}
    def local(self):
        port=str(self.server.server_port)
        public=urlsplit(os.environ.get('BI_PUBLIC_ORIGIN','')).netloc
        allowed={'127.0.0.1:'+port,'localhost:'+port}
        if public and AUTH_REQUIRED:allowed.add(public)
        return self.headers.get('Host') in allowed
    def do_GET(self):
        if not self.local():return self.reply({'error':'Invalid host'},403)
        path=unquote(urlsplit(self.path).path)
        if path not in ('/healthz','/login.js'):
            principal=self.principal()
            if not principal:
                if path=='/':return self.reply((APP_DIR/'static'/'login.html').read_bytes(),kind='text/html; charset=utf-8')
                return self.reply({'error':'Sign in required'},401)
        if path=='/login.js':return self.reply((APP_DIR/'static'/'login.js').read_bytes(),kind='application/javascript; charset=utf-8')
        if path=='/api/session':return self.reply({k:v for k,v in principal.items() if k in ('name','role','csrf')})
        if path=='/healthz':return self.reply({'status':'ok','mode':'local','api_tokens_used':False})
        if path=='/':return self.reply((APP_DIR/'static'/'bi.html').read_bytes(),kind='text/html; charset=utf-8')
        if path.startswith('/chart-downloads/'):
            filename=path.rsplit('/',1)[-1]
            if not filename.endswith('.svg') or not valid_id(filename[:-4]):return self.reply({'error':'Invalid chart'},404)
            file=ROOT/'charts'/filename
            if not file.is_file():return self.reply({'error':'Unknown chart'},404)
            return self.reply(file.read_bytes(),kind='image/svg+xml',download='chart-'+filename[:8]+'.svg')
        if path=='/charts.js':return self.reply((APP_DIR/'static'/'charts.js').read_bytes(),kind='application/javascript; charset=utf-8')
        if path=='/workspace.js':return self.reply((APP_DIR/'static'/'workspace.js').read_bytes(),kind='application/javascript; charset=utf-8')
        if path=='/api/config':return self.reply({'paid':False,'models':MODELS+['Compare all'],'name':'Alphacentauric Business Intelligence'})
        if path=='/api/projects':
            return self.reply(project_list())
        if path=='/api/library':
            return self.reply(library_list())
        if path=='/sample.csv':return self.reply((APP_DIR/'data'/'market_sample.csv').read_bytes(),kind='text/csv; charset=utf-8')
        if path.startswith('/api/jobs/') and path.endswith('/charts'):
            job_id=path.split('/')[3]
            with LOCK:job=get_job(job_id)
            if not job or job['status']!='done' or 'report' not in job:return self.reply({'error':'No completed chart evidence'},404)
            return self.reply({name:pd.read_csv(job['folder']/(name+'.csv'),float_precision='round_trip').astype(object).where(lambda x:x.notna(),None).to_dict('records') for name in ('history','backtest')})
        if path.startswith('/api/jobs/'):

            with LOCK:
                job=get_job(path.rsplit('/',1)[-1]);value={k:v for k,v in job.items() if k!='folder'} if job else None
            return self.reply(value or {'error':'Unknown job'},200 if value else 404)
        if path.startswith('/files/'):
            parts=path.split('/',3)
            if len(parts)!=4:return self.reply({'error':'Invalid file'},404)
            with LOCK:job=get_job(parts[2])
            if not job or job['status']!='done' or parts[3] not in job['files']:return self.reply({'error':'Unknown file'},404)
            target=job['folder']/parts[3]
            if not target.resolve().is_relative_to(job['folder'].resolve()):return self.reply({'error':'Invalid path'},403)
            kind={'.html':'text/html; charset=utf-8','.pdf':'application/pdf','.svg':'image/svg+xml','.csv':'text/csv; charset=utf-8','.json':'application/json; charset=utf-8','.Rmd':'text/plain; charset=utf-8','.R':'text/plain; charset=utf-8','.zip':'application/zip'}.get(target.suffix,'application/octet-stream')
            filename=target.name if target.suffix!='.svg' else None
            return self.reply(target.read_bytes(),kind=kind,download=filename)
        return self.reply({'error':'Not found'},404)
    def do_POST(self):
        if not self.local():return self.reply({'error':'Invalid host'},403)
        port=str(self.server.server_port);origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:'+port,'http://localhost:'+port,os.environ.get('BI_PUBLIC_ORIGIN','')):return self.reply({'error':'Invalid origin'},403)
        if self.headers.get('Content-Type')!='application/json':return self.reply({'error':'Expected JSON'},415)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<15*1024*1024:raise ValueError('Upload must be at most 10 MiB.')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('Expected an object.')
            if ACCOUNTS and self.path=='/api/login':
                name=body.get('name');password=body.get('password')
                if not isinstance(name,str) or not isinstance(password,str) or len(name)>100 or len(password)>1024:raise ValueError('Invalid credentials')
                token,csrf,role=ACCOUNTS.login(name,password,self.client_address[0])
                secure='; Secure' if os.environ.get('BI_SECURE_COOKIE','0')=='1' else ''
                data=json.dumps({'csrf':csrf,'role':role}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('Set-Cookie','bi_session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'+secure);self.end_headers();self.wfile.write(data);return
            principal=self.principal()
            if not principal:return self.reply({'error':'Sign in required'},401)
            if ACCOUNTS and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),principal['csrf']):return self.reply({'error':'Invalid request token'},403)
            if self.path=='/api/logout' and ACCOUNTS:
                ACCOUNTS.logout(principal);return self.reply({'ok':True})
            if principal['role']=='viewer':return self.reply({'error':'Viewer accounts cannot change data or run analyses'},403)
            if self.path=='/api/chart-export':
                svg=str(body.get('svg',''))
                if len(svg.encode())>500000:raise ValueError('Chart export is too large.')
                try:root=ET.fromstring(svg)
                except ET.ParseError:raise ValueError('Invalid SVG chart.')
                tags={'svg','rect','text','line','path','polygon','polyline','desc','title','g','circle'}
                attrs={'viewBox','width','height','rx','x','y','x1','x2','y1','y2','d','points','fill','stroke','stroke-width','stroke-dasharray','opacity','font-size','font-family','text-anchor','role','tabindex','aria-label','data-index','class','cx','cy','r'}
                if root.tag!='{http://www.w3.org/2000/svg}svg' or any(e.tag.removeprefix('{http://www.w3.org/2000/svg}') not in tags or any(a not in attrs for a in e.attrib) or any('url(' in v.lower() for v in e.attrib.values()) for e in root.iter()):raise ValueError('Only static chart shapes and labels can be exported.')
                filename=uuid.uuid4().hex+'.svg';folder=ROOT/'charts';folder.mkdir(exist_ok=True);(folder/filename).write_text(svg)
                return self.reply({'url':'/chart-downloads/'+filename},201)
            if self.path=='/api/datasets':
                with LOCK:
                    if any(x['status']=='running' for x in JOBS.values()):return self.reply({'error':'Wait for the current analysis to finish.'},409)
                filename=Path(str(body['filename'])).name;data=validate_upload(base64.b64decode(body['data'],validate=True),filename)
                orientation=body.get('orientation','long')
                if orientation not in ('long','wide'):raise ValueError('Choose long or wide table layout.')
                frame,sheets,sheet=load_table(data,filename,body.get('sheet'),orientation)
                if len(frame)>100000 or len(frame.columns)>250:raise ValueError('Limit uploads to 100,000 rows and 250 columns.')
                meta=profile(frame,filename,sheets,sheet,orientation)
                dataset_id=uuid.uuid4().hex
                with LOCK:
                    # One active dataset limits memory usage. Existing reports remain accessible.
                    DATASETS.clear();DATASETS[dataset_id]={'frame':frame,'filename':filename,'sha256':hashlib.sha256(data).hexdigest(),'sheet':sheet,'orientation':orientation,'data':data}
                return self.reply({'id':dataset_id,**meta},201)
            if self.path=='/api/review':
                dataset=DATASETS.get(body.get('dataset_id'))
                if not dataset:raise ValueError('Inspect a dataset first.')
                return self.reply(review(dataset['frame'],body['config']))
            if self.path=='/api/projects/save':
                dataset=DATASETS.get(body.get('dataset_id'))
                if not dataset:raise ValueError('Inspect a dataset first.')
                project_id=uuid.uuid4().hex;folder=ROOT/'projects'/project_id;folder.mkdir(parents=True)
                name=str(body.get('name','Untitled investigation')).strip()[:100] or 'Untitled investigation'
                metadata={'id':project_id,'name':name,'filename':dataset['filename'],'sheet':dataset['sheet'],'orientation':dataset['orientation'],'sha256':dataset['sha256'],'config':body['config'],'saved_at':datetime.now(timezone.utc).isoformat()}
                (folder/'source.bin').write_bytes(dataset['data']);(folder/'project.json').write_text(json.dumps(metadata,allow_nan=False))
                return self.reply(metadata,201)
            if self.path=='/api/projects/load':
                with LOCK:
                    if any(x['status']=='running' for x in JOBS.values()):return self.reply({'error':'Wait for the current analysis to finish.'},409)
                project_id=str(body.get('id',''))
                if not valid_id(project_id):raise ValueError('Invalid project id.')
                folder=ROOT/'projects'/project_id;meta=json.loads((folder/'project.json').read_text());data=(folder/'source.bin').read_bytes()
                if hashlib.sha256(data).hexdigest()!=meta['sha256']:raise ValueError('Saved source hash changed.')
                frame,sheets,sheet=load_table(data,meta['filename'],meta['sheet'],meta['orientation']);dataset_id=uuid.uuid4().hex
                DATASETS.clear();DATASETS[dataset_id]={'frame':frame,'filename':meta['filename'],'sheet':sheet,'orientation':meta['orientation'],'sha256':meta['sha256'],'data':data}
                return self.reply({'dataset':{'id':dataset_id,**profile(frame,meta['filename'],sheets,sheet,meta['orientation'])},'config':meta['config'],'name':meta['name']})
            if self.path=='/api/notes':
                job=get_job(str(body.get('job_id','')))
                if not job or job['status']!='done' or 'final_test' not in job['report']:raise ValueError('Open a current-format completed analysis first.')
                notes=str(body.get('notes',''))
                if len(notes)>10000:raise ValueError('Notes must be at most 10,000 characters.')
                new_id=uuid.uuid4().hex;folder=ROOT/'reports'/new_id;old=job['folder']
                report=json.loads(json.dumps(job['report']));report['analyst_notes']=notes;report['configuration']['analyst_notes']=notes;report['created_at']=datetime.now(timezone.utc).isoformat();report['revision_of']=job['id']
                result=(report,pd.read_csv(old/'history.csv',float_precision='round_trip'),pd.read_csv(old/'forecast.csv',float_precision='round_trip'),pd.read_csv(old/'backtest.csv',float_precision='round_trip'),(old/'forecast.svg').read_text())
                files=export_report(folder,result);JOBS[new_id]={'id':new_id,'status':'done','report':report,'files':files,'folder':folder,'forecast':result[2].to_dict('records'),'stage':'Edited report version'}
                return self.reply({'id':new_id},201)
            if self.path=='/api/jobs':
                config=body['config']
                if not isinstance(config,dict):raise ValueError('Invalid analysis settings.')
                with LOCK:
                    dataset=DATASETS.get(body.get('dataset_id'))
                    if not dataset:raise ValueError('Inspect a dataset first.')
                    if any(x['status']=='running' for x in JOBS.values()):return self.reply({'error':'One analysis is already running.'},409)
                    job_id=uuid.uuid4().hex;JOBS[job_id]={'id':job_id,'status':'running','stage':'Queued'}
                target=run_batch if config.get('batch_column') else run_job
                persist_job(job_id)
                threading.Thread(target=guarded_run,args=(target,job_id,dataset,config),daemon=True).start()
                return self.reply({'id':job_id},202)
            return self.reply({'error':'Not found'},404)
        except (ValueError,KeyError,TypeError,UnicodeError) as exc:return self.reply({'error':str(exc)},400)
        except Exception:
            LOG.exception('request_failed')
            return self.reply({'error':'Unable to complete request. Check the file and settings.'},400)

def main():
    logging.basicConfig(level=logging.INFO)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=int(os.environ.get('BI_PORT','8000')));parser.add_argument('--host',default=os.environ.get('BI_BIND_HOST','127.0.0.1'));args=parser.parse_args()
    if args.host not in ('127.0.0.1','localhost') and not AUTH_REQUIRED:raise SystemExit('Non-loopback binding requires BI_AUTH_REQUIRED=1')
    with ThreadingHTTPServer((args.host,args.port),Handler) as server:
        print(f'Alphacentauric Business Intelligence: http://127.0.0.1:{args.port}',flush=True)
        print('LOCAL FORECASTING ONLY · No OpenAI API calls or token charges',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
if __name__=='__main__':main()

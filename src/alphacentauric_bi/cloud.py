"""Stateless hosted forecasting: no uploads or reports survive the HTTP request.

Browser IndexedDB owns the user's library. Local/server mode remains available
for operators who need durable server-side files and workspace accounts.
"""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import logging
from pathlib import Path
import tempfile
import threading
import uuid
import zipfile
import numpy as np
import pandas as pd
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,Response
from starlette.concurrency import run_in_threadpool
from .bi_engine import load_table,profile,prepare,chart_svg
from .bi_plus import MODELS,analyze,export_report,review

APP=Path(__file__).resolve().parent
MAX_FILE=3*1024*1024
MAX_BODY=4_300_000
LOG=logging.getLogger('alphacentauric.cloud')
GATE=threading.BoundedSemaphore(2)
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@app.middleware('http')
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
    return response

def decode(body):
    dataset=body.get('dataset')
    if not isinstance(dataset,dict):raise ValueError('Inspect a dataset first.')
    name=Path(str(dataset.get('filename',''))).name
    data=base64.b64decode(dataset.get('data',''),validate=True)
    if not data or len(data)>MAX_FILE:raise ValueError('Hosted uploads must be nonempty and at most 3 MiB.')
    if Path(name).suffix.lower()=='.xlsx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist())>10000 or sum(x.file_size for x in archive.infolist())>100*1024*1024:raise ValueError('Expanded workbook exceeds safe limits.')
    orientation=dataset.get('orientation','long')
    if orientation not in ('long','wide'):raise ValueError('Choose long or wide layout.')
    frame,sheets,sheet=load_table(data,name,dataset.get('sheet'),orientation)
    if len(frame.columns)>250:raise ValueError('Use at most 250 columns.')
    return frame,{'filename':name,'sha256':hashlib.sha256(data).hexdigest(),'sheet':sheet,'orientation':orientation},sheets

def checked_config(frame,body):
    config=body.get('config')
    if not isinstance(config,dict):raise ValueError('Select analysis settings.')
    config=dict(config)
    if len(json.dumps(config))>50000:raise ValueError('Analysis settings are too large.')
    if config.get('model') not in MODELS+['Compare all']:raise ValueError('Select a forecasting model.')
    series,_=prepare(frame,dict(config,combine_segments=True) if config.get('batch_column') else config)
    if len(series)>2000:raise ValueError('Hosted forecasting supports up to 2,000 aggregated periods. Choose monthly/quarterly aggregation or reduce the file.')
    return config

def records(frame):
    return json.loads(frame.to_json(orient='records'))

def package(result,folder):
    files=export_report(folder,result)
    encoded={p:base64.b64encode((folder/p).read_bytes()).decode() for p in files if (folder/p).is_file()}
    job={'id':uuid.uuid4().hex,'status':'done','stage':'Complete','report':result[0],'forecast':records(result[2]),'files':list(encoded),'charts':{'history':records(result[1]),'backtest':records(result[3])},'filesData':encoded}
    return job

def execute(action,body):
    frame,source,sheets=decode(body)
    if action=='inspect':return {'id':uuid.uuid4().hex,**profile(frame,source['filename'],sheets,source['sheet'],source['orientation'])}
    config=checked_config(frame,body)
    config.update(sheet=source['sheet'],orientation=source['orientation'])
    if action=='review':return review(frame,dict(config,combine_segments=True) if config.get('batch_column') else config)
    if action not in ('analyze','revise'):raise ValueError('Unknown action')
    if not config.get('review_accepted'):raise ValueError('Review and acknowledge data-quality findings before forecasting.')
    column=config.get('batch_column')
    with tempfile.TemporaryDirectory(prefix='alphacentauric-') as temp:
        root=Path(temp)
        if not column:
            if action=='revise':
                previous=body.get('previous')
                if not isinstance(previous,dict) or previous.get('source',{}).get('sha256')!=source['sha256']:raise ValueError('Original report and source are required.')
                # Revisions use original numbers; do not refit to manufacture a new history.
                charts=body.get('charts',{});forecast=body.get('forecast',[])
                if not isinstance(charts,dict) or not isinstance(forecast,list):raise ValueError('Missing original evidence')
                report=json.loads(json.dumps(previous));notes=str(body.get('notes',''))[:10000]
                report.update(analyst_notes=notes,revision_of=body.get('previous_id'),created_at=pd.Timestamp.now(tz='UTC').isoformat())
                report['configuration']['analyst_notes']=notes
                history=pd.DataFrame(charts['history']);pred=pd.DataFrame(forecast);backtest=pd.DataFrame(charts['backtest'])
                observed=pd.Series(history['observed'].to_numpy(),index=pd.to_datetime(history['period']))
                chart=chart_svg(observed,pred['forecast'].to_numpy(),pred['lower'].to_numpy(),pred['upper'].to_numpy(),pd.DatetimeIndex(pd.to_datetime(pred['period'])),report['model']+' · '+report['configuration']['value_column'],report['configuration'].get('unit',''))
                result=(report,history,pred,backtest,chart)
            else:result=analyze(frame,config,source)
            job=package(result,root/'report')
        else:
            if column not in frame:raise ValueError('Choose an existing batch dimension.')
            selected=frame
            for c,v in config.get('filters',{}).items():
                if c!=column:selected=selected[selected[c].astype(str).eq(v)]
            groups=sorted(selected[column].dropna().astype(str).unique())
            if not 1<=len(groups)<=12:raise ValueError('Filter to 1–12 groups before running a batch.')
            rows=[];failures=[];members={}
            for i,value in enumerate(groups):
                try:
                    c=dict(config,filters={**config.get('filters',{}),column:value},batch_column='')
                    member=package(analyze(frame,c,source),root/str(i));members[member['id']]=member
                    r=member['report'];s=r['summary'];t=r['final_test']
                    rows.append({'group':value,'job_id':member['id'],'model':r['model'],'latest_observed':s['latest_observed'],'forecast_endpoint':s['forecast_endpoint'],'historical_cagr_percent':s['historical_cagr_percent'],'forecast_cagr_percent':s['forecast_cagr_percent'],'final_test_rmse':t['rmse'],'coverage_percent':t['coverage'],'unit':c.get('unit','')})
                except (ValueError,TypeError,KeyError) as exc:failures.append({'group':value,'error':str(exc)})
            if not rows:raise ValueError('No segment could be forecasted. '+json.dumps(failures))
            batch={'column':column,'rows':rows,'failures':failures,'created_at':pd.Timestamp.now(tz='UTC').isoformat(),'filename':source['filename'],'note':'Independent forecasts. Verify common units and avoid overlapping totals.'}
            artifacts={'market_comparison.csv':pd.DataFrame(rows).to_csv(index=False).encode(),'batch.json':json.dumps(batch).encode(),'comparison.html':('<!doctype html><meta charset="utf-8"><h1>Market comparison</h1>'+pd.DataFrame(rows).to_html(index=False,escape=True)).encode()}
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
                for n,data in artifacts.items():z.writestr(n,data)
                for key,m in members.items():z.writestr(key+'/report.zip',base64.b64decode(m['filesData']['report.zip']))
            artifacts['comparison.zip']=buffer.getvalue()
            job={'id':uuid.uuid4().hex,'status':'done','stage':'Batch complete','batch':batch,'files':list(artifacts),'filesData':{n:base64.b64encode(v).decode() for n,v in artifacts.items()},'members':members}
        output=json.dumps(job,allow_nan=False)
        if len(output.encode())>4_000_000:raise ValueError('Reports exceed the hosted response limit. Reduce batch groups or data length; use local mode for larger reports.')
        return job

@app.get('/healthz')
def health():return {'status':'ok','mode':'stateless-browser-library','paid_ai':False}

@app.get('/')
def home():
    page=(APP/'static/bi.html').read_text().replace('Up to 10 MiB','Up to 3 MiB in hosted mode').replace('Local models · zero API tokens','Statistical models · zero AI tokens').replace('<script>','<script src="/cloud.js"></script><script>',1)
    return Response(page,media_type='text/html')

@app.get('/api/config')
def configuration():return {'paid':False,'models':MODELS+['Compare all'],'name':'Alphacentauric Business Intelligence','mode':'cloud','max_upload_bytes':MAX_FILE}

@app.get('/api/kaggle')
def kaggle():return json.loads((APP/'data/kaggle_retail_source.json').read_text())

@app.get('/{filename}')
def static(filename:str):
    if filename in ('workspace.js','charts.js','cloud.js'):return Response((APP/'static'/filename).read_bytes(),media_type='application/javascript')
    if filename in ('sample.csv','kaggle.csv'):
        path=APP/'data'/('market_sample.csv' if filename=='sample.csv' else 'kaggle_retail_monthly.csv')
        return Response(path.read_bytes(),media_type='text/csv')
    return JSONResponse({'error':'Not found'},404)

@app.post('/api/cloud/{action}')
async def api(action:str,request:Request):
    origin=request.headers.get('origin')
    if origin and origin!=str(request.base_url).rstrip('/'):return JSONResponse({'error':'Invalid origin'},403)
    payload=bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload)>MAX_BODY:return JSONResponse({'error':'Hosted request exceeds 4.3 MB. Use a file under 3 MiB.'},413)
    if request.headers.get('content-type','').split(';')[0]!='application/json':return JSONResponse({'error':'Expected JSON'},415)
    if not GATE.acquire(blocking=False):return JSONResponse({'error':'Server is busy. Retry shortly.'},429)
    try:
        body=json.loads(payload)
        if not isinstance(body,dict):raise ValueError('Expected an object')
        return await run_in_threadpool(execute,action,body)
    except (ValueError,KeyError,TypeError,UnicodeError,zipfile.BadZipFile) as exc:return JSONResponse({'error':str(exc)[:1000]},400)
    except Exception:
        LOG.exception('forecast_request_failed')
        return JSONResponse({'error':'Analysis failed unexpectedly. Review the file and try a simpler model.'},500)
    finally:GATE.release()

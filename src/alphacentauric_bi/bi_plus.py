"""Evidence, validation, scenarios and model extensions for the local BI app."""
import hashlib
import html
import json
import math
import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import norm
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.forecasting.stl import STLForecast
from statsmodels.tsa.forecasting.theta import ThetaModel
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
try:
    from . import bi_engine as core
except ImportError:
    import bi_engine as core

MODELS=core.MODELS+['ETS','Seasonal Theta','STL-ARIMA','Ensemble','SARIMAX']
SEASONAL={'SARIMA','Holt-Winters','Seasonal naive','Seasonal Theta','STL-ARIMA'}


def review(frame, config):
    date=config.get('date_column');value=config.get('value_column');selected=frame.copy()
    for column,target in config.get('filters',{}).items():
        if column not in selected:raise ValueError('Unknown filter field.')
        selected=selected[selected[column].notna() & selected[column].astype(str).eq(target)]
    if date not in selected or value not in selected or date==value:raise ValueError('Select different time and numeric value fields.')
    nums=pd.to_numeric(selected[value],errors='coerce');issues=[]
    missing=int(nums.isna().sum());infinite=int(np.isinf(nums.to_numpy(dtype=float)).sum())
    if missing or infinite:issues.append({'severity':'block','title':'Unusable measured values','detail':f'{missing} missing/non-numeric and {infinite} infinite values. Fix the file; the app will not silently discard them.'})
    if selected.empty:issues.append({'severity':'block','title':'Empty segment','detail':'No records match the filters.'})
    duplicates=int(selected.duplicated().sum())
    if duplicates:issues.append({'severity':'warning','title':'Duplicate full records','detail':f'{duplicates} exact duplicate rows. They may double count totals. Remove them in your source or confirm they are intentional.'})
    repeated=int(selected[date].duplicated().sum())
    if repeated:issues.append({'severity':'warning','title':'Repeated timestamps','detail':f'{repeated} repeated time labels will be combined by your selected {config.get("aggregation","sum")} rule.'})
    for column in selected:
        if any(k in column.lower() for k in ('currency','unit')) and selected[column].nunique(dropna=True)>1:
            issues.append({'severity':'block','title':'Mixed units or currencies','detail':f'{column} contains multiple values. Filter or normalize units before analysis.'})
    finite=nums[np.isfinite(nums)]
    if len(finite)>=8:
        q1,q3=finite.quantile([.25,.75]);iqr=q3-q1
        outliers=int(((finite<q1-3*iqr)|(finite>q3+3*iqr)).sum()) if iqr>0 else 0
        if outliers:issues.append({'severity':'warning','title':'Potential outliers','detail':f'{outliers} values fall outside the 3×IQR fences. Growth and seasonal patterns can also produce these; values are retained.'})
    total_columns=[c for c in selected if selected[c].astype(str).str.fullmatch(r'(?i)(total|all|global|world)').any()]
    if total_columns and config.get('combine_segments'):issues.append({'severity':'warning','title':'Potential overlapping totals','detail':'Aggregate labels occur in '+', '.join(total_columns)+'. Do not sum totals together with their components.'})
    try:
        series,quality=core.prepare(selected,config)
        if len(series)>1:
            issues.append({'severity':'info','title':'Regular time series','detail':f'{len(series)} periods; {quality["missing_periods_filled"]} absent periods carried forward from past data.'})
    except Exception as exc:
        issues.append({'severity':'block','title':'Series preparation','detail':str(exc)});quality=None
    reference=config.get('source_evidence',{})
    if not reference.get('publisher') or not reference.get('url'):
        issues.append({'severity':'warning','title':'Source provenance incomplete','detail':'Publisher and source URL are not both supplied. Uploaded figures are not independently verified.'})
    if reference.get('figure_type')=='forecast':issues.append({'severity':'warning','title':'Previously forecasted input','detail':'These figures are already forecasts. New projections compound the original assumptions and are not validation against observed market outcomes.'})
    return {'selected_rows':len(selected),'exact_duplicates':duplicates,'repeated_time_labels':repeated,'issues':issues,'ready':not any(x['severity']=='block' for x in issues),'quality':quality,'source_evidence':reference}


def fit(name,y,season,spec=None,exog=None):
    if name in core.MODELS:return core.fit_model(name,y,season,spec)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        if name=='ETS':
            result=ETSModel(y,error='add',trend='add',damped_trend=True,seasonal=None).fit(disp=False,maxiter=100)
            return result,{'error':'additive','trend':'additive damped','seasonal':None}
        if name=='Seasonal Theta':return ThetaModel(y,period=season,deseasonalize=True,use_test=False,method='additive').fit(),{'period':season,'theta':2,'seasonal_adjustment':'additive'}
        if name=='STL-ARIMA':return STLForecast(y,ARIMA,model_kwargs={'order':(1,1,0)},period=season,robust=True).fit(),{'order':[1,1,0],'period':season,'decomposition':'robust STL'}
        if name=='SARIMAX':
            result=SARIMAX(y,exog=exog,order=(1,1,1),seasonal_order=(0,1,1,season) if season>1 else (0,0,0,0)).fit(disp=False,maxiter=150)
            if not result.mle_retvals.get('converged',True):raise ValueError('SARIMAX optimizer did not converge.')
            names=result.param_names
            coefficients=[{'name':str(k),'estimate':float(v)} for k,v in zip(names,result.params)]
            return result,{'order':[1,1,1],'seasonal_order':[0,1,1,season] if season>1 else [0,0,0,0],'driver_standardized_per_training_window':True,'coefficients':coefficients}
        if name=='Ensemble':
            results=[]
            for component in spec['components']:
                f,info=fit(component,y,season,spec['specs'].get(component));results.append((component,f))
            return results,{'components':spec['components'],'weights':'equal; components selected on rolling validation RMSE'}
    raise ValueError('Unsupported model.')


def predict(name,fitted,horizon,level,season,training,exog_future=None):
    if name in core.MODELS:return core.predict(name,fitted,horizon,level,season)
    if name=='Seasonal Theta':return core.predict('Theta',fitted,horizon,level,season)
    if name=='SARIMAX':
        f=fitted.get_forecast(steps=horizon,exog=exog_future);ci=np.asarray(f.conf_int(alpha=1-level/100))
        return np.asarray(f.predicted_mean),ci[:,0],ci[:,1],'Conditional SARIMAX model intervals; the assumed future driver path is treated as known.'
    if name=='Ensemble':
        results=[predict(n,f,horizon,level,season,training) for n,f in fitted]
        return np.mean([x[0] for x in results],axis=0),np.min([x[1] for x in results],axis=0),np.max([x[2] for x in results],axis=0),'Equal-weight ensemble point forecast with an envelope of component intervals; envelope coverage is not a calibrated probability.'
    point=np.asarray(fitted.forecast(horizon))
    residuals=np.asarray(fitted.resid) if name=='ETS' else np.diff(training)
    scale=float(np.std(residuals,ddof=1));width=norm.ppf(.5+level/200)*scale*np.sqrt(np.arange(1,horizon+1))
    return point,point-width,point+width,'Approximate normal bands using '+('ETS residuals' if name=='ETS' else 'training first differences for STL-ARIMA')+'; not model-specific calibrated intervals.'


def driver_series(frame,config,index):
    column=config.get('driver_column')
    if not column or column==config['value_column']:raise ValueError('SARIMAX needs a separate numeric historical driver.')
    dconfig=dict(config,value_column=column,aggregation='mean',model='ARIMA')
    d,quality=core.prepare(frame,dconfig)
    d=d.reindex(index)
    if d.isna().any():raise ValueError('Driver and market observations must cover identical periods.')
    values=np.asarray(config.get('future_driver',[]),dtype=float)
    if len(values)!=int(config['horizon']) or not np.isfinite(values).all():raise ValueError('Supply one finite future driver value for each forecast period.')
    return d.values,values


def break_scan(series):
    y=series.values;window=max(4,min(12,len(y)//6));candidates=[]
    # Descriptive screening on first differences reduces confounding from a level trend.
    changes=np.diff(y);scale=float(np.std(changes)) or 1
    for i in range(window,len(y)-window):
        delta=float(np.mean(changes[i:i+window])-np.mean(changes[i-window:i]))
        score=abs(delta)/scale
        if score>=1.5:candidates.append({'period':series.index[i].strftime('%Y-%m-%d'),'mean_change_shift':delta,'score':score})
    candidates.sort(key=lambda x:x['score'],reverse=True)
    return {'method':f'Descriptive adjacent {window}-period change-window screening; score ≥ 1.5. Not a statistical break test or causal evidence.', 'candidates':candidates[:3]}


def analyze(frame,config,source):
    model=config.get('model')
    if model not in MODELS+['Compare all']:raise ValueError('Select a forecasting model.')
    assessment=review(frame,config)
    if not assessment['ready']:raise ValueError('; '.join(x['detail'] for x in assessment['issues'] if x['severity']=='block'))
    if not config.get('review_accepted'):raise ValueError('Review and acknowledge data-quality findings before forecasting.')
    horizon=int(config.get('horizon',12));level=int(config.get('confidence',95));season=int(config.get('season',12));folds=int(config.get('validation_folds',3))
    if not 1<=horizon<=36 or level not in (80,90,95) or not 1<=season<=52 or not 3<=folds<=6:raise ValueError('Invalid horizon, interval, season or validation-window count.')
    series,quality=core.prepare(frame,config)
    complete_series=series.copy()
    if config.get('window_start'):
        start=pd.to_datetime(config['window_start'],errors='raise');series=series[series.index>=start]
        quality['training_window_start']=str(start);quality['observations_excluded_before_start']=len(complete_series)-len(series)
    n=len(series);seasonal=SEASONAL|({'SARIMAX'} if season>1 else set())
    if model in seasonal and season<2:raise ValueError('Seasonal models require a season length of at least 2.')
    minimum=max(20,2*season+8) if model in seasonal else 16
    if model=='Compare all' and season>=2 and n>=2*season+8+folds+1:minimum=max(20,2*season+8)
    block=min(horizon,max(1,(n-minimum)//(folds+1)),max(1,n//10))
    first=n-(folds+1)*block;cv_end=n-block
    if first<minimum:raise ValueError(f'{model} needs at least {minimum+folds+1} periods for {folds} rolling windows and an untouched final test; found {n}.')
    exog=future_exog=None
    if model=='SARIMAX':exog,future_exog=driver_series(frame,config,series.index)
    names=[x for x in MODELS if x not in ('Ensemble','SARIMAX')] if model=='Compare all' else list(dict.fromkeys([model,'Naive']))
    if model=='Ensemble':names=['ARIMA','Holt','Theta','Naive','Drift']
    ranks=[];skipped=[];specs={};evidence={}
    def evaluate(name,spec=None):
        rows=[];scores=[]
        for origin in range(first,cv_end,block):
            train=series.values[:origin];actual=series.values[origin:origin+block]
            xx=exog[:origin] if name=='SARIMAX' else None
            mean=float(np.mean(xx)) if xx is not None else 0;std=float(np.std(xx)) if xx is not None else 1
            if xx is not None and std<1e-9:raise ValueError('Driver has no variation in an early training window.')
            f,info=fit(name,train,season,spec,(xx-mean)/std if xx is not None else None)
            future=(exog[origin:origin+block]-mean)/std if name=='SARIMAX' else None
            point,low,high,_=predict(name,f,len(actual),level,season,train,future)
            if not np.isfinite(np.r_[point,low,high]).all():raise ValueError('Nonfinite model output.')
            lag=season if season>1 and len(train)>season else 1;scale=float(np.mean(np.abs(train[lag:]-train[:-lag])))
            mm=core.metrics(actual,point,scale)
            scores.append({'train_end':series.index[origin-1].isoformat(),'test_start':series.index[origin].isoformat(),'test_end':series.index[origin+block-1].isoformat(),**mm})
            for step,(a,p,l,u) in enumerate(zip(actual,point,low,high),1):rows.append({'period':series.index[origin+step-1].isoformat(),'actual':float(a),'prediction':float(p),'lower':float(l),'upper':float(u),'lead':step,'train_end':series.index[origin-1].isoformat(),'partition':'rolling_validation'})
        evidence[name]=rows
        actual=np.array([x['actual'] for x in rows]);points=np.array([x['prediction'] for x in rows]);mm=core.metrics(actual,points,1)
        scales=[x['mase'] for x in scores if x['mase'] is not None];mm['mase']=float(np.mean(scales)) if scales else None
        return {'model':name,**mm,'coverage':float(np.mean([(x['lower']<=x['actual']<=x['upper']) for x in rows])*100),'test_points':len(rows),'folds':scores,'spec':spec}
    for name in names:
        if name in seasonal and (season<2 or first<2*season+8):skipped.append({'model':name,'reason':'Not enough seasonal training history.'});continue
        try:
            spec=core.choose_order(name,series.values[:first],season) if name in ('ARIMA','SARIMA') else None
            specs[name]=spec;ranks.append(evaluate(name,spec))
        except Exception as e:skipped.append({'model':name,'reason':str(e)})
    ranks.sort(key=lambda x:x['rmse'])
    if not ranks:raise ValueError('No candidate passed rolling validation.')
    if model in ('Ensemble','Compare all'):
        components=[x['model'] for x in ranks[:3]]
        if len(components)>=2:
            spec={'components':components,'specs':{k:specs[k] for k in components}};specs['Ensemble']=spec
            try:ranks.append(evaluate('Ensemble',spec));ranks.sort(key=lambda x:x['rmse'])
            except Exception as e:skipped.append({'model':'Ensemble','reason':str(e)})
    winner=ranks[0]['model'] if model=='Compare all' else model
    selected=next((x for x in ranks if x['model']==winner),None)
    if selected is None:raise ValueError('Selected model failed: '+str(next((x['reason'] for x in skipped if x['model']==winner),'no usable fit')))
    spec=specs.get(winner);cv=evidence[winner]
    errors=np.abs(np.array([x['actual']-x['prediction'] for x in cv]));qrank=min(len(errors),int(math.ceil((len(errors)+1)*level/100)))
    radius=float(np.sort(errors)[qrank-1]);calibrate=bool(config.get('calibrate_intervals',True))
    def widen(p,l,u):return np.minimum(l,p-radius),np.maximum(u,p+radius)
    train=series.values[:cv_end];xx=exog[:cv_end] if winner=='SARIMAX' else None
    mean=float(np.mean(xx)) if xx is not None else 0;std=float(np.std(xx)) if xx is not None else 1
    test_fit,_=fit(winner,train,season,spec,(xx-mean)/std if xx is not None else None)
    conditional=(exog[cv_end:]-mean)/std if winner=='SARIMAX' else None
    tp,tl,tu,method=predict(winner,test_fit,block,level,season,train,conditional)
    if not np.isfinite(np.r_[tp,tl,tu]).all():raise ValueError('Nonfinite final-test model output.')
    nominal_low=tl.copy();nominal_high=tu.copy()
    if calibrate:tl,tu=widen(tp,tl,tu)
    actual=series.values[cv_end:];lag=season if season>1 and len(train)>season else 1
    final_test=core.metrics(actual,tp,float(np.mean(np.abs(train[lag:]-train[:-lag]))))
    final_test.update(coverage=float(np.mean((actual>=tl)&(actual<=tu))*100),nominal_coverage=float(np.mean((actual>=nominal_low)&(actual<=nominal_high))*100),test_points=block,train_end=series.index[cv_end-1].isoformat(),test_start=series.index[cv_end].isoformat(),test_end=series.index[-1].isoformat())
    nb=np.full(block,train[-1]);final_test['naive_rmse']=float(np.sqrt(np.mean((actual-nb)**2)))
    final_rows=[{'period':series.index[cv_end+i].isoformat(),'actual':float(a),'prediction':float(p),'lower':float(l),'upper':float(u),'lead':i+1,'train_end':series.index[cv_end-1].isoformat(),'partition':'untouched_final_test'} for i,(a,p,l,u) in enumerate(zip(actual,tp,tl,tu))]
    xx=exog if winner=='SARIMAX' else None;mean=float(np.mean(xx)) if xx is not None else 0;std=float(np.std(xx)) if xx is not None else 1
    fitted,info=fit(winner,series.values,season,spec,(xx-mean)/std if xx is not None else None)
    future_driver=(future_exog-mean)/std if winner=='SARIMAX' else None
    point,low,high,method=predict(winner,fitted,horizon,level,season,series.values,future_driver)
    if calibrate:low,high=widen(point,low,high)
    if not np.isfinite(np.r_[point,low,high]).all():raise ValueError('Nonfinite final forecast.')
    dates=pd.date_range(series.index[-1],periods=horizon+1,freq=core.FREQUENCIES[config['frequency']])[1:]
    forecast=pd.DataFrame({'period':dates.strftime('%Y-%m-%d'),'forecast':point,'lower':low,'upper':high})
    upside=float(config.get('upside_growth',5));downside=float(config.get('downside_growth',-5))
    if not -99<downside<=upside<=100:raise ValueError('Scenario annual adjustments must be ordered and between -99% and 100%.')
    elapsed=np.array([(d-series.index[-1]).days/365.2425 for d in dates])
    forecast['upside']=point*(1+upside/100)**elapsed;forecast['downside']=point*(1+downside/100)**elapsed
    forecast['upside_lower']=low*(1+upside/100)**elapsed;forecast['upside_upper']=high*(1+upside/100)**elapsed
    forecast['downside_lower']=low*(1+downside/100)**elapsed;forecast['downside_upper']=high*(1+downside/100)**elapsed
    def cagr(a,b,years):return float(((b/a)**(1/years)-1)*100) if a>0 and b>0 and years>0 else None
    latest=float(series.iloc[-1]);endpoint=float(point[-1]);allow=config.get('metric_kind','level')=='level' and not any(x in config['value_column'].lower() for x in ('cagr','rate','share','index','percent','%'))
    summary={'latest_observed':latest,'forecast_endpoint':endpoint,'historical_cagr_percent':cagr(float(series.iloc[0]),latest,(series.index[-1]-series.index[0]).days/365.2425) if allow else None,'forecast_cagr_percent':cagr(latest,endpoint,float(elapsed[-1])) if allow else None,'endpoint_change_percent':(endpoint/latest-1)*100 if latest>0 else None,'rmse_improvement_vs_naive_percent':(1-final_test['rmse']/final_test['naive_rmse'])*100 if final_test['naive_rmse']>0 else None}
    horizon_metrics=[]
    for lead in range(1,block+1):
        rows=[x for x in cv if x['lead']==lead];mm=core.metrics(np.array([x['actual'] for x in rows]),np.array([x['prediction'] for x in rows]),1)
        horizon_metrics.append({'lead':lead,'rmse':mm['rmse'],'mae':mm['mae'],'observations':len(rows)})
    evidence_source=dict(source,register=config.get('source_evidence',{}))
    limits=['Forecasts extrapolate historical patterns; no causal market claims or external source verification.',
            f'{level}% is nominal marginal coverage, not a guarantee for the complete forecast path.',
            'A dedicated final test is excluded from order search, model choice and interval calibration, then included only in the production refit.',
            'Rolling errors also support candidate/ensemble selection and can be optimistic; the separate final test provides additional evidence.',
            'Calibration uses pooled absolute errors from rolling windows and only widens intervals. Time dependence and limited samples prevent guaranteed coverage.',
            f'Final test has only {block} observations; rolling windows have {selected["test_points"]}. Long-horizon confidence remains limited.',
            'Scenario paths multiply the baseline by user-specified annual growth adjustments; these are assumptions, not model-estimated probabilities.',
            'Structural-break screening is descriptive and may reflect seasonality; no automatic data deletion or regime change.',
            'Intervals omit parameter selection, future driver uncertainty, data revisions and economic shocks.']
    if block<horizon:limits.append(f'Validation covers leads 1–{block}; the {horizon}-period production horizon extends beyond tested lead lengths.')
    if model=='SARIMAX':limits.append('Historical SARIMAX tests condition on observed held-out driver values, which may not have been known at forecast time. Real-time deployable accuracy is not established.')
    if 'synthetic' in source['filename'].lower():limits.append('Synthetic demonstration data, not measured market evidence.')
    if final_test['rmse']>=final_test['naive_rmse']:limits.append('The selected model did not beat the last-value baseline on the untouched final test.')
    explanation=[{'title':'What was reviewed','detail':f'{assessment["selected_rows"]} selected rows; {assessment["exact_duplicates"]} duplicate full records. Warnings explicitly acknowledged; no outliers silently removed.'},
        {'title':'What was used','detail':f'{source["filename"]}, SHA-256 recorded; {config["date_column"]} → {config["value_column"]}; {config["aggregation"]} per {config["frequency"]}. Source register is user-supplied and unverified.'},
        {'title':'What was fitted','detail':f'{winner}. Parameters: {json.dumps({k:v for k,v in info.items() if k!="coefficients"})}. ARIMA/SARIMA orders are chosen only on the earliest training window.'},
        {'title':'How it was validated','detail':f'{folds} expanding rolling windows, {block} periods each. A separate final test ({final_test["test_start"][:10]} to {final_test["test_end"][:10]}) has RMSE {final_test["rmse"]:.4g}; naive RMSE {final_test["naive_rmse"]:.4g}.'},
        {'title':'How intervals were checked','detail':f'{method} Calibration {"enabled" if calibrate else "disabled"}; pooled validation absolute-error radius {radius:.4g}. Final-test coverage: nominal {final_test["nominal_coverage"]:.1f}%, displayed {final_test["coverage"]:.1f}% over {block} observations.'},
        {'title':'Scenario assumptions','detail':f'Upside adds {upside}% annual growth relative to baseline; downside adds {downside}%. Shifts are conditional planning assumptions.'}]
    report={'title':'Alphacentauric Business Intelligence','created_at':core.datetime.now(core.timezone.utc).isoformat(),'source':evidence_source,'configuration':config,'data_quality':quality,'quality_review':assessment,'model':winner,'model_parameters':info,'validation':selected,'final_test':final_test,'horizon_metrics':horizon_metrics,'calibration':{'enabled':calibrate,'radius':radius,'validation_points':len(errors),'method':'Pooled absolute validation errors; finite-sample quantile; widen-only; no independence guarantee'},'break_screen':break_scan(complete_series),'leaderboard':ranks,'skipped_models':skipped,'summary':summary,'interval_method':method,'explanation':explanation,'limitations':limits,'diagnostics':{'interpretation':'Chronological error and coverage tests are prioritized. Descriptive break screening does not establish structural change.'},'versions':{'python':__import__('sys').version.split()[0],'pandas':pd.__version__,'numpy':np.__version__,'statsmodels':core.statsmodels.__version__,'scipy':core.scipy.__version__},'documentation':['https://www.statsmodels.org/stable/tsa.html']}
    report['answer']=f'{winner} forecasts {config["value_column"]} at {endpoint:,.4g} {config.get("unit","")} by {dates[-1].strftime("%Y-%m-%d")}. Displayed {level}% nominal interval: {low[-1]:,.4g}–{high[-1]:,.4g}. Untouched final-test RMSE: {final_test["rmse"]:.4g}.'
    report['analyst_notes']=str(config.get('analyst_notes',''))[:10000]
    history=pd.DataFrame({'period':series.index.strftime('%Y-%m-%d'),'observed':series.values})
    backtest=pd.DataFrame(cv+final_rows)
    chart=core.chart_svg(series,point,low,high,dates,f'{winner} · {config["value_column"]}',config.get('unit','Units unspecified'))
    for old,new in {'#f9fbfa':'#111d31','#17362d':'#d4e1f5','#d9e2df':'#2d3c53','#e1bd67':'#9c8df4','#1c634c':'#67ead2','#a36b1c':'#b9a4ff','Green: observed · Amber: forecast':'Cyan: observed · Violet: forecast'}.items():chart=chart.replace(old,new)
    return report,history,forecast,backtest,chart


def export_report(folder,result):
    report,history,forecast,backtest,chart=result
    extra=[{'title':'Untouched final test','detail':json.dumps(report['final_test'])},{'title':'Source evidence register','detail':json.dumps(report['source'].get('register',{}))},{'title':'Accuracy by forecast lead','detail':json.dumps(report['horizon_metrics'])},{'title':'Break screening','detail':json.dumps(report['break_screen'])}]
    if report.get('analyst_notes'):extra.append({'title':'Analyst-authored commentary (not model evidence)','detail':report['analyst_notes']})
    report['explanation']=[x for x in report['explanation'] if x['title'] not in {e['title'] for e in extra}]+extra
    files=core.export_report(folder,result)
    (folder/'bi_plus.py').write_bytes(Path(__file__).read_bytes())
    try:
        from .r_exports import export_r
    except ImportError:
        from r_exports import export_r
    export_r(folder,report,history,forecast,backtest)
    (folder/'r_exports.py').write_bytes(Path(__file__).with_name('r_exports.py').read_bytes())
    # Replace rerun entry point so the upgraded validation and scenarios are reproduced.
    (folder/'rerun.py').write_text("import hashlib,json,sys\nfrom pathlib import Path\nfrom bi_engine import load_table\nfrom bi_plus import analyze,export_report\nr=json.loads(Path('findings.json').read_text());p=Path(sys.argv[1]);d=p.read_bytes()\nif hashlib.sha256(d).hexdigest()!=r['source']['sha256']:raise SystemExit('Source hash mismatch')\nf,_,_=load_table(d,p.name,r['configuration'].get('sheet'),r['configuration'].get('orientation','long'))\nexport_report(Path('rerun-output'),analyze(f,r['configuration'],r['source']))\n")
    with core.zipfile.ZipFile(folder/'report.zip','w',core.zipfile.ZIP_DEFLATED) as z:
        for p in folder.iterdir():
            if p.is_file() and p.name!='report.zip':z.write(p,p.name)
    return sorted(p.name for p in folder.iterdir() if p.is_file())

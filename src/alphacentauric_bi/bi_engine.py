"""Local, auditable time-series analysis. No remote services or API credentials."""
import base64
import hashlib
import html
import io
import json
import math
import re
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
import statsmodels
from scipy.stats import norm
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.forecasting.theta import ThetaModel
from statsmodels.tsa.stattools import adfuller
from statsmodels.stats.diagnostic import acorr_ljungbox

MODELS = ['ARIMA', 'SARIMA', 'Holt-Winters', 'Holt', 'Theta', 'Naive', 'Seasonal naive', 'Drift']
FREQUENCIES = {'monthly': 'MS', 'quarterly': 'QS', 'annual': 'YS', 'weekly': 'W-MON', 'daily': 'D'}
SEASONS = {'monthly': 12, 'quarterly': 4, 'annual': 1, 'weekly': 52, 'daily': 7}


def load_table(data, filename, sheet=None, orientation='long'):
    suffix = Path(filename).suffix.lower()
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('Maximum upload size is 10 MiB.')
    if suffix == '.csv':
        try:
            frame = pd.read_csv(io.BytesIO(data), sep=None, engine='python', encoding='utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Save the CSV as UTF-8 first.') from None
        sheets = []
    elif suffix in ('.xlsx', '.xls'):
        if suffix == '.xlsx':
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(x.file_size for x in z.infolist()) > 100 * 1024 * 1024:
                    raise ValueError('Expanded workbook exceeds 100 MiB.')
        with pd.ExcelFile(io.BytesIO(data)) as workbook:
            sheets = workbook.sheet_names
            selected = sheet or sheets[0]
            if selected not in sheets:
                raise ValueError('Unknown worksheet.')
            frame = pd.read_excel(workbook, sheet_name=selected)
        sheet = selected
    else:
        raise ValueError('Upload CSV, XLSX or XLS.')
    if frame.empty or len(frame) > 100000 or len(frame.columns) > 1000:
        raise ValueError('Use a nonempty table with at most 100,000 rows and 1,000 columns.')
    frame.columns = [str(c).strip() for c in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError('Rename duplicate column headings before uploading.')
    if orientation == 'wide':
        periods = [c for c in frame if re.fullmatch(r'(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?', c)]
        if len(periods) < 2:
            raise ValueError('Wide layout needs at least two year/date column headings, e.g. 2022, 2023 or 2023-01.')
        if '_period' in frame or '_value' in frame:
            raise ValueError('Rename reserved columns _period and _value for wide layout.')
        frame = frame.melt(id_vars=[c for c in frame if c not in periods], value_vars=periods, var_name='_period', value_name='_value')
    if len(frame) > 200000:
        raise ValueError('The reshaped dataset exceeds 200,000 rows. Reduce the workbook.')
    return frame, sheets, sheet


def profile(frame, filename, sheets, sheet, orientation):
    numeric = []
    dates = []
    filters = {}
    for c in frame:
        series = frame[c].dropna()
        if len(series) and pd.to_numeric(series, errors='coerce').notna().mean() >= .9:
            numeric.append(c)
        sample = series.astype(str).head(100)
        if c == '_period' or any(x in c.lower() for x in ('date', 'time', 'year', 'month', 'period')):
            dates.append(c)
        elif len(sample) and sample.str.match(r'^\d{4}[-/]\d{1,2}').mean() > .8:
            dates.append(c)
        if 1 <= series.nunique() <= 200 and c not in dates and c not in numeric:
            filters[c] = sorted(set(series.astype(str)), key=str.casefold)
    display = frame.head(8).astype(object).where(frame.head(8).notna(), None)
    return {'filename': filename, 'rows': len(frame), 'columns': list(frame.columns), 'numeric': numeric,
            'dates': dates, 'filters': filters, 'sheets': sheets, 'sheet': sheet, 'orientation': orientation,
            'sample': [[None if x is None else str(x) for x in row] for row in display.values.tolist()]}


def prepare(frame, config):
    date = config['date_column']; value = config['value_column']
    if date == value or date not in frame or value not in frame:
        raise ValueError('Select different existing time and value columns.')
    frequency = config.get('frequency', 'monthly')
    if frequency not in FREQUENCIES:
        raise ValueError('Choose a supported frequency.')
    aggregation = config.get('aggregation', 'sum')
    if aggregation not in ('sum', 'mean', 'last'):
        raise ValueError('Choose sum, mean or last aggregation.')
    selected = frame.copy(); original_rows = len(selected)
    filters = config.get('filters', {})
    for column, target in filters.items():
        if column not in frame or not isinstance(target, str):
            raise ValueError('Invalid segment filter.')
        selected = selected[selected[column].notna() & selected[column].astype(str).eq(target)]
    if selected.empty:
        raise ValueError('No rows match the selected segment.')
    raw_dates = selected[date]
    if pd.api.types.is_datetime64_any_dtype(raw_dates):
        parsed = pd.to_datetime(raw_dates, errors='coerce')
    else:
        strings = raw_dates.astype('string').str.replace(r'\.0$', '', regex=True)
        if pd.api.types.is_numeric_dtype(raw_dates) and not strings.dropna().str.fullmatch(r'(?:19|20)\d{2}').all():
            raise ValueError('Numeric dates must be four-digit years. Convert Excel serial dates to real dates first.')
        parsed = pd.to_datetime(strings, format='mixed', errors='coerce', dayfirst=bool(config.get('dayfirst', False)))
    values = pd.to_numeric(selected[value], errors='coerce')
    invalid_dates = int(parsed.isna().sum())
    nonnumeric = int((values.isna() & selected[value].notna()).sum())
    if invalid_dates:
        raise ValueError(f'{invalid_dates} selected rows have missing/unparseable dates. Fix them first; rows were not discarded.')
    if nonnumeric or np.isinf(values.to_numpy(dtype=float)).any():
        raise ValueError('The value column contains nonnumeric or infinite values. Use numeric cells with consistent units.')
    # Prevent accidental combining of several markets by requiring each varying categorical dimension to be explicit.
    ambiguous = []
    for column in selected:
        if column in (date, value) or column in filters:
            continue
        s = selected[column].dropna()
        if 1 < s.nunique() <= 200 and pd.to_numeric(s, errors='coerce').notna().mean() < .9:
            ambiguous.append(column)
    if ambiguous and not config.get('combine_segments'):
        raise ValueError('Multiple segments remain in '+', '.join(ambiguous)+'. Filter them or explicitly allow combining segments.')
    work = pd.DataFrame({'date': parsed.to_numpy(), 'value': values.to_numpy()}).sort_values('date')
    dates = pd.DatetimeIndex(work['date'])
    if dates.tz is not None:
        raise ValueError('Use timezone-free dates for this market analysis.')
    if dates.max().year - dates.min().year > 200:
        raise ValueError('Date range exceeds 200 years.')
    # No partial missing groups: unresolved source values cannot be silently hidden by aggregation.
    if work['value'].isna().any():
        raise ValueError(f'{int(work.value.isna().sum())} selected values are missing. Resolve source values before forecasting.')
    series = work.set_index('date')['value'].resample(FREQUENCIES[frequency])
    series = series.sum(min_count=1) if aggregation == 'sum' else getattr(series, aggregation)()
    gaps = int(series.isna().sum())
    if len(series) > 2500:
        raise ValueError('Forecast at a coarser frequency; maximum 2,500 periods.')
    policy = config.get('missing_policy', 'reject')
    if policy not in ('reject', 'ffill'):
        raise ValueError('Unknown missing-period policy.')
    if gaps:
        if policy != 'ffill':
            raise ValueError(f'{gaps} time periods are absent. Choose past-value carry-forward or fix the data. Missing periods are not assumed zero.')
        series = series.ffill()
    if series.isna().any():
        raise ValueError('Cannot fill leading missing periods.')
    if len(series) < 20:
        raise ValueError(f'Only {len(series)} regular observations. At least 20 are required for training and historical validation; a standalone CAGR is not a time series.')
    if len(series) > 1000 and config.get('model') in ('SARIMA', 'Compare all'):
        raise ValueError('SARIMA comparison supports up to 1,000 periods. Aggregate to a coarser frequency.')
    if aggregation == 'sum' and any(word in value.lower() for word in ('cagr', 'rate', 'share', 'percent', '%', 'index')):
        raise ValueError('For rates, CAGR, shares or indices, choose mean or last; summing them is not meaningful.')
    quality = {'original_rows': original_rows, 'selected_rows': len(selected), 'observations': len(series),
               'missing_periods_filled': gaps, 'duplicate_timestamps': int(work.date.duplicated().sum()),
               'frequency': frequency, 'aggregation': aggregation, 'filters': filters, 'combined_dimensions': ambiguous,
               'missing_policy': policy, 'first_period': series.index[0].isoformat(), 'last_period': series.index[-1].isoformat()}
    return series.astype(float), quality


def fit_model(name, values, season, spec=None):
    y = np.asarray(values, dtype=float)
    if name in ('ARIMA', 'SARIMA'):
        order, seasonal = spec
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            if name == 'ARIMA':
                result = ARIMA(y, order=order).fit()
            else:
                result = SARIMAX(y, order=order, seasonal_order=seasonal, trend='c' if order[1]+seasonal[1] == 0 else None).fit(disp=False, maxiter=100)
        if not result.mle_retvals.get('converged', True):
            raise ValueError('Optimizer did not converge.')
        intervals = np.asarray(result.conf_int())
        coefficients = []
        for i, name in enumerate(result.param_names):
            clean = lambda v: float(v) if np.isfinite(v) else None
            coefficients.append({'name':name,'estimate':clean(result.params[i]),'standard_error':clean(result.bse[i]),
                                 'p_value':clean(result.pvalues[i]),'lower_95':clean(intervals[i,0]),'upper_95':clean(intervals[i,1])})
        return result, {'order': list(order), 'seasonal_order': list(seasonal), 'aic': float(result.aic), 'coefficients':coefficients}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        if name in ('Holt', 'Holt-Winters'):
            result = ExponentialSmoothing(y, trend='add', damped_trend=True,
                seasonal='add' if name == 'Holt-Winters' else None,
                seasonal_periods=season if name == 'Holt-Winters' else None).fit(optimized=True)
            return result, {'trend': 'additive damped', 'seasonal': 'additive' if name == 'Holt-Winters' else None}
        if name == 'Theta':
            return ThetaModel(y, period=1, deseasonalize=False).fit(), {'theta': 2, 'deseasonalize': False}
    return y, {'method': name}


def predict(name, fitted, horizon, level, season):
    alpha = 1-level/100
    if name in ('ARIMA', 'SARIMA'):
        pred = fitted.get_forecast(steps=horizon)
        point = np.asarray(pred.predicted_mean)
        ci = np.asarray(pred.conf_int(alpha=alpha))
        return point, ci[:, 0], ci[:, 1], 'Model-based prediction intervals; estimated parameters treated as fixed.'
    if name == 'Theta':
        point = np.asarray(fitted.forecast(horizon))
        ci = fitted.prediction_intervals(steps=horizon, alpha=alpha)
        return point, ci['lower'].to_numpy(), ci['upper'].to_numpy(), 'Theta prediction intervals assuming integrated moving-average dynamics and normal innovations.'
    if name in ('Holt', 'Holt-Winters'):
        point = np.asarray(fitted.forecast(horizon))
        residuals = np.asarray(fitted.resid)
    else:
        y = fitted
        if name == 'Naive': point = np.full(horizon, y[-1]); residuals = np.diff(y)
        elif name == 'Seasonal naive': point = np.resize(y[-season:], horizon); residuals = y[season:]-y[:-season]
        else:
            slope = (y[-1]-y[0])/(len(y)-1)
            point = y[-1]+slope*np.arange(1,horizon+1); residuals = np.diff(y)-slope
    scale = float(np.std(residuals, ddof=1))
    width = norm.ppf(1-alpha/2)*scale*np.sqrt(np.arange(1,horizon+1))
    return point, point-width, point+width, 'Approximate normal prediction bands from training residuals, widening by sqrt(horizon); not calibrated model-specific intervals.'


def choose_order(name, training, season):
    # Selection is done on earliest training window only, never withheld validation observations.
    orders = [(0,1,0), (1,1,0), (0,1,1), (1,1,1), (1,0,0), (0,0,1)]
    seasonal_orders = [(0,0,0,0)] if name == 'ARIMA' else [(1,0,0,season), (0,1,1,season)]
    best = None
    for order in orders:
        for seasonal in seasonal_orders:
            try:
                result, info = fit_model(name, training, season, (order,seasonal))
                if np.isfinite(info['aic']) and (best is None or info['aic'] < best[0]):
                    best = (info['aic'], (order,seasonal))
            except Exception:
                continue
    if best is None:
        raise ValueError('No candidate converged for this model.')
    return best[1]


def metrics(actual, predicted, scale):
    error = actual-predicted
    denom = np.abs(actual)+np.abs(predicted)
    smape = np.divide(2*np.abs(error), denom, out=np.zeros_like(error), where=denom != 0).mean()*100
    return {'mae': float(np.abs(error).mean()), 'rmse': float(np.sqrt((error**2).mean())),
            'smape': float(smape), 'mase': float(np.abs(error).mean()/scale) if scale>1e-12 else None}


def chart_svg(series, forecast, lower, upper, future, title, unit):
    historical = np.asarray(series.values); point=np.asarray(forecast)
    all_values=np.concatenate([historical,point,np.asarray(lower),np.asarray(upper)])
    lo=float(np.min(all_values)); hi=float(np.max(all_values)); span=hi-lo or max(abs(lo)*.1,1)
    lo-=span*.1; hi+=span*.1
    width=940; height=370; n=len(historical)+len(point)
    x=lambda i: 80+i/max(n-1,1)*790
    y=lambda v: 300-(v-lo)/(hi-lo)*235
    coords=lambda indices,values:' '.join(f'{x(i):.2f},{y(v):.2f}' for i,v in zip(indices,values))
    indices=list(range(len(historical),n)); polygon=coords(indices,upper)+' '+coords(indices[::-1],np.asarray(lower)[::-1])
    grid=''
    for i in range(5):
        v=lo+(hi-lo)*i/4; yy=y(v)
        grid+=f'<line x1="80" x2="870" y1="{yy:.2f}" y2="{yy:.2f}" stroke="#d9e2df"/><text x="72" y="{yy+4:.2f}" text-anchor="end" font-size="11">{v:,.3g}</text>'
    labels=[(0,series.index[0]),(len(series)-1,series.index[-1]),(n-1,future[-1])]
    axis=''.join(f'<text x="{x(i):.2f}" y="326" text-anchor="middle" font-size="11">{d.strftime("%Y-%m-%d")}</text>' for i,d in labels)
    return f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(title,quote=True)}" viewBox="0 0 {width} {height}"><rect width="940" height="370" rx="12" fill="#f9fbfa"/><g fill="#17362d" font-family="sans-serif"><text x="80" y="28" font-size="17">{html.escape(title)}</text><text x="80" y="48" font-size="11">{html.escape(unit)}</text>{grid}<polygon points="{polygon}" fill="#e1bd67" opacity=".3"/><polyline points="{coords(range(len(historical)),historical)}" fill="none" stroke="#1c634c" stroke-width="2.5"/><polyline points="{coords([len(historical)-1]+indices,np.r_[historical[-1],point])}" fill="none" stroke="#a36b1c" stroke-width="2.5" stroke-dasharray="6 4"/>{axis}<text x="80" y="353" font-size="11">Green: observed · Amber: forecast · Shaded: prediction interval</text></g></svg>'


def analyze(frame, config, source):
    if len(str(config.get('unit',''))) > 120 or len(str(config.get('source_reference',''))) > 500:
        raise ValueError('Keep units under 120 characters and the source citation under 500.')
    model = config.get('model')
    if model not in MODELS+['Compare all']:
        raise ValueError('Choose ARIMA, SARIMA or another model before analysis.')
    horizon = int(config.get('horizon',12)); level=int(config.get('confidence',95))
    if not 1<=horizon<=36 or level not in (80,90,95):
        raise ValueError('Horizon must be 1–36; interval level must be 80, 90 or 95%.')
    season = int(config.get('season', SEASONS.get(config.get('frequency'),12)))
    if not 1<=season<=52:
        raise ValueError('Season length must be 1–52.')
    series,quality = prepare(frame, config); n=len(series)
    if model in ('SARIMA','Holt-Winters','Seasonal naive') and season<2:
        raise ValueError('Seasonal models need a season length of at least 2.')
    seasonal_models={'SARIMA','Holt-Winters','Seasonal naive'}
    minimum = max(20,2*season+8) if model in seasonal_models else 16
    if model=='Compare all' and season>=2 and n>=2*season+11:
        minimum=max(20,2*season+8)
    holdout=min(horizon, max(1,(n-minimum)//3), max(1,n//8))
    first=n-3*holdout
    if first<minimum:
        raise ValueError(f'{model} needs at least {minimum+3} observations for three validation windows; found {n}. Use a nonseasonal model or provide more history.')
    names=MODELS if model=='Compare all' else list(dict.fromkeys([model,'Naive']))
    leaderboard=[]; skipped=[]; specs={}; evidences={}
    for name in names:
        if name in seasonal_models and (season<2 or first<2*season+8):
            skipped.append({'model':name,'reason':'Insufficient seasonal history for training and validation.'}); continue
        try:
            spec=choose_order(name,series.values[:first],season) if name in ('ARIMA','SARIMA') else None
            specs[name]=spec; actuals=[]; predictions=[]; scales=[]; coverage=[]; folds=[]
            for start in [first,first+holdout,first+2*holdout]:
                train=series.values[:start]; actual=series.values[start:start+holdout]
                fit,info=fit_model(name,train,season,spec)
                pred,low,high,_=predict(name,fit,len(actual),level,season)
                if not np.isfinite(np.r_[pred,low,high]).all(): raise ValueError('Forecast contains nonfinite values.')
                lag=season if season>=2 and len(train)>season else 1
                scale=float(np.mean(np.abs(train[lag:]-train[:-lag])))
                mm=metrics(actual,pred,scale)
                folds.append({'train_end':series.index[start-1].isoformat(),'test_start':series.index[start].isoformat(),'test_end':series.index[start+len(actual)-1].isoformat(),**mm})
                scales.append(mm['mase']);actuals.extend(actual);predictions.extend(pred);coverage.extend((actual>=low)&(actual<=high))
                for offset,(a,p,l,u) in enumerate(zip(actual,pred,low,high)):
                    evidences.setdefault(name,[]).append({'period':series.index[start+offset].isoformat(),'actual':float(a),'prediction':float(p),'lower':float(l),'upper':float(u),'train_end':series.index[start-1].isoformat()})
            mm=metrics(np.array(actuals),np.array(predictions),1)
            mm['mase']=float(np.mean([s for s in scales if s is not None])) if any(s is not None for s in scales) else None
            leaderboard.append({'model':name,**mm,'coverage':float(np.mean(coverage)*100),'test_points':len(actuals),'folds':folds,'spec':spec})
        except Exception as exc:
            skipped.append({'model':name,'reason':str(exc)})
    leaderboard.sort(key=lambda x:x['rmse'])
    if not leaderboard: raise ValueError('No model could produce a valid backtest.')
    winner=leaderboard[0]['model'] if model=='Compare all' else model
    selected=next((x for x in leaderboard if x['model']==winner),None)
    if selected is None:
        reason=next((x['reason'] for x in skipped if x['model']==winner),'Fit failed')
        raise ValueError('Selected model failed: '+reason+'. No silent model substitution was performed.')
    fitted,info=fit_model(winner,series.values,season,specs[winner])
    point,lower,upper,interval_method=predict(winner,fitted,horizon,level,season)
    if not np.isfinite(np.r_[point,lower,upper]).all(): raise ValueError('Nonfinite final forecast.')
    future=pd.date_range(series.index[-1],periods=horizon+1,freq=FREQUENCIES[quality['frequency']])[1:]
    historical=pd.DataFrame({'period':series.index.strftime('%Y-%m-%d'),'observed':series.values})
    forecast=pd.DataFrame({'period':future.strftime('%Y-%m-%d'),'forecast':point,'lower':lower,'upper':upper})
    elapsed=(series.index[-1]-series.index[0]).days/365.2425
    years_ahead=(future[-1]-series.index[-1]).days/365.2425
    first_value=float(series.iloc[0]);last_value=float(series.iloc[-1]);final=float(point[-1])
    cagr=lambda a,b,t: float(((b/a)**(1/t)-1)*100) if a>0 and b>0 and t>0 else None
    cagr_allowed=config.get('metric_kind','level')=='level' and not any(word in config['value_column'].lower() for word in ('cagr','rate','share','percent','%','index'))
    hist_cagr=cagr(first_value,last_value,elapsed) if cagr_allowed else None
    projected_cagr=cagr(last_value,final,years_ahead) if cagr_allowed else None
    growth=(final/last_value-1)*100 if last_value>0 else None
    residuals=np.asarray(fitted.resid) if winner in ('ARIMA','SARIMA','Holt','Holt-Winters') else np.diff(series.values)
    residuals=residuals[np.isfinite(residuals)]
    # ARIMA initialization and differencing transient residuals are excluded from residual diagnostics.
    burn=max(2,season if winner=='SARIMA' else 2); residuals=residuals[burn:]
    diagnostics={}
    try:
        if np.ptp(series.values)>0:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                p = float(adfuller(series.values,autolag='AIC',result_object=False)[1])
            if np.isfinite(p): diagnostics['adf_p_value']=p
    except Exception: pass
    try:
        lag=min(10,len(residuals)//5)
        df=sum(specs[winner][0][i] for i in (0,2)) if winner in ('ARIMA','SARIMA') else 0
        if winner=='SARIMA': df+=specs[winner][1][0]+specs[winner][1][2]
        if lag>df and winner != 'Theta':
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                lb=acorr_ljungbox(residuals,lags=[lag],model_df=df,return_df=True)
            p=float(lb['lb_pvalue'].iloc[0]); diagnostics['ljung_box_p_value']=p if np.isfinite(p) else None
            diagnostics['ljung_box_lag']=lag
    except Exception: pass
    baseline=next((x for x in leaderboard if x['model']=='Naive'),None)
    skill=(1-selected['rmse']/baseline['rmse'])*100 if baseline and baseline['rmse']>1e-12 else None
    diagnostics['interpretation']='ADF tests a unit-root null; Ljung–Box tests remaining serial correlation. Diagnostic p-values are not forecast accuracy or proof of market validity.'
    cautions=['Forecasts extrapolate observed patterns; they do not prove causes or incorporate policy, competitors or market shocks.',
              f'{level}% is a nominal prediction interval, not a {level}% chance the whole forecast path is correct.',
              'Intervals omit uncertainty from model choice, data revisions and structural breaks.',
              'Three historical validation windows are a limited test. Model comparison winner has selection optimism; no independent final test set.',
              'Evidence comes from the uploaded file. External source credibility, geographic completeness and units are not independently verified.',
              'CAGR describes annualized endpoint change; it is not a causal driver or a substitute for historical observations.']
    if holdout<horizon: cautions.append(f'Backtests cover {holdout} periods ahead, shorter than the {horizon}-period forecast. Long-horizon accuracy is unverified.')
    if selected['test_points']<20: cautions.append('Fewer than 20 validation observations: interval coverage is a noisy estimate.')
    if quality['missing_periods_filled']: cautions.append('Absent periods were carried forward from past observations; this is an assumption, not measured data.')
    if skill is not None and skill<=0: cautions.append('The selected model did not outperform the naive baseline on validation RMSE.')
    if diagnostics.get('ljung_box_p_value',1) is not None and diagnostics.get('ljung_box_p_value',1)<.05: cautions.append('Residual autocorrelation test indicates unexplained temporal structure (p < 0.05).')
    if np.any(lower<0): cautions.append('Some prediction bounds are negative. They are displayed without clipping; consider a positive-scale model for nonnegative market sizes.')
    if 'synthetic' in source['filename'].lower(): cautions.append('This report uses a synthetic demonstration dataset, not measured market evidence.')
    explanation=[{'title':'What was used','detail':f'{source["filename"]}; time = {config["date_column"]}; value = {config["value_column"]}; {quality["aggregation"]} per {quality["frequency"]} period. Units: {config.get("unit","not specified")}. Uploaded source SHA-256 is recorded. User-provided citation (not verified): {config.get("source_reference") or "not supplied"}.'},
                 {'title':'What was prepared','detail':f'{quality["selected_rows"]} selected rows became {n} chronological observations. {quality["duplicate_timestamps"]} repeated timestamps aggregated; {quality["missing_periods_filled"]} missing periods carried forward. Filters: {json.dumps(quality["filters"])}.'},
                 {'title':'What was fitted','detail':f'{winner}: {json.dumps({k:v for k,v in info.items() if k != "coefficients"})}. ARIMA/SARIMA candidate orders selected by AIC on the first training window; that order remains fixed throughout validation and final fitting. Fitted coefficients and their standard errors are in findings.json.'},
                 {'title':'How it was tested','detail':f'Three expanding training windows, each followed by {holdout} unseen periods. RMSE = {selected["rmse"]:.4g}, MAE = {selected["mae"]:.4g}, sMAPE = {selected["smape"]:.2f}%. Baseline is last observed value. No random train/test split.'},
                 {'title':'What confidence means','detail':f'{interval_method} Historical coverage = {selected["coverage"]:.1f}% over {selected["test_points"]} test points; nominal level = {level}%.'}]
    answer=f'{winner} forecasts {config["value_column"]} at {final:,.4g} {config.get("unit","")} by {future[-1].strftime("%Y-%m-%d")}, with a {level}% nominal prediction interval of {lower[-1]:,.4g} to {upper[-1]:,.4g}. Validation RMSE is {selected["rmse"]:,.4g}.'
    report={'title':'Alphacentauric Business Intelligence','created_at':datetime.now(timezone.utc).isoformat(),'answer':answer,'source':source,'configuration':config,'data_quality':quality,
            'model':winner,'model_parameters':info,'validation':selected,'leaderboard':leaderboard,'skipped_models':skipped,'diagnostics':diagnostics,
            'summary':{'latest_observed':last_value,'forecast_endpoint':final,'historical_cagr_percent':hist_cagr,'forecast_cagr_percent':projected_cagr,'endpoint_change_percent':growth,'rmse_improvement_vs_naive_percent':skill},
            'interval_method':interval_method,'explanation':explanation,'limitations':cautions,
            'versions':{'python':__import__('sys').version.split()[0],'pandas':pd.__version__,'numpy':np.__version__,'statsmodels':statsmodels.__version__,'scipy':scipy.__version__},
            'documentation':['https://www.statsmodels.org/stable/tsa.html','https://www.statsmodels.org/stable/examples/notebooks/generated/theta-model.html']}
    chart=chart_svg(series,point,lower,upper,future,f'{winner} · {config["value_column"]}',config.get('unit','Units not specified'))
    return report,historical,forecast,pd.DataFrame(evidences[winner]),chart


def export_report(folder, result):
    report,history,forecast,backtest,chart=result
    folder.mkdir(parents=True,exist_ok=True)
    history.to_csv(folder/'history.csv',index=False); forecast.to_csv(folder/'forecast.csv',index=False); backtest.to_csv(folder/'backtest.csv',index=False)
    (folder/'forecast.svg').write_text(chart,encoding='utf-8')
    (folder/'findings.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    esc=lambda x:html.escape(str(x))
    sections=''.join('<h2>'+esc(e['title'])+'</h2><p>'+esc(e['detail'])+'</p>' for e in report['explanation'])
    ranks='<table><tr><th>Model</th><th>RMSE</th><th>MAE</th><th>sMAPE %</th><th>Coverage %</th></tr>'+''.join('<tr>'+''.join('<td>'+esc(x[k] if k=='model' else round(x[k],4))+'</td>' for k in ('model','rmse','mae','smape','coverage'))+'</tr>' for x in report['leaderboard'])+'</table>'
    content='<!doctype html><html lang="en"><meta charset="utf-8"><title>Alphacentauric market report</title><style>body{max-width:1000px;margin:40px auto;padding:24px;color:#17362d;font:15px/1.65 system-ui}h1{font:36px Georgia}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #d9e2df;text-align:left}svg{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}</style><h1>Alphacentauric Business Intelligence</h1><p>Market segment analysis · '+esc(report['created_at'])+'</p><p>'+esc(report['answer'])+'</p>'+chart+'<h2>Validation evidence</h2>'+ranks+sections+'<h2>Forecast table</h2>'+forecast.to_html(index=False,escape=True)+'<h2>Limits and interpretation</h2><ul>'+''.join('<li>'+esc(x)+'</li>' for x in report['limitations'])+'</ul><h2>Source and reproducibility</h2><pre>'+esc(json.dumps({'source':report['source'],'configuration':report['configuration'],'versions':report['versions'],'summary':report['summary'],'diagnostics':report['diagnostics']},indent=2))+'</pre></html>'
    (folder/'report.html').write_text(content,encoding='utf-8')
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    styles=getSampleStyleSheet(); story=[]
    def para(text,style='BodyText'):
        spacer=Spacer(1,10)
        if style in ('Heading2','Title'): spacer.keepWithNext=True
        story.extend([Paragraph(esc(text),styles[style]),spacer])
    para('Alphacentauric Business Intelligence','Title');para(report['answer'])
    if 'synthetic' in report['source']['filename'].lower(): para('SYNTHETIC DEMONSTRATION DATA: not measured market evidence.')
    # Vector chart generated from the same numerical forecast, embedded in the PDF.
    from reportlab.graphics.shapes import Drawing,PolyLine,Polygon,String,Line
    draw=Drawing(480,205); vals=np.r_[history.observed,forecast['lower'],forecast['upper']]; lo=float(vals.min()); hi=float(vals.max()); span=hi-lo or 1
    xx=lambda i:40+i/(len(history)+len(forecast)-1)*420
    yy=lambda v:35+(v-lo)/span*140
    inds=list(range(len(history),len(history)+len(forecast)))
    polygon=[]
    for i,v in zip(inds,forecast['upper']): polygon.extend([xx(i),yy(v)])
    for i,v in zip(inds[::-1],forecast['lower'].to_numpy()[::-1]): polygon.extend([xx(i),yy(v)])
    draw.add(Polygon(polygon,fillColor=colors.HexColor('#f3e4bc'),strokeColor=None))
    observed=[]
    for i,v in enumerate(history.observed): observed.extend([xx(i),yy(v)])
    predicted=[xx(len(history)-1),yy(history.observed.iloc[-1])]
    for i,v in zip(inds,forecast['forecast']): predicted.extend([xx(i),yy(v)])
    draw.add(PolyLine(observed,strokeColor=colors.HexColor('#1c634c'),strokeWidth=1.5));draw.add(PolyLine(predicted,strokeColor=colors.HexColor('#a36b1c'),strokeWidth=1.5))
    draw.add(String(40,190,report['configuration']['value_column'][:65],fontSize=10))
    for i,label in [(0,history.period.iloc[0]),(len(history)-1,history.period.iloc[-1]),(len(history)+len(forecast)-1,forecast.period.iloc[-1])]: draw.add(String(xx(i),17,label,fontSize=7,textAnchor='middle'))
    for v in [lo,(lo+hi)/2,hi]: draw.add(String(35,yy(v),f'{v:.3g}',fontSize=7,textAnchor='end'))
    draw.add(String(40,0,'Observed: green | Forecast: amber | Shaded: nominal prediction interval',fontSize=7));story.append(draw);story.append(Spacer(1,12))
    para('Validation evidence','Heading2')
    table=[['Model','RMSE','MAE','sMAPE','Coverage']]+[[x['model'],f'{x["rmse"]:.4g}',f'{x["mae"]:.4g}',f'{x["smape"]:.1f}%',f'{x["coverage"]:.1f}%'] for x in report['leaderboard']]
    t=Table(table,repeatRows=1); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8efe9')),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),8)]));story.append(t)
    for e in report['explanation']: para(e['title'],'Heading2');para(e['detail'])
    para('Forecast periods','Heading2')
    table=[['Period','Forecast','Lower','Upper']]+[[x.period,f'{x.forecast:.5g}',f'{x.lower:.5g}',f'{x.upper:.5g}'] for x in forecast.itertuples()]
    t=Table(table,repeatRows=1);t.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8efe9'))]));story.append(t)
    para('Limits and interpretation','Heading2')
    for line in report['limitations']: para(line)
    para('Source, parameters and reproducibility','Heading2')
    for key in ('source','configuration','versions','summary','diagnostics'): para(key+': '+json.dumps(report[key]))
    SimpleDocTemplate(str(folder/'report.pdf'),pagesize=A4,rightMargin=40,leftMargin=40).build(story)
    # A standalone rerun entry point and engine copy accompany the report. Input is not bundled.
    engine=Path(__file__).read_bytes(); (folder/'bi_engine.py').write_bytes(engine)
    (folder/'rerun.py').write_text("import hashlib,json,sys\nfrom pathlib import Path\nfrom bi_engine import load_table,analyze,export_report\nr=json.loads(Path('findings.json').read_text())\np=Path(sys.argv[1]);d=p.read_bytes()\nif hashlib.sha256(d).hexdigest()!=r['source']['sha256']: raise SystemExit('Input SHA-256 differs from the report source.')\nf,_,_=load_table(d,p.name,r['configuration'].get('sheet'),r['configuration'].get('orientation','long'))\nexport_report(Path('rerun-output'),analyze(f,r['configuration'],r['source']))\n",encoding='utf-8')
    requirements = Path(__file__).parent/'data'/'requirements.txt'
    if not requirements.is_file():requirements=Path(__file__).parent/'requirements.txt'
    if requirements.is_file(): (folder/'requirements.txt').write_bytes(requirements.read_bytes())
    (folder/'README.txt').write_text('Report evidence is in history.csv, forecast.csv, backtest.csv and findings.json.\nInstall requirements from the app, then run: python rerun.py /path/to/original-file\nThe uploaded input is not included. Verify its SHA-256 against findings.json before rerunning.\n',encoding='utf-8')
    with zipfile.ZipFile(folder/'report.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in folder.iterdir():
            if p.name!='report.zip' and p.is_file(): z.write(p,p.name)
    return sorted(p.name for p in folder.iterdir() if p.is_file())

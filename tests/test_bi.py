import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from alphacentauric_bi.bi_engine import load_table,profile,prepare,analyze,export_report

class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.frame=pd.DataFrame({'date':pd.date_range('2020-01-01',periods=60,freq='MS'),'value':100+np.arange(60)*.6+np.sin(np.arange(60)*2*np.pi/12)*8,'country':['Germany']*60})
        self.config={'date_column':'date','value_column':'value','frequency':'monthly','aggregation':'sum','model':'ARIMA','season':12,'horizon':6,'confidence':95,'unit':'USD million','filters':{'country':'Germany'}}
    def test_missing_period_not_zero(self):
        frame=self.frame.drop(index=10)
        with self.assertRaisesRegex(ValueError,'absent'): prepare(frame,self.config)
        series,q=prepare(frame,dict(self.config,missing_policy='ffill'))
        self.assertEqual(series.iloc[10],series.iloc[9]);self.assertEqual(q['missing_periods_filled'],1)
    def test_source_missing_values_not_hidden(self):
        frame=self.frame.copy();frame.loc[10,'value']=np.nan
        with self.assertRaisesRegex(ValueError,'values are missing'):prepare(frame,self.config)
    def test_bad_dates_not_discarded(self):
        frame=self.frame.copy();frame['date']=frame['date'].astype(str);frame.loc[5,'date']='bad date'
        with self.assertRaisesRegex(ValueError,'not discarded'):prepare(frame,self.config)
    def test_ambiguous_market_requires_consent(self):
        frame=pd.concat([self.frame,self.frame.assign(country='India')])
        c=dict(self.config,filters={})
        with self.assertRaisesRegex(ValueError,'Multiple segments'):prepare(frame,c)
        series,q=prepare(frame,dict(c,combine_segments=True));self.assertAlmostEqual(series.iloc[0],200)
    def test_rates_cannot_sum_or_get_cagr(self):
        frame=self.frame.rename(columns={'value':'CAGR'})
        with self.assertRaisesRegex(ValueError,'summing'):prepare(frame,dict(self.config,value_column='CAGR'))
        r=analyze(frame,dict(self.config,value_column='CAGR',aggregation='mean',metric_kind='rate',model='Naive'),{'filename':'rate.csv'})[0]
        self.assertIsNone(r['summary']['historical_cagr_percent'])
    def test_cagr_single_observation_rejected(self):
        with self.assertRaisesRegex(ValueError,'standalone CAGR'):prepare(self.frame.head(1),self.config)
    def test_wide_excel_years(self):
        buf=io.BytesIO()
        # Input fixture only: this test does not author a user-facing workbook.
        from openpyxl import Workbook
        w=Workbook();s=w.active;s.title='Markets';s.append(['country',2020,2021,2022]);s.append(['India',1,2,3]);w.save(buf)
        f,sheets,sheet=load_table(buf.getvalue(),'input.xlsx','Markets','wide')
        self.assertEqual(sheets,['Markets']);self.assertEqual(f['_value'].tolist(),[1,2,3]);self.assertEqual(f['_period'].tolist(),['2020','2021','2022'])
    def test_arima_validation_and_export(self):
        result=analyze(self.frame,self.config,{'filename':'synthetic.csv','sha256':'test'})
        r,h,f,b,svg=result
        self.assertEqual(r['model'],'ARIMA');self.assertEqual(len(f),6)
        self.assertIn('coefficients',r['model_parameters'])
        self.assertTrue(np.isfinite(f[['forecast','lower','upper']].values).all())
        self.assertTrue((f.lower<=f.forecast).all());self.assertTrue((f.forecast<=f.upper).all())
        self.assertEqual(len(r['validation']['folds']),3)
        for row in b.itertuples():self.assertLess(row.train_end,row.period)
        self.assertIn('Naive',[x['model'] for x in r['leaderboard']])
        self.assertEqual(f.period.iloc[0],'2025-01-01')
        with tempfile.TemporaryDirectory() as temp:
            files=export_report(Path(temp),result)
            self.assertIn('report.pdf',files)
            self.assertTrue((Path(temp)/'report.pdf').read_bytes().startswith(b'%PDF'))
            json.loads((Path(temp)/'findings.json').read_text())
            with zipfile.ZipFile(Path(temp)/'report.zip') as z:self.assertIsNone(z.testzip())
    def test_selected_seasonal_model_does_not_fallback(self):
        with self.assertRaisesRegex(ValueError,'needs at least'):analyze(self.frame.head(25),dict(self.config,model='SARIMA'),{'filename':'short.csv'})
    def test_zero_and_negative_no_invalid_cagr(self):
        result=analyze(self.frame.assign(value=-np.arange(60)),dict(self.config,model='Naive'),{'filename':'negative.csv'})
        self.assertIsNone(result[0]['summary']['historical_cagr_percent'])
        json.dumps(result[0],allow_nan=False)
    def test_geographic_profile(self):
        p=profile(self.frame,'x.csv',[],None,'long')
        self.assertIn('country',p['filters']);self.assertIn('value',p['numeric']);self.assertIn('date',p['dates'])
    def test_all_eight_model_families(self):
        sample=Path(__file__).resolve().parents[1]/'examples/market_sample.csv'
        frame,_,_=load_table(sample.read_bytes(),sample.name)
        config=dict(self.config,value_column='market_size_usd_million',model='Compare all')
        result=analyze(frame,config,{'filename':'synthetic.csv'})
        self.assertEqual(len(result[0]['leaderboard']),8,result[0]['skipped_models'])
        self.assertEqual(result[0]['skipped_models'],[])
    def test_order_search_never_uses_held_out_periods(self):
        from unittest.mock import patch
        from alphacentauric_bi import bi_engine
        original=bi_engine.choose_order
        lengths=[]
        def wrapped(name,training,season):
            lengths.append(len(training))
            return original(name,training,season)
        with patch.object(bi_engine,'choose_order',side_effect=wrapped):
            report=analyze(self.frame,self.config,{'filename':'x.csv'})[0]
        first_test=report['validation']['folds'][0]['test_start']
        self.assertEqual(lengths,[int((self.frame.date<pd.Timestamp(first_test)).sum())])

if __name__=='__main__':unittest.main()

import sys,unittest, tempfile, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from alphacentauric_bi import bi_plus as plus

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.frame=pd.read_csv(Path(__file__).resolve().parents[1]/'examples/market_sample.csv')
        self.config=dict(date_column='date',value_column='market_size_usd_million',filters={'country':'Germany'},frequency='monthly',aggregation='mean',model='Naive',season=12,horizon=12,confidence=95,review_accepted=True,unit='USD million')
    def test_review_blocks_mixed_units(self):
        f=self.frame.copy();f['currency']=np.where(np.arange(len(f))<100,'USD','EUR')
        self.assertFalse(plus.review(f,self.config)['ready'])
    def test_acknowledgement_required(self):
        with self.assertRaisesRegex(ValueError,'acknowledge'):plus.analyze(self.frame,dict(self.config,review_accepted=False),{'filename':'sample.csv'})
    def test_final_test_excluded_and_calibration_widens(self):
        r,h,f,b,_=plus.analyze(self.frame,self.config,{'filename':'sample.csv'})
        cv=b[b.partition=='rolling_validation'];hold=b[b.partition=='untouched_final_test']
        self.assertLess(cv.period.max(),hold.period.min())
        self.assertTrue((b.train_end<b.period).all())
        self.assertEqual(len(cv),3*len(hold))
        self.assertGreaterEqual(r['final_test']['coverage'],r['final_test']['nominal_coverage'])
        self.assertTrue((f.downside<f.forecast).all());self.assertTrue((f.forecast<f.upside).all())
        changed=self.frame.copy();changed.loc[(changed.country=='Germany') & (changed.date>=hold.period.min()[:10]),'market_size_usd_million']*=3
        r2=plus.analyze(changed,self.config,{'filename':'sample.csv'})[0]
        self.assertEqual(r['calibration']['radius'],r2['calibration']['radius'])
        self.assertEqual(r['validation']['rmse'],r2['validation']['rmse'])
    def test_all_extended_candidates_and_export(self):
        result=plus.analyze(self.frame,dict(self.config,model='Compare all'),{'filename':'synthetic.csv','sha256':'test'})
        self.assertEqual(len(result[0]['leaderboard']),12,result[0]['skipped_models'])
        self.assertFalse(result[0]['skipped_models'])
        with tempfile.TemporaryDirectory() as d:
            plus.export_report(Path(d),result)
            with zipfile.ZipFile(Path(d)/'report.zip') as z:
                self.assertIn('bi_plus.py',z.namelist());self.assertIn('report.Rmd',z.namelist());self.assertIn('verification.R',z.namelist());self.assertIn('r_exports.py',z.namelist());self.assertIsNone(z.testzip())
    def test_r_export_embeds_data_as_a_quoted_string(self):
        import json
        from alphacentauric_bi.r_exports import export_r
        result=plus.analyze(self.frame,self.config,{'filename':'sample.csv','sha256':'test'})
        result[0]['analyst_notes']='Unicode: café.\n```\nstop("injected")\n```'
        with tempfile.TemporaryDirectory() as d:
            export_r(Path(d),*result[:4])
            script=(Path(d)/'verification.R').read_text()
            line=next(x for x in script.splitlines() if x.startswith('evidence <-'))
            data=json.loads(json.loads(line[len('evidence <- jsonlite::fromJSON('):-1]))
            self.assertEqual(data['report']['analyst_notes'],result[0]['analyst_notes'])
            self.assertEqual(len(data['history']),72)
            markdown=(Path(d)/'report.Rmd').read_text()
            self.assertEqual(sum(line.startswith('```') for line in markdown.splitlines()),18)
            self.assertNotIn('\nstop("injected")\n',markdown)

    def test_future_driver_required(self):
        with self.assertRaisesRegex(ValueError,'driver'):plus.analyze(self.frame,dict(self.config,model='SARIMAX'),{'filename':'sample.csv'})
    def test_sarimax_conditional_driver(self):
        rng=np.random.default_rng(4);n=90;x=np.cumsum(rng.normal(size=n));y=100+2*x+rng.normal(size=n)
        f=pd.DataFrame({'date':pd.date_range('2018-01-01',periods=n,freq='MS'),'value':y,'driver':x})
        c=dict(self.config,value_column='value',filters={},model='SARIMAX',season=1,driver_column='driver',future_driver=[float(x[-1])]*12)
        r,_,pred,_,_=plus.analyze(f,c,{'filename':'driver.csv'})
        self.assertEqual(r['model'],'SARIMAX');self.assertTrue(np.isfinite(pred.forecast).all())
        self.assertTrue(any('held-out driver' in s for s in r['limitations']))
    def test_chosen_training_window(self):
        result=plus.analyze(self.frame,dict(self.config,window_start='2022-01-01'),{'filename':'sample.csv'})
        self.assertEqual(len(result[1]),48)

if __name__=='__main__':unittest.main()

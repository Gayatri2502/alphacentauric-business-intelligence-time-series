import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from fastapi.testclient import TestClient
from alphacentauric_bi.cloud import app

class HostedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)
        cls.raw=(ROOT/'src/alphacentauric_bi/data/kaggle_retail_monthly.csv').read_bytes()
        cls.dataset={'filename':'kaggle_retail.csv','data':base64.b64encode(cls.raw).decode(),'orientation':'long'}
        cls.config=dict(date_column='date',value_column='sales',filters={'region':'West','category':'Technology'},frequency='monthly',aggregation='sum',missing_policy='reject',model='SARIMA',season=12,horizon=6,confidence=95,validation_folds=3,review_accepted=True,unit='source sales units',source_evidence={'publisher':'Kaggle · rohitsahoo','url':'https://www.kaggle.com/datasets/rohitsahoo/sales-forecasting','figure_type':'observed'})
    def post(self,action,**extra):return self.client.post('/api/cloud/'+action,json={'dataset':self.dataset,'config':self.config,**extra})
    def test_real_kaggle_workflow(self):
        r=self.post('inspect');self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['rows'],573)
        r=self.post('review');self.assertTrue(r.json()['ready'],r.text)
        r=self.post('analyze');self.assertEqual(r.status_code,200,r.text[:500]);job=r.json()
        self.assertEqual(job['report']['model'],'SARIMA');self.assertEqual(len(job['forecast']),6)
        self.assertEqual(job['report']['source']['sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(len(job['charts']['history']),47)
        self.assertTrue(base64.b64decode(job['filesData']['report.pdf']).startswith(b'%PDF'))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(job['filesData']['report.zip']))) as z:
            self.assertIsNone(z.testzip());self.assertIn('report.Rmd',z.namelist());self.assertIn('verification.R',z.namelist())
        revised=self.post('revise',previous=job['report'],previous_id=job['id'],forecast=job['forecast'],charts=job['charts'],notes='Historical retail analysis.').json()
        self.assertEqual(revised['forecast'],job['forecast']);self.assertEqual(revised['report']['revision_of'],job['id'])
    def test_compare_all(self):
        r=self.post('analyze',config=dict(self.config,model='Compare all'));self.assertEqual(r.status_code,200,r.text[:500]);self.assertGreaterEqual(len(r.json()['report']['leaderboard']),10)
    def test_review_is_mandatory(self):self.assertEqual(self.post('analyze',config=dict(self.config,review_accepted=False)).status_code,400)
    def test_no_shared_server_library(self):self.assertEqual(self.client.get('/api/library').status_code,404)
    def test_origin_and_bounds(self):
        self.assertEqual(self.client.post('/api/cloud/inspect',json={},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.post('inspect',dataset={'filename':'bad.csv','data':'%%%'}).status_code,400)
    def test_batch(self):
        r=self.post('analyze',config=dict(self.config,model='Naive',batch_column='category',filters={'region':'West'}));self.assertEqual(r.status_code,200,r.text[:500]);self.assertEqual(len(r.json()['batch']['rows']),3);self.assertEqual(len(r.json()['members']),3)
    def test_sarimax(self):
        r=self.post('analyze',config=dict(self.config,model='SARIMAX',driver_column='order_lines',future_driver=[40]*6));self.assertEqual(r.status_code,200,r.text[:500]);self.assertEqual(r.json()['report']['model'],'SARIMAX')

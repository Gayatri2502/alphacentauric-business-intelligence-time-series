import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from alphacentauric_bi.security import Accounts
from alphacentauric_bi import app as local
from alphacentauric_bi.asgi import app as asgi
from fastapi.testclient import TestClient

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.accounts=Accounts(self.temp.name)
        self.accounts.add('viewer','long-test-passphrase','viewer')
    def tearDown(self):self.temp.cleanup()
    def test_password_reset_revokes_sessions(self):
        token,csrf,role=self.accounts.login('viewer','long-test-passphrase','local')
        self.assertEqual(role,'viewer');self.assertEqual(self.accounts.authenticate('bi_session='+token)['csrf'],csrf)
        self.accounts.add('viewer','different-long-passphrase','analyst')
        self.assertIsNone(self.accounts.authenticate('bi_session='+token))
    def test_expiry_and_logout(self):
        token,_,_=self.accounts.login('viewer','long-test-passphrase','local')
        p=self.accounts.authenticate('bi_session='+token);self.accounts.logout(p)
        self.assertIsNone(self.accounts.authenticate('bi_session='+token))
        token,_,_=self.accounts.login('viewer','long-test-passphrase','local')
        with self.accounts.db() as db:db.execute('UPDATE sessions SET seen=?',(time.time()-1801,))
        self.assertIsNone(self.accounts.authenticate('bi_session='+token))
    def test_login_throttle(self):
        for _ in range(5):
            with self.assertRaisesRegex(ValueError,'Invalid credentials'):self.accounts.login('viewer','wrong','local')
        with self.assertRaisesRegex(ValueError,'temporarily unavailable'):self.accounts.login('viewer','long-test-passphrase','local')
    def test_route_csrf_viewer_and_logout(self):
        with patch.object(local,'ACCOUNTS',self.accounts):
            with TestClient(asgi,base_url='http://127.0.0.1:8000') as c:
                self.assertEqual(c.get('/api/projects').status_code,401)
                login=c.post('/api/login',json={'name':'viewer','password':'long-test-passphrase'})
                self.assertEqual(login.status_code,200,login.text);csrf=login.json()['csrf']
                self.assertEqual(c.get('/api/session').json()['role'],'viewer')
                self.assertEqual(c.post('/api/datasets',json={}).status_code,403)
                self.assertEqual(c.post('/api/datasets',json={},headers={'X-CSRF-Token':csrf}).status_code,403)
                self.assertEqual(c.post('/api/logout',json={},headers={'X-CSRF-Token':csrf}).status_code,200)
                self.assertEqual(c.get('/api/session').status_code,401)

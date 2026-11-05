import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from alphacentauric_bi import app
spec=importlib.util.spec_from_file_location('backup',ROOT/'tools/backup.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)

class HardeningTests(unittest.TestCase):
    def test_file_boundaries(self):
        for data,name in [(b'','a.csv'),(b'x','a.exe'),(b'x'*(10*1024*1024+1),'a.csv')]:
            with self.assertRaises(ValueError):app.validate_upload(data,name)
        self.assertEqual(app.validate_upload(b'date,value\n2020,1','a.csv'),b'date,value\n2020,1')
    def test_zip_expansion(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('sheet.xml',b'0'*(101*1024*1024))
        with self.assertRaises(ValueError):app.validate_upload(b.getvalue(),'a.xlsx')
    def test_interrupted_job_survives(self):
        old=app.ROOT
        try:
            with tempfile.TemporaryDirectory() as d:
                app.ROOT=Path(d);key='a'*32
                app.JOBS[key]={'id':key,'status':'running','stage':'Queued'};app.persist_job(key);app.JOBS.pop(key)
                self.assertEqual(app.get_job(key)['status'],'failed')
        finally:app.ROOT=old;app.JOBS.pop('a'*32,None)
    def test_backup_restore(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'data';source.mkdir();(source/'test.json').write_text('{"x":1}')
            ops.backup(source,root/'backup.zip');ops.restore(root/'backup.zip',root/'restored')
            self.assertEqual((root/'restored/test.json').read_bytes(),(source/'test.json').read_bytes())
            with self.assertRaises(ValueError):ops.restore(root/'backup.zip',root/'restored')
    def test_restore_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);payload=b'x'
            import hashlib
            with zipfile.ZipFile(root/'evil.zip','w') as z:
                z.writestr('../escape',payload);z.writestr('backup-manifest.json',json.dumps({'../escape':hashlib.sha256(payload).hexdigest()}))
            with self.assertRaises(ValueError):ops.restore(root/'evil.zip',root/'restored')
            self.assertFalse((root/'escape').exists())

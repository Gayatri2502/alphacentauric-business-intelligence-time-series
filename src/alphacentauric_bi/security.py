"""Single-workspace accounts, revocable sessions and role checks.

One deployment is one workspace. Separate deployments are required for separate
customers until database-enforced multi-tenant isolation has been implemented.
"""
import argparse
from contextlib import contextmanager
import getpass
import hashlib
from http.cookies import SimpleCookie
import secrets
import sqlite3
import time
from pathlib import Path

class Accounts:
    def __init__(self, root):
        self.path=Path(root)/'accounts.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('owner','analyst','viewer')));
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user TEXT NOT NULL REFERENCES users(name) ON DELETE CASCADE,csrf TEXT NOT NULL,created REAL NOT NULL,seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,failures INTEGER NOT NULL,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL NOT NULL,actor TEXT NOT NULL,event TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()
    def add(self,name,password,role):
        if not name or len(name)>100 or role not in ('owner','analyst','viewer'):raise ValueError('Invalid account or role')
        if len(password)<14 or len(password)>1024:raise ValueError('Use a password of 14–1024 characters')
        salt=secrets.token_hex(16)
        digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
        with self.db() as db:
            db.execute('INSERT INTO users VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET password=excluded.password,role=excluded.role',(name,salt+':'+digest,role))
            db.execute('DELETE FROM sessions WHERE user=?',(name,))
            db.execute('INSERT INTO audit(at,actor,event) VALUES(?,?,?)',(time.time(),name,'account_configured'))
    def login(self,name,password,ip):
        now=time.time();keys=['user:'+name,'ip:'+ip]
        with self.db() as db:
            for key in keys:
                row=db.execute('SELECT failures,until FROM attempts WHERE key=?',(key,)).fetchone()
                if row and row[0]>=5 and row[1]>now:raise ValueError('Sign-in temporarily unavailable. Try again in 15 minutes.')
            row=db.execute('SELECT password,role FROM users WHERE name=?',(name,)).fetchone()
            stored=row[0] if row else '0'*32+':'+'0'*128
            salt,expected=stored.split(':')
            actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
            if not row or not secrets.compare_digest(actual,expected):
                for key in keys:
                    db.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=CASE WHEN until<? THEN 1 ELSE failures+1 END,until=excluded.until',(key,now+900,now))
                db.execute('INSERT INTO audit(at,actor,event) VALUES(?,?,?)',(now,'anonymous','login_failed'))
                failure=True
            else:
                failure=False
                db.execute('DELETE FROM attempts WHERE key=?',(keys[0],))
                token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE created<? OR seen<?',(now-43200,now-1800))
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),name,csrf,now,now))
                db.execute('INSERT INTO audit(at,actor,event) VALUES(?,?,?)',(now,name,'login_success'))
        if failure:raise ValueError('Invalid credentials')
        return token,csrf,row[1]
    def authenticate(self,cookie):
        try:
            cookies=SimpleCookie();cookies.load(cookie or '');token=cookies['bi_session'].value
        except (KeyError,ValueError):return None
        digest=hashlib.sha256(token.encode()).hexdigest();now=time.time()
        with self.db() as db:
            row=db.execute('SELECT u.name,u.role,s.csrf,s.created,s.seen FROM sessions s JOIN users u ON u.name=s.user WHERE token=?',(digest,)).fetchone()
            if not row or row[3]<now-43200 or row[4]<now-1800:
                db.execute('DELETE FROM sessions WHERE token=?',(digest,));return None
            db.execute('UPDATE sessions SET seen=? WHERE token=?',(now,digest))
            return {'name':row[0],'role':row[1],'csrf':row[2],'token_hash':digest}
    def logout(self,principal):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?',(principal['token_hash'],))
            db.execute('INSERT INTO audit(at,actor,event) VALUES(?,?,?)',(time.time(),principal['name'],'logout'))

if __name__=='__main__':
    p=argparse.ArgumentParser(description='Create or reset an account in this deployment workspace')
    p.add_argument('--data-dir',required=True);p.add_argument('--name',required=True);p.add_argument('--role',choices=['owner','analyst','viewer'],default='analyst')
    args=p.parse_args();password=getpass.getpass('New password (at least 14 characters): ')
    if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ')
    Accounts(args.data_dir).add(args.name,password,args.role);print('Account saved; previous sessions revoked.')

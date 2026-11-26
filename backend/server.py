#!/usr/bin/env python3
"""Shared data service for the openly accessible creators prototype."""
import json, os, re, sqlite3, tempfile, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(os.environ.get('CREATORS_DATA_DIR', './data')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
FILES = ROOT / 'files'; FILES.mkdir(exist_ok=True)
DB = ROOT / 'creators.sqlite3'
MAX_FILE = 30 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')

def db():
    con = sqlite3.connect(DB, timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated INTEGER NOT NULL)')
    return con

class Handler(BaseHTTPRequestHandler):
    server_version = 'CreatorsAPI/1'
    def cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
    def reply(self, code, obj):
        data=json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code);self.cors();self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def read(self, limit):
        try: n=int(self.headers.get('Content-Length','-1'))
        except ValueError: raise ValueError('Invalid length')
        if n<0 or n>limit: raise ValueError('Payload too large')
        data=self.rfile.read(n)
        if len(data)!=n: raise ValueError('Incomplete request')
        return data
    def pathparts(self):
        return [p for p in urlsplit(self.path).path.split('/') if p]
    def do_OPTIONS(self):
        self.send_response(204);self.cors();self.send_header('Content-Length','0');self.end_headers()
    def do_GET(self):
        parts=self.pathparts()
        if parts==['api','profiles']:
            with db() as con: rows=con.execute('SELECT payload FROM profiles ORDER BY updated DESC').fetchall()
            return self.reply(200, [json.loads(r[0]) for r in rows])
        if len(parts)==3 and parts[:2]==['api','files'] and ID.fullmatch(parts[2]):
            path=FILES/parts[2]
            if not path.is_file(): return self.reply(404,{'error':'not found'})
            self.send_response(200);self.cors();self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment');self.send_header('Content-Length',str(path.stat().st_size));self.end_headers()
            with path.open('rb') as f:
                while chunk:=f.read(65536): self.wfile.write(chunk)
            return
        return self.reply(404,{'error':'not found'})
    def do_POST(self):
        parts=self.pathparts()
        try:
            if parts==['api','files']:
                raw=self.read(MAX_FILE); from uuid import uuid4
                file_id=uuid4().hex
                fd,tmp=tempfile.mkstemp(dir=FILES,prefix='.upload-')
                try:
                    with os.fdopen(fd,'wb') as f: f.write(raw)
                    os.replace(tmp, FILES/file_id)
                finally:
                    if os.path.exists(tmp):os.unlink(tmp)
                return self.reply(201,{'id':file_id})
            if parts==['api','profiles']:
                obj=json.loads(self.read(MAX_JSON))
                if not isinstance(obj,dict) or not isinstance(obj.get('id'),str) or not ID.fullmatch(obj['id']) or not isinstance(obj.get('name'),str) or not obj['name'].strip() or not isinstance(obj.get('roles'),list):return self.reply(400,{'error':'invalid profile'})
                with db() as con:
                    try:con.execute('INSERT INTO profiles VALUES(?,?,?)',(obj['id'],json.dumps(obj,ensure_ascii=False),time.time_ns()))
                    except sqlite3.IntegrityError:return self.reply(409,{'error':'profile exists'})
                return self.reply(201,{'ok':True})
            if parts==['api','seed']:
                records=json.loads(self.read(MAX_JSON))
                if not isinstance(records,list) or len(records)>100:return self.reply(400,{'error':'invalid seed'})
                with db() as con:
                    for p in records:
                        if isinstance(p,dict) and p.get('demo') is True and isinstance(p.get('id'),str) and ID.fullmatch(p['id']):con.execute('INSERT OR IGNORE INTO profiles VALUES(?,?,?)',(p['id'],json.dumps(p,ensure_ascii=False),time.time_ns()))
                return self.reply(200,{'ok':True})
        except (ValueError,UnicodeDecodeError,json.JSONDecodeError): return self.reply(400,{'error':'invalid request or too large'})
        return self.reply(404,{'error':'not found'})
    def do_PATCH(self):
        parts=self.pathparts()
        if len(parts)!=3 or parts[:2]!=['api','profiles'] or not ID.fullmatch(parts[2]):return self.reply(404,{'error':'not found'})
        try: patch=json.loads(self.read(MAX_JSON))
        except (ValueError,UnicodeDecodeError,json.JSONDecodeError):return self.reply(400,{'error':'invalid request'})
        allowed={'status','reviews','personScores','teamNotes','collaborations','shortlisted','reviewedAt','name','city','timezone','timezoneOther','contacts','links','portfolioLinks','portfolioFiles','bio','roles','details','projects','otherRoles'}
        if not isinstance(patch,dict) or not set(patch)<=allowed:return self.reply(400,{'error':'invalid patch'})
        with db() as con:
            con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT payload FROM profiles WHERE id=?',(parts[2],)).fetchone()
            if not row:return self.reply(404,{'error':'not found'})
            obj=json.loads(row[0]);obj.update(patch);con.execute('UPDATE profiles SET payload=?,updated=? WHERE id=?',(json.dumps(obj,ensure_ascii=False),time.time_ns(),parts[2]))
        return self.reply(200,{'ok':True})

if __name__=='__main__':
    with db():pass
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('CREATORS_PORT','4187'))),Handler).serve_forever()

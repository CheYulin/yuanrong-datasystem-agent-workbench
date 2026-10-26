"""SQLite job journal shared by HTTP workers and one durable execution daemon."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

TERMINAL = ('succeeded', 'completed_with_failures', 'failed', 'cancelled', 'interrupted')


class Conflict(RuntimeError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, user TEXT NOT NULL, action TEXT NOT NULL,
                config TEXT NOT NULL, plan TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT, UNIQUE(user,idempotency_key));
                CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                recorded REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, user, action, config, plan, key):
        if action not in ('deploy', 'run', 'stop'):
            raise ValueError('未知任务动作')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,96}', key):
            raise ValueError('必须提供8–96字符的幂等提交标识')
        fingerprint = hashlib.sha256(json.dumps([action, config], sort_keys=True).encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM jobs WHERE user=? AND idempotency_key=?', (user, key)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise Conflict('同一幂等标识已绑定不同参数')
                return prior['id']
            pending = db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
            if pending >= 12 and action != 'stop':
                raise Conflict('队列已满，请等待已提交任务完成')
            if action == 'stop':
                db.execute("UPDATE jobs SET cancel_requested=1, updated=? WHERE status='running'", (now,))
                db.execute("UPDATE jobs SET status='cancelled',updated=?,error='被回收请求取消，未启动' WHERE status='queued'", (now,))
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO jobs(id,user,action,config,plan,idempotency_key,fingerprint,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (job_id, user, action, json.dumps(config), json.dumps(plan), key, fingerprint, 'queued', now, now))
            return job_id

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]:
                return None
            blocked = db.execute("SELECT value FROM state WHERE key='cleanup_required'").fetchone()
            clause = " AND action='stop'" if blocked and blocked[0] == '1' else ''
            row = db.execute("SELECT * FROM jobs WHERE status='queued'" + clause + " ORDER BY CASE action WHEN 'stop' THEN 0 ELSE 1 END,created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (time.time(), row['id']))
                return self.decode(row, status='running')
        return None

    @staticmethod
    def decode(row, **overrides):
        return {**dict(row), 'config': json.loads(row['config']), 'plan': json.loads(row['plan']), **overrides}

    def get(self, job_id, user=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or (user is not None and row['user'] != user):
                raise KeyError('任务不存在')
            job = self.decode(row)
            events = [json.loads(r[0]) for r in db.execute('SELECT payload FROM events WHERE job_id=? ORDER BY seq', (job_id,))]
        job.pop('fingerprint', None)
        job.pop('idempotency_key', None)
        job['steps'] = [e.get('step', e) for e in events if e.get('kind') == 'step']
        job['logs'] = [e.get('message', e.get('text', str(e))) for e in events if e.get('kind') == 'log'][-500:]
        job['results'] = []
        for e in events:
            if e.get('kind') == 'result':
                job['results'].append(e.get('result', e))
            if e.get('kind') == 'complete':
                job['results'] = e.get('results', job['results'])
                job['completion'] = e
        job['duration_s'] = max(0, job['updated'] - job['created'])
        return job

    def recent(self, user, limit=30):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM jobs WHERE user=? ORDER BY created DESC LIMIT ?', (user, limit))]
        return [self.get(job_id, user) for job_id in ids]

    def event(self, job_id, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 8 * 1024 * 1024:
            raise ValueError('单事件超出上限')
        with self.connect() as db:
            db.execute('INSERT INTO events(job_id,recorded,payload) VALUES(?,?,?)', (job_id, time.time(), encoded))
            db.execute('UPDATE jobs SET updated=? WHERE id=?', (time.time(), job_id))
            if payload.get('kind') == 'deployment':
                db.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)', ('deployment', json.dumps(payload.get('deployment', payload))))
            if payload.get('kind') == 'complete' and 'deployment' in payload:
                db.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)', ('deployment', json.dumps(payload['deployment'])))

    def finish(self, job_id, status, error=None):
        if status not in TERMINAL:
            raise ValueError('非法任务终态')
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?', (status, error, time.time(), job_id))

    def deployment(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key='deployment'").fetchone()
        return json.loads(row[0]) if row else None

    def cleanup_required(self, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)', ('cleanup_required', '1' if value else '0'))
            row = db.execute("SELECT value FROM state WHERE key='cleanup_required'").fetchone()
        return bool(row and row[0] == '1')

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
                CREATE TABLE IF NOT EXISTS event_keys (
                job_id TEXT NOT NULL, event_hash TEXT NOT NULL, PRIMARY KEY(job_id,event_hash));
                CREATE TABLE IF NOT EXISTS remote_cursors (
                job_id TEXT PRIMARY KEY, byte_offset INTEGER NOT NULL, prefix_sha256 TEXT NOT NULL);
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
        job['provenance'] = None
        for e in events:
            if e.get('kind') == 'provenance':
                job['provenance'] = e
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

    @staticmethod
    def event_hash(payload):
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()

    def seed_event_keys(self, db, job_id):
        marker = 'event_keys_indexed:' + job_id
        if db.execute('SELECT 1 FROM state WHERE key=?', (marker,)).fetchone():
            return
        rows = db.execute('SELECT payload FROM events WHERE job_id=?', (job_id,)).fetchall()
        db.executemany('INSERT OR IGNORE INTO event_keys(job_id,event_hash) VALUES(?,?)',
                       [(job_id, self.event_hash(json.loads(row[0]))) for row in rows])
        db.execute('INSERT INTO state(key,value) VALUES(?,?)', (marker, '1'))

    @staticmethod
    def write_event(db, job_id, payload, encoded):
        db.execute('INSERT INTO events(job_id,recorded,payload) VALUES(?,?,?)', (job_id, time.time(), encoded))
        db.execute('UPDATE jobs SET updated=? WHERE id=?', (time.time(), job_id))
        if payload.get('kind') == 'deployment':
            db.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)', ('deployment', json.dumps(payload.get('deployment', payload))))
        if payload.get('kind') == 'complete' and 'deployment' in payload:
            db.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)', ('deployment', json.dumps(payload['deployment'])))

    def event(self, job_id, payload, remote=False):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 8 * 1024 * 1024:
            raise ValueError('单事件超出上限')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if remote:
                self.seed_event_keys(db, job_id)
            inserted = db.execute('INSERT OR IGNORE INTO event_keys(job_id,event_hash) VALUES(?,?)',
                                  (job_id, self.event_hash(payload))).rowcount
            if remote and not inserted:
                return False
            self.write_event(db, job_id, payload, encoded)
        return True

    def events(self, job_id):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM events WHERE job_id=? ORDER BY seq', (job_id,))]

    def remote_cursor(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT byte_offset,prefix_sha256 FROM remote_cursors WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else {'byte_offset': 0, 'prefix_sha256': hashlib.sha256(b'').hexdigest()}

    def ingest_remote_log(self, job_id, data):
        if not isinstance(data, bytes) or len(data) > 16 << 20:
            raise ValueError('远端事件快照必须是不超过16MiB的字节串')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('SELECT byte_offset,prefix_sha256 FROM remote_cursors WHERE job_id=?', (job_id,)).fetchone()
            offset, expected = (cursor[0], cursor[1]) if cursor else (0, hashlib.sha256(b'').hexdigest())
            if len(data) < offset or hashlib.sha256(data[:offset]).hexdigest() != expected:
                raise ValueError('远端事件日志已截断或改写，拒绝跳过既有事件')
            end = data.rfind(b'\n') + 1
            records = []
            for line in data[offset:end].splitlines():
                payload = json.loads(line)
                if not isinstance(payload, dict) or payload.get('job_id') != job_id:
                    raise ValueError('远端事件不属于当前任务')
                encoded = json.dumps(payload, ensure_ascii=False)
                if len(encoded) > 8 * 1024 * 1024:
                    raise ValueError('远端单事件超出上限')
                records.append((payload, encoded))
            self.seed_event_keys(db, job_id)
            added = 0
            for payload, encoded in records:
                inserted = db.execute('INSERT OR IGNORE INTO event_keys(job_id,event_hash) VALUES(?,?)',
                                      (job_id, self.event_hash(payload))).rowcount
                if inserted:
                    self.write_event(db, job_id, payload, encoded)
                    added += 1
            prefix_sha256 = hashlib.sha256(data[:end]).hexdigest()
            db.execute('INSERT OR REPLACE INTO remote_cursors(job_id,byte_offset,prefix_sha256) VALUES(?,?,?)',
                       (job_id, end, prefix_sha256))
        return {'added': added, 'byte_offset': end, 'prefix_sha256': prefix_sha256,
                'duplicates': len(records) - added, 'partial_bytes': len(data) - end}

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

"""Short SQLite transactions plus an OS lock covering external write workflows.

The lock is deliberately coarse for the local exercise; it also coordinates
multiple workers using this database. It is released by the OS on process death.
"""
import fcntl
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

from .domain import RuleError


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS operations (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS series (
                scope TEXT PRIMARY KEY, next_no INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS documents (
                number TEXT PRIMARY KEY, scope TEXT NOT NULL, operation_key TEXT NOT NULL,
                status TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, operation_key TEXT NOT NULL,
                actor TEXT NOT NULL, event TEXT NOT NULL, reason TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        return dict(key=row['key'], kind=row['kind'], state=row['state'],
                    data=json.loads(row['data']), updated_at=row['updated_at'])

    @contextmanager
    def workflow_lock(self):
        with open(str(self.path)+'.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def get(self, key, kind=None, body=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        if kind and (row['kind'] != kind or row['fingerprint'] != fingerprint(body)):
            raise RuleError('idempotency_conflict', 'Operation key already used with different input', 409)
        return self.decode(row)

    def has_series(self, scope):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM series WHERE scope=?', (scope,)).fetchone() is not None

    def create(self, key, kind, body, data, scope, prefix, observed_max=0):
        """Reserve number and operation together: no orphan reservation on crash."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO series VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET next_no=MAX(next_no,excluded.next_no)',
                       (scope, observed_max+1))
            num = db.execute('SELECT next_no FROM series WHERE scope=?', (scope,)).fetchone()[0]
            db.execute('UPDATE series SET next_no=? WHERE scope=?', (num+1, scope))
            number = f'{prefix}{num:05d}'
            data = {**data, 'document_no': number}
            now = datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO documents VALUES (?,?,?,?)', (number, scope, key, 'pending'))
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)', (key, kind, fingerprint(body), 'prepared', json.dumps(data), now))
            db.execute('INSERT INTO audit(at,operation_key,actor,event,reason) VALUES (?,?,?,?,?)',
                       (now, key, data['staff_id'], 'prepared', data.get('reason_code')))
        return self.get(key)

    def save(self, operation, state, **changes):
        data = {**operation['data'], **changes}
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('UPDATE operations SET state=?,data=?,updated_at=? WHERE key=?',
                       (state, json.dumps(data), now, operation['key']))
            if state in ('completed', 'failed'):
                db.execute('UPDATE documents SET status=? WHERE operation_key=?',
                           ('issued' if state == 'completed' else 'cancelled', operation['key']))
            db.execute('INSERT INTO audit(at,operation_key,actor,event,reason) VALUES (?,?,?,?,?)',
                       (now, operation['key'], data['staff_id'], state, data.get('reason_code')))
        operation.update(state=state, data=data, updated_at=now)
        return operation

    def pending(self, store_code=None):
        return [o for o in self.operations(store_code) if o['state'] not in ('completed','failed')]

    def operations(self, store_code=None):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM operations').fetchall()
        ops = [self.decode(row) for row in rows]
        return [o for o in ops if not store_code or o['data']['store_code'] == store_code]

    def refund_for(self, order_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM operations WHERE kind='refund' AND state!='failed'").fetchall()
        return next((o for row in rows if (o := self.decode(row))['data']['order_id'] == order_id), None)

    def ledger(self):
        with self.connect() as db:
            return dict(documents=[dict(r) for r in db.execute('SELECT * FROM documents ORDER BY scope,number')],
                        events=[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id')])

"""Local results and durable upload outbox, using SQLite transactions."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class LocalScores:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, day_key TEXT NOT NULL,
                    total_ms INTEGER NOT NULL, balls INTEGER NOT NULL,
                    pocketed INTEGER NOT NULL, shots INTEGER NOT NULL,
                    created_at REAL NOT NULL, rules_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    round_id TEXT PRIMARY KEY, server TEXT NOT NULL, device TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL, next_try REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT ''
                );
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, key, table):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO results VALUES(?,?,?,?,?,?,?,?,?)',
                       (key, table.mode, table.day_key, table.play_ms, len(table.icons),
                        table.pocketed_icons, table.shots, time.time(), table.rules_version))

    def recent(self, limit=10):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM results ORDER BY created_at DESC LIMIT ?', (min(100, max(1, limit)),))]

    def enqueue(self, server, device, payload):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO outbox(round_id,server,device,payload,created_at) VALUES(?,?,?,?,?)',
                       (payload['round_id'], server, device, json.dumps(payload), time.time()))

    def pending(self, server, device):
        with self.connection() as db:
            db.execute("UPDATE outbox SET state='expired',error='round_expired' "
                       "WHERE state='pending' AND created_at < ?", (time.time() - 6 * 3600,))
            return [dict(row) for row in db.execute(
                "SELECT * FROM outbox WHERE server=? AND device=? AND state='pending' AND next_try<=? "
                'ORDER BY created_at LIMIT 20', (server, device, time.time()))]

    def complete(self, round_id):
        with self.connection() as db:
            db.execute("UPDATE outbox SET state='sent',error='' WHERE round_id=?", (round_id,))

    def upload_state(self, round_id):
        with self.connection() as db:
            row = db.execute('SELECT state FROM outbox WHERE round_id=?',(round_id,)).fetchone()
            return row['state'] if row else None

    def upload_summary(self):
        with self.connection() as db:
            return {row['state']:row['count'] for row in db.execute(
                'SELECT state,COUNT(*) AS count FROM outbox GROUP BY state')}

    def failed(self, round_id, code, *, terminal=False):
        with self.connection() as db:
            db.execute('UPDATE outbox SET state=?,error=?,attempts=attempts+1,next_try=? WHERE round_id=?',
                       ('rejected' if terminal else 'pending', code, time.time()+30, round_id))

    def cancel(self, server):
        with self.connection() as db:
            db.execute("UPDATE outbox SET state='cancelled' WHERE server=? AND state='pending'", (server,))

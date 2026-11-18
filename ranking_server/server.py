"""SQLite-backed ranking service compatible with ``dib.rank.RankApi``.

The service intentionally uses only the Python standard library.  It exposes
the protocol already used by the desktop client:

* ``POST /v1/device/register`` registers a salted machine hash and returns a
  device id plus an HMAC secret.
* ``POST /v1/round/start`` creates a short-lived signed round.
* ``POST /v1/round/finish`` validates and records a result.
* ``GET /v1/leaderboard`` and ``GET /v1/rankings`` return the best score per
  device, ordered by average milliseconds per icon.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get('DIB_RANK_DATA_DIR', Path(__file__).resolve().parent / 'data'))
DB_PATH = Path(os.environ.get('DIB_RANK_DB', DATA_DIR / 'rankings.db'))
HOST = os.environ.get('DIB_RANK_HOST', '127.0.0.1')
PORT = int(os.environ.get('DIB_RANK_PORT', '8787'))

MAX_BODY = 16 * 1024
MAX_RESPONSE = 64 * 1024
TIMESTAMP_TOLERANCE = 5 * 60
ROUND_TTL = 6 * 60 * 60
MIN_BALLS = 15
MAX_BALLS = 45
MIN_AVG_MS = 250
MAX_TOTAL_MS = 6 * 60 * 60 * 1000
MAX_SHOTS = 10_000
MACHINE_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
DEVICE_ID_RE = re.compile(r'^[0-9a-f-]{36}$')
SECRET_RE = re.compile(r'^[0-9a-f]{64}$')


class ApiError(Exception):
    def __init__(self, code: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(code)
        self.code = code
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def current_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def sign(secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    message = f'{method}\n{path}\n{timestamp}\n{body_hash}'
    return hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()


def percentile(rank: int, total: int) -> int:
    if total <= 1:
        return 0
    return max(0, min(100, int(((total - rank) / total) * 100)))


def parse_limit(value: str) -> int:
    try:
        return max(1, min(50, int(value)))
    except (TypeError, ValueError):
        raise ApiError('bad_request') from None


def trusted_proxy_networks(value: str):
    """Only explicitly configured reverse proxies may supply a forwarded IP."""
    return tuple(ipaddress.ip_network(item.strip(), strict=False)
                 for item in value.split(',') if item.strip())


def connection_ip(peer_ip: str, headers, trusted_proxies=()) -> str:
    if not peer_ip:
        return ''  # Old stored results have no observed network address.
    address = ipaddress.ip_address(peer_ip)
    if not any(address in network for network in trusted_proxies):
        return str(address)
    forwarded = headers.get('X-Forwarded-For', '')
    if not forwarded:
        return str(address)
    if len(forwarded) > 2048 or len(forwarded.split(',')) > 32:
        raise ApiError('bad_request')
    try:
        chain = [ipaddress.ip_address(item.strip()) for item in forwarded.split(',')]
    except ValueError:
        raise ApiError('bad_request') from None
    # Walk from the proxy toward the client; ignore attacker-prepended entries.
    for candidate in reversed(chain):
        if not any(address in network for network in trusted_proxies):
            break
        address = candidate
    return str(address)


def system_label(value) -> str:
    if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ApiError('bad_request')
    return value.strip()


class RankingStore:
    """Persistence and ranking calculations, isolated from the HTTP layer."""

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA journal_mode=WAL')
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                '''
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    machine_hash TEXT NOT NULL UNIQUE,
                    secret TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS rounds (
                    round_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    started_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    finished_at REAL,
                    UNIQUE(round_id, device_id)
                );
                CREATE INDEX IF NOT EXISTS rounds_device_idx ON rounds(device_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id TEXT NOT NULL UNIQUE REFERENCES rounds(round_id),
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    total_ms INTEGER NOT NULL,
                    icon_count INTEGER NOT NULL,
                    shots INTEGER NOT NULL,
                    avg_ms REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scores_rank_idx ON scores(avg_ms, created_at, id);
                CREATE INDEX IF NOT EXISTS scores_device_idx ON scores(device_id, avg_ms, created_at, id);
                '''
            )
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(scores)')}
            for name, definition in (('hidden', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('ip_address', "TEXT NOT NULL DEFAULT ''"),
                                     ('os_name', "TEXT NOT NULL DEFAULT ''"),
                                     ('source', "TEXT NOT NULL DEFAULT 'player'"),
                                     ('display_name', "TEXT NOT NULL DEFAULT ''"),
                                     ('skill_label', "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    connection.execute(f'ALTER TABLE scores ADD COLUMN {name} {definition}')
            for table, definitions in (
                    ('scores', [('board', "TEXT NOT NULL DEFAULT 'legacy'"),
                                ('day_key', "TEXT NOT NULL DEFAULT ''"),
                                ('rules_version', 'INTEGER NOT NULL DEFAULT 1'),
                                ('pocketed', 'INTEGER NOT NULL DEFAULT 0')]),
                    ('rounds', [('board', "TEXT NOT NULL DEFAULT 'legacy'"),
                                ('day_key', "TEXT NOT NULL DEFAULT ''"),
                                ('rules_version', 'INTEGER NOT NULL DEFAULT 1'),
                                ('response_json', "TEXT NOT NULL DEFAULT ''")])):
                existing = {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')}
                for name, definition in definitions:
                    if name not in existing:
                        connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    def register(self, machine_hash: str) -> tuple[str, str]:
        now = utc_now()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                'SELECT device_id, secret, disabled FROM devices WHERE machine_hash=?', (machine_hash,)
            ).fetchone()
            if row:
                if row['disabled']:
                    raise ApiError('device_disabled', HTTPStatus.FORBIDDEN)
                connection.execute('UPDATE devices SET last_seen_at=? WHERE device_id=?', (now, row['device_id']))
                return row['device_id'], row['secret']
            device_id = str(uuid.uuid4())
            secret = secrets.token_hex(32)
            connection.execute(
                'INSERT INTO devices(device_id,machine_hash,secret,created_at,last_seen_at) VALUES (?,?,?,?,?)',
                (device_id, machine_hash, secret, now, now),
            )
            return device_id, secret

    def device_secret(self, device_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT secret FROM devices WHERE device_id=? AND disabled=0', (device_id,)
            ).fetchone()
        return row['secret'] if row else None

    def touch(self, device_id: str) -> None:
        with self._connection() as connection:
            connection.execute('UPDATE devices SET last_seen_at=? WHERE device_id=?', (utc_now(), device_id))

    def start_round(self, device_id: str, *, board='legacy', day_key='', rules_version=1) -> str:
        now = time.time()
        round_id = str(uuid.uuid4())
        with self.lock, self._connection() as connection:
            connection.execute(
                'INSERT INTO rounds(round_id,device_id,started_at,expires_at,board,day_key,rules_version) VALUES (?,?,?,?,?,?,?)',
                (round_id, device_id, now, now + ROUND_TTL, board, day_key, rules_version),
            )
        return round_id

    def _best_rows(self, connection, board='legacy', day_key='', *, all_sources=False) -> list[sqlite3.Row]:
        where = '' if all_sources else (" AND s.source='generated'" if board == 'computer' else " AND s.source='player' AND s.board=? AND s.day_key=?")
        args = () if all_sources or board == 'computer' else (board, day_key)
        ordering = 'pocketed DESC,total_ms,shots,created_at,id' if board == 'sprint' else 'avg_ms,created_at,id'
        inner_ordering = ','.join('s.'+part for part in ordering.split(','))
        return connection.execute(
            f'''
            SELECT * FROM (
                SELECT s.*, ROW_NUMBER() OVER (
                    PARTITION BY s.device_id ORDER BY {inner_ordering}
                ) AS position
                FROM scores s JOIN devices d ON d.device_id=s.device_id
                WHERE s.hidden=0 AND d.disabled=0 {where}
            ) WHERE position=1
            ORDER BY {ordering}
            ''', args
        ).fetchall()

    @staticmethod
    def _rank_for(rows, device_id: str, avg_ms: float, created_at: str, score_id: int) -> int:
        # Match the public ordering, including tie-breaks. A round replaces its
        # player's best for this comparison; the player never competes with themself.
        score_key = (avg_ms, created_at, score_id)
        return 1 + sum(
            row['device_id'] != device_id and (row['avg_ms'], row['created_at'], row['id']) < score_key
            for row in rows
        )

    def finish_round(self, device_id: str, round_id: str, total_ms: int, icon_count: int, shots: int,
                     *, ip_address: str = '', os_name: str = '', pocketed=None) -> dict:
        now = time.time()
        with self.lock, self._connection() as connection:
            round_row = connection.execute(
                'SELECT * FROM rounds WHERE round_id=?', (round_id,)
            ).fetchone()
            if not round_row or round_row['device_id'] != device_id:
                raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
            if round_row['finished_at'] is not None:
                score = connection.execute('SELECT * FROM scores WHERE round_id=?', (round_id,)).fetchone()
                if (score and (score['total_ms'],score['icon_count'],score['shots']) == (total_ms,icon_count,shots)
                        and (pocketed is None or pocketed == score['pocketed']) and round_row['response_json']):
                    return json.loads(round_row['response_json'])
                raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
            if now > float(round_row['expires_at']):
                raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
            avg_ms = total_ms / icon_count
            board, day_key, rules_version = round_row['board'],round_row['day_key'],round_row['rules_version']
            pocketed = icon_count if pocketed is None else pocketed
            if rules_version >= 3:
                if icon_count != 15 or not 0 <= pocketed <= icon_count:
                    raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
                if total_ms > (now - round_row['started_at']) * 1000 + 2000:
                    raise ApiError('implausible_time', HTTPStatus.UNPROCESSABLE_ENTITY)
                if board == 'sprint':
                    if total_ms > 90_000 or (total_ms < 90_000 and pocketed != 15):
                        raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
                elif pocketed != 15:
                    raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
            if board != 'sprint' and avg_ms < MIN_AVG_MS:
                raise ApiError('too_fast', HTTPStatus.UNPROCESSABLE_ENTITY)
            if total_ms > MAX_TOTAL_MS or avg_ms > MAX_TOTAL_MS / MIN_BALLS:
                raise ApiError('implausible_time', HTTPStatus.UNPROCESSABLE_ENTITY)
            ordering = 'pocketed DESC,total_ms,shots,created_at,id' if board == 'sprint' else 'avg_ms,created_at,id'
            previous = connection.execute(
                'SELECT * FROM scores WHERE device_id=? AND hidden=0 AND board=? AND day_key=? '
                f'ORDER BY {ordering} LIMIT 1', (device_id,board,day_key)
            ).fetchone()
            new_best = previous is None or (
                (-pocketed,total_ms,shots) < (-previous['pocketed'],previous['total_ms'],previous['shots'])
                if board == 'sprint' else avg_ms < previous['avg_ms'])
            created_at = utc_now()
            inserted = connection.execute(
                'INSERT INTO scores(round_id,device_id,total_ms,icon_count,shots,avg_ms,created_at,ip_address,os_name,board,day_key,rules_version,pocketed) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (round_id, device_id, total_ms, icon_count, shots, avg_ms, created_at, ip_address, os_name,board,day_key,rules_version,pocketed),
            )
            connection.execute('UPDATE rounds SET finished_at=? WHERE round_id=?', (now, round_id))
            rows = self._best_rows(connection,board,day_key)
            rank = self._rank_for(rows, device_id, avg_ms, created_at, inserted.lastrowid)
            best_avg = next(row['avg_ms'] for row in rows if row['device_id'] == device_id)
            best_rank = next(index for index, row in enumerate(rows, 1) if row['device_id'] == device_id)
            if board == 'sprint':
                score_key = (-pocketed,total_ms,shots,created_at,inserted.lastrowid)
                rank = 1 + sum(row['device_id'] != device_id and
                               (-row['pocketed'],row['total_ms'],row['shots'],row['created_at'],row['id']) < score_key for row in rows)
            total_players = len(rows)
            result = {
                'ok': True,
                'avg_ms': avg_ms,
                'is_personal_best': new_best,
                'round': {'rank': rank, 'percentile': percentile(rank, total_players)},
                'best': {
                    'rank': best_rank,
                    'percentile': percentile(best_rank, total_players),
                    'avg_ms': best_avg,
                },
                'total_players': total_players,
                'board': board, 'rules_version': rules_version, 'day_key': day_key,
            }
            if board == 'sprint':
                result['is_personal_best'] = rows[best_rank-1]['round_id'] == round_id
            connection.execute('UPDATE rounds SET response_json=? WHERE round_id=?', (json.dumps(result),round_id))
            return result

    def rankings(self, device_id: str | None = None, limit: int = 50, *, board='legacy', day_key='') -> dict:
        limit = max(1, min(50, int(limit)))
        with self._connection() as connection:
            rows = self._best_rows(connection,board,day_key)
            all_rows = self._best_rows(connection,all_sources=True)
        entries = []
        mine = None
        next_target = None
        generated_players = sum(row['source'] == 'generated' for row in rows)
        for index, row in enumerate(rows, start=1):
            entry = {'rank': index, 'avg_ms': row['avg_ms'], 'is_me': bool(device_id and row['device_id'] == device_id),
                     'ip_address': row['ip_address'], 'os_name': row['os_name'], 'source': row['source'],
                     'display_name': row['display_name'], 'skill_label': row['skill_label']}
            entry.update(pocketed=row['pocketed'],total_ms=row['total_ms'],shots=row['shots'])
            if index <= limit:
                entries.append(entry)
            if entry['is_me']:
                mine = entry
                if index > 1 and board != 'sprint':
                    previous = rows[index - 2]
                    next_target = {'rank': index - 1, 'avg_ms': previous['avg_ms'],
                                   'gap_ms': max(0, row['avg_ms'] - previous['avg_ms'])}
        return {
            'entries': entries,
            'me': mine,
            'generated_at': utc_now(),
            'total_players': len(rows),
            'real_players': len(rows) - generated_players,
            'generated_players': generated_players,
            'next_target': next_target,
            'board': board, 'day_key': day_key, 'rules_version': 3 if board in ('standard','sprint','daily') else 1,
            'computer_opponents': sum(row['source'] == 'generated' for row in all_rows),
        }

    def admin_overview(self) -> dict:
        with self._connection() as connection:
            counts = {
                'devices': connection.execute('SELECT COUNT(*) FROM devices').fetchone()[0],
                'scores': connection.execute('SELECT COUNT(*) FROM scores').fetchone()[0],
                'hidden_scores': connection.execute('SELECT COUNT(*) FROM scores WHERE hidden=1').fetchone()[0],
                'disabled_devices': connection.execute('SELECT COUNT(*) FROM devices WHERE disabled=1').fetchone()[0],
            }
            best = self._best_rows(connection,all_sources=True)
        counts['players'] = len(best)
        counts['generated_players'] = sum(row['source'] == 'generated' for row in best)
        counts['real_players'] = len(best) - counts['generated_players']
        counts['best_avg_ms'] = best[0]['avg_ms'] if best else None
        return counts

    def admin_scores(self, limit=50, offset=0) -> dict:
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT s.id,s.device_id,s.total_ms,s.icon_count,s.shots,s.avg_ms,s.created_at,s.hidden,'
                's.ip_address,s.os_name,s.source,s.display_name,s.skill_label,d.disabled '
                'FROM scores s JOIN devices d ON d.device_id=s.device_id ORDER BY s.id DESC LIMIT ? OFFSET ?',
                (max(1, min(100, limit)), max(0, offset)),
            ).fetchall()
            total = connection.execute('SELECT COUNT(*) FROM scores').fetchone()[0]
        return {'entries': [dict(row) for row in rows], 'total': total}

    def moderate_score(self, score_id: int, hidden: bool) -> None:
        with self.lock, self._connection() as connection:
            if not connection.execute('UPDATE scores SET hidden=? WHERE id=?', (int(hidden), score_id)).rowcount:
                raise ApiError('score_not_found', HTTPStatus.NOT_FOUND)

    def moderate_device(self, device_id: str, disabled: bool) -> None:
        with self.lock, self._connection() as connection:
            if not connection.execute(
                'UPDATE devices SET disabled=? WHERE device_id=?', (int(disabled), device_id)
            ).rowcount:
                raise ApiError('device_not_found', HTTPStatus.NOT_FOUND)


class RankingProtocol:
    """One protocol implementation shared by the standalone and website hosts."""

    def __init__(self, store):
        self.store = store

    def authenticate(self, method, path, headers, body):
        device_id = headers.get('X-Device-Id', '')
        timestamp = headers.get('X-Timestamp', '')
        signature = headers.get('X-Signature', '')
        if not DEVICE_ID_RE.fullmatch(device_id) or not signature:
            raise ApiError('unauthorized', HTTPStatus.UNAUTHORIZED)
        try:
            stamp = int(timestamp)
        except ValueError:
            raise ApiError('stale_timestamp', HTTPStatus.UNAUTHORIZED) from None
        if abs(time.time() - stamp) > TIMESTAMP_TOLERANCE:
            raise ApiError('stale_timestamp', HTTPStatus.UNAUTHORIZED)
        secret = self.store.device_secret(device_id)
        if not secret or not hmac.compare_digest(sign(secret, method, path, timestamp, body), signature):
            raise ApiError('unauthorized', HTTPStatus.UNAUTHORIZED)
        self.store.touch(device_id)
        return device_id

    def handle(self, method, target, headers, body=b'', *, peer_ip='', trusted_proxies=()):
        parsed = urlparse(target)
        path = parsed.path
        if method == 'GET':
            if path in ('/healthz', '/api/health'):
                return {'ok': True, 'service': 'desktop-icon-billiards-ranking'}
            if path in ('/v1/leaderboard', '/v1/rankings'):
                device_id = None
                # The desktop client also opens the ranking when participation is off.
                if path == '/v1/rankings' and any(headers.get(name) for name in
                        ('X-Device-Id', 'X-Timestamp', 'X-Signature')):
                    device_id = self.authenticate(method, target, headers, body)
                limit = parse_limit(parse_qs(parsed.query).get('limit', ['50'])[0])
                query = parse_qs(parsed.query)
                board = query.get('board',['legacy'])[0]
                day = query.get('day',[current_day() if board == 'daily' else ''])[0]
                if board not in ('legacy','standard','sprint','daily','computer') or (board == 'daily' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day)):
                    raise ApiError('bad_request')
                return self.store.rankings(device_id, limit, board=board,day_key=day)
        elif method == 'POST':
            if len(body) > MAX_BODY:
                raise ApiError('bad_request')
            try:
                payload = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                raise ApiError('bad_request') from None
            if not isinstance(payload, dict):
                raise ApiError('bad_request')
            if path == '/v1/device/register':
                machine_hash = payload.get('machine_hash')
                if not isinstance(machine_hash, str) or not MACHINE_HASH_RE.fullmatch(machine_hash):
                    raise ApiError('bad_request')
                device_id, secret = self.store.register(machine_hash)
                return {'device_id': device_id, 'secret': secret}
            if path in ('/v1/round/start', '/v1/round/finish'):
                device_id = self.authenticate(method, path, headers, body)
                if path == '/v1/round/start':
                    if payload:
                        if (payload.get('mode') not in ('standard','sprint','daily')
                                or payload.get('rules_version') != 3
                                or set(payload) - {'mode','rules_version','day_key'}):
                            raise ApiError('bad_request')
                        mode = payload['mode']
                        day = payload.get('day_key','')
                        if day != (current_day() if mode == 'daily' else ''):
                            raise ApiError('bad_request')
                        return {'round_id':self.store.start_round(device_id,board=mode,day_key=day,rules_version=3)}
                    if payload:
                        raise ApiError('bad_request')
                    return {'round_id': self.store.start_round(device_id)}
                round_id = payload.get('round_id')
                values = [payload.get(name) for name in ('total_ms', 'icon_count', 'shots')]
                if not isinstance(round_id, str) or not all(type(value) is int for value in values):
                    raise ApiError('round_invalid', HTTPStatus.UNPROCESSABLE_ENTITY)
                total_ms, icon_count, shots = values
                if 'pocketed' in payload and (type(payload['pocketed']) is not int or not 0 <= payload['pocketed'] <= icon_count):
                    raise ApiError('round_invalid',HTTPStatus.UNPROCESSABLE_ENTITY)
                if not (1 <= total_ms <= MAX_TOTAL_MS and MIN_BALLS <= icon_count <= MAX_BALLS
                        and 1 <= shots <= MAX_SHOTS):
                    raise ApiError('implausible_time', HTTPStatus.UNPROCESSABLE_ENTITY)
                return self.store.finish_round(device_id, round_id, total_ms, icon_count, shots,
                    ip_address=connection_ip(peer_ip, headers, trusted_proxies),
                    os_name=system_label(payload.get('os_name', '')),pocketed=payload.get('pocketed'))
        raise ApiError('not_found', HTTPStatus.NOT_FOUND)


class RankingHandler(BaseHTTPRequestHandler):
    store = None
    trusted_proxies = ()

    def log_message(self, format, *args):  # noqa: A003
        print(f'[{self.log_date_time_string()}] {format % args}')

    def _send(self, status, data):
        raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        try:
            body = b''
            if method == 'POST':
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > MAX_BODY:
                    raise ApiError('bad_request')
                body = self.rfile.read(length)
            data = RankingProtocol(self.store).handle(method, self.path, self.headers, body,
                peer_ip=self.client_address[0], trusted_proxies=self.trusted_proxies)
            self._send(HTTPStatus.OK, data)
        except ApiError as error:
            self._send(error.status, {'error': error.code})
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, {'error': 'bad_request'})
        except sqlite3.Error:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {'error': 'server_error'})

    def do_GET(self):  # noqa: N802
        self._handle('GET')

    def do_POST(self):  # noqa: N802
        self._handle('POST')


def create_server(host: str = HOST, port: int = PORT, db_path: Path | None = None, *, trusted_proxies=None):
    store = RankingStore(Path(db_path) if db_path else DB_PATH)
    store.init()
    networks = trusted_proxy_networks(os.environ.get('DIB_TRUSTED_PROXIES', '')
                                     if trusted_proxies is None else trusted_proxies)
    handler = type('ConfiguredRankingHandler', (RankingHandler,), {'store': store, 'trusted_proxies': networks})
    return ThreadingHTTPServer((host, int(port)), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Desktop Icon Billiards ranking server')
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--db', default=str(DB_PATH))
    args = parser.parse_args(argv)
    httpd = create_server(args.host, args.port, Path(args.db))
    print(f'ranking server listening on http://{args.host}:{args.port}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()

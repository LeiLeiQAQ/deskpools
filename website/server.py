"""Small release website with a SQLite-backed publishing API.

The service intentionally uses only Python's standard library so it can be
deployed beside the Windows build without sharing the desktop client's runtime.
The client never calls this service automatically; downloads are manual from the
website.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Support both the documented script command and python -m website.server.
import sys
import logging
import secrets
import shutil
import tempfile
from urllib.parse import unquote

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ranking_server.server import ApiError as RankingApiError, RankingProtocol, RankingStore, trusted_proxy_networks

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('DIB_SITE_DB', WEB_ROOT / 'data' / 'site.db'))
RANK_DB_PATH = Path(os.environ.get('DIB_RANK_DB', ROOT / 'ranking_server' / 'data' / 'rankings.db'))
DOWNLOAD_DIR = Path(os.environ.get('DIB_SITE_DOWNLOAD_DIR', ROOT / 'dist'))
ADMIN_TOKEN = os.environ.get('DIB_SITE_ADMIN_TOKEN', '').strip()
VERSION_RE = re.compile(r'^\d{1,6}\.\d{1,6}\.\d{1,6}$')
MAX_NOTES = 4000
MAX_BODY = 256 * 1024
MAX_UPLOAD = int(os.environ.get('DIB_SITE_MAX_UPLOAD_MB', '256')) * 1024 * 1024
PACKAGE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.(?:exe|msi|zip)$', re.IGNORECASE)
DOWNLOAD_TIMEZONE = timezone(timedelta(hours=8))
DOWNLOAD_SOURCES = {'hero', 'release', 'direct', 'windows', 'macos'}

MEDIA = {
    'logo.png': ROOT / 'assets' / 'desktop-icon-billiards.png',
    'gameplay.jpg': ROOT / 'docs' / 'images' / 'desktop-gameplay.jpg',
    'gameplay-detail.jpg': ROOT / 'docs' / 'images' / 'desktop-gameplay-detail.jpg',
    'result-card.png': ROOT / 'docs' / 'images' / 'result-card.png',
    'round-summary.jpg': ROOT / 'docs' / 'images' / 'round-summary.jpg',
}


class ReleaseError(ValueError):
    """Invalid release or feature payload."""


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ReleaseError('version must use major.minor.patch')
    return tuple(int(part) for part in value.split('.'))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _features(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReleaseError('features must be an array of strings')
    result = [item.strip() for item in value if item.strip()]
    if len(result) > 24 or any(len(item) > 160 for item in result):
        raise ReleaseError('features contains too many or too-long entries')
    return result


def validate_release(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ReleaseError('request body must be a JSON object')
    version = payload.get('version')
    version_tuple(version)
    title = payload.get('title') or f'桌面图标台球 {version}'
    notes = payload.get('notes', '')
    download_url = payload.get('download_url', '')
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ReleaseError('title must be non-empty text up to 120 characters')
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        raise ReleaseError('notes must be text up to 4000 characters')
    if not isinstance(download_url, str) or not download_url:
        raise ReleaseError('download_url is required')
    if any(ord(char) <= 32 or ord(char) == 127 for char in download_url):
        raise ReleaseError('download_url must not contain spaces or control characters')
    if download_url.startswith('https://'):
        parsed_url = urlparse(download_url)
        if parsed_url.scheme != 'https' or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise ReleaseError('download_url must be a complete HTTPS URL')
        parsed_url.port
    elif download_url.startswith('/downloads/'):
        if not PACKAGE_RE.fullmatch(download_url.removeprefix('/downloads/')):
            raise ReleaseError('download_url must not contain a nested path')
    else:
        raise ReleaseError('download_url must be HTTPS or /downloads/<file>')
    if type(payload.get('current', True)) is not bool:
        raise ReleaseError('current must be a boolean')
    checksum, size = payload.get('sha256',''), payload.get('size',0)
    if not isinstance(checksum,str) or (checksum and not re.fullmatch(r'[0-9a-f]{64}',checksum)):
        raise ReleaseError('sha256 must contain 64 lowercase hexadecimal digits')
    if type(size) is not int or size < 0:
        raise ReleaseError('size must be a nonnegative integer')
    return {
        'version': version,
        'title': title.strip(),
        'notes': notes,
        'download_url': download_url,
        'features': _features(payload.get('features')),
        'current': bool(payload.get('current', True)),
        'sha256': checksum, 'size': size,
    }


def _row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data['features'] = json.loads(data.pop('features_json') or '[]')
    data['current'] = bool(data['current'])
    return data


class ReleaseStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self, seed_path: Path = ROOT / 'update.json') -> None:
        with self._connection() as connection:
            connection.executescript(
                '''
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    current INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feature_flags (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS download_daily (
                    day TEXT NOT NULL,
                    version TEXT NOT NULL,
                    source TEXT NOT NULL,
                    clicks INTEGER NOT NULL DEFAULT 0 CHECK(clicks >= 0),
                    PRIMARY KEY(day, version, source)
                );
                CREATE INDEX IF NOT EXISTS download_daily_version ON download_daily(version, day);
                '''
            )
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(releases)')}
            for name, definition in (('sha256', "TEXT NOT NULL DEFAULT ''"),('size','INTEGER NOT NULL DEFAULT 0')):
                if name not in columns:
                    connection.execute(f'ALTER TABLE releases ADD COLUMN {name} {definition}')
            exists = connection.execute('SELECT 1 FROM releases LIMIT 1').fetchone()
            if exists or not seed_path.exists():
                return
            seed = json.loads(seed_path.read_text(encoding='utf-8'))
            release = validate_release({
                'version': seed['version'],
                'notes': seed.get('notes', ''),
                'download_url': seed['download_url'],
                'sha256': seed.get('sha256',''), 'size': seed.get('size',0),
                'features': [
                    '桌面图标直接作为台球运动', '隐藏台面与回收站洞口',
                    '排行榜、成绩卡片和击打音效',
                ],
            })
            connection.execute(
                'INSERT INTO releases(version,title,notes,download_url,features_json,current,published_at,sha256,size) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (release['version'], release['title'], release['notes'], release['download_url'],
                 json.dumps(release['features'], ensure_ascii=False), 1, _utc_now(),release['sha256'],release['size']),
            )

    def latest(self) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                'SELECT version,title,notes,download_url,features_json,current,published_at,sha256,size '
                'FROM releases WHERE current=1 ORDER BY id DESC LIMIT 1'
            ).fetchone()
        return _row(row) if row else None

    def get_release(self, version: str) -> dict | None:
        version_tuple(version)
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM releases WHERE version=?', (version,)).fetchone()
        return _row(row) if row else None

    def record_download_click(self, version, source, *, now=None):
        version_tuple(version)
        if source not in DOWNLOAD_SOURCES:
            raise ReleaseError('invalid download source')
        day = (now or datetime.now(DOWNLOAD_TIMEZONE)).astimezone(DOWNLOAD_TIMEZONE).date().isoformat()
        with self._connection() as connection:
            # One atomic increment, including concurrent requests; no IP, user
            # agent, or identifying browser data is needed for click totals.
            connection.execute(
                'INSERT INTO download_daily(day,version,source,clicks) VALUES (?,?,?,1) '
                'ON CONFLICT(day,version,source) DO UPDATE SET clicks=clicks+1', (day, version, source))

    def download_stats(self, *, now=None):
        today = (now or datetime.now(DOWNLOAD_TIMEZONE)).astimezone(DOWNLOAD_TIMEZONE).date()
        end = today.isoformat()
        week = (today - timedelta(days=6)).isoformat()
        month = (today - timedelta(days=29)).isoformat()
        start = (today - timedelta(days=13)).isoformat()
        with self._connection() as connection:
            totals = dict(connection.execute(
                'SELECT COALESCE(SUM(clicks),0) AS total_clicks, '
                'COALESCE(SUM(CASE WHEN day=? THEN clicks ELSE 0 END),0) AS today_clicks, '
                'COALESCE(SUM(CASE WHEN day BETWEEN ? AND ? THEN clicks ELSE 0 END),0) AS last_7_days, '
                'COALESCE(SUM(CASE WHEN day BETWEEN ? AND ? THEN clicks ELSE 0 END),0) AS last_30_days '
                'FROM download_daily', (end, week, end, month, end)).fetchone())
            days = {row['day']: row['clicks'] for row in connection.execute(
                'SELECT day,SUM(clicks) AS clicks FROM download_daily WHERE day BETWEEN ? AND ? GROUP BY day',
                (start, end))}
            versions = [dict(row) for row in connection.execute(
                'SELECT r.version,COALESCE(SUM(d.clicks),0) AS clicks, '
                'COALESCE(SUM(CASE WHEN d.day=? THEN d.clicks ELSE 0 END),0) AS today_clicks '
                'FROM releases r LEFT JOIN download_daily d ON d.version=r.version '
                'GROUP BY r.id ORDER BY clicks DESC,r.id DESC', (end,))]
            sources = [dict(row) for row in connection.execute(
                'SELECT source,SUM(clicks) AS clicks FROM download_daily GROUP BY source ORDER BY clicks DESC,source')]
        return {**totals, 'timezone': 'Asia/Shanghai', 'date': end,
                'daily': [{'date': (today - timedelta(days=offset)).isoformat(),
                           'clicks': days.get((today - timedelta(days=offset)).isoformat(), 0)}
                          for offset in range(13, -1, -1)],
                'versions': versions, 'sources': sources}

    def list(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(50, int(limit)))
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT version,title,notes,download_url,features_json,current,published_at,sha256,size '
                'FROM releases ORDER BY id DESC LIMIT ?', (limit,)
            ).fetchall()
        return [_row(row) for row in rows]

    def publish(self, payload: dict) -> dict:
        release = validate_release(payload)
        now = _utc_now()
        with self._connection() as connection:
            if release['current']:
                connection.execute('UPDATE releases SET current=0 WHERE current=1')
            connection.execute(
                'INSERT INTO releases(version,title,notes,download_url,features_json,current,published_at,sha256,size) '
                'VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(version) DO UPDATE SET title=excluded.title, notes=excluded.notes, '
                'download_url=excluded.download_url, features_json=excluded.features_json, '
                'current=excluded.current, published_at=excluded.published_at,sha256=excluded.sha256,size=excluded.size',
                (release['version'], release['title'], release['notes'], release['download_url'],
                 json.dumps(release['features'], ensure_ascii=False), int(release['current']), now,release['sha256'],release['size']),
            )
            row = connection.execute(
                'SELECT version,title,notes,download_url,features_json,current,published_at,sha256,size '
                'FROM releases WHERE version=?', (release['version'],)
            ).fetchone()
        return _row(row)

    def set_current(self, version):
        version_tuple(version)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM releases WHERE version=?', (version,)).fetchone():
                raise ReleaseError('version not found')
            connection.execute('UPDATE releases SET current=0 WHERE current=1')
            connection.execute('UPDATE releases SET current=1 WHERE version=?', (version,))
        return self.latest()

    def set_flags(self, flags: dict) -> dict:
        if not isinstance(flags, dict) or len(flags) > 32:
            raise ReleaseError('flags must be an object with at most 32 entries')
        now = _utc_now()
        with self._connection() as connection:
            for name, value in flags.items():
                if not re.fullmatch(r'[a-z][a-z0-9_-]{1,48}', str(name)):
                    raise ReleaseError(f'invalid feature flag: {name}')
                connection.execute(
                    'INSERT INTO feature_flags(name,value,updated_at) VALUES (?,?,?) '
                    'ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                    (name, json.dumps(value, ensure_ascii=False), now),
                )
        return self.flags()

    def flags(self) -> dict:
        with self._connection() as connection:
            rows = connection.execute('SELECT name,value FROM feature_flags ORDER BY name').fetchall()
        return {row['name']: json.loads(row['value']) for row in rows}


class WebsiteHandler(BaseHTTPRequestHandler):
    store = None
    rank_store = None
    admin_token = ''
    download_dir = DOWNLOAD_DIR
    trusted_proxies = ()

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        print(f'[{self.log_date_time_string()}] {format % args}', flush=True)

    def _headers(self, status, length, content_type, cache='no-store', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        path = urlparse(self.path).path
        if (path in ('/admin', '/api/admin', '/static/admin.js')
                or path.startswith(('/admin/', '/api/admin/'))):
            self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
        policy = "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if content_type.startswith('text/html') and path == '/':
            policy += "; script-src 'self' https://tj.xlsat.cn; connect-src 'self' https://tj.xlsat.cn"
        self.send_header('Content-Security-Policy', policy)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _send(self, status, body, content_type='text/plain; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self._headers(status, len(body), content_type)
        self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _error(self, status, message):
        self._json(status, {'error': message})

    def _template(self, name):
        return (WEB_ROOT / 'templates' / name).read_text(encoding='utf-8')

    def _authorized(self):
        token = self.headers.get('X-Admin-Token', '')
        if not token:
            token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        return bool(self.admin_token) and hmac.compare_digest(token.encode(), self.admin_token.encode())

    def _read_body(self, limit=MAX_BODY):
        if self.headers.get('Transfer-Encoding'):
            raise ReleaseError('Content-Length is required')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ReleaseError('invalid Content-Length') from None
        if length <= 0 or length > limit:
            raise ReleaseError('request body size is invalid')
        body = self.rfile.read(length)
        if len(body) != length:
            raise ReleaseError('incomplete request body')
        return body

    def _payload(self):
        payload = json.loads(self._read_body().decode('utf-8'))
        if not isinstance(payload, dict):
            raise ReleaseError('request body must be a JSON object')
        return payload

    def _ranking(self, method):
        body = self._read_body(16 * 1024) if method == 'POST' else b''
        data = RankingProtocol(self.rank_store).handle(method, self.path, self.headers, body,
            peer_ip=self.client_address[0], trusted_proxies=self.trusted_proxies)
        self._json(HTTPStatus.OK, data)

    def _health(self):
        # Read both databases: an open HTTP port alone does not mean the backend is ready.
        for store in (self.store, self.rank_store):
            with store._connection() as connection:
                connection.execute('SELECT 1').fetchone()
        return {'ok': True, 'service': 'desktop-icon-billiards-backend', 'website': True, 'ranking': True}

    def _admin_get(self, path, query):
        if not self._authorized():
            return self._error(HTTPStatus.UNAUTHORIZED, 'admin token required')
        if path == '/api/admin/downloads':
            return self._json(HTTPStatus.OK, self.store.download_stats())
        if path == '/api/admin/overview':
            return self._json(HTTPStatus.OK, {
                'ranking': self.rank_store.admin_overview(), 'current_release': self.store.latest(),
                'ranking_origin': 'same-origin', 'max_upload_bytes': MAX_UPLOAD,
            })
        if path == '/api/admin/ranking':
            limit = int(query.get('limit', ['50'])[0])
            offset = int(query.get('offset', ['0'])[0])
            return self._json(HTTPStatus.OK, self.rank_store.admin_scores(limit, offset))
        if path == '/api/admin/files':
            files = []
            for item in sorted(self.download_dir.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True):
                if item.is_file() and PACKAGE_RE.fullmatch(item.name):
                    files.append({'name': item.name, 'size': item.stat().st_size,
                                  'download_url': '/downloads/' + item.name})
            return self._json(HTTPStatus.OK, {'files': files})
        return self._error(HTTPStatus.NOT_FOUND, 'not found')

    def _download(self, query):
        # Resolve only a published version. Never accept an arbitrary redirect
        # target from the public query string.
        if set(query) - {'version', 'source'} or any(len(values) != 1 for values in query.values()):
            raise ReleaseError('invalid download parameters')
        source = query.get('source', ['direct'])[0]
        if source not in DOWNLOAD_SOURCES:
            raise ReleaseError('invalid download source')
        version = query.get('version', [None])[0]
        release = self.store.get_release(version) if version is not None else self.store.latest()
        if not release:
            if self.command == 'HEAD':
                return self._headers(HTTPStatus.NOT_FOUND, 0, 'text/plain; charset=utf-8')
            return self._error(HTTPStatus.NOT_FOUND, 'release not found')
        validate_release(release)
        purpose = (self.headers.get('Sec-Purpose', '') + ' ' + self.headers.get('Purpose', '')).lower()
        if self.command == 'GET' and 'prefetch' not in purpose and 'prerender' not in purpose:
            self.store.record_download_click(release['version'], source)
        self._headers(HTTPStatus.FOUND, 0, 'text/plain; charset=utf-8',
                      extra={'Location': release['download_url'], 'X-Robots-Tag': 'noindex, nofollow'})

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path != '/download':
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            self._download(parse_qs(parsed.query, keep_blank_values=True))
        except ValueError:
            self._headers(HTTPStatus.BAD_REQUEST, 0, 'text/plain; charset=utf-8')
        except (OSError, sqlite3.Error):
            logging.exception('Backend download HEAD failed')
            self._headers(HTTPStatus.INTERNAL_SERVER_ERROR, 0, 'text/plain; charset=utf-8')

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith('/v1/'):
                return self._ranking('GET')
            if path in ('/healthz', '/api/health'):
                return self._json(HTTPStatus.OK, self._health())
            if path == '/download':
                return self._download(parse_qs(parsed.query, keep_blank_values=True))
            if path == '/':
                return self._send(HTTPStatus.OK, self._template('index.html'), 'text/html; charset=utf-8')
            if path == '/robots.txt':
                return self._send(HTTPStatus.OK,
                    b'User-agent: *\nDisallow: /admin\nDisallow: /api/admin/\n'
                    b'Sitemap: https://deskpools.com/sitemap.xml\n')
            if path == '/sitemap.xml':
                # Only public canonical pages belong here, never admin or downloads.
                return self._send(HTTPStatus.OK,
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    '<url><loc>https://deskpools.com/</loc></url></urlset>\n',
                    'application/xml; charset=utf-8')
            if path in ('/admin', '/admin/'):
                return self._send(HTTPStatus.OK, self._template('admin.html'), 'text/html; charset=utf-8')
            if path.startswith('/api/admin/'):
                return self._admin_get(path, parse_qs(parsed.query))
            if path == '/api/releases/latest':
                return self._json(HTTPStatus.OK, self.store.latest() or {})
            if path == '/api/releases':
                limit = int(parse_qs(parsed.query).get('limit', ['20'])[0])
                return self._json(HTTPStatus.OK, {'latest': self.store.latest(),
                    'releases': self.store.list(limit), 'features': self.store.flags()})
            if path == '/api/features':
                return self._json(HTTPStatus.OK, self.store.flags())
            if path.startswith('/static/'):
                name = path.removeprefix('/static/')
                if name and Path(name).name == name and name not in ('.', '..'):
                    return self._serve_file(WEB_ROOT / 'static' / name)
            if path.startswith('/media/'):
                return self._serve_file(MEDIA.get(path.removeprefix('/media/')))
            if path.startswith('/downloads/'):
                name = unquote(path.removeprefix('/downloads/'))
                if PACKAGE_RE.fullmatch(name):
                    return self._serve_file(self.download_dir / name, attachment=True)
            return self._error(HTTPStatus.NOT_FOUND, 'not found')
        except RankingApiError as error:
            return self._error(error.status, error.code)
        except ValueError as error:
            return self._error(HTTPStatus.BAD_REQUEST, str(error))
        except (OSError, sqlite3.Error):
            logging.exception('Backend GET failed: %s', path)
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, 'server_error')

    def _serve_file(self, path, attachment=False):
        if not path or not Path(path).is_file():
            return self._error(HTTPStatus.NOT_FOUND, 'file not found')
        path = Path(path).resolve()
        roots = (ROOT.resolve(), self.download_dir.resolve())
        if not any(root in path.parents for root in roots):
            return self._error(HTTPStatus.NOT_FOUND, 'file not found')
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        extra = {'Content-Disposition': f'attachment; filename="{path.name}"'} if attachment else {}
        # Stream release binaries: simultaneous downloads should not allocate an EXE-sized buffer.
        with path.open('rb') as stream:
            self._headers(HTTPStatus.OK, os.fstat(stream.fileno()).st_size, content_type,
                          'no-store' if attachment else 'public, max-age=300', extra)
            shutil.copyfileobj(stream, self.wfile, 64 * 1024)

    def _upload(self):
        name = unquote(self.headers.get('X-Filename', ''))
        if not PACKAGE_RE.fullmatch(name):
            raise ReleaseError('package filename must use letters, numbers, ._- and end with .exe, .msi or .zip')
        if self.headers.get('Transfer-Encoding'):
            raise ReleaseError('Content-Length is required')
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= MAX_UPLOAD:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'package size exceeds upload limit or is empty')
        self.download_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.download_dir, suffix='.upload', delete=False) as output:
                temp_path = Path(output.name)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise ReleaseError('incomplete upload')
                    digest.update(chunk)
                    output.write(chunk)
                    remaining -= len(chunk)
            checksum = digest.hexdigest()
            # Content-addressed filenames keep published download URLs immutable.
            filename = f'{Path(name).stem[:120]}-{checksum[:12]}{Path(name).suffix.lower()}'
            os.replace(temp_path, self.download_dir / filename)
            return self._json(HTTPStatus.OK, {
                'name': filename, 'download_url': '/downloads/' + filename,
                'size': length, 'sha256': checksum,
            })
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def _publish(self, payload):
        release = validate_release(payload)
        if release['download_url'].startswith('/downloads/'):
            package = self.download_dir / release['download_url'].removeprefix('/downloads/')
            if not package.is_file():
                raise ReleaseError('upload the installation package before publishing')
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            size = package.stat().st_size
            if (release['sha256'] and release['sha256'] != checksum) or (release['size'] and release['size'] != size):
                raise ReleaseError('release checksum or size differs from installation package')
            release.update(sha256=checksum,size=size)
        return self.store.publish(release)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path.startswith('/v1/'):
                return self._ranking('POST')
            if not path.startswith('/api/admin/'):
                return self._error(HTTPStatus.NOT_FOUND, 'not found')
            if not self._authorized():
                return self._error(HTTPStatus.UNAUTHORIZED, 'admin token required')
            if path == '/api/admin/uploads':
                return self._upload()
            payload = self._payload()
            if path == '/api/admin/releases':
                return self._json(HTTPStatus.OK, self._publish(payload))
            if path == '/api/admin/releases/current':
                return self._json(HTTPStatus.OK, self.store.set_current(payload.get('version')))
            if path == '/api/admin/features':
                return self._json(HTTPStatus.OK, self.store.set_flags(payload))
            if path == '/api/admin/ranking/score':
                if type(payload.get('score_id')) is not int or type(payload.get('hidden')) is not bool:
                    raise ReleaseError('score_id and hidden are required')
                self.rank_store.moderate_score(payload['score_id'], payload['hidden'])
                return self._json(HTTPStatus.OK, {'ok': True})
            if path == '/api/admin/ranking/device':
                if not isinstance(payload.get('device_id'), str) or type(payload.get('disabled')) is not bool:
                    raise ReleaseError('device_id and disabled are required')
                self.rank_store.moderate_device(payload['device_id'], payload['disabled'])
                return self._json(HTTPStatus.OK, {'ok': True})
            return self._error(HTTPStatus.NOT_FOUND, 'not found')
        except RankingApiError as error:
            return self._error(error.status, error.code)
        except (ValueError, UnicodeDecodeError) as error:
            return self._error(HTTPStatus.BAD_REQUEST, str(error))
        except (OSError, sqlite3.Error):
            logging.exception('Backend POST failed: %s', path)
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, 'server_error')


def create_server(host='127.0.0.1', port=8070, db_path=None, rank_db_path=None,
                  admin_token=None, download_dir=None, trusted_proxies=None):
    store = ReleaseStore(Path(db_path) if db_path else DB_PATH)
    store.init()
    rank_store = RankingStore(Path(rank_db_path) if rank_db_path else RANK_DB_PATH)
    rank_store.init()
    # Bind state per server, so two instances never share a mutable handler class.
    handler = type('ConfiguredWebsiteHandler', (WebsiteHandler,), {
        'store': store, 'rank_store': rank_store,
        'admin_token': ADMIN_TOKEN if admin_token is None else admin_token,
        'download_dir': Path(download_dir) if download_dir is not None else DOWNLOAD_DIR,
        'trusted_proxies': trusted_proxy_networks(os.environ.get('DIB_TRUSTED_PROXIES', '')
                                                if trusted_proxies is None else trusted_proxies),
    })
    return ThreadingHTTPServer((host, int(port)), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Desktop Icon Billiards unified backend')
    parser.add_argument('--host', default=os.environ.get('DIB_SITE_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('DIB_SITE_PORT', '8070')))
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--rank-db', default=str(RANK_DB_PATH))
    args = parser.parse_args(argv)
    token = ADMIN_TOKEN
    if not token:
        token_path = Path(args.db).resolve().parent / 'admin-token.txt'
        token_path.parent.mkdir(parents=True, exist_ok=True)
        if not token_path.exists():
            with token_path.open('x', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(32))
        token = token_path.read_text(encoding='utf-8').strip()
        if len(token) < 24:
            parser.error(f'admin token file is invalid: {token_path}')
        print(f'Administrator token file: {token_path}', flush=True)
    server = create_server(args.host, args.port, args.db, args.rank_db, admin_token=token)
    print(f'Backend listening on http://{args.host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

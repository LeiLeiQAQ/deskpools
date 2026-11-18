"""Update feed parsing and fetching.

The feed contract is documented in ``docs/UPDATE.md``. The top-level release is
authoritative; ``history`` is optional display-only data. The download link is
opened in the browser, never executed or written over the running executable.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import queue
import re
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request

MAX_FEED_BYTES = 64 * 1024
MAX_NOTES = 4000
VERSION_PATTERN = re.compile(r'\d{1,6}\.\d{1,6}\.\d{1,6}')
PACKAGE_PATTERN = re.compile(r'/downloads/[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.(?:exe|msi|zip)', re.I)


def validated_url(url):
    """Public backends use HTTPS; plain HTTP is limited to the local machine."""
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url):
        raise ValueError('Invalid update URL')
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Invalid update URL')
    parsed.port  # Validate malformed or out-of-range ports before making a request.
    local = parsed.hostname.lower() == 'localhost'
    try:
        local = local or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
        raise ValueError('Update URL must use HTTPS (HTTP is allowed on loopback only)')
    return parsed


def update_feed_url(website):
    parsed = validated_url(website)
    if parsed.path not in ('', '/') or parsed.query:
        raise ValueError('Website URL must be an origin without a path or query')
    return urllib.parse.urlunsplit(parsed._replace(path='/api/releases/latest'))


class UpdateError(ValueError):
    """A user-facing backend state, separate from a malformed feed."""


def version_tuple(value):
    """Return ``(major, minor, patch)`` for a release version string."""
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ValueError('version must use major.minor.patch')
    return tuple(int(part) for part in value.split('.'))


def release_entries(info):
    """Return the display list: latest release first, then valid history entries."""
    latest = version_tuple(info['version'])
    entries = [{'version': info['version'], 'notes': info.get('notes', '')}]
    seen = {latest}
    history = info.get('history', [])
    if not isinstance(history, list):
        return entries
    for row in history[:100]:
        if not isinstance(row, dict):
            continue
        try:
            key = version_tuple(row.get('version'))
        except ValueError:
            continue
        notes = row.get('notes', '')
        if key >= latest or key in seen or not isinstance(notes, str) or len(notes) > MAX_NOTES:
            continue
        seen.add(key)
        entries.append({'version': row['version'], 'notes': notes})
    entries[1:] = sorted(entries[1:], key=lambda row: version_tuple(row['version']), reverse=True)
    return entries[:21]


def parse_update(data, current, *, source_url=None):
    """Parse a raw feed response. Returns a release dict, or None when up to date."""
    info = json.loads(data.decode('utf-8-sig'))
    if not isinstance(info, dict):
        raise ValueError('Update JSON must be an object')
    if not info:
        raise UpdateError('后台尚未发布版本')
    latest = version_tuple(info.get('version'))
    installed = version_tuple(current)
    if latest <= installed:
        return None
    url = info.get('download_url', '')
    notes = info.get('notes', '')
    if not isinstance(url, str):
        raise ValueError('Invalid download URL')
    if source_url and PACKAGE_PATTERN.fullmatch(url):
        validated_url(source_url)
        url = urllib.parse.urljoin(source_url, url)
    elif urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('download_url must be an HTTPS URL')
    validated_url(url)
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        raise ValueError('notes must be text, up to 4000 characters')
    result = {'version': info['version'], 'download_url': url, 'notes': notes}
    if 'history' in info:
        result['history'] = release_entries(info)[1:]
    return result


def download(url, limit, version):
    request = urllib.request.Request(
        url,
        headers={'User-Agent': f'DesktopIconBilliards/{version}', 'Cache-Control': 'no-cache'},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        validated_url(response.geturl())
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Response is too large')
    return data


def fetch_update(url, current):
    """Fetch the published release, accepting local HTTP and public HTTPS."""
    if not url:
        return None
    validated_url(url)
    return parse_update(download(url, MAX_FEED_BYTES, current), current, source_url=url)


@dataclass(frozen=True)
class UpdateResult:
    info: dict | None = None
    error: str = ''
    details: str = ''


def _check(inbox, website, current):
    # This worker owns no Tk widgets or app callbacks. A slow request cannot
    # block drawing, F1, shutdown, or destroy Tk variables on a worker thread.
    try:
        result = UpdateResult(info=fetch_update(update_feed_url(website), current))
    except Exception as exc:
        if isinstance(exc, UpdateError):
            message = str(exc)
        elif isinstance(exc, urllib.error.HTTPError):
            message = f'后台返回错误（HTTP {exc.code}），请重试'
        elif isinstance(exc, (urllib.error.URLError, OSError)):
            message = '连接后台失败，请检查服务与网络后重试'
        elif isinstance(exc, ValueError):
            message = '后台地址或版本信息格式错误，请检查配置'
        else:
            message = '检查更新失败，请重试'
        result = UpdateResult(error=message, details=traceback.format_exc())
    inbox.put(result)


class UpdateChecker:
    """One manual request at a time; the UI consumes results on its own thread."""

    def __init__(self):
        self.inbox = queue.SimpleQueue()
        self.busy = False

    def check(self, website, current):
        if self.busy:
            return False
        self.busy = True
        try:
            threading.Thread(target=_check, args=(self.inbox, website, current),
                             name='check-update', daemon=True).start()
        except Exception:
            self.busy = False
            raise
        return True

    def take_result(self):
        try:
            result = self.inbox.get_nowait()
        except queue.Empty:
            return None
        self.busy = False
        return result

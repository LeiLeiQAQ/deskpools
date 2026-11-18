import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from dib.rank import RankApi
from dib.update import fetch_update, update_feed_url
from website import server as site


def valid_release(**overrides):
    payload = {
        'version': '0.3.0',
        'title': '桌面图标台球 0.3.0',
        'notes': '新增桌面玩法',
        'download_url': '/downloads/DesktopIconBilliards-0.3.0-win-x64.exe',
        'features': ['更清晰的图标', '新的击打音效'],
        'current': True,
    }
    payload.update(overrides)
    return payload


class WebsiteDataTests(unittest.TestCase):
    def test_release_validation_and_version_order(self):
        result = site.validate_release(valid_release())
        self.assertEqual(result['version'], '0.3.0')
        for value in ('1.2', 'v1.2.3', 'http://example.com/app.exe', 'https://',
                      'https://user:pass@example.com/app.exe', 'javascript:alert(1)'):
            with self.subTest(value=value):
                with self.assertRaises(site.ReleaseError):
                    site.validate_release(valid_release(version=value, download_url=value))

    def test_store_seeds_from_update_json_and_publishes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'site.db'
            store = site.ReleaseStore(path)
            seed = Path(folder)/'seed.json'
            seed.write_text(json.dumps(valid_release(version='0.2.1')))
            store.init(seed)
            self.assertEqual(store.latest()['version'], '0.2.1')
            published = store.publish(valid_release())
            self.assertEqual(published['version'], '0.3.0')
            self.assertTrue(published['current'])
            self.assertFalse(store.list()[1]['current'])
            flags = store.set_flags({'sound': True, 'hidden_table': False})
            self.assertEqual(flags, {'hidden_table': False, 'sound': True})

    def test_store_rejects_invalid_feature_flag_name(self):
        with tempfile.TemporaryDirectory() as folder:
            store = site.ReleaseStore(Path(folder) / 'site.db')
            store.init()
            with self.assertRaises(site.ReleaseError):
                store.set_flags({'Not Valid': True})


class WebsiteHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'site.db'
        self.rank_db = self.root / 'rank.db'
        self.downloads = self.root / 'downloads'
        self.downloads.mkdir()
        (self.downloads / 'demo.exe').write_bytes(b'fake release bytes')
        self.patches = [
            patch.object(site, 'ADMIN_TOKEN', 'test-token'),
            patch.object(site, 'DOWNLOAD_DIR', self.downloads),
        ]
        for item in self.patches:
            item.start()
        seed = self.root/'seed.json'
        seed.write_text(json.dumps(valid_release(version='0.2.1')))
        site.ReleaseStore(self.db).init(seed)
        self.httpd = site.create_server('127.0.0.1', 0, self.db, self.rank_db)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, path, method='GET', payload=None, token=None, extra_headers=None):
        headers = {'Accept': 'application/json'}
        data = None
        if payload is not None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/octet-stream' if isinstance(payload, bytes) else 'application/json'
        if token:
            headers['X-Admin-Token'] = token
        headers.update(extra_headers or {})
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_public_pages_and_static_assets(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn('桌面图标台球'.encode(), body)
        status, headers, body = self.request('/static/styles.css')
        self.assertEqual(status, 200)
        self.assertIn('text/css', headers['Content-Type'])
        self.assertIn(b'--green', body)

    def test_admin_api_requires_token_and_publishes_release(self):
        status, _, body = self.request('/api/admin/releases', 'POST', valid_release())
        self.assertEqual(status, 401)
        self.assertIn(b'admin token required', body)
        status, _, body = self.request('/api/admin/releases', 'POST', valid_release(download_url='/downloads/demo.exe'), 'test-token')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['version'], '0.3.0')
        status, _, body = self.request('/api/releases/latest')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['version'], '0.3.0')

    def test_feature_api_and_download_headers(self):
        status, _, body = self.request('/api/admin/features', 'POST', {'sound': True}, 'test-token')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'sound': True})
        status, _, body = self.request('/api/features')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['sound'], True)
        status, headers, body = self.request('/downloads/demo.exe')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'fake release bytes')
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertIn('demo.exe', headers['Content-Disposition'])

    def test_published_checksum_matches_package_and_rejects_mismatch(self):
        package = (self.downloads/'demo.exe').read_bytes()
        status,_,body = self.request('/api/admin/releases','POST',
            valid_release(download_url='/downloads/demo.exe'),'test-token')
        self.assertEqual(status,200)
        release=json.loads(body)
        self.assertEqual(release['sha256'],hashlib.sha256(package).hexdigest())
        self.assertEqual(release['size'],len(package))
        status,_,_ = self.request('/api/admin/releases','POST',
            valid_release(download_url='/downloads/demo.exe',sha256='0'*64),'test-token')
        self.assertEqual(status,400)
        latest=json.loads(self.request('/api/releases/latest')[2])
        self.assertEqual(latest['sha256'],release['sha256'])

    def test_same_backend_serves_signed_ranking_protocol(self):
        health_status, _, health_body = self.request('/healthz')
        self.assertEqual(health_status, 200)
        health = json.loads(health_body)
        self.assertTrue(health['website'])
        self.assertTrue(health['ranking'])

        api = RankApi(self.base, '0.2.1', timeout=2)
        device_id, secret = api.register('a' * 64)
        credentials = {'device_id': device_id, 'secret': secret}
        round_id = api.start_round(credentials)
        result = api.finish_round(credentials, round_id, 60_000, 15, 12)
        self.assertEqual(result['round']['rank'], 1)
        leaderboard = api.leaderboard()
        self.assertEqual(leaderboard['total_players'], 1)
        self.assertEqual(leaderboard['entries'][0]['ip_address'], '127.0.0.1')
        self.assertRegex(leaderboard['entries'][0]['os_name'], r'^Windows (10|11) \(build \d+\)$')

    def test_upload_publish_download_and_switch_current_version(self):
        package = b'MZ' + bytes(range(256)) * 1024
        status, _, body = self.request('/api/admin/uploads', 'POST', package, 'test-token',
                                      {'X-Filename': 'DesktopIconBilliards-0.3.0.exe'})
        self.assertEqual(status, 200)
        uploaded = json.loads(body)
        self.assertEqual(uploaded['sha256'], hashlib.sha256(package).hexdigest())
        release = valid_release(download_url=uploaded['download_url'])
        self.assertEqual(self.request('/api/admin/releases', 'POST', release, 'test-token')[0], 200)
        status, headers, downloaded = self.request(uploaded['download_url'])
        self.assertEqual(status, 200)
        self.assertEqual(downloaded, package)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(self.request('/api/admin/releases', 'POST', valid_release(
            version='0.4.0', current=False, download_url='/downloads/demo.exe'), 'test-token')[0], 200)
        listing = json.loads(self.request('/api/releases')[2])
        self.assertEqual(listing['latest']['version'], '0.3.0')
        self.assertEqual(listing['releases'][0]['version'], '0.4.0')
        self.assertEqual(self.request('/api/admin/releases/current', 'POST', {'version': '0.4.0'}, 'test-token')[0], 200)
        self.assertEqual(json.loads(self.request('/api/releases/latest')[2])['version'], '0.4.0')
        self.assertEqual(self.request('/api/admin/releases/current', 'POST', {'version': '9.9.9'}, 'test-token')[0], 400)
        self.assertEqual(json.loads(self.request('/api/releases/latest')[2])['version'], '0.4.0')
        store = site.ReleaseStore(self.db)
        self.assertEqual(store.latest()['version'], '0.4.0')

    def test_client_checks_the_admin_published_release_and_downloads_its_package(self):
        self.assertIsNone(fetch_update(update_feed_url(self.base), '0.2.1'))
        package = b'MZ' + bytes(range(256)) * 16
        status, _, body = self.request('/api/admin/uploads', 'POST', package, 'test-token',
                                      {'X-Filename': 'DesktopIconBilliards-0.3.0.exe'})
        self.assertEqual(status, 200)
        uploaded = json.loads(body)
        status, _, _ = self.request('/api/admin/releases', 'POST',
                                    valid_release(download_url=uploaded['download_url']), 'test-token')
        self.assertEqual(status, 200)
        release = fetch_update(update_feed_url(self.base), '0.2.1')
        self.assertEqual(release['version'], '0.3.0')
        self.assertEqual(release['notes'], '新增桌面玩法')
        self.assertEqual(release['download_url'], self.base + uploaded['download_url'])
        with urlopen(release['download_url'], timeout=3) as response:
            self.assertEqual(response.read(), package)
        self.assertEqual(self.request('/api/admin/releases/current', 'POST',
                                      {'version': '0.2.1'}, 'test-token')[0], 200)
        self.assertIsNone(fetch_update(update_feed_url(self.base), '0.2.1'))

    def test_admin_moderation_updates_client_and_public_rankings(self):
        api = RankApi(self.base, '0.2.1', timeout=2)
        device_id, secret = api.register('b' * 64)
        creds = {'device_id': device_id, 'secret': secret}
        for total_ms in (60_000, 90_000):
            round_id = api.start_round(creds)
            api.finish_round(creds, round_id, total_ms, 15, 12)
        self.assertEqual(api.rankings()['total_players'], 1)
        self.assertEqual(self.request('/api/admin/ranking')[0], 401)
        self.assertEqual(self.request('/api/admin/overview')[0], 401)
        status, _, body = self.request('/api/admin/ranking', token='test-token')
        self.assertEqual(status, 200)
        scores = json.loads(body)['entries']
        self.assertNotIn('secret', body.decode())
        self.assertNotIn('machine_hash', body.decode())
        fastest = min(scores, key=lambda score: score['avg_ms'])
        self.assertEqual(self.request('/api/admin/ranking/score', 'POST',
            {'score_id': fastest['id'], 'hidden': True}, 'test-token')[0], 200)
        self.assertEqual(api.leaderboard()['entries'][0]['avg_ms'], 6000)
        self.assertEqual(self.request('/api/admin/ranking/score', 'POST',
            {'score_id': fastest['id'], 'hidden': False}, 'test-token')[0], 200)
        self.assertEqual(api.rankings(creds)['me']['avg_ms'], 4000)
        self.assertEqual(self.request('/api/admin/ranking/device', 'POST',
            {'device_id': device_id, 'disabled': True}, 'test-token')[0], 200)
        self.assertEqual(api.leaderboard()['total_players'], 0)
        self.assertEqual(self.request('/v1/device/register', 'POST', {'machine_hash': 'b' * 64})[0], 403)
        self.assertEqual(self.request('/api/admin/ranking/device', 'POST',
            {'device_id': device_id, 'disabled': False}, 'test-token')[0], 200)
        self.assertEqual(api.rankings(creds)['total_players'], 1)
        overview = json.loads(self.request('/api/admin/overview', token='test-token')[2])
        self.assertEqual(overview['ranking']['scores'], 2)

    def test_invalid_upload_and_missing_package_do_not_publish(self):
        for name in ('../escape.exe', 'C:%5Cescape.exe', 'script.html', 'file.exe?x=1'):
            with self.subTest(name=name):
                status, _, _ = self.request('/api/admin/uploads', 'POST', b'bad', 'test-token', {'X-Filename': name})
                self.assertEqual(status, 400)
        self.assertEqual(self.request('/api/admin/uploads', 'POST', b'bad', extra_headers={'X-Filename': 'x.exe'})[0], 401)
        self.assertEqual(self.request('/api/admin/releases', 'POST', valid_release(), 'test-token')[0], 400)
        self.assertEqual(json.loads(self.request('/api/releases/latest')[2])['version'], '0.2.1')
        self.assertFalse(list(self.downloads.glob('*.upload')))

    def test_two_servers_keep_their_own_databases(self):
        other = site.create_server('127.0.0.1', 0, self.root / 'other.db', self.root / 'other-rank.db',
                                   admin_token='different-token', download_dir=self.downloads)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            api = RankApi(self.base, '0.2.1', timeout=2)
            creds = api.register('c' * 64)
            api.finish_round({'device_id': creds[0], 'secret': creds[1]},
                             api.start_round({'device_id': creds[0], 'secret': creds[1]}), 60_000, 15, 4)
            other_api = RankApi(f'http://127.0.0.1:{other.server_port}', '0.2.1', timeout=2)
            self.assertEqual(other_api.leaderboard()['total_players'], 0)
            self.assertEqual(api.leaderboard()['total_players'], 1)
        finally:
            other.shutdown(); other.server_close(); thread.join(2)


if __name__ == '__main__':
    unittest.main()

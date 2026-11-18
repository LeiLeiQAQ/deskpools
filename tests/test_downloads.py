"""Actual HTTP redirects and persistent, concurrent download click accounting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from website.server import ReleaseStore, create_server, validate_release, ReleaseError

QUARK = 'https://pan.quark.cn/s/a223a1da77fc'


def release(version='0.3.1', **overrides):
    return dict(version=version, download_url=QUARK, **overrides)


class DownloadStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'site.db'
        self.store = ReleaseStore(self.path)
        self.store.init(Path(self.temp.name) / 'absent.json')
        self.store.publish(release())

    def test_timezone_windows_and_zero_filled_dates(self):
        for stamp in ('2026-10-01T12:00:00+08:00', '2026-10-25T12:00:00+08:00',
                      '2026-11-17T23:59:59+08:00', '2026-11-17T16:00:01+00:00'):
            self.store.record_download_click('0.3.1', 'hero', now=datetime.fromisoformat(stamp))
        stats = self.store.download_stats(now=datetime.fromisoformat('2026-11-18T00:00:02+08:00'))
        self.assertEqual((stats['total_clicks'], stats['today_clicks'], stats['last_7_days'],
                          stats['last_30_days']), (4, 1, 2, 3))
        self.assertEqual(stats['timezone'], 'Asia/Shanghai')
        self.assertEqual(len(stats['daily']), 14)
        self.assertEqual(stats['daily'][-1], dict(date='2026-11-18', clicks=1))
        self.assertEqual(stats['daily'][-2]['clicks'], 1)
        self.assertEqual(stats['daily'][0]['clicks'], 0)

    def test_existing_release_is_retained_when_schema_is_initialized_again(self):
        self.store.record_download_click('0.3.1', 'hero')
        # Simulate a database created by the previous server, which had neither
        # the aggregate table nor its index. Existing versions remain intact.
        with self.store._connection() as db:
            db.execute('DROP TABLE download_daily')
        self.store.init(Path(self.temp.name) / 'absent.json')
        self.assertEqual(self.store.latest()['version'], '0.3.1')
        self.assertEqual(self.store.download_stats()['total_clicks'], 0)

    def test_redirect_header_control_characters_are_rejected(self):
        for target in (QUARK + '\r\nX-Test: x', 'https://pan.quark.cn/has space',
                       'https://pan.quark.cn:bad/s/abc', '//evil.example/app.exe'):
            with self.subTest(target=target), self.assertRaises((ReleaseError, ValueError)):
                validate_release(dict(version='0.3.1', download_url=target))


class DownloadHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db, self.rank = self.root / 'site.db', self.root / 'rank.db'
        store = ReleaseStore(self.db)
        store.init(self.root / 'absent.json')
        store.publish(release('0.3.0'))
        store.publish(release())
        self.start()

    def start(self):
        self.server = create_server('127.0.0.1', 0, self.db, self.rank,
                                    admin_token='download-test-token', download_dir=self.root / 'downloads')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def request(self, path, method='GET', headers=None):
        # HTTPConnection leaves 302 responses unfollowed so this exercises the
        # real redirect without downloading the external provider's page.
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=8)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def stats(self):
        status, headers, body = self.request('/api/admin/downloads', headers={'X-Admin-Token': 'download-test-token'})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn('noindex', headers['X-Robots-Tag'])
        return json.loads(body)

    def test_page_views_and_unauthorized_requests_do_not_count(self):
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/api/releases/latest')[0], 200)
        self.assertEqual(self.request('/api/admin/downloads')[0], 401)
        self.assertEqual(self.stats()['total_clicks'], 0)

    def test_both_buttons_and_direct_entry_count_once_per_redirect(self):
        for query in ('?version=0.3.1&source=hero', '?version=0.3.1&source=release', ''):
            status, headers, body = self.request('/download' + query)
            self.assertEqual(status, 302)
            self.assertEqual(headers['Location'], QUARK)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(body, b'')
        stats = self.stats()
        self.assertEqual((stats['total_clicks'], stats['today_clicks']), (3, 3))
        self.assertEqual({item['source']: item['clicks'] for item in stats['sources']},
                         dict(hero=1, release=1, direct=1))

    def test_head_prefetch_and_prerender_are_not_clicks(self):
        self.assertEqual(self.request('/download', 'HEAD')[0], 302)
        self.assertEqual(self.request('/download?version=9.9.9', 'HEAD')[0], 404)
        self.assertEqual(self.request('/download?version=invalid', 'HEAD')[0], 400)
        for header in ({'Purpose': 'prefetch'}, {'Sec-Purpose': 'prefetch;prerender'}):
            self.assertEqual(self.request('/download', headers=header)[0], 302)
        self.assertEqual(self.stats()['total_clicks'], 0)

    def test_platform_buttons_keep_destination_and_count_separate_sources(self):
        for source in ('windows', 'macos'):
            path = '/download?source=' + source
            self.assertEqual(self.request(path, 'HEAD')[1]['Location'], QUARK)
            self.assertEqual(self.stats()['total_clicks'], 0 if source == 'windows' else 1)
            status, headers, _ = self.request(path)
            self.assertEqual(status, 302)
            self.assertEqual(headers['Location'], QUARK)
        self.assertEqual(self.stats()['total_clicks'], 2)
        self.assertEqual({item['source']: item['clicks'] for item in self.stats()['sources']},
                         dict(windows=1, macos=1))

    def test_invalid_inputs_and_missing_release_do_not_count_or_redirect(self):
        for query in ('?url=https://evil.example/', '?version=0.3.1&version=0.3.0',
                      '?source=unknown', '?source=', '?version=', '?version=invalid'):
            status, headers, _ = self.request('/download' + query)
            self.assertEqual(status, 400, query)
            self.assertNotIn('Location', headers)
        self.assertEqual(self.request('/download?version=9.9.9')[0], 404)
        self.assertEqual(self.stats()['total_clicks'], 0)

    def test_clicks_are_attributed_to_requested_version_after_current_changes(self):
        self.request('/download?version=0.3.0&source=hero')
        self.request('/download?source=hero')
        versions = {item['version']: item['clicks'] for item in self.stats()['versions']}
        self.assertEqual(versions, {'0.3.0': 1, '0.3.1': 1})

    def test_counts_survive_actual_server_restart(self):
        self.assertEqual(self.request('/download')[0], 302)
        self.stop()
        self.start()
        self.assertEqual(self.stats()['total_clicks'], 1)

    def test_concurrent_redirects_do_not_lose_increments(self):
        with ThreadPoolExecutor(max_workers=8) as workers:
            statuses = list(workers.map(lambda _: self.request('/download?source=hero')[0], range(32)))
        self.assertEqual(statuses, [302] * 32)
        self.assertEqual(self.stats()['total_clicks'], 32)

    def test_failed_database_write_is_reported_instead_of_claiming_a_redirect(self):
        with patch.object(self.server.RequestHandlerClass.store, 'record_download_click',
                          side_effect=sqlite3.OperationalError('database write failed')):
            status, headers, _ = self.request('/download')
        self.assertEqual(status, 500)
        self.assertNotIn('Location', headers)
        self.assertEqual(self.stats()['total_clicks'], 0)

    def test_empty_release_list_has_no_click_target(self):
        with self.server.RequestHandlerClass.store._connection() as db:
            db.execute('DELETE FROM releases')
        self.assertEqual(self.request('/download')[0], 404)
        self.assertEqual(self.stats()['total_clicks'], 0)


if __name__ == '__main__':
    unittest.main()

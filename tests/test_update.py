import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dib.update import (MAX_FEED_BYTES, UpdateChecker, UpdateResult, fetch_update,
                        parse_update, release_entries, update_feed_url, version_tuple)
from dib.ui import DesktopBilliardsApp


def feed(**overrides):
    info = {
        'version': '0.2.1',
        'download_url': 'https://example.com/DesktopIconBilliards-0.2.1-win-x64.exe',
        'notes': '新版本说明',
    }
    info.update(overrides)
    return info


def encoded(info):
    return json.dumps(info, ensure_ascii=False).encode('utf-8')


class VersionTupleTests(unittest.TestCase):
    def test_accepts_three_part_versions(self):
        self.assertEqual(version_tuple('0.2.10'), (0, 2, 10))
        self.assertEqual(version_tuple('65535.0.1'), (65535, 0, 1))

    def test_rejects_invalid_versions(self):
        for value in ('1.2', '1.2.3.4', 'v1.2.3', '1.-2.3', '', None, 123):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    version_tuple(value)


class ParseUpdateTests(unittest.TestCase):
    def test_returns_release_when_newer(self):
        result = parse_update(encoded(feed()), '0.1.0')
        self.assertEqual(result['version'], '0.2.1')
        self.assertEqual(result['download_url'], feed()['download_url'])
        self.assertEqual(result['notes'], '新版本说明')
        self.assertNotIn('history', result)

    def test_returns_none_when_current_or_newer(self):
        self.assertIsNone(parse_update(encoded(feed()), '0.2.1'))
        self.assertIsNone(parse_update(encoded(feed()), '0.3.0'))

    def test_resolves_backend_packages_against_the_feed_origin(self):
        for source in ('https://billiards.example/api/releases/latest',
                       'http://127.0.0.1:8070/api/releases/latest'):
            result = parse_update(encoded(feed(download_url='/downloads/app.exe')), '0.1.0', source_url=source)
            self.assertEqual(result['download_url'], source.removesuffix('/api/releases/latest') + '/downloads/app.exe')
        for url in ('//other.example/app.exe', '/downloads/../app.exe', '/downloads/app.exe?x=1',
                    '/downloads/app.html', 'http://example.com/app.exe'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_update(encoded(feed(download_url=url)), '0.1.0', source_url=source)

    def test_empty_backend_release_is_not_reported_as_up_to_date(self):
        with self.assertRaisesRegex(ValueError, '尚未发布'):
            parse_update(b'{}', '0.2.1')

    def test_accepts_utf8_bom(self):
        data = b'\xef\xbb\xbf' + encoded(feed())
        self.assertEqual(parse_update(data, '0.1.0')['version'], '0.2.1')

    def test_rejects_unsafe_download_urls(self):
        for url in ('http://example.com/app.exe', 'javascript:alert(1)', 'file:///tmp/a.exe',
                    'https://user:pass@example.com/app.exe', 'https:///app.exe', ''):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    parse_update(encoded(feed(download_url=url)), '0.1.0')

    def test_rejects_invalid_payloads(self):
        with self.assertRaises(ValueError):
            parse_update(b'[]', '0.1.0')
        with self.assertRaises(ValueError):
            parse_update(encoded(feed(version='1.2')), '0.1.0')
        with self.assertRaises(ValueError):
            parse_update(encoded(feed(notes='x' * 4001)), '0.1.0')
        with self.assertRaises(ValueError):
            parse_update(encoded(feed(notes=None)), '0.1.0')

    def test_history_is_validated_sorted_and_capped(self):
        history = [
            {'version': '0.1.2', 'notes': 'older'},
            {'version': '0.2.1', 'notes': 'duplicate of latest'},
            {'version': '0.3.0', 'notes': 'newer than latest'},
            {'version': 'bad', 'notes': 'invalid'},
            {'version': '0.1.2', 'notes': 'duplicate'},
            {'version': '0.1.9', 'notes': 'x' * 4001},
            {'version': '0.1.5', 'notes': 'newest history'},
        ]
        result = parse_update(encoded(feed(history=history)), '0.1.0')
        self.assertEqual([row['version'] for row in result['history']], ['0.1.5', '0.1.2'])
        self.assertEqual(result['history'][0]['notes'], 'newest history')

    def test_history_cannot_override_latest_download(self):
        info = feed(history=[{'version': '0.1.9', 'notes': 'old', 'download_url': 'https://evil.example/x'}])
        result = parse_update(encoded(info), '0.1.0')
        self.assertEqual(result['download_url'], feed()['download_url'])


class ReleaseEntriesTests(unittest.TestCase):
    def test_latest_first_then_numeric_order(self):
        info = feed(history=[
            {'version': '0.1.9', 'notes': 'a'},
            {'version': '0.1.10', 'notes': 'b'},
        ])
        entries = release_entries(info)
        self.assertEqual([row['version'] for row in entries], ['0.2.1', '0.1.10', '0.1.9'])

    def test_caps_display_entries_at_21(self):
        history = [{'version': f'0.0.{index}', 'notes': 'n'} for index in range(50)]
        self.assertEqual(len(release_entries(feed(history=history))), 21)


class _FeedHandler(BaseHTTPRequestHandler):
    payload = b'{}'
    gate = None
    entered = None

    def do_GET(self):  # noqa: N802 - http.server API
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            self.gate.wait(3)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


class FetchUpdateTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _FeedHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/update.json'

    def tearDown(self):
        if _FeedHandler.gate is not None:
            _FeedHandler.gate.set()
        _FeedHandler.gate = _FeedHandler.entered = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_fetches_and_parses_over_local_http(self):
        _FeedHandler.payload = encoded(feed())
        result = fetch_update(self.url, '0.1.0')
        self.assertEqual(result['version'], '0.2.1')

    def test_nonlocal_http_and_invalid_origins_are_rejected_before_network(self):
        with patch('dib.update.urllib.request.urlopen') as request:
            for url in ('http://example.com', 'http://localhost.evil.example', 'file:///app',
                        'https://user:pass@example.com', 'https://example.com:99999',
                        'https://example.com\n', ''):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    update_feed_url(url)
            request.assert_not_called()
        self.assertEqual(update_feed_url('http://[::1]:8070/'), 'http://[::1]:8070/api/releases/latest')

    def test_rejects_oversized_response(self):
        _FeedHandler.payload = b'x' * (MAX_FEED_BYTES + 1)
        with self.assertRaises(ValueError):
            fetch_update(self.url, '0.1.0')

    def test_empty_url_is_a_noop(self):
        self.assertIsNone(fetch_update('', '0.1.0'))

    def wait_result(self, checker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = checker.take_result()
            if result is not None:
                return result
            time.sleep(.01)
        self.fail('manual update worker did not finish')

    def test_slow_backend_does_not_block_or_start_duplicate_requests(self):
        _FeedHandler.payload = encoded(feed(download_url='/downloads/app.exe'))
        _FeedHandler.entered = threading.Event()
        _FeedHandler.gate = threading.Event()
        base = self.url.removesuffix('/update.json')
        checker = UpdateChecker()
        self.assertFalse(checker.busy)
        self.assertFalse(_FeedHandler.entered.is_set())  # No startup request.
        self.assertTrue(checker.check(base, '0.1.0'))
        self.assertTrue(_FeedHandler.entered.wait(2))
        self.assertTrue(checker.busy)
        self.assertFalse(checker.check(base, '0.1.0'))
        self.assertIsNone(checker.take_result())
        _FeedHandler.gate.set()
        result = self.wait_result(checker)
        self.assertFalse(result.error)
        self.assertEqual(result.info['download_url'], base + '/downloads/app.exe')
        self.assertFalse(checker.busy)

    def test_bad_response_is_visible_and_check_can_be_retried(self):
        checker = UpdateChecker()
        base = self.url.removesuffix('/update.json')
        _FeedHandler.payload = b'not json'
        checker.check(base, '0.1.0')
        result = self.wait_result(checker)
        self.assertIn('格式错误', result.error)
        self.assertIn('JSONDecodeError', result.details)
        _FeedHandler.payload = encoded(feed())
        self.assertTrue(checker.check(base, '0.2.1'))
        result = self.wait_result(checker)
        self.assertFalse(result.error)
        self.assertIsNone(result.info)


class ManualUpdateUiTests(unittest.TestCase):
    def app(self, result):
        app = DesktopBilliardsApp.__new__(DesktopBilliardsApp)
        app.closing = app.pause_hidden = False
        app.panel_visible = True
        app.update_dialog = None
        app.update_checker = Mock()
        app.update_checker.take_result.return_value = result
        app.root = Mock()
        app.panel = SimpleNamespace(settings_window=Mock(), settings_open=True)
        return app

    def test_current_release_and_errors_are_shown_in_settings(self):
        app = self.app(UpdateResult())
        app.poll_update()
        self.assertIn('已是最新版本', app.panel.settings_window.set_update_status.call_args.args[0])
        app.update_checker.take_result.return_value = UpdateResult(error='连接后台失败', details='network detail')
        with patch('dib.ui._write_log') as log:
            app.poll_update()
        app.panel.settings_window.set_update_status.assert_called_with('连接后台失败', error=True)
        log.assert_called_once_with('update.log', 'network detail')

    @patch('dib.ui.show_update_dialog')
    def test_new_release_dialog_stays_hidden_when_paused_or_settings_closed(self, show):
        app = self.app(UpdateResult(info=feed()))
        app.pause_hidden = True
        app.poll_update()
        app.pause_hidden = False
        app.panel.settings_open = False
        app.poll_update()
        show.assert_not_called()
        app.panel.settings_open = True
        app.poll_update()
        show.assert_called_once()

    def test_closing_never_touches_tk_or_waits_for_network(self):
        app = self.app(UpdateResult())
        app.closing = True
        app.check_update()
        app.poll_update()
        app.update_checker.check.assert_not_called()
        app.update_checker.take_result.assert_not_called()
        app.panel.settings_window.set_update_status.assert_not_called()

    @patch('dib.ui.webbrowser.open', return_value=True)
    def test_download_uses_the_published_url_in_the_browser(self, browser):
        app = self.app(None)
        dialog = Mock()
        app.open_update_download(dialog, 'https://example.com/app.exe')
        browser.assert_called_once_with('https://example.com/app.exe')
        dialog.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()

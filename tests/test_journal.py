"""Windows readers must not prevent the UI from closing its recovery journal."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from dib.shell import LayoutSession, atomic_json, read_json
from dib.ui import DesktopBilliardsApp


class JournalTests(unittest.TestCase):
    def test_independent_reader_process_cannot_block_repeated_atomic_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'layout.json'
            atomic_json(path, {'index': 0})
            reader = subprocess.Popen([sys.executable, '-c',
                'from dib.shell import read_json; import sys; '
                '[read_json(sys.argv[1]) for _ in range(2000)]', str(path)])
            try:
                for index in range(500):
                    atomic_json(path, {'index': index})
            finally:
                reader.wait(timeout=10)
            self.assertEqual(reader.returncode, 0)
            self.assertEqual(read_json(path), {'index': 499})

    def test_ui_waits_for_watchdog_reader_before_replacing_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'layout.json'
            atomic_json(path, {'active': True, 'items': list(range(5000))})
            original = json.load
            opened, finish, written = threading.Event(), threading.Event(), threading.Event()
            result, errors = [], []
            def held_read(stream):
                opened.set()
                if not finish.wait(3):
                    raise TimeoutError('writer did not complete')
                return original(stream)
            def reader():
                try:
                    result.append(read_json(path))
                except Exception as exc:
                    errors.append(exc)
            def writer():
                try:
                    for index in range(50):
                        atomic_json(path, {'active': False, 'index': index})
                    written.set()
                except Exception as exc:
                    errors.append(exc)
            with patch('dib.shell.json.load', side_effect=held_read):
                thread = threading.Thread(target=reader)
                thread.start()
                writing = threading.Thread(target=writer)
                try:
                    self.assertTrue(opened.wait(2))
                    writing.start()
                    self.assertFalse(written.wait(.05))
                finally:
                    finish.set()
                    thread.join(3)
                    writing.join(3)
            self.assertFalse(errors)
            self.assertTrue(written.is_set())
            self.assertEqual(result, [{'active': True, 'items': list(range(5000))}])
            self.assertEqual(read_json(path), {'active': False, 'index': 49})
            self.assertFalse(list(Path(folder).glob('*.tmp-*')))

    def test_failed_save_leaves_session_active_for_a_real_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'layout.json'
            snapshot = {'active': True, 'overlay_only': True, 'session_id': 'original'}
            atomic_json(path, snapshot)
            session = LayoutSession(Mock(), path)
            with patch('dib.shell.atomic_json', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):
                    session.restore()
            self.assertTrue(session.snapshot['active'])
            self.assertEqual(read_json(path), snapshot)
            self.assertTrue(session.restore()['success'])
            self.assertFalse(read_json(path)['active'])

    def test_missing_journal_has_the_expected_file_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                read_json(Path(folder) / 'absent.json')

    def test_failed_replace_cleans_its_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'layout.json'
            atomic_json(path, {'active': True})
            with path.open('r', encoding='utf-8'):
                with self.assertRaises(PermissionError):
                    atomic_json(path, {'active': False})
            self.assertEqual(read_json(path), {'active': True})
            self.assertFalse(list(Path(folder).glob('*.tmp-*')))


class ClosingTests(unittest.TestCase):
    def test_restore_failure_dialog_cannot_run_a_poll_on_a_cleared_runner(self):
        app = DesktopBilliardsApp.__new__(DesktopBilliardsApp)
        app.closing = False
        app.pulse = None
        app.runner = None
        app.root = Mock()
        app.fail = Mock()
        app.restore = Mock(side_effect=PermissionError('journal locked'))
        def warning(*args, **kwargs):
            self.assertTrue(app.closing)
            app.poll()  # Tk's modal dialog processes pending timer callbacks.
        with patch('dib.ui.messagebox.showwarning', side_effect=warning), patch('dib.ui._write_log'):
            app.close()
        self.assertFalse(app.closing)
        app.fail.assert_not_called()
        app.root.destroy.assert_not_called()
        app.poll()
        app.fail.assert_not_called()

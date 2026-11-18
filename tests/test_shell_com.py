"""Native vtable calls must not postpone Release beyond the Shell apartment."""
import ctypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
import weakref

from dib.shell import ShellDesktop


class ShellComTests(unittest.TestCase):
    def test_vtable_call_does_not_retain_owning_pointer(self):
        signature = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int)
        received = []

        @signature
        def method(address, value):
            received.append((address, value))
            return 1

        class Interface(ctypes.Structure):
            _fields_ = [('vtable', ctypes.POINTER(ctypes.c_void_p))]

        slots = (ctypes.c_void_p * 4)()
        slots[3] = ctypes.cast(method, ctypes.c_void_p).value
        interface = Interface(slots)
        owner = ctypes.pointer(interface)
        reference = weakref.ref(owner)
        address = ctypes.addressof(interface)
        enabled = gc.isenabled()
        gc.disable()
        try:
            self.assertEqual(ShellDesktop._invoke(owner, 3, [ctypes.c_int], 17), 1)
            self.assertEqual(received, [(address, 17)])
            del owner
            self.assertIsNone(reference(), 'The borrowed call introduced an ownership cycle')
        finally:
            if enabled:
                gc.enable()

    def test_hresult_failure_still_raises(self):
        signature = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)

        @signature
        def method(address):
            return -2147467259  # E_FAIL

        class Interface(ctypes.Structure):
            _fields_ = [('vtable', ctypes.POINTER(ctypes.c_void_p))]

        slots = (ctypes.c_void_p * 4)()
        slots[3] = ctypes.cast(method, ctypes.c_void_p).value
        owner = ctypes.pointer(Interface(slots))
        with self.assertRaisesRegex(OSError, '80004005'):
            ShellDesktop._invoke(owner, 3, [])

    def test_real_shell_factories_release_before_worker_apartment_closes(self):
        # An isolated process keeps GC/destructor instrumentation out of the app
        # and other tests. The image factory and bitmap extraction are real.
        code = textwrap.dedent('''
            import ctypes, gc, json, threading
            import comtypes
            from dib.overlay import ImageFactory, artwork

            events, errors = [], []
            active = False
            pointer_type = ctypes.POINTER(ImageFactory)
            original_del = pointer_type.__del__

            def released(self):
                if self:
                    events.append((active, threading.get_ident()))
                original_del(self)

            pointer_type.__del__ = released

            def worker():
                global active
                gc.collect()
                enabled = gc.isenabled()
                gc.disable()
                comtypes.CoInitialize()
                active = True
                try:
                    for _ in range(3):
                        image = artwork('::{645FF040-5081-101B-9F08-00AA002F954E}', 128)
                        assert image.width > 0 and image.height > 0
                except BaseException as exc:
                    errors.append(repr(exc))
                finally:
                    comtypes.CoUninitialize()
                    active = False
                    gc.collect()
                    if enabled:
                        gc.enable()

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            print(json.dumps(dict(events=events, errors=errors, worker=thread.ident)))
        ''')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['events']), 3)
        self.assertTrue(all(active and thread == report['worker'] for active, thread in report['events']),
                        report)


if __name__ == '__main__':
    unittest.main()

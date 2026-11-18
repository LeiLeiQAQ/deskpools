"""Out-of-process desktop recovery if the UI dies; never handles audio or animation."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path

from .shell import LayoutSession, ShellDesktop, atomic_json, read_json
from .health import HealthPulse, stalled_reason


def main(args):
    """Watch the UI process and restore the journaled layout when it disappears."""
    journal = Path(args[0])
    nonce = args[1]
    parent = int(args[2])
    ready = journal.with_name(journal.stem + '.watchdog.json')
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel32.OpenProcess(0x00100001, False, parent)
    if not handle:
        atomic_json(
            ready,
            {'session_id': nonce, 'ready': False, 'error': 'Cannot monitor UI process'},
        )
        raise SystemExit(2)
    atomic_json(ready, {'session_id': nonce, 'ready': True, 'pid': os.getpid()})
    pulse = HealthPulse(nonce)
    previous = time.monotonic()
    grace_until = 0.
    try:
        while True:
            try:
                snapshot = read_json(journal)
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.3)
                continue
            if snapshot.get('session_id') != nonce or not snapshot.get('active'):
                break
            state = kernel32.WaitForSingleObject(handle, 400)
            now = time.monotonic()
            # Suspend/resume must not be misclassified as a hung game.
            gap = now - previous
            previous = now
            if gap > 5:
                grace_until = now + 20
            reason = stalled_reason(pulse, now) if now >= grace_until else ''
            if reason and state != 0:
                atomic_json(journal.with_name(journal.stem + '.watchdog-stall.json'),
                            {'session_id': nonce, 'reason': reason, 'time': time.time()})
                if not kernel32.TerminateProcess(handle, 124):
                    raise ctypes.WinError()
                kernel32.WaitForSingleObject(handle, 5000)
            if state == 0 or reason:
                try:
                    desktop = ShellDesktop()
                    result = LayoutSession(desktop, journal).restore()
                    atomic_json(
                        journal.with_name(journal.stem + '.watchdog-recovered.json'),
                        {'session_id': nonce, **result},
                    )
                except Exception as exc:
                    atomic_json(
                        journal.with_name(journal.stem + '.watchdog-error.json'),
                        {'session_id': nonce, 'error': str(exc)},
                    )
                break
    finally:
        pulse.close()
        kernel32.CloseHandle(handle)


if __name__ == '__main__':
    main(sys.argv[1:])

"""Shared heartbeats read by the independent desktop recovery process."""
import mmap
import struct
import time

UI, RUNNER, ARMED, EMERGENCY, CLOSING = 0, 8, 16, 24, 32


class HealthPulse:
    def __init__(self, session_id, *, create=False):
        self.memory = mmap.mmap(-1, 64, tagname='Local\\DeskPoolsHealth-' + session_id)
        if create:
            self.memory[:] = bytes(64)
            self.beat(UI)
            self.beat(RUNNER)

    def beat(self, offset):
        struct.pack_into('d', self.memory, offset, time.monotonic())

    def get(self, offset):
        return struct.unpack_from('d', self.memory, offset)[0]

    def arm(self):
        self.beat(UI)
        self.beat(RUNNER)
        self.beat(ARMED)

    def request_stop(self):
        self.beat(EMERGENCY)

    def close(self):
        self.memory.close()


def stalled_reason(pulse, now):
    emergency = pulse.get(EMERGENCY)
    if emergency and now - emergency > 2:
        return 'emergency_exit'
    closing = pulse.get(CLOSING)
    if closing and now - closing > 6:
        return 'shutdown_timeout'
    if pulse.get(ARMED):
        if now - pulse.get(UI) > 15:
            return 'ui_heartbeat_timeout'
        if not closing and now - pulse.get(RUNNER) > 15:
            return 'animation_heartbeat_timeout'
    return ''

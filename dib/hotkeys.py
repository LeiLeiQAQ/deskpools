"""A message-only window receives global game hotkeys through Tk."""
from __future__ import annotations

import ctypes
import queue

import win32api
import win32con
import win32gui

from . import config

DEFAULT_PAUSE = 'F1'
DEFAULT_EXIT = 'Ctrl+Alt+Shift+X'


def parse_binding(text):
    parts = text.strip().split('+')
    modifiers = 0x4000
    seen = set()
    for part in parts[:-1]:
        token = part.lower()
        values = {'ctrl': 2, 'alt': 1, 'shift': 4, 'win': 8}
        if token not in values or token in seen:
            raise ValueError('快捷键格式示例：F1 或 Ctrl+Alt+P')
        seen.add(token)
        modifiers |= values[token]
    key = parts[-1].upper()
    if key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isascii() and key.isalnum():
        if modifiers == 0x4000:
            raise ValueError('字母/数字快捷键需要 Ctrl、Alt、Shift 或 Win 组合')
        vk = ord(key)
    else:
        raise ValueError('按键支持 F1～F24 或字母/数字组合')
    canonical = '+'.join([name for name, token in [('Ctrl','ctrl'),('Alt','alt'),('Shift','shift'),('Win','win')]
                          if token in seen] + [key])
    return modifiers, vk, canonical


class Hotkeys:
    def __init__(self, pause=DEFAULT_PAUSE, quit=DEFAULT_EXIT, pulse=None):
        self.pending = queue.SimpleQueue()
        self.registered = set()
        self.pulse = pulse
        self.errors = []
        self.instance = win32api.GetModuleHandle(None)
        self.class_name = f'DesktopIconBilliardsHotkeys-{id(self)}'
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = self.instance
        window_class.lpszClassName = self.class_name
        window_class.lpfnWndProc = self._message
        win32gui.RegisterClass(window_class)
        # HWND_MESSAGE: invisible, but dispatched by Tk's native message loop.
        self.hwnd = win32gui.CreateWindow(self.class_name, '', 0, 0, 0, 0, 0, -3, 0, self.instance, None)
        self.user32 = user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.labels = {}
        for identifier, binding in ((config.PAUSE_HOTKEY_ID, pause), (config.HOTKEY_ID, quit)):
            try:
                self.bind(identifier, binding)
            except (ValueError, OSError) as error:
                self.errors.append(str(error))

    def bind(self, identifier, binding):
        modifiers, key, canonical = parse_binding(binding)
        if identifier in self.registered and self.labels.get(identifier) == canonical:
            return canonical
        # Register a temporary id before releasing the old chord. Failed edits
        # therefore retain the working shortcut rather than disabling it.
        probe = identifier + 20
        if not self.user32.RegisterHotKey(self.hwnd, probe, modifiers, key):
            raise OSError(ctypes.get_last_error(), f'{canonical} 已被占用，请更换组合键')
        self.user32.UnregisterHotKey(self.hwnd, probe)
        if identifier in self.registered:
            self.user32.UnregisterHotKey(self.hwnd, identifier)
            self.registered.remove(identifier)
        if not self.user32.RegisterHotKey(self.hwnd, identifier, modifiers, key):
            raise OSError(ctypes.get_last_error(), f'{canonical} 注册失败，请重试')
        self.registered.add(identifier)
        self.labels[identifier] = canonical
        return canonical

    def _message(self, hwnd, message, wparam, lparam):
        if message == win32con.WM_HOTKEY and wparam in self.registered:
            if wparam == config.HOTKEY_ID and self.pulse:
                self.pulse.request_stop()
            self.pending.put(wparam)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def close(self):
        if self.hwnd is None:
            return
        for identifier in self.registered:
            self.user32.UnregisterHotKey(self.hwnd, identifier)
        self.registered.clear()
        win32gui.DestroyWindow(self.hwnd)
        self.hwnd = None
        win32gui.UnregisterClass(self.class_name, self.instance)

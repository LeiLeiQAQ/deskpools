"""Product constants shared by the window, the single-instance guard and the build."""
from __future__ import annotations

import os
from pathlib import Path

from . import __version__

# Single source of truth for the release version. The build script reads it.
VERSION: str = __version__
VERSION_PARTS = tuple(int(part) for part in VERSION.split('.'))
if len(VERSION_PARTS) != 3 or any(part < 0 for part in VERSION_PARTS):
    raise RuntimeError(f'Invalid version: {VERSION!r}')

PRODUCT_NAME = '桌面图标台球'
PRODUCT_NAME_EN = 'Desktop Icon Billiards'
INTERNAL_NAME = 'DesktopIconBilliards'
WINDOW_TITLE = f'{PRODUCT_NAME} {VERSION}'

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / 'assets'
ICON_PATH = ASSETS / 'desktop-icon-billiards.ico'

# Settings checks /api/releases/latest on this origin only when requested.
# Startup never polls for updates; downloads open in the browser.
DEFAULT_WEBSITE_URL = 'https://deskpools.com'
WEBSITE_URL = os.environ.get('DIB_WEBSITE_URL', DEFAULT_WEBSITE_URL).strip()

# Origin of the ranking server (https, no path). Empty disables the ranking feature.
DEFAULT_RANK_URL = DEFAULT_WEBSITE_URL
RANK_URL = os.environ.get('DIB_RANK_URL', DEFAULT_RANK_URL).strip()

# Separate app identity and a shared guard for the Explorer icon layer.
DESKTOP_GUARD = r'Local\MusicRhythmNativeV2'
MUTEX = r'Local\DesktopIconBilliards'
VERSION_PROPERTY = 'DesktopIconBilliards.Version'
EXE_PATTERN = r'DesktopIconBilliards-(\d+\.\d+\.\d+)-win-x64\.exe'

# Ctrl+Alt+Shift+X stops the game, restores the desktop and exits.
HOTKEY_ID = 0x5A32
HOTKEY_MODIFIERS = 0x4000 | 0x2 | 0x1 | 0x4  # MOD_NOREPEAT | MOD_CONTROL | MOD_ALT | MOD_SHIFT
HOTKEY_VK = 0x58  # X

# F1 pauses the game, exposes the desktop and minimizes the toolbar to the taskbar.
PAUSE_HOTKEY_ID = 0x5A33
PAUSE_HOTKEY_MODIFIERS = 0x4000  # MOD_NOREPEAT
PAUSE_HOTKEY_VK = 0x70  # VK_F1

"""Tk window, game lifecycle, and single-instance handling."""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import time
import traceback
import webbrowser
import uuid

import tkinter as tk
import win32api
import win32con
import win32gui
import win32process
from tkinter import messagebox

from . import config, diag
from .display import dpi_context
from .hotkeys import Hotkeys
from .health import HealthPulse, UI, CLOSING
from .history import LocalScores
from .modes import MODES, CUE_SKINS
from .tray import Tray
from .instance import acquire_instance, claim_instance_mutex, publish_version
from .overlay import BilliardsOverlay
from .panel import MainPanel, ResultDialog, format_duration, show_update_dialog
from .paths import runtime_directory
from .leaderboard import LeaderboardDialog
from .rank import RankApi, RankController, RankSettings, average_seconds, data_directory
from .runner import BilliardsRunner
from .shell import LayoutSession, ShellDesktop, read_json
from .update import UpdateChecker, release_entries


DESKTOP_WINDOW_CLASSES = {
    'Progman', 'WorkerW', 'SHELLDLL_DefView', 'SysListView32',
    'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
}


def desktop_or_app_foreground(hwnd, process_id):
    """The game HUD belongs on the desktop, never over another active application."""
    if not hwnd:
        return False
    # The foreground window can disappear between GetForegroundWindow and this
    # check (especially while switching Chrome tabs). Treat that frame as
    # "not our desktop" instead of letting a transient Win32 1400 abort Tk.
    try:
        if win32process.GetWindowThreadProcessId(hwnd)[1] == process_id or win32gui.IsIconic(hwnd):
            return True
    except Exception:
        return False
    while hwnd:
        try:
            if win32gui.GetClassName(hwnd) in DESKTOP_WINDOW_CLASSES:
                return True
            hwnd = win32gui.GetParent(hwnd)
        except Exception:
            return False
    return False


RANK_CONSENT = (
    '开启后，每局清台时会向排名服务器提交：\n'
    '  • 本局用时、图标数量、击球次数\n'
    '  • 当前 Windows 系统版本与构建号、设备哈希标识\n\n'
    '排行榜公开显示连接 IP 地址和系统版本。IP 由服务器从连接中获取。\n'
    '不上传图标名称、桌面截图或个人文件。可随时在设置中关闭排名。\n\n'
    '是否开启全球排名？'
)
RANK_INTRO = '现在可以和全球玩家比谁清台更快了。\n\n' + RANK_CONSENT


def _write_log(name, text):
    try:
        (runtime_directory() / name).write_text(text, encoding='utf-8')
    except OSError:
        pass


def describe_error(error):
    """The error text plus where it was raised (``file.py:line in function``).

    A bare message such as "'NoneType' object is not iterable" says nothing on its own; the
    location lets a player's screenshot pin the bug down. The last frame inside this package wins.
    """
    frames = traceback.extract_tb(error.__traceback__)
    frame = next((f for f in reversed(frames) if 'dib' in f.filename.replace('\\', '/').split('/')), None)
    frame = frame or (frames[-1] if frames else None)
    where = f'{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}' if frame else ''
    text = f'{error}\n\n出错位置：{where}' if where else str(error)
    return f'{text}\n\n详细信息见 %TEMP%\\DesktopIconBilliards\\error.log'


class DesktopBilliardsApp:
    """Own the Tk window, the layout journal and the animation runner."""

    def __init__(self, root, instance_handle):
        self.root = root
        self.instance_handle = instance_handle
        self.runner = None
        self.session = None
        self.watchdog = None
        self.closing = False
        self.panel_visible = True
        self.pause_hidden = False
        self.background_hidden = False
        self.pulse = None
        self.history_window = None
        self.local_recorded = None
        self.session_key = uuid.uuid4().hex
        self.poll_id = None
        self.toolbar_hidden = False
        self.toolbar_eye = None
        self.toolbar_region = None
        self.toolbar_hover_since = None
        self.toolbar_leave_since = None
        self.stealth_announced = False
        self.deadline = 0.0
        self.journal = runtime_directory() / 'layout.json'
        root.title(config.WINDOW_TITLE)
        root.resizable(False, False)
        try:
            root.iconbitmap(str(config.ICON_PATH))
        except tk.TclError:
            pass
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-2))
        self.rank = self.make_rank()
        self.rank_offered = False
        self.guide_enabled = True
        self.leaderboard_dialog = None
        self.result_dialog = None
        self.result_serial = None
        self.update_checker = UpdateChecker()
        self.update_dialog = None
        self.settings = self.make_settings()
        self.guide_enabled = bool(self.settings.get('guide', True))
        self.mode = self.settings.get('mode', 'practice')
        if self.mode not in MODES:
            self.mode = 'practice'
        self.cue_skin = self.settings.get('cue_skin', 'wood')
        if self.cue_skin not in CUE_SKINS:
            self.cue_skin = 'wood'
        self.volume = max(0., min(1., float(self.settings.get('volume', .45))))
        self.muted = bool(self.settings.get('muted', False))
        self.scores = LocalScores(data_directory() / 'scores.sqlite3')
        self.rank.local_scores = self.scores
        self.felt_enabled = bool(self.settings.get('felt', True))
        self.panel = panel = MainPanel(root, config.VERSION, {
            'restart': self.restart, 'toggle_guide': self.toggle_guide, 'toggle_rank': self.toggle_rank,
            'quit': self.close, 'toggle_felt': self.toggle_felt, 'leaderboard': self.show_leaderboard,
            'open_website': self.open_website, 'check_update': self.check_update,
            'set_mode': self.set_mode, 'set_cue': self.set_cue, 'set_volume': self.set_volume,
            'toggle_mute': self.toggle_mute, 'history': self.show_history, 'set_hotkeys': self.set_hotkeys,
        })
        publish_version(root, config.VERSION)
        self.time_title, self.time_value = panel.time_title, panel.time_value
        self.count_title, self.count_value = panel.count_title, panel.count_value
        self.remaining_value = panel.remaining_value
        self.refresh_buttons()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Escape>', self.escape)
        self.place_window()
        self.hotkeys = Hotkeys(self.settings.get('pause_key','F1'), self.settings.get('exit_key','Ctrl+Alt+Shift+X'))
        self.tray = Tray()
        self.update_preferences()
        if self.hotkeys.errors:
            panel.show_settings()
            panel.settings_window.shortcut_status.set('\n'.join(self.hotkeys.errors))
        root.after(100, self.heartbeat)
        root.after(20, self.start)

    def heartbeat(self):
        if self.closing:
            return
        if self.pulse:
            self.pulse.beat(UI)
        self.root.after(100, self.heartbeat)

    def escape(self, event=None):
        self.panel.hide_settings()
        if self.runner and self.runner.overlay.table.charge:
            self.runner.overlay.cancel_charge.set()
        else:
            self.toggle_pause_hidden()
        return 'break'

    def update_preferences(self):
        settings = self.panel.settings_window
        settings.mode_var.set(MODES[self.mode][0])
        settings.cue_var.set(CUE_SKINS[self.cue_skin])
        settings.rule_hint.set(MODES[self.mode][1])
        settings.volume_var.set(self.volume * 100)
        settings.mute_button.set('取消静音' if self.muted else '静音', 'on' if self.muted else 'soft')
        pause = self.settings.get('pause_key','F1')
        quit = self.settings.get('exit_key','Ctrl+Alt+Shift+X')
        settings.pause_key.set(pause)
        settings.exit_key.set(quit)
        settings.shortcut_hint.set(f'{pause} 暂停/恢复 · Shift 显示球杆\n{quit} 退出 · 右键取消蓄力')
        self.panel.hotkey_hint.configure(text=f'{pause} 暂停 / 恢复')

    def set_hotkeys(self, pause, quit):
        status = self.panel.settings_window.shortcut_status
        try:
            p = self.hotkeys.bind(config.PAUSE_HOTKEY_ID,pause)
            self.settings.update(pause_key=p)
            q = self.hotkeys.bind(config.HOTKEY_ID,quit)
            self.settings.update(exit_key=q)
        except (ValueError,OSError) as error:
            status.set(str(error))
        else:
            status.set('快捷键已生效')
        self.update_preferences()

    def set_volume(self, value):
        self.volume = max(0.,min(1.,float(value)))
        self.settings.update(volume=self.volume)
        if self.runner:
            self.runner.overlay.want_volume = 0. if self.muted else self.volume

    def toggle_mute(self):
        self.muted = not self.muted
        self.settings.update(muted=self.muted)
        self.set_volume(self.volume)
        self.update_preferences()

    def set_cue(self, skin):
        self.cue_skin = skin
        self.settings.update(cue_skin=skin)
        if self.runner:
            self.runner.overlay.cue_skin = skin
            self.runner.overlay.cue_key = None

    def set_mode(self, mode):
        if mode == self.mode:
            return
        if (self.runner and self.runner.overlay.table.first_shot_at is not None
                and self.runner.overlay.table.state != 'finished'):
            if not messagebox.askyesno('切换玩法','切换会结束当前未完成的一局，是否继续？',parent=self.root):
                self.update_preferences()
                return
        self.restore()
        if self.result_dialog is not None:
            self.result_dialog.close()
            self.result_dialog = None
        self.result_serial = None
        self.mode = mode
        self.settings.update(mode=mode)
        self.pause_hidden = self.background_hidden = False
        self.local_recorded = None
        self.update_preferences()
        self.start()

    def show_history(self):
        self.rank.retry_pending(force=True)
        if self.history_window is not None and self.history_window.winfo_exists():
            self.history_window.destroy()
        top = self.history_window = tk.Toplevel(self.root)
        top.title('最近十局 · 本机成绩')
        from tkinter import ttk
        from datetime import datetime
        tree = ttk.Treeview(top,columns=('mode','score','shots','time'),show='headings',height=10)
        for key,label,width in [('mode','玩法',110),('score','成绩',180),('shots','杆数',60),('time','完成时间',150)]:
            tree.heading(key,text=label); tree.column(key,width=width)
        tree.pack(fill='both',expand=True,padx=12,pady=12)
        for row in self.scores.recent():
            score = f'{row["pocketed"]}/{row["balls"]} 球 · {format_duration(row["total_ms"])}'
            tree.insert('', 'end', values=(MODES[row['mode']][0],score,row['shots'],
                        datetime.fromtimestamp(row['created_at']).strftime('%m-%d %H:%M')))
        summary = self.scores.upload_summary()
        note = ('已请求重试。' if self.rank.enabled else '排名已关闭。') + (
            f'待提交 {summary.get("pending",0)} · 已提交 {summary.get("sent",0)} · '
            f'过期/未通过 {summary.get("expired",0)+summary.get("rejected",0)}\n'
            '自由练习和离线成绩仅保存在本机；无联网局次的成绩不会补造上榜。')
        tk.Label(top,text=note,padx=12,pady=8).pack()
        top.bind('<Escape>',lambda e: top.destroy())

    @staticmethod
    def make_rank():
        """Ranking is optional: a missing or invalid server address just disables it."""
        if config.RANK_URL:
            try:
                api = RankApi(config.RANK_URL, config.VERSION)
                return RankController(api, RankSettings(data_directory() / 'rank.json'))
            except (ValueError, OSError):
                _write_log('rank.log', traceback.format_exc())
        return RankController(None, None)

    @staticmethod
    def make_settings():
        """Window layout and position, and the table-frame switch (kept across runs)."""
        try:
            return RankSettings(data_directory() / 'ui.json')
        except OSError:
            return RankSettings(runtime_directory() / 'ui.json')

    def refresh_rank_button(self):
        if not self.rank.available:
            self.panel.set_look('rank', '排名 —', 'off', enabled=False)
            self.panel.set_look('leaderboard', '排行榜 —', 'off', enabled=False)
            return
        on = self.rank.enabled
        self.panel.set_look('rank', '排名 开' if on else '排名 关', 'on' if on else 'off', enabled=True)
        self.panel.set_look('leaderboard', '排行榜', 'soft', enabled=True)

    def refresh_buttons(self):
        self.refresh_rank_button()
        self.panel.set_look('guide', '辅助线 开' if self.guide_enabled else '辅助线 关',
                            'on' if self.guide_enabled else 'off')
        self.panel.set_look('felt', '台面 开' if self.felt_enabled else '台面 关')

    # -- toolbar position --------------------------------------------------
    def place_window(self):
        root = self.root
        root.update_idletasks()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        x, y = self.settings.get('bar_x'), self.settings.get('bar_y')
        if not (isinstance(x, int) and isinstance(y, int)):
            x, y = left + max(0, (right - left - width) // 2), top + 2
        else:
            monitor = win32api.MonitorFromPoint((x, y), win32con.MONITOR_DEFAULTTONEAREST)
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        root.geometry(f'+{x}+{y}')

    def remember_position(self):
        if self.root.state() == 'normal':
            self.settings.update(bar_x=self.root.winfo_x(), bar_y=self.root.winfo_y())

    def auxiliary_windows(self):
        windows = []
        if self.panel is not None and self.panel.settings_open and self.panel.settings_window.top.winfo_exists():
            windows.append(self.panel.settings_window.top)
        for dialog in (self.result_dialog, self.leaderboard_dialog):
            if dialog is not None and dialog.alive():
                windows.append(dialog.top)
                share = getattr(dialog, 'share_dialog', None)
                if share is not None and share.alive():
                    windows.append(share.top)
        update_dialog = getattr(self, 'update_dialog', None)
        if update_dialog is not None and update_dialog.winfo_exists():
            windows.append(update_dialog)
        history = getattr(self, 'history_window', None)
        if history is not None and history.winfo_exists():
            windows.append(history)
        return windows

    def sync_panel_visibility(self):
        if self.closing or not self.runner or not self.runner.shown.is_set():
            return
        if self.pause_hidden:
            if (not win32gui.IsIconic(self.panel.hwnd)
                    and win32gui.IsWindowVisible(self.panel.hwnd)
                    and win32gui.GetForegroundWindow() == self.panel.hwnd):
                self.toggle_pause_hidden()
            return
        foreground = win32gui.GetForegroundWindow()
        visible = desktop_or_app_foreground(foreground, os.getpid())
        self.background_hidden = not visible
        self.runner.overlay.want_hidden = not visible
        state = self.root.state()
        auxiliary = self.auxiliary_windows()
        eye = self.toolbar_eye
        if not visible and eye is not None and eye.winfo_exists():
            eye.withdraw()
        if visible and self.toolbar_hidden:
            self.show_toolbar_eye()
            return
        if visible:
            if not self.panel_visible or state in ('withdrawn', 'iconic') or not self.root.winfo_viewable():
                self.root.deiconify()
                self.root.lift()
                for window in auxiliary:
                    window.deiconify()
                    window.lift()
            self.panel_visible = True
        elif self.panel_visible or state != 'withdrawn':
            self.remember_position()
            for window in auxiliary:
                window.withdraw()
            self.root.withdraw()
            self.panel_visible = False

    def offer_rank(self):
        """Ask once, on the first run after the feature exists, whether to join the ranking."""
        if self.closing or not self.rank.needs_prompt:
            return
        if self.pause_hidden or self.background_hidden:
            self.root.after(250, self.offer_rank)
            return
        self.rank.set_enabled(messagebox.askyesno('全球排名', RANK_INTRO, parent=self.root))
        self.refresh_rank_button()

    def toggle_rank(self):
        if not self.rank.available:
            return
        if self.rank.enabled:
            self.rank.set_enabled(False)
        elif messagebox.askokcancel('全球排名', RANK_CONSENT, parent=self.root):
            self.rank.set_enabled(True)
        self.refresh_rank_button()

    def open_website(self):
        """Open the release page manually; the game never polls it in the background."""
        if not config.WEBSITE_URL:
            return
        webbrowser.open(config.WEBSITE_URL)

    def check_update(self):
        """Only the settings button starts a request; no launch-time check."""
        if self.closing:
            return
        if self.update_dialog is not None and self.update_dialog.winfo_exists():
            self.update_dialog.lift()
            return
        if self.update_checker.check(config.WEBSITE_URL, config.VERSION):
            self.panel.settings_window.set_update_status('正在连接后台…', busy=True)

    def poll_update(self):
        if self.closing:
            return
        result = self.update_checker.take_result()
        if result is None:
            return
        settings = self.panel.settings_window
        if result.error:
            settings.set_update_status(result.error, error=True)
            _write_log('update.log', result.details)
        elif result.info is None:
            settings.set_update_status(f'已是最新版本 v{config.VERSION}')
        else:
            info = result.info
            settings.set_update_status(f'发现新版本 v{info["version"]}')
            # A request finishing during F1 pause must not reveal the game.
            if self.pause_hidden or not self.panel_visible or not self.panel.settings_open:
                return
            self.update_dialog = show_update_dialog(
                self.root, config.VERSION, info, release_entries(info),
                lambda dialog: self.open_update_download(dialog, info['download_url']))

    def open_update_download(self, dialog, url):
        try:
            if not webbrowser.open(url):
                raise OSError('No browser accepted the download URL')
        except Exception:
            _write_log('update.log', traceback.format_exc())
            self.panel.settings_window.set_update_status('打开下载链接失败，请检查默认浏览器后重试', error=True)
            return
        dialog.destroy()

    # -- game lifecycle ----------------------------------------------------
    def start(self):
        if self.closing:
            return
        diag.start_session(config.VERSION)
        try:
            desktop = ShellDesktop()
            # Recover a journal left behind by an earlier crash before starting.
            self.session = LayoutSession(desktop, self.journal)
            self.session.restore()
            self.session = LayoutSession(desktop, self.journal)
            items = self.session.begin(overlay_only=True)
            for item in items:
                item['name'] = desktop.folder.GetDisplayNameOf(
                    [desktop.cache[item['id']]], desktop.shellcon.SHGDN_NORMAL)
            with dpi_context(-4):
                overlay = BilliardsOverlay(desktop, items, mode=self.mode,
                                           volume=0. if self.muted else self.volume, cue_skin=self.cue_skin)
                overlay.guide_enabled = self.guide_enabled
                overlay.want_felt = self.felt_enabled
            self.pulse = HealthPulse(self.session.snapshot['session_id'], create=True)
            self.hotkeys.pulse = self.pulse
            self.watchdog = self.spawn_watchdog()
            self.runner = BilliardsRunner(overlay, self.pulse)
            self.runner.start()
            self.deadline = time.monotonic() + 30
            self.poll()
        except Exception as exc:
            self.fail(exc)

    def spawn_watchdog(self):
        frozen = bool(getattr(sys, 'frozen', False))
        command = [sys.executable]
        if not frozen:
            command.append(str(config.BASE / 'app.pyw'))
        command.extend(['--watchdog', str(self.journal), self.session.snapshot['session_id'], str(os.getpid())])
        environment = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
        return subprocess.Popen(command, env=environment,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                                cwd=str(runtime_directory()))

    def poll(self):
        if self.closing or self.runner is None:
            return
        try:
            while True:
                try:
                    command = self.tray.pending.get_nowait()
                except queue.Empty:
                    break
                if command == 'quit':
                    self.close()
                    return
                if command in ('pause', 'settings'):
                    if command == 'pause' or self.pause_hidden:
                        self.toggle_pause_hidden()
                    if command == 'settings':
                        self.show_toolbar()
                        self.panel.show_settings()
            while True:
                try:
                    identifier = self.hotkeys.pending.get_nowait()
                except queue.Empty:
                    break
                if identifier == config.PAUSE_HOTKEY_ID:
                    self.toggle_pause_hidden()
                elif identifier == config.HOTKEY_ID:
                    self.close()
                    return
            self.poll_update()
            if self.runner.error:
                raise self.runner.error
            if not self.runner.shown.is_set():
                if time.monotonic() > self.deadline:
                    raise RuntimeError('等待桌面恢复守护就绪超时。')
                ready = self.journal.with_name(self.journal.stem + '.watchdog.json')
                if ready.exists():
                    data = read_json(ready)
                    if data.get('session_id') == self.session.snapshot['session_id'] and self.runner.prepared.is_set():
                        if not data.get('ready'):
                            raise RuntimeError('无法启动桌面恢复守护。')
                        self.runner.request_show(self.session.snapshot, self.journal)
                        self.pulse.arm()
            else:
                self.sync_panel_visibility()
                if not self.rank_offered and self.runner.overlay.table.state == 'finished':
                    self.rank_offered = True
                    self.root.after(800, self.offer_rank)
                overlay = self.runner.overlay
                self.update_toolbar()
                table = overlay.table
                if table.first_shot_at is None and not self.settings.get('tutorial_done',False):
                    self.panel.hotkey_hint.configure(text='按住蓄力 · 松开击球')
                elif table.first_shot_at is not None and not self.settings.get('tutorial_done',False):
                    self.settings.update(tutorial_done=True)
                    self.update_preferences()
                self.rank.retry_pending()
                state = table.state
                minutes, seconds = divmod(table.round_seconds, 60)
                if table.mode == 'sprint':
                    remaining = max(0, 90 - table.round_seconds)
                    self.time_value.set(f'{remaining//60:02d}:{remaining%60:02d}')
                else:
                    self.time_value.set(f'{minutes:02d}:{seconds:02d}')
                total = len(table.icons)
                pocketed = table.pocketed_icons
                finished = state == 'finished'
                self.time_title.set('剩余时间' if table.mode == 'sprint' else '总用时' if finished else MODES[self.mode][0])
                self.count_title.set('挑战进球' if table.mode == 'sprint' else '图标总数' if finished else '图标入袋')
                self.count_value.set(str(total) if finished and table.mode != 'sprint' else f'{pocketed}/{total}')
                self.remaining_value.set(str(total - pocketed))
                key = f'{self.session_key}-{id(overlay)}-{table.round_serial}'
                if finished and self.local_recorded != key:
                    self.scores.record(key, table)
                    self.local_recorded = key
                try:
                    self.rank.observe(table)
                    if not self.pause_hidden and not self.background_hidden:
                        self.update_result_dialog(overlay, table, finished)
                except Exception:
                    # Ranking and dialogs must never interrupt a game or the desktop restore.
                    _write_log('rank.log', traceback.format_exc())
            self.poll_id = self.root.after(50, self.poll)
        except Exception as exc:
            self.fail(exc)

    def restart(self):
        if self.runner and self.runner.shown.is_set():
            if self.runner.overlay.table.first_shot_at is not None and self.runner.overlay.table.state != 'finished':
                if not messagebox.askyesno('重新开始', '当前成绩尚未完成，确定重开？', parent=self.root):
                    return
            self.runner.overlay.request_restart()

    def toggle_guide(self):
        self.guide_enabled = not self.guide_enabled
        self.settings.update(guide=self.guide_enabled)
        self.refresh_buttons()
        if self.runner:
            self.runner.overlay.guide_enabled = self.guide_enabled

    def toggle_felt(self):
        """Toggle full felt mode; off mode becomes a discoverable mini toolbar."""
        self.felt_enabled = not self.felt_enabled
        self.stealth_announced = False
        self.settings.update(felt=self.felt_enabled)
        self.refresh_buttons()
        if self.runner:
            self.runner.overlay.want_felt = self.felt_enabled

    def reveal_toolbar(self, event=None):
        button = win32con.VK_RBUTTON if win32api.GetSystemMetrics(23) else win32con.VK_LBUTTON
        if not win32api.GetAsyncKeyState(button) & 0x8000:
            self.show_toolbar()

    def show_toolbar_eye(self):
        if self.toolbar_eye is None:
            eye = self.toolbar_eye = tk.Toplevel(self.root)
            eye.withdraw()
            eye.overrideredirect(True)
            eye.attributes('-topmost', False)
            eye.attributes('-transparentcolor', '#010203')
            eye.attributes('-alpha', .78)
            canvas = tk.Canvas(eye, width=34, height=26, bg='#010203', highlightthickness=0, bd=0)
            canvas.pack()
            shape = (4, 13, 10, 6, 17, 4, 24, 6, 30, 13, 24, 20, 17, 22, 10, 20)
            canvas.create_polygon(*shape, smooth=True, fill='#15362c', outline='#dff8ec', width=2)
            canvas.create_oval(13, 9, 21, 17, fill='#edc276', outline='')
            canvas.bind('<Enter>', self.reveal_toolbar)
        if not self.toolbar_region:
            return
        left, top, _, bottom = self.toolbar_region
        self.toolbar_eye.geometry(f'34x26+{left}+{top + max(0, (bottom - top - 26) // 2)}')
        self.toolbar_eye.deiconify()

    def show_toolbar(self):
        eye = getattr(self, 'toolbar_eye', None)
        if eye is not None and eye.winfo_exists():
            eye.withdraw()
        if getattr(self, 'toolbar_hidden', False):
            self.root.deiconify()
            self.root.lift()
            self.panel_visible = True
            self.toolbar_hidden = False
        self.toolbar_hover_since = self.toolbar_leave_since = None

    def update_toolbar(self):
        """Auto-collapse the toolbar when the felt is hidden, leaving a hover eye."""
        # F1 owns the hidden state.  Do not let the 50 ms UI poll deiconify the
        # toolbar while the game is intentionally paused in the taskbar.
        if self.pause_hidden or self.background_hidden:
            if self.toolbar_eye is not None and self.toolbar_eye.winfo_exists():
                self.toolbar_eye.withdraw()
            return
        if self.felt_enabled:
            self.show_toolbar()
            return
        if not self.stealth_announced:
            self.stealth_announced = True
            self.show_toolbar()
            messagebox.showinfo('隐藏台面模式',
                                '按住 Shift：临时显示半透明球杆和辅助线\n'
                                '鼠标移到小眼睛上：恢复迷你工具条\n'
                                f'{self.settings.get("exit_key", "Ctrl+Alt+Shift+X")}：退出并恢复桌面', parent=self.root)
            self.toolbar_leave_since = time.monotonic()
        update_dialog = getattr(self, 'update_dialog', None)
        if (update_dialog is not None and update_dialog.winfo_exists()) or self.root.grab_current() is not None or any(
                dialog is not None and dialog.alive()
                for dialog in (self.result_dialog, self.leaderboard_dialog)):
            self.show_toolbar()
            return
        now = time.monotonic()
        if not self.toolbar_hidden:
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            self.toolbar_region = (x, y, x + self.root.winfo_width(), y + self.root.winfo_height())
        if self.toolbar_region is None:
            return
        x, y = self.root.winfo_pointerxy()
        left, top, right, bottom = self.toolbar_region
        inside = left <= x < right and top <= y < bottom
        settings_bounds = self.panel.settings_window.bounds()
        if settings_bounds is not None:
            sx0, sy0, sx1, sy1 = settings_bounds
            inside = inside or (sx0 <= x < sx1 and sy0 <= y < sy1)
        button = win32con.VK_RBUTTON if win32api.GetSystemMetrics(23) else win32con.VK_LBUTTON
        if win32api.GetAsyncKeyState(button) & 0x8000:
            self.toolbar_hover_since = self.toolbar_leave_since = None
            return
        if inside:
            self.toolbar_leave_since = None
            if self.toolbar_hidden:
                if self.toolbar_hover_since is None:
                    self.toolbar_hover_since = now
                elif now - self.toolbar_hover_since >= .3:
                    self.show_toolbar()
        else:
            self.toolbar_hover_since = None
            if not self.toolbar_hidden:
                if self.toolbar_leave_since is None:
                    self.toolbar_leave_since = now
                elif now - self.toolbar_leave_since >= 1:
                    self.remember_position()
                    self.toolbar_region = (left, top, right, bottom)
                    self.panel.hide_settings()
                    self.root.withdraw()
                    self.toolbar_hidden = True
                    self.show_toolbar_eye()

    def toggle_pause_hidden(self):
        """Pause the overlay and minimize the borderless toolbar to the taskbar, or restore it."""
        if not self.runner or not self.runner.shown.is_set():
            return
        self.pause_hidden = not self.pause_hidden
        self.background_hidden = False
        self.runner.overlay.want_hidden = self.pause_hidden
        if self.pause_hidden:
            if self.toolbar_eye is not None:
                self.toolbar_eye.withdraw()
            self.panel.hide_settings()
            self.remember_position()
            for window in self.auxiliary_windows():
                window.withdraw()
            win32gui.ShowWindow(self.panel.hwnd, win32con.SW_MINIMIZE)
            self.panel_visible = False
            return
        # A taskbar restore must also clear the eye-collapse state.  Otherwise
        # the root can be restored for one frame and disappear again on the
        # next toolbar poll.
        self.show_toolbar()
        win32gui.ShowWindow(self.panel.hwnd, win32con.SW_RESTORE)
        self.root.deiconify()
        self.root.lift()
        for window in self.auxiliary_windows():
            window.deiconify()
            window.lift()
        self.panel_visible = True
        self.panel.hotkey_hint.configure(text=f'{self.settings.get("pause_key", "F1")} 暂停 / 恢复')

    # -- round result ------------------------------------------------------
    def update_result_dialog(self, overlay, table, finished):
        """Open the result window once per cleared round and keep its numbers current."""
        if finished and self.result_serial != table.round_serial:
            self.result_serial = table.round_serial
            self.result_dialog = ResultDialog(self.root, {'again': self.play_again,
                                                          'enable_rank': self.enable_rank_from_dialog,
                                                          'leaderboard': self.show_leaderboard})
        dialog = self.result_dialog
        if dialog is None:
            return
        if not dialog.alive():
            self.result_dialog = None
        elif not finished or table.round_serial != self.result_serial:
            dialog.close()  # a new round has begun
            self.result_dialog = None
        else:
            count = len(table.icons)
            dialog.update({
                'time': format_duration(table.play_ms),
                'average': f'{average_seconds(table.play_ms, count):.2f}',
                'balls': str(count),
                'shots': str(table.shots),
                'note': f'{MODES[self.mode][0]} · {table.pocketed_icons}/{len(table.icons)} 球 · {table.scratches} 次白球落袋',
                'rank': self.rank.view(table),
                'mode': table.mode,
                'pocketed': table.pocketed_icons,
            })

    def play_again(self):
        if self.result_dialog is not None:
            self.result_dialog.close()
            self.result_dialog = None
        self.restart()

    def show_leaderboard(self):
        if self.leaderboard_dialog is not None and self.leaderboard_dialog.alive():
            self.leaderboard_dialog.top.lift()
            self.leaderboard_dialog.top.focus_set()
            return
        self.leaderboard_dialog = LeaderboardDialog(self.root, self.rank.fetch_rankings)

    def enable_rank_from_dialog(self):
        if self.rank.available and not self.rank.enabled:
            self.toggle_rank()

    def fail(self, error):
        self.show_toolbar()
        try:
            self.restore()
        except Exception as exc:
            messagebox.showerror(config.PRODUCT_NAME, f'{describe_error(error)}\n\n{exc}', parent=self.root)
            return
        _write_log('error.log', traceback.format_exc())
        messagebox.showerror(config.PRODUCT_NAME, describe_error(error), parent=self.root)
        self.close()

    def restore(self):
        if self.poll_id:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if self.runner:
            self.runner.stop()
            self.runner = None
        if self.session:
            self.session.restore()
            self.session = None
        if self.pulse:
            self.hotkeys.pulse = None
            self.pulse.close()
            self.pulse = None

    def close(self):
        if self.closing:
            return
        # Tk message boxes dispatch queued timers. Mark closing before stopping
        # the runner so a pending poll cannot dereference the cleared runner.
        self.closing = True
        if self.pulse:
            self.pulse.beat(CLOSING)
        try:
            self.restore()
        except Exception as exc:
            _write_log('error.log', traceback.format_exc())
            messagebox.showwarning(config.PRODUCT_NAME, f'桌面恢复未完成：{exc}\n\n请重试退出。',
                                   parent=self.root)
            self.closing = False
            return
        self.rank.close()
        self.remember_position()
        self.hotkeys.close()
        self.tray.close()
        if self.panel is not None:
            self.panel.destroy_settings()
        eye = getattr(self, 'toolbar_eye', None)
        if eye is not None:
            try:
                if eye.winfo_exists():
                    eye.destroy()
            finally:
                self.toolbar_eye = None
        ctypes.windll.kernel32.CloseHandle(self.instance_handle)
        self.root.destroy()


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    app = None
    desktop_guard = None
    try:
        handle = acquire_instance(config.VERSION)
        desktop_guard = claim_instance_mutex(config.DESKTOP_GUARD)
        if desktop_guard is None:
            ctypes.windll.kernel32.CloseHandle(handle)
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(config.PRODUCT_NAME,
                                '检测到「桌面蹦迪」正在运行。\n请先退出它，再打开桌面图标台球。',
                                parent=root)
            root.destroy()
            return
        root = tk.Tk()
        app = DesktopBilliardsApp(root, handle)
        root.mainloop()
    except SystemExit:
        raise
    except Exception as error:
        _write_log('error.log', traceback.format_exc())
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        else:
            try:
                messagebox.showerror(config.PRODUCT_NAME, f'启动失败：{describe_error(error)}')
            except Exception:
                pass
        raise
    finally:
        if desktop_guard:
            ctypes.windll.kernel32.CloseHandle(desktop_guard)

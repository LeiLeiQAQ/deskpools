"""Windows made of plain Tk widgets: the status strip, settings popover and result dialogs.

Only widgets live here. The game logic stays in :mod:`dib.ui`, which fills the string
variables and supplies the callbacks, so every window can also be previewed on its own.

The main panel stays compact; secondary actions live in its floating settings popover.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

import win32api
import win32con
import win32gui

from PIL import Image, ImageTk

from . import card
from .modes import MODES, CUE_SKINS

BG = '#071512'
CARD = '#10241f'
TEXT = '#eefaf4'
MUTED = '#86a99a'
ACCENT = '#69ddb3'
WARN = '#efad6f'
GOLD = '#edc276'
EDGE = '#25473b'
FONT = 'Microsoft YaHei UI'
NUMBERS = 'Segoe UI Semibold'

# kind -> (background, hover, foreground)
KINDS = {
    'primary': (GOLD, '#f5d99a', '#14231e'),
    'soft': ('#17362d', '#235143', TEXT),
    'on': ('#1b5947', '#28775d', '#c8ffe9'),
    'off': ('#1a2823', '#263a31', '#668476'),
    'ghost': (BG, '#123128', MUTED),
}


def expose_taskbar_button(root):
    """Keep the borderless toolbar represented in the taskbar when it is minimized."""
    root.update_idletasks()
    hwnd = win32gui.GetAncestor(root.winfo_id(), win32con.GA_ROOT)
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    style = (style | win32con.WS_EX_APPWINDOW) & ~win32con.WS_EX_TOOLWINDOW
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
    flags = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER |
             win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)
    return hwnd


def format_duration(milliseconds):
    """``MM:SS.cc`` (``H:MM:SS.cc`` from one hour) for a round time in whole milliseconds."""
    centiseconds = max(0, int(milliseconds)) // 10
    seconds, centiseconds = divmod(centiseconds, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    clock = f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
    return f'{clock}.{centiseconds:02d}'


class Pill(tk.Label):
    """A flat button with hover feedback; ``set`` changes its text, look and availability."""

    def __init__(self, parent, text, command, kind='soft', *, bold=False, padx=12, pady=6):
        super().__init__(parent, text=text, font=(FONT, 9, 'bold' if bold else 'normal'), cursor='hand2',
                         padx=padx, pady=pady, bd=0)
        self.command = command
        self.kind = kind
        self.enabled = True
        self.hover = False
        self.bind('<Enter>', lambda event: self._hover(True))
        self.bind('<Leave>', lambda event: self._hover(False))
        self.bind('<ButtonRelease-1>', self._release)
        self._paint()

    def set(self, text=None, kind=None, enabled=None):
        if text is not None:
            self.configure(text=text)
        if kind is not None:
            self.kind = kind
        if enabled is not None:
            self.enabled = enabled
        self._paint()

    def _hover(self, value):
        self.hover = value
        self._paint()

    def _release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if self.enabled and inside and self.command:
            self.command()

    def _paint(self):
        background, hover, foreground = KINDS[self.kind]
        if not self.enabled:
            self.configure(bg=KINDS['off'][0], fg='#5d726c', cursor='arrow')
            return
        self.configure(bg=hover if self.hover else background, fg=foreground, cursor='hand2',
                       highlightthickness=1, highlightbackground=EDGE,
                       highlightcolor=GOLD)


def configure_styles(root):
    """ttk styles for the dialogs (the main window uses plain Tk widgets)."""
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('Panel.TFrame', background=BG)
    style.configure('Table.TLabel', background=BG, foreground=TEXT, font=(FONT, 9))
    style.configure('Table.TButton', padding=(11, 6), font=(FONT, 9), background=CARD, foreground=TEXT,
                    borderwidth=0, focusthickness=1, focuscolor=ACCENT)
    style.map('Table.TButton', background=[('active', '#1d3531'), ('pressed', '#24423c')])
    style.configure('Dark.Vertical.TScrollbar', background='#2b5b4a', troughcolor=CARD, bordercolor=CARD,
                    lightcolor='#2b4a44', darkcolor='#2b4a44', arrowcolor=MUTED, relief='flat', gripcount=0)
    style.map('Dark.Vertical.TScrollbar', background=[('active', '#4c8b70'), ('pressed', '#4c8b70')])


class SettingsPopover:
    """A compact floating settings window opened from the toolbar gear."""

    DEFAULT_LOOK = {
        'guide': {'text': '辅助线', 'kind': 'on'},
        'rank': {'text': '全球排名', 'kind': 'off'},
        'felt': {'text': '显示台面', 'kind': 'ghost'},
        'website': {'text': '打开官网', 'kind': 'ghost'},
    }

    def __init__(self, root, version, callbacks):
        self.root = root
        self.opened = False
        self.pills = {}
        self.actions = {}
        self.look = {key: dict(value) for key, value in self.DEFAULT_LOOK.items()}
        top = self.top = tk.Toplevel(root)
        top.withdraw()
        top.overrideredirect(True)
        top.transient(root)
        top.configure(bg=BG)
        top.resizable(False, False)
        try:
            top.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        except tk.TclError:
            pass

        shell = tk.Frame(top, bg=BG, padx=10, pady=9,
                         highlightthickness=1, highlightbackground=EDGE)
        shell.pack(fill='both', expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill='x')
        tk.Label(header, text='设置', font=(FONT, 11, 'bold'), fg=TEXT, bg=BG).pack(side='left')
        close = tk.Label(header, text='×', font=('Segoe UI', 14), fg=MUTED, bg=BG, cursor='hand2', padx=4)
        close.pack(side='right')
        close.bind('<Enter>', lambda event: close.configure(fg=GOLD))
        close.bind('<Leave>', lambda event: close.configure(fg=MUTED))
        close.bind('<ButtonRelease-1>', lambda event: self.hide())
        header.bind('<ButtonPress-1>', self._drag_start)
        header.bind('<B1-Motion>', self._drag_move)
        shell.bind('<ButtonPress-1>', self._drag_start)
        shell.bind('<B1-Motion>', self._drag_move)
        tk.Label(shell, text='桌面图标台球', font=(FONT, 8), fg=MUTED, bg=BG).pack(anchor='w', pady=(0, 7))

        grid = tk.Frame(shell, bg=BG)
        grid.pack(fill='x')
        for column in range(2):
            grid.columnconfigure(column, weight=1, uniform='settings')
        for index, (key, text, command, kind) in enumerate((
                ('guide', '辅助线', callbacks['toggle_guide'], 'on'),
                ('rank', '全球排名', callbacks['toggle_rank'], 'off'),
                ('felt', '显示台面', callbacks['toggle_felt'], 'ghost'),
                ('website', '打开官网', callbacks['open_website'], 'ghost'))):
            look = self.look.get(key, {})
            pill = Pill(grid, look.get('text', text), command, look.get('kind', kind), padx=5, pady=5)
            pill.set(enabled=look.get('enabled', True))
            pill.grid(row=index // 2, column=index % 2, sticky='ew', padx=(0 if index % 2 == 0 else 5, 0),
                      pady=(0, 5))
            self.pills[key] = pill

        options = tk.Frame(shell, bg=BG)
        options.pack(fill='x', pady=(4, 5))
        tk.Label(options, text='玩法', font=(FONT, 8), fg=MUTED, bg=BG).grid(row=0, column=0, sticky='w')
        self.mode_var = tk.StringVar(top)
        self.mode_box = ttk.Combobox(options, textvariable=self.mode_var, state='readonly',
                                    values=[item[0] for item in MODES.values()], width=18)
        self.mode_box.grid(row=0, column=1, sticky='ew', padx=(8,0))
        self.mode_box.bind('<<ComboboxSelected>>', lambda e: callbacks['set_mode'](
            list(MODES)[self.mode_box.current()]))
        tk.Label(options, text='球杆', font=(FONT, 8), fg=MUTED, bg=BG).grid(row=1, column=0, sticky='w', pady=5)
        self.cue_var = tk.StringVar(top)
        self.cue_box = ttk.Combobox(options, textvariable=self.cue_var, state='readonly',
                                   values=list(CUE_SKINS.values()), width=18)
        self.cue_box.grid(row=1, column=1, sticky='ew', padx=(8,0))
        self.cue_box.bind('<<ComboboxSelected>>', lambda e: callbacks['set_cue'](
            list(CUE_SKINS)[self.cue_box.current()]))
        self.volume_var = tk.DoubleVar(top, value=45)
        tk.Label(options, text='音量', font=(FONT, 8), fg=MUTED, bg=BG).grid(row=2, column=0, sticky='w')
        volume = ttk.Scale(options, from_=0, to=100, variable=self.volume_var,
                           command=lambda value: callbacks['set_volume'](float(value)/100))
        volume.grid(row=2, column=1, sticky='ew', padx=(8,0))
        self.mute_button = Pill(options, '静音', callbacks['toggle_mute'], 'soft', padx=6, pady=3)
        self.mute_button.grid(row=3, column=0, pady=4)
        Pill(options, '本机成绩 / 重试提交', callbacks['history'], 'ghost', padx=5, pady=3).grid(row=3, column=1)
        options.columnconfigure(1, weight=1)
        self.rule_hint = tk.StringVar(top)
        tk.Label(shell, textvariable=self.rule_hint, font=(FONT,8), fg=MUTED, bg=BG,
                 wraplength=275, justify='left').pack(fill='x', pady=(0,7))

        separator = tk.Frame(shell, bg=EDGE, height=1)
        separator.pack(fill='x', pady=(2, 7))
        release = tk.Frame(shell, bg=BG)
        release.pack(fill='x', pady=(0, 3))
        self.version_label = tk.Label(release, text=f'当前版本 v{version}', font=(FONT, 8), fg=MUTED, bg=BG)
        self.version_label.pack(side='left')
        self.update_button = Pill(release, '检查更新', callbacks['check_update'], 'soft', padx=8, pady=4)
        self.update_button.pack(side='right', padx=(12, 0))
        self.update_status = tk.StringVar(top, value='手动检查后台发布的版本')
        self.update_status_label = tk.Label(shell, textvariable=self.update_status, font=(FONT, 8),
                                            fg=MUTED, bg=BG, anchor='w', justify='left', wraplength=265)
        self.update_status_label.pack(fill='x', pady=(0, 8))
        shortcuts = tk.Frame(shell, bg='#0d211c', padx=7, pady=6,
                             highlightthickness=1, highlightbackground=EDGE)
        shortcuts.pack(fill='x')
        tk.Label(shortcuts, text='快捷键', font=(FONT, 8, 'bold'), fg=GOLD, bg='#0d211c').pack(anchor='w')
        self.shortcut_hint = tk.StringVar(top)
        tk.Label(shortcuts, textvariable=self.shortcut_hint,
                 font=(FONT, 8), fg='#c4d9d2', bg='#0d211c', justify='left', wraplength=270).pack(anchor='w', pady=(3,0))
        keys = tk.Frame(shortcuts, bg='#0d211c')
        keys.pack(fill='x', pady=(5,0))
        self.pause_key = tk.StringVar(top, value='F1')
        self.exit_key = tk.StringVar(top, value='Ctrl+Alt+Shift+X')
        for index,(label,var) in enumerate((('暂停',self.pause_key),('退出',self.exit_key))):
            tk.Label(keys,text=label,font=(FONT,8),fg=MUTED,bg='#0d211c').grid(row=index,column=0,sticky='w')
            ttk.Entry(keys,textvariable=var,width=21).grid(row=index,column=1,pady=2,padx=5)
        Pill(shortcuts, '应用快捷键', lambda: callbacks['set_hotkeys'](self.pause_key.get(),self.exit_key.get()),
             'soft', padx=6,pady=3).pack(anchor='e',pady=(4,0))
        self.shortcut_status = tk.StringVar(top)
        tk.Label(shortcuts,textvariable=self.shortcut_status,font=(FONT,8),fg=WARN,bg='#0d211c',
                 wraplength=270,justify='left').pack(fill='x')

        actions = tk.Frame(shell, bg=BG)
        actions.pack(fill='x', pady=(8, 0))
        restart = Pill(actions, '重新开始', callbacks['restart'], 'primary', bold=True, padx=8, pady=5)
        restart.pack(side='left', fill='x', expand=True)
        self.actions['restart'] = restart
        quit_button = Pill(actions, '退出', callbacks['quit'], 'soft', padx=8, pady=5)
        quit_button.pack(side='left', padx=(6, 0))
        self.actions['quit'] = quit_button
        top.bind('<Escape>', lambda event: self.hide())
        top.protocol('WM_DELETE_WINDOW', self.hide)
        top.update_idletasks()

    def set_update_status(self, text, *, busy=False, error=False):
        self.update_status.set(text)
        self.update_status_label.configure(fg=WARN if error else MUTED if busy else ACCENT)
        self.update_button.set(text='检查中…' if busy else '检查更新', enabled=not busy)
        if self.opened:
            self.top.update_idletasks()
            self.reposition()

    def show(self):
        if not self.top.winfo_exists():
            return
        self.opened = True
        self.top.update_idletasks()
        self.reposition()
        self.top.deiconify()
        self.top.lift()

    def reposition(self):
        if not self.top.winfo_exists():
            return
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        width, height = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        monitor = win32api.MonitorFromPoint((root_x, root_y), win32con.MONITOR_DEFAULTTONEAREST)
        left, upper, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        x = max(left, min(root_x + self.root.winfo_width() - width, right - width))
        y = root_y + self.root.winfo_height() + 7
        if y + height > bottom:
            y = max(upper, root_y - height - 7)
        self.top.geometry(f'+{x}+{y}')

    def _drag_start(self, event):
        self.drag_offset = (event.x_root - self.top.winfo_x(), event.y_root - self.top.winfo_y())

    def _drag_move(self, event):
        x, y = self.drag_offset
        self.top.geometry(f'+{event.x_root - x}+{event.y_root - y}')

    def hide(self):
        self.opened = False
        if self.top.winfo_exists():
            self.top.withdraw()

    def withdraw(self):
        """Temporarily hide while another application is foreground."""
        if self.top.winfo_exists():
            self.top.withdraw()

    def bounds(self):
        if not self.opened or not self.top.winfo_exists() or not self.top.winfo_viewable():
            return None
        x, y = self.top.winfo_rootx(), self.top.winfo_rooty()
        return x, y, x + self.top.winfo_width(), y + self.top.winfo_height()

    def destroy(self):
        self.opened = False
        if self.top.winfo_exists():
            self.top.destroy()

    def set_look(self, key, text=None, kind=None, enabled=None):
        look = self.look.setdefault(key, {})
        for name, value in (('text', text), ('kind', kind), ('enabled', enabled)):
            if value is not None:
                look[name] = value
        pill = self.pills.get(key)
        if pill is not None and pill.winfo_exists():
            pill.set(text, kind, enabled)


class MainPanel:
    """A slim single-row desktop toolbar with a separate settings popover."""

    DEFAULT_LOOK = {
        'guide': {'text': '辅助线 开', 'kind': 'on'},
        'rank': {'text': '排名', 'kind': 'off'},
        'felt': {'text': '台面 开', 'kind': 'ghost'},
        'website': {'text': '打开官网', 'kind': 'ghost'},
        'leaderboard': {'text': '排行榜', 'kind': 'soft'},
    }

    def __init__(self, root, version, callbacks):
        configure_styles(root)
        root.configure(bg=BG)
        root.overrideredirect(True)
        self.root = root
        self.pills = {}
        self.look = {key: dict(value) for key, value in self.DEFAULT_LOOK.items()}
        self.time_title = tk.StringVar(root, value='本局用时')
        self.time_value = tk.StringVar(root, value='00:00')
        self.count_title = tk.StringVar(root, value='图标入袋')
        self.count_value = tk.StringVar(root, value='—')
        self.remaining_title = tk.StringVar(root, value='剩余')
        self.remaining_value = tk.StringVar(root, value='—')
        self.shell = tk.Frame(root, bg=BG, padx=8, pady=5,
                              highlightthickness=1, highlightbackground=EDGE)
        self.shell.pack(fill='both', expand=True)
        self.header = tk.Frame(self.shell, bg=BG)
        self.header.pack(side='left')
        self._draggable(self.header)
        with Image.open(card.LOGO) as source:
            logo = source.convert('RGBA').resize((26, 26), Image.Resampling.LANCZOS)
        self.logo_photo = ImageTk.PhotoImage(logo, master=root)
        brand = tk.Label(self.header, image=self.logo_photo, bg=BG, bd=0)
        brand.pack(side='left', padx=(0, 6))
        self._draggable(brand)
        brand_title = tk.Label(self.header, text='图标台球', font=(FONT, 9, 'bold'),
                               fg=TEXT, bg=BG)
        brand_title.pack(side='left', padx=(0, 12))
        self._draggable(brand_title)
        tk.Frame(self.shell, bg=EDGE, width=1, height=24).pack(side='left', padx=(0, 10))
        self._draggable(self.shell)
        self.stats = tk.Frame(self.shell, bg=BG)
        self.stats.pack(side='left', padx=(0, 8))
        self.stat_boxes = []
        for index, (title, value) in enumerate(((self.time_title, self.time_value),
                                               (self.count_title, self.count_value),
                                               (self.remaining_title, self.remaining_value))):
            box = tk.Frame(self.stats, bg=BG, padx=5, pady=2)
            box.grid(row=0, column=index, sticky='nsew', padx=(0, 4))
            self.stat_boxes.append(box)
            self._draggable(box)
            for variable, color, font in ((title, MUTED, (FONT, 8)), (value, TEXT, (NUMBERS, 11))):
                label = tk.Label(box, textvariable=variable, font=font, fg=color, bg=BG, anchor='w',
                                 width=(10 if index == 0 else 4) if variable is value else 0)
                label.pack(side='left', padx=(0, 5))
                self._draggable(label)
        self.actions = tk.Frame(self.shell, bg=BG)
        self.actions.pack(side='right')
        self.hotkey_hint = tk.Label(self.actions, text='F1 暂停 / 恢复', font=(FONT, 8),
                                    fg=GOLD, bg='#0d211c', padx=7, pady=4)
        self.hotkey_hint.pack(side='left', padx=(0, 8))
        self._draggable(self.hotkey_hint)
        leaderboard = Pill(self.actions, '排行榜', callbacks['leaderboard'], 'soft', padx=9, pady=4)
        leaderboard.pack(side='left', padx=(0, 5))
        self.pills['leaderboard'] = leaderboard
        self.settings_window = SettingsPopover(root, version, callbacks)
        settings = Pill(self.actions, '设置', self.toggle_settings, 'ghost', padx=9, pady=4)
        settings.pack(side='left')
        self.pills['settings'] = settings
        root.update_idletasks()
        self.hwnd = expose_taskbar_button(root)

    def _draggable(self, widget):
        widget.bind('<ButtonPress-1>', self._drag_start)
        widget.bind('<B1-Motion>', self._drag_move)

    def _drag_start(self, event):
        self.drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        x, y = self.drag_offset
        self.root.geometry(f'+{event.x_root - x}+{event.y_root - y}')
        if self.settings_open:
            self.settings_window.reposition()

    def set_look(self, key, text=None, kind=None, enabled=None):
        look = self.look.setdefault(key, {})
        for name, value in (('text', text), ('kind', kind), ('enabled', enabled)):
            if value is not None:
                look[name] = value
        pill = self.pills.get(key)
        if pill is not None and pill.winfo_exists():
            pill.set(text, kind, enabled)
        self.settings_window.set_look(key, text, kind, enabled)

    def is_enabled(self, key):
        return self.look.get(key, {}).get('enabled', True)

    @property
    def settings_open(self):
        return self.settings_window.opened

    def toggle_settings(self):
        if self.settings_open:
            self.settings_window.hide()
        else:
            self.settings_window.show()

    def show_settings(self):
        self.settings_window.show()

    def hide_settings(self):
        self.settings_window.hide()

    def withdraw_settings(self):
        self.settings_window.withdraw()

    def destroy_settings(self):
        self.settings_window.destroy()


class ResultDialog:
    """The round-end window: a preview of the shareable result card and the buttons to share it.

    :meth:`update` takes a ``view`` dict and may be called every frame; the card is only redrawn
    when the view changes. Keys: ``time``, ``average``, ``balls``, ``shots``, ``note`` and
    ``rank`` (phase ``off`` / ``unavailable`` / ``skipped`` / ``pending`` / ``ok`` / ``error``).
    """

    PREVIEW = (600, 750)

    def __init__(self, root, callbacks, *, image=None, share_text=None):
        self.callbacks = callbacks
        self.image = image
        self.share_text = share_text
        self.photo = None
        self.last = None
        self.folder = None
        self.root = root
        self.top = tk.Toplevel(root)
        top = self.top
        top.title('排行榜分享卡片' if share_text is not None else '清台完成')
        top.configure(bg=BG)
        top.transient(root)
        top.resizable(False, False)
        top.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        shell = tk.Frame(top, bg=BG)
        shell.pack(fill='both', expand=True)

        self.photo = ImageTk.PhotoImage(Image.new('RGB', self.PREVIEW, CARD), master=top)
        self.preview = tk.Label(shell, image=self.photo, bg=BG, bd=0)
        self.preview.pack()

        if callbacks.get('leaderboard'):
            Pill(shell, '查看排行榜  ›', callbacks['leaderboard'], 'ghost', pady=8).pack(fill='x')

        share = tk.Frame(shell, bg=BG, padx=12, pady=12)
        share.pack(fill='x')
        for column, (text, command, kind) in enumerate((
                ('保存图片', self.save, 'soft'), ('复制图片', self.copy, 'soft'),
                ('复制文案' if share_text is not None else '再来一局',
                 self.copy_text if share_text is not None else callbacks['again'], 'primary'))):
            share.columnconfigure(column, weight=1, uniform='actions')
            Pill(share, text, command, kind, bold=kind == 'primary', padx=8, pady=11).grid(
                row=0, column=column, sticky='ew', padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        status = self.status = tk.Frame(shell, bg=BG, height=30)
        status.pack_propagate(False)
        self.feedback_text = tk.StringVar(top)
        self.feedback = tk.Label(status, textvariable=self.feedback_text, font=(FONT, 8), fg=MUTED, bg=BG,
                                 anchor='w', justify='left')
        self.feedback.pack(side='left', fill='x', expand=True)
        self.feedback.bind('<ButtonRelease-1>', lambda event: self.open_folder())

        self.offer = Pill(status, '开启全球排名', callbacks.get('enable_rank'), 'ghost', padx=0, pady=0)
        top.bind('<Escape>', lambda event: self.close())
        top.protocol('WM_DELETE_WINDOW', self.close)
        top.update_idletasks()
        self._fit_screen(root)
        top.lift()

    def _fit_screen(self, root):
        # Use the toolbar's monitor and its work area (excluding the taskbar),
        # with measured native borders and DPI-scaled controls rather than a fixed height.
        monitor = win32api.MonitorFromPoint((root.winfo_rootx(), root.winfo_rooty()),
                                           win32con.MONITOR_DEFAULTTONEAREST)
        left, upper, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        top = self.top
        hwnd = win32gui.GetAncestor(top.winfo_id(), win32con.GA_ROOT)
        x0, y0, x1, y1 = win32gui.GetWindowRect(hwnd)
        border_w = x1 - x0 - top.winfo_width()
        border_h = y1 - y0 - top.winfo_height()
        extra_w = top.winfo_reqwidth() - self.photo.width() + border_w
        extra_h = top.winfo_reqheight() - self.photo.height() + border_h
        scale = min(1, (right - left - extra_w - 32) / self.PREVIEW[0],
                    (bottom - upper - extra_h - 32) / self.PREVIEW[1])
        self.preview_size = (max(1, int(self.PREVIEW[0] * scale)), max(1, int(self.PREVIEW[1] * scale)))
        frame = self.image.resize(self.preview_size, Image.Resampling.LANCZOS) if self.image else Image.new('RGB', self.preview_size, CARD)
        self.photo = ImageTk.PhotoImage(frame, master=top)
        self.preview.configure(image=self.photo)
        top.update_idletasks()
        width, height = top.winfo_reqwidth() + border_w, top.winfo_reqheight() + border_h
        x = left + max(0, (right - left - width) // 2)
        y = upper + max(0, (bottom - upper - height) // 2)
        top.geometry(f'+{x}+{y}')

    def alive(self):
        return self.top is not None and self.top.winfo_exists()

    def close(self):
        if self.alive():
            self.top.destroy()
        self.top = None

    def update(self, view):
        if not self.alive() or view == self.last:
            return
        self.last = view
        self.image = card.render_card(view)
        if view.get('mode') == 'sprint':
            self.top.title('90 秒挑战完成')
        if view['rank']['phase'] == 'off':
            self.offer.pack(side='right')
        else:
            self.offer.pack_forget()
        self._layout_status()

    def _layout_status(self):
        if self.feedback_text.get() or (self.last and self.last['rank']['phase'] == 'off'):
            self.status.pack(fill='x', padx=12, pady=(0, 8))
        else:
            self.status.pack_forget()
        self.top.update_idletasks()
        self._fit_screen(self.root)

    def save(self):
        if self.image is None:
            return
        try:
            path = card.save_card(self.image, kind='排行榜' if self.share_text is not None else '成绩')
        except OSError as error:
            self._say(f'保存失败：{error}', MUTED)
            return
        self.folder = path.parent
        self._say('已保存，点此打开所在文件夹', ACCENT)

    def copy(self):
        if self.image is None:
            return
        try:
            card.copy_card(self.image)
        except Exception:  # the clipboard can be locked by another program
            self._say('复制失败，请改用「保存图片」', WARN)
            return
        self._say('已复制，可直接粘贴到微信、QQ 或帖子里', ACCENT)

    def copy_text(self):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(self.share_text)
        except tk.TclError:
            self._say('文案复制失败，请稍后重试', WARN)
            return
        self._say('文案和官网地址已复制', ACCENT)

    def open_folder(self):
        if self.folder is not None:
            try:
                os.startfile(self.folder)
            except OSError:
                pass

    def _say(self, text, colour):
        self.feedback_text.set(text)
        self._layout_status()
        self.feedback.configure(fg=colour, cursor='hand2' if self.folder is not None and '点此' in text else 'arrow')


def show_update_dialog(root, current, info, entries, on_download):
    """A dark release-notes dialog. ``entries`` is the display list from ``release_entries``;
    ``on_download(dialog)`` opens the download and closes the dialog when it succeeds."""
    dialog = tk.Toplevel(root)
    dialog.title(f'发现新版本 {info["version"]}')
    dialog.configure(bg=BG)
    dialog.transient(root)
    dialog.resizable(False, False)
    shell = tk.Frame(dialog, bg=BG, padx=18, pady=16)
    shell.pack(fill='both', expand=True)

    tk.Label(shell, text='发现新版本', font=(FONT, 12, 'bold'), fg=TEXT, bg=BG).pack(anchor='w')
    versions = tk.Frame(shell, bg=BG)
    versions.pack(fill='x', pady=(4, 10))
    tk.Label(versions, text=f'v{current}', font=(FONT, 9), fg=MUTED, bg=BG).pack(side='left')
    tk.Label(versions, text='  →  ', font=(FONT, 9), fg=MUTED, bg=BG).pack(side='left')
    tk.Label(versions, text=f'v{info["version"]}', font=(NUMBERS, 11), fg=ACCENT, bg=BG).pack(side='left')

    card = tk.Frame(shell, bg=CARD)
    card.pack(fill='both', expand=True)
    notes = tk.Text(card, width=46, height=12, wrap='word', bg=CARD, fg=TEXT, relief='flat', bd=0,
                    highlightthickness=0, padx=14, pady=10, font=(FONT, 9), spacing3=3, cursor='arrow',
                    insertwidth=0, selectbackground='#2b4a44')
    scrollbar = ttk.Scrollbar(card, orient='vertical', command=notes.yview, style='Dark.Vertical.TScrollbar')
    notes.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    notes.pack(side='left', fill='both', expand=True)
    notes.tag_configure('latest', font=(FONT, 10, 'bold'), foreground=ACCENT, spacing1=0)
    notes.tag_configure('older', font=(FONT, 10, 'bold'), foreground=TEXT, spacing1=8)
    notes.tag_configure('body', foreground='#c4d9d2')
    for index, entry in enumerate(entries):
        notes.insert('end', f'{entry["version"]}\n', 'latest' if index == 0 else 'older')
        notes.insert('end', (entry['notes'] or '暂无更新说明') + '\n', 'body')
    notes.configure(state='disabled')

    buttons = tk.Frame(shell, bg=BG)
    buttons.pack(fill='x', pady=(14, 0))
    Pill(buttons, '去更新', lambda: on_download(dialog), 'primary', bold=True, padx=18).pack(side='right')
    Pill(buttons, '稍后', dialog.destroy, 'soft', padx=16).pack(side='right', padx=(0, 8))
    dialog.bind('<Escape>', lambda event: dialog.destroy())

    # Align the dialog's right edge with the main window, just below it, and keep it on screen.
    dialog.update_idletasks()
    x = max(0, root.winfo_x() + root.winfo_width() - dialog.winfo_reqwidth())
    dialog.geometry(f'+{x}+{root.winfo_y() + root.winfo_height() + 10}')
    return dialog

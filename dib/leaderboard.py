"""Scrollable top 50 with a persistent personal best; network work stays off Tk."""
from datetime import datetime
import queue
import threading
import tkinter as tk
from tkinter import ttk
from tkinter.font import Font

import win32api
import win32con
import win32gui

from . import card
from .panel import ACCENT, BG, CARD, FONT, GOLD, MUTED, TEXT, Pill, ResultDialog, configure_styles
from .rank import RankError
from .modes import BOARDS


class LeaderboardDialog:
    def __init__(self, root, load):
        self.load = load
        self.inbox = queue.SimpleQueue()
        self.busy = False
        self.data = None
        self.share_busy = False
        self.share_dialog = None
        self.board = 'standard'
        self.top = top = tk.Toplevel(root)
        top.title('全球排行榜')
        top.configure(bg=BG)
        top.transient(root)
        top.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        top.protocol('WM_DELETE_WINDOW', self.close)
        top.bind('<Escape>', lambda event: self.close())
        shell = tk.Frame(top, bg=BG, padx=26, pady=22)
        shell.pack(fill='both', expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill='x')
        tk.Label(header, text='全球排行榜', font=(FONT, 19, 'bold'), bg=BG, fg=TEXT).pack(side='left')
        tk.Label(header, text='TOP 50', font=('Segoe UI', 10, 'bold'), bg=CARD, fg=ACCENT,
                 padx=12, pady=5).pack(side='right')
        self.share_button = Pill(header, '生成卡片', self.share, 'soft', padx=10, pady=5)
        self.share_button.pack(side='right', padx=(0, 10))
        self.share_button.set(enabled=False)
        switch = tk.Frame(shell,bg=BG)
        switch.pack(fill='x',pady=(8,12))
        self.board_var = tk.StringVar(top,value=BOARDS[self.board])
        box = self.board_box = ttk.Combobox(switch,state='readonly',textvariable=self.board_var,
                            values=list(BOARDS.values()),width=14)
        box.pack(side='left')
        box.bind('<<ComboboxSelected>>',self._change_board)
        Pill(switch,'刷新',self.refresh,'ghost',padx=8,pady=3).pack(side='right')

        self.status = tk.StringVar(top)
        self.status_label = tk.Label(shell, textvariable=self.status, font=(FONT, 8), bg=BG, fg=MUTED,
                                     anchor='w')
        self.status_label.pack(side='bottom', fill='x', pady=(12, 0))
        self.status_label.bind('<ButtonRelease-1>', self._retry)
        self.failed = False
        own = tk.Frame(shell, bg=CARD, padx=16, pady=14)
        own.pack(side='bottom', fill='x', pady=(16, 0))
        own_left = tk.Frame(own, bg=CARD)
        own_left.pack(side='left')
        tk.Label(own_left, text='我的最佳', font=(FONT, 9), fg=MUTED, bg=CARD).pack(anchor='w')
        self.personal = tk.StringVar(top, value='正在查询…')
        tk.Label(own_left, textvariable=self.personal, font=(FONT, 12, 'bold'), fg=ACCENT,
                 bg=CARD).pack(anchor='w', pady=(5, 0))
        self.personal_score = tk.StringVar(top)
        tk.Label(own, textvariable=self.personal_score, font=('Segoe UI', 15, 'bold'),
                 fg=TEXT, bg=CARD).pack(side='right')

        body = tk.Frame(shell, bg=BG)
        body.pack(fill='both', expand=True)
        configure_styles(top)
        style = ttk.Style(top)
        style.configure('Ranking.Vertical.TScrollbar', background='#29433c', troughcolor=BG,
                        bordercolor=BG, lightcolor='#29433c', darkcolor='#29433c',
                        borderwidth=0, arrowsize=9, width=7, gripcount=0, relief='flat')
        style.layout('Ranking.Vertical.TScrollbar', [
            ('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
                ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        self.entries = []
        self.identity_font = Font(top, font=(FONT, 8))
        self.identity_bold = Font(top, font=(FONT, 8, 'bold'))
        self.row_height = max(76, int(top.winfo_fpixels('1i') * .65))
        self.canvas = tk.Canvas(body, bg=BG, bd=0, highlightthickness=0, width=500, height=430,
                                takefocus=True, yscrollincrement=self.row_height)
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview,
                                  style='Ranking.Vertical.TScrollbar')
        scrollbar.pack(side='right', fill='y', padx=(8, 0))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self._draw_rows())
        self.canvas.bind('<Button-1>', lambda event: self.canvas.focus_set())
        top.bind('<MouseWheel>', self._wheel)
        for key, step in (('<Down>', 1), ('<Up>', -1), ('<Next>', 6), ('<Prior>', -6)):
            self.canvas.bind(key, lambda event, step=step: self.canvas.yview_scroll(step, 'units'))
        top.update_idletasks()
        monitor = win32api.MonitorFromPoint((root.winfo_rootx(), root.winfo_rooty()),
                                           win32con.MONITOR_DEFAULTTONEAREST)
        left, upper, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        hwnd = win32gui.GetAncestor(top.winfo_id(), win32con.GA_ROOT)
        x0, y0, x1, y1 = win32gui.GetWindowRect(hwnd)
        bw, bh = x1 - x0 - top.winfo_width(), y1 - y0 - top.winfo_height()
        width = min(max(600, top.winfo_reqwidth()), right - left - bw - 32)
        height = min(max(720, top.winfo_reqheight()), bottom - upper - bh - 32)
        x, y = left + (right - left - width - bw) // 2, upper + (bottom - upper - height - bh) // 2
        top.geometry(f'{width}x{height}+{x}+{y}')
        top.minsize(min(width, 420), min(height, 350))
        top.maxsize(right - left - bw - 32, bottom - upper - bh - 32)
        self.poll_id = top.after(100, self._poll)
        self.refresh()

    def _draw_rows(self):
        canvas = self.canvas
        canvas.delete('all')
        width = canvas.winfo_width()
        h = self.row_height
        colors = {1: GOLD, 2: '#bbcbd5', 3: '#d5ae93'}
        for index, entry in enumerate(self.entries):
            y = index * h
            cy = y + h / 2
            mine = entry['is_me']
            colour = ACCENT if mine else colors.get(entry['rank'], MUTED)
            if mine:
                canvas.create_rectangle(0, y + 3, width, y + h - 3, fill='#183a30', outline='')
                canvas.create_rectangle(0, y + 12, 3, y + h - 12, fill=ACCENT, outline='')
            elif index:
                canvas.create_line(12, y, width - 12, y, fill='#1e302b')
            canvas.create_text(14, cy, text='TOP', anchor='w', font=('Segoe UI', 8, 'bold'), fill=colour)
            canvas.create_text(64, cy, text=str(entry['rank']), anchor='w',
                               font=('Segoe UI', 16, 'bold'), fill=colour)
            label_width = max(90, width - 260)
            # Pixel-based truncation keeps IPv6/system labels clear of the score.
            def fit(text, font):
                if font.measure(text) <= label_width:
                    return text
                while text and font.measure(text + '…') > label_width:
                    text = text[:-1]
                return text + '…'
            computer = entry.get('source') == 'generated'
            ip = entry['display_name'] if computer else entry['ip_address'] or '历史 IP 未记录'
            system = f'{entry["skill_label"]} · 电脑对手' if computer else entry['os_name'] or '历史系统未记录'
            canvas.create_text(110, cy - 11, text=fit(('我 · ' if mine else '') + ip, self.identity_bold), anchor='w',
                               font=self.identity_bold, fill=ACCENT if mine else TEXT)
            canvas.create_text(110, cy + 12, text=fit(system, self.identity_font), anchor='w',
                               font=self.identity_font, fill=MUTED)
            score = str(entry.get('pocketed',0)) if self.board == 'sprint' else f'{entry["avg_ms"] / 1000:.2f}'
            canvas.create_text(width - 64, cy, text=score, anchor='e',
                               font=('Segoe UI', 16, 'bold'), fill=ACCENT if mine else TEXT)
            canvas.create_text(width - 10, cy + 2, text='进球' if self.board == 'sprint' else '秒/颗', anchor='e', font=(FONT, 8), fill=MUTED)
        canvas.configure(scrollregion=(0, 0, width, max(1, len(self.entries) * h)))

    def _wheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _retry(self, event):
        if self.failed:
            self.refresh()

    def alive(self):
        return self.top is not None and self.top.winfo_exists()

    def close(self):
        if self.share_dialog is not None:
            self.share_dialog.close()
        if self.alive():
            self.top.after_cancel(self.poll_id)
            self.top.destroy()
        self.top = None

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.board_box.configure(state='disabled')
        self.failed = False
        self.share_button.set(enabled=False)
        self.status_label.configure(cursor='arrow', fg=MUTED)
        self.status.set('正在获取最新排名…')
        threading.Thread(target=self._load, name='Billiards-leaderboard', daemon=True).start()

    def _change_board(self,event=None):
        self.board = list(BOARDS)[self.board_box.current()]
        self.entries = []
        self.data = None
        self._draw_rows()
        self.refresh()

    def share(self):
        if self.data is None or self.busy or self.share_busy:
            return
        self.share_busy = True
        self.share_button.set(text='正在生成…', enabled=False)
        # Render off Tk's event thread so the game continues responding while
        # drawing the high-resolution card. Use exactly the displayed snapshot.
        snapshot = self.data
        def render():
            try:
                self.inbox.put(('card', (card.render_leaderboard_card(snapshot), card.leaderboard_copy(snapshot))))
            except Exception:
                self.inbox.put(('card_error', None))
        threading.Thread(target=render, name='Billiards-share-card', daemon=True).start()

    def _load(self):
        try:
            self.inbox.put(('ok', self.load(self.board)))
        except RankError as error:
            self.inbox.put(('error', error))
        except Exception:
            self.inbox.put(('error', RankError('bad_response')))

    def _poll(self):
        if not self.alive():
            return
        try:
            kind, data = self.inbox.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind in ('card', 'card_error'):
                self.share_busy = False
                self.share_button.set(text='生成卡片', enabled=not self.busy and not self.failed)
                if kind == 'card':
                    if self.share_dialog is not None:
                        self.share_dialog.close()
                    image, text = data
                    self.share_dialog = ResultDialog(self.top, {}, image=image, share_text=text)
                else:
                    self.status.set('卡片生成失败，请点击“生成卡片”重试')
            elif kind == 'ok':
                self.busy = False
                self.board_box.configure(state='readonly')
                self.data = data
                self.share_button.set(enabled=not self.share_busy)
                self.entries = data['entries']
                self._draw_rows()
                self.canvas.yview_moveto(0)
                me = data['me']
                self.personal.set(f'第 {me["rank"]:,} 名' if me else '暂无上榜成绩')
                personal_score = f'{me["avg_ms"] / 1000:.2f} 秒/颗' if me else ''
                if me and self.board == 'sprint':
                    personal_score = f'{me.get("pocketed",0)} 球 · {me.get("shots",0)} 杆'
                target = data.get('next_target')
                if me and target:
                    # Round up the gap so the displayed target is achievable at .01s precision.
                    gap = (int(target['gap_ms'] / 10) + 1) / 100
                    personal_score += f'\n再快 {gap:.2f} 秒/颗可超一位'
                self.personal_score.set(personal_score)
                stamp = datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00')).astimezone()
                participants = (f'{data["real_players"]} 位玩家 · {data["generated_players"]} 个电脑对手 · '
                                if data.get('generated_players') else '')
                self.status.set(('榜单暂无成绩 · ' if not data['entries'] else participants)
                                + f'更新于 {stamp:%m-%d %H:%M:%S}')
            else:
                self.busy = False
                self.board_box.configure(state='readonly')
                message = ('排名服务尚未支持新榜单' if data.status == 404 else
                           '刷新过于频繁，请稍后重试' if data.code == 'rate_limited' else '暂时无法获取排名，请重试')
                self.failed = True
                self.status_label.configure(cursor='hand2', fg=ACCENT)
                self.status.set(message + ' · 点击重试')
                if not self.entries:
                    self.personal.set('暂时无法查询')
        self.poll_id = self.top.after(100, self._poll)

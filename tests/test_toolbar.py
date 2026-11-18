"""The compact toolbar and hotkeys must survive a withdrawn Tk window."""
import gc
from types import SimpleNamespace

import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import win32con
import win32gui

from dib import config
from dib.ui import DesktopBilliardsApp, desktop_or_app_foreground
from dib.hotkeys import Hotkeys
from dib.panel import MainPanel


class ToolbarTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.destroy_root)

    def destroy_root(self):
        self.root.destroy()
        self.root = None
        # Panel callbacks form cycles. Release their Tk variables on the owning
        # thread before later HTTP workers can trigger cyclic garbage collection.
        gc.collect()

    def test_pause_and_exit_hotkeys_are_enabled(self):
        hotkeys = Hotkeys()
        self.addCleanup(hotkeys.close)
        self.assertIn(config.PAUSE_HOTKEY_ID, hotkeys.registered)
        self.assertIn(config.HOTKEY_ID, hotkeys.registered)
        self.assertEqual(config.PAUSE_HOTKEY_VK, 0x70)  # F1
        self.assertEqual(config.PAUSE_HOTKEY_MODIFIERS, 0x4000)  # MOD_NOREPEAT
        self.assertTrue(config.HOTKEY_MODIFIERS & 0x4)  # MOD_SHIFT avoids the occupied Ctrl+Alt+X chord.
        self.root.withdraw()
        for identifier in (config.PAUSE_HOTKEY_ID, config.HOTKEY_ID):
            win32gui.PostMessage(hotkeys.hwnd, win32con.WM_HOTKEY, identifier, 0)
        self.root.after(100, self.root.quit)
        self.root.mainloop()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertEqual(hotkeys.pending.get_nowait(), config.PAUSE_HOTKEY_ID)
        self.assertEqual(hotkeys.pending.get_nowait(), config.HOTKEY_ID)
        self.assertTrue(hotkeys.pending.empty())

    def test_status_strip_has_a_compact_settings_popover(self):
        calls = []
        names = ('restart', 'toggle_guide', 'toggle_rank', 'toggle_felt', 'leaderboard',
                 'open_website', 'quit', 'check_update', 'set_mode', 'set_cue', 'set_volume', 'toggle_mute', 'history', 'set_hotkeys')
        callbacks = {name: lambda name=name: calls.append(name) for name in names}
        panel = MainPanel(self.root, config.VERSION, callbacks)
        self.root.update()
        self.assertTrue(self.root.overrideredirect())
        self.assertEqual(set(panel.pills), {'leaderboard', 'settings'})
        self.assertEqual(set(panel.settings_window.pills), {'guide', 'rank', 'felt', 'website'})
        self.assertEqual(set(panel.settings_window.actions), {'restart', 'quit'})
        self.assertFalse(panel.settings_open)
        self.assertEqual(panel.hotkey_hint.cget('text'), 'F1 暂停 / 恢复')
        self.assertTrue(panel.hotkey_hint.winfo_ismapped())
        scale = self.root.winfo_fpixels('1i') / 96
        self.assertLess(self.root.winfo_width() / scale, 1000)
        self.assertLess(self.root.winfo_height() / scale, 55)
        self.assertLess(panel.header.winfo_rootx(), panel.stats.winfo_rootx())
        self.assertLess(panel.stats.winfo_rootx(), panel.actions.winfo_rootx())
        self.assertLessEqual(abs(panel.header.winfo_rooty() - panel.stats.winfo_rooty()), 10 * scale)
        for left, right in zip(panel.stat_boxes, panel.stat_boxes[1:] + [panel.pills['leaderboard']]):
            self.assertLessEqual(left.winfo_rootx() + left.winfo_width(), right.winfo_rootx())
        ex_style = win32gui.GetWindowLong(panel.hwnd, win32con.GWL_EXSTYLE)
        self.assertTrue(ex_style & win32con.WS_EX_APPWINDOW)
        self.assertFalse(ex_style & win32con.WS_EX_TOOLWINDOW)
        win32gui.ShowWindow(panel.hwnd, win32con.SW_MINIMIZE)
        self.root.update()
        self.assertTrue(win32gui.IsIconic(panel.hwnd))
        win32gui.ShowWindow(panel.hwnd, win32con.SW_RESTORE)
        self.root.update()
        self.assertFalse(win32gui.IsIconic(panel.hwnd))
        self.assertTrue(panel.pills['settings'].winfo_ismapped())
        panel.pills['settings']._release(SimpleNamespace(x=1, y=1))
        self.root.update()
        self.assertTrue(panel.settings_open)
        self.assertTrue(panel.settings_window.top.winfo_viewable())
        self.assertEqual(panel.settings_window.version_label.cget('text'), f'当前版本 v{config.VERSION}')
        update_button = panel.settings_window.update_button
        self.assertTrue(update_button.winfo_ismapped())
        panel.settings_window.set_update_status('正在连接后台…', busy=True)
        self.assertFalse(update_button.enabled)
        update_button._release(SimpleNamespace(x=1, y=1))
        self.assertEqual(calls, [])
        panel.settings_window.set_update_status('已是最新版本')
        self.assertTrue(update_button.enabled)
        update_button._release(SimpleNamespace(x=1, y=1))
        self.assertEqual(calls.pop(), 'check_update')
        positions = [pill.winfo_rooty() for pill in panel.settings_window.pills.values()]
        self.assertLessEqual(max(positions) - min(positions), 45)
        for pill in panel.settings_window.pills.values():
            self.assertTrue(pill.winfo_ismapped())
            pill._release(SimpleNamespace(x=1, y=1))
        panel.pills['leaderboard']._release(SimpleNamespace(x=1, y=1))
        for pill in panel.settings_window.actions.values():
            self.assertTrue(pill.winfo_ismapped())
            pill._release(SimpleNamespace(x=1, y=1))
        self.assertEqual(calls, ['toggle_guide', 'toggle_rank', 'toggle_felt', 'open_website',
                                 'leaderboard', 'restart', 'quit'])
        panel.hide_settings()
        self.assertFalse(panel.settings_open)

    def test_an_occupied_f1_is_reported_and_released_on_close(self):
        first = Hotkeys()
        self.addCleanup(first.close)
        second = Hotkeys()
        self.assertTrue(any('F1' in error for error in second.errors))
        self.assertNotIn(config.PAUSE_HOTKEY_ID, second.registered)
        second.close()
        first.close()
        second = Hotkeys()
        self.addCleanup(second.close)
        self.assertIn(config.PAUSE_HOTKEY_ID, second.registered)

    def test_automatic_rank_prompt_waits_until_resume(self):
        app = DesktopBilliardsApp.__new__(DesktopBilliardsApp)
        app.toolbar_eye = None
        app.background_hidden = False
        app.settings = Mock()
        app.panel = Mock()
        app.closing = False
        app.pause_hidden = True
        app.root = Mock()
        app.rank = Mock(needs_prompt=True)
        app.offer_rank()
        app.rank.mark_prompted.assert_not_called()
        app.root.after.assert_called_once_with(250, app.offer_rank)

    def test_f1_toggles_pause_and_taskbar_minimize(self):
        app = DesktopBilliardsApp.__new__(DesktopBilliardsApp)
        app.toolbar_eye = None
        app.background_hidden = False
        app.settings = Mock()
        app.panel = Mock()
        app.closing = False
        app.pause_hidden = False
        app.panel_visible = True
        app.panel = SimpleNamespace(hwnd=123, hide_settings=Mock(), hotkey_hint=Mock())
        app.root = Mock()
        app.runner = SimpleNamespace(
            shown=SimpleNamespace(is_set=lambda: True),
            overlay=SimpleNamespace(want_hidden=False),
        )
        app.auxiliary_windows = lambda: []
        app.remember_position = lambda: None
        with patch('dib.ui.win32gui.ShowWindow') as show:
            app.toggle_pause_hidden()
            self.assertTrue(app.pause_hidden)
            self.assertTrue(app.runner.overlay.want_hidden)
            self.assertFalse(app.panel_visible)
            show.assert_called_once_with(123, win32con.SW_MINIMIZE)

            with patch('dib.ui.win32gui.GetForegroundWindow') as foreground:
                app.sync_panel_visibility()
                foreground.assert_not_called()

            show.reset_mock()
            app.toggle_pause_hidden()
            self.assertFalse(app.pause_hidden)
            self.assertFalse(app.runner.overlay.want_hidden)
            self.assertTrue(app.panel_visible)
            show.assert_called_once_with(123, win32con.SW_RESTORE)
        app.root.deiconify.assert_called_once_with()
        app.root.lift.assert_called_once_with()

    def test_foreground_detection_hides_hud_over_other_apps(self):
        with patch('dib.ui.win32process.GetWindowThreadProcessId', return_value=(1, 222)), \
                patch('dib.ui.win32gui.IsIconic', return_value=False), \
                patch('dib.ui.win32gui.GetClassName', return_value='Chrome_WidgetWin_1'), \
                patch('dib.ui.win32gui.GetParent', return_value=0):
            self.assertFalse(desktop_or_app_foreground(10, 111))

    def test_foreground_detection_allows_desktop_and_our_dialogs(self):
        with patch('dib.ui.win32process.GetWindowThreadProcessId', return_value=(1, 111)):
            self.assertTrue(desktop_or_app_foreground(10, 111))
        with patch('dib.ui.win32process.GetWindowThreadProcessId', return_value=(1, 222)), \
                patch('dib.ui.win32gui.IsIconic', return_value=False), \
                patch('dib.ui.win32gui.GetClassName', side_effect=('SysListView32',)), \
                patch('dib.ui.win32gui.GetParent', return_value=0):
            self.assertTrue(desktop_or_app_foreground(10, 111))

    def test_minimized_foreground_means_the_desktop_is_exposed(self):
        with patch('dib.ui.win32process.GetWindowThreadProcessId', return_value=(1, 222)), \
                patch('dib.ui.win32gui.IsIconic', return_value=True):
            self.assertTrue(desktop_or_app_foreground(10, 111))

    def test_disappearing_foreground_window_is_ignored(self):
        with patch('dib.ui.win32process.GetWindowThreadProcessId', side_effect=RuntimeError('window closed')):
            self.assertFalse(desktop_or_app_foreground(10, 111))


if __name__ == '__main__':
    unittest.main()

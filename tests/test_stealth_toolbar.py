"""The hidden-table toolbar remains discoverable and F1 does not undo hiding."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dib.ui import DesktopBilliardsApp


class StealthToolbarTests(unittest.TestCase):
    def app(self):
        app = object.__new__(DesktopBilliardsApp)
        app.root = Mock()
        app.root.grab_current.return_value = None
        app.root.winfo_children.return_value = []
        app.root.winfo_rootx.return_value = 100
        app.root.winfo_rooty.return_value = 20
        app.root.winfo_width.return_value = 460
        app.root.winfo_height.return_value = 34
        app.root.winfo_pointerxy.return_value = (1000, 400)
        settings_window = Mock()
        settings_window.bounds.return_value = None
        app.panel = SimpleNamespace(settings_open=False, settings_window=settings_window, hide_settings=Mock())
        app.result_dialog = app.leaderboard_dialog = None
        app.felt_enabled = False
        app.pause_hidden = False
        app.background_hidden = False
        app.settings = Mock()
        app.settings.get.side_effect = lambda key,default=None: default
        app.stealth_announced = False
        app.toolbar_hidden = False
        app.toolbar_eye = Mock()
        app.toolbar_region = None
        app.toolbar_hover_since = app.toolbar_leave_since = None
        app.remember_position = Mock()
        return app

    @patch('dib.ui.win32api.GetAsyncKeyState', return_value=0)
    @patch('dib.ui.messagebox.showinfo')
    @patch('dib.ui.time.monotonic')
    def test_prompt_precedes_hiding_and_hover_restores_toolbar(self, clock, prompt, keys):
        app = self.app()
        clock.return_value = 10
        app.update_toolbar()
        prompt.assert_called_once()
        self.assertIn('Shift', prompt.call_args.args[1])
        app.root.withdraw.assert_not_called()
        clock.return_value = 11.1
        app.update_toolbar()
        self.assertTrue(app.toolbar_hidden)
        app.root.winfo_pointerxy.return_value = (200, 30)
        app.update_toolbar()
        clock.return_value = 11.5
        app.update_toolbar()
        self.assertFalse(app.toolbar_hidden)
        app.root.deiconify.assert_called_once()

    @patch('dib.ui.win32api.GetAsyncKeyState', return_value=0)
    def test_pause_keeps_toolbar_hidden_until_resume(self, keys):
        app = self.app()
        app.pause_hidden = True
        app.toolbar_hidden = True
        app.update_toolbar()
        app.root.deiconify.assert_not_called()
        app.toolbar_eye.withdraw.assert_called_once()

    @patch('dib.ui.win32api.GetAsyncKeyState', return_value=0)
    def test_eye_hover_restores_toolbar_without_click(self, keys):
        app = self.app()
        app.toolbar_hidden = True
        app.reveal_toolbar()
        app.root.deiconify.assert_called_once()
        app.toolbar_eye.withdraw.assert_called_once()
        self.assertFalse(app.toolbar_hidden)

    @patch('dib.ui.win32api.GetAsyncKeyState', return_value=0)
    @patch('dib.ui.time.monotonic')
    def test_settings_window_counts_as_hover_area_then_hides_with_the_strip(self, clock, keys):
        app = self.app()
        app.stealth_announced = True
        app.panel.settings_open = True
        app.panel.settings_window.bounds.return_value = (600, 400, 900, 700)
        app.toolbar_region = (100, 20, 560, 54)
        app.root.winfo_pointerxy.return_value = (700, 500)
        clock.return_value = 10
        app.update_toolbar()
        app.root.withdraw.assert_not_called()

        app.root.winfo_pointerxy.return_value = (1000, 800)
        clock.return_value = 10.1
        app.update_toolbar()
        clock.return_value = 11.2
        app.update_toolbar()
        app.panel.hide_settings.assert_called_once()
        app.root.withdraw.assert_called_once()
        self.assertTrue(app.toolbar_hidden)


if __name__ == '__main__':
    unittest.main()

"""Collision artwork is reused without changing pixels or shared layers."""
from collections import OrderedDict
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from dib.overlay import BALL_CANVAS, CHARGE_SECONDS, RING_LEVELS, BilliardsOverlay, wait_for_listview_items


class CollisionCacheTests(unittest.TestCase):
    def test_charge_window_is_longer_than_the_previous_short_tap_window(self):
        self.assertAlmostEqual(CHARGE_SECONDS, 1.35)

    def test_colliding_sprites_reuse_layers_without_mutating_them(self):
        overlay = object.__new__(BilliardsOverlay)
        overlay.table = SimpleNamespace(radius=35)
        overlay.frame_cache = OrderedDict()
        overlay.frame_cache_bytes = 0
        overlay.ring_frames = {level: overlay.ring_layer(level) for level in range(1, RING_LEVELS + 1)}
        originals = {level: frame.tobytes() for level, frame in overlay.ring_frames.items()}
        source = Image.new('RGBA', (64, 64), (80, 140, 200, 180))
        with patch.object(overlay, 'ring_layer', side_effect=AssertionError('redrawing a cached ring')):
            for level in range(1, RING_LEVELS + 1):
                for identity in range(45):
                    frame = overlay.sprite_frame(source, identity, 64, 64, identity, ring=level)
                    plain = overlay.sprite_frame(source, identity, 64, 64, identity).copy()
                    plain.alpha_composite(overlay.ring_frames[level])
                    self.assertEqual(frame.tobytes(), plain.tobytes())
        self.assertEqual(originals, {level: frame.tobytes() for level, frame in overlay.ring_frames.items()})
        self.assertEqual(sum(frame.width * frame.height * 4 for frame in overlay.ring_frames.values()),
                         RING_LEVELS * BALL_CANVAS * BALL_CANVAS * 4)

    def test_waits_for_explorer_to_repopulate_desktop_icons(self):
        with patch('dib.overlay.listview_count', side_effect=(0, 0, 45)), \
                patch('dib.overlay.time.sleep'):
            self.assertEqual(wait_for_listview_items(100, timeout=1), 45)

    def test_returns_zero_when_the_desktop_view_stays_empty(self):
        with patch('dib.overlay.listview_count', return_value=0):
            self.assertEqual(wait_for_listview_items(100, timeout=0), 0)


if __name__ == '__main__':
    unittest.main()

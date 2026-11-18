"""The shareable result card."""
import struct
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path

from PIL import Image

from dib import card

BASE = {'time': '06:12.34', 'average': '10.63', 'balls': '35', 'shots': '41', 'note': ''}
WHEN = time.strptime('2026-11-11', '%Y-%m-%d')


def view(**rank):
    return {**BASE, 'rank': rank}


def ok(**changes):
    return view(phase='ok', rank=1204, percentile=87, best_rank=860, new_best=False, **changes)


def render(v):
    return card.render_card(v, when=WHEN)


class TierTests(unittest.TestCase):
    def test_tiers_follow_the_percentile(self):
        expected = {100: '传奇球王', 99: '传奇球王', 98: '桌面球王', 95: '桌面球王', 94: '大师', 85: '大师',
                    84: '高手', 65: '高手', 64: '熟手', 40: '熟手', 39: '新秀', 0: '新秀'}
        for percentile, title in expected.items():
            with self.subTest(percentile=percentile):
                self.assertEqual(card.tier_for(percentile)[0], title)

    def test_every_tier_has_its_own_colour(self):
        colours = {colour for _, _, colour in card.TIERS}
        self.assertEqual(len(colours), len(card.TIERS))


class RenderTests(unittest.TestCase):
    def test_the_card_is_a_4_by_5_rgb_poster(self):
        image = render(ok())
        self.assertEqual((image.size, image.mode), ((1080, 1350), 'RGB'))

    def test_every_ranking_phase_renders(self):
        for rank in ({'phase': 'ok', 'rank': 1, 'percentile': 100, 'best_rank': 1, 'new_best': True},
                     {'phase': 'ok', 'rank': 1234567, 'percentile': 0, 'best_rank': 9999999, 'new_best': False},
                     {'phase': 'off'}, {'phase': 'pending'}, {'phase': 'unavailable'},
                     {'phase': 'skipped', 'message': '本局开始后才开启排名，下一局起计入。'},
                     {'phase': 'error', 'message': '成绩校验未通过，本局不计入排名'}):
            with self.subTest(phase=rank['phase'], rank=rank.get('rank')):
                self.assertEqual(render({**BASE, 'rank': rank}).size, (1080, 1350))

    def test_a_huge_rank_still_fits_inside_the_card(self):
        image = render(view(phase='ok', rank=12_345_678, percentile=50, best_rank=1, new_best=False))
        # Nothing but the background may touch the extreme left and right columns around the hero.
        for x in (0, 1079):
            for y in range(380, 660, 40):
                r, g, b = image.getpixel((x, y))
                self.assertLess(max(r, g, b), 120)

    def test_tiers_change_the_look(self):
        low = render(view(phase='ok', rank=900, percentile=20, best_rank=900, new_best=False))
        top = render(view(phase='ok', rank=2, percentile=99, best_rank=2, new_best=False))
        self.assertNotEqual(low.tobytes(), top.tobytes())

    def test_the_tier_badge_is_translucent_not_a_solid_slab(self):
        image = render(ok())  # percentile 87 -> 大师, purple
        _, colour = card.tier_for(87)
        # A point inside the badge, left of the text: tinted, but far darker than the solid colour.
        r, g, b = image.getpixel((470, 277))
        self.assertLess(r + g + b, (sum(colour)) * .75)
        self.assertGreater(r + g + b, 3 * 40)

    def test_new_record_badge_only_when_it_is_one(self):
        plain = render(ok())
        record = render(view(phase='ok', rank=1204, percentile=87, best_rank=860, new_best=True))
        area = (800, 1150, 1010, 1225)
        self.assertNotEqual(plain.crop(area).tobytes(), record.crop(area).tobytes())

    def test_ranked_card_has_a_percentile_progress_bar(self):
        image = render(view(phase='ok', rank=1204, percentile=50, best_rank=860, new_best=False))
        # The centre of the filled half is the tier colour; the far right is
        # the dark track.  Both samples must be painted, not background.
        self.assertNotEqual(image.getpixel((270, 880)), image.getpixel((810, 880)))

    def test_unranked_rounds_lead_with_the_average_time(self):
        off = render(view(phase='off'))
        self.assertNotEqual(off.tobytes(), render(ok()).tobytes())

    def test_sprint_with_no_pockets_shows_conversion_not_a_clearance_average(self):
        from PIL import ImageDraw
        from unittest.mock import patch
        strings = []
        original = ImageDraw.ImageDraw.text
        def observe(draw, xy, text, *args, **kwargs):
            strings.append(text)
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', observe):
            render({**BASE, 'mode':'sprint', 'pocketed':0, 'balls':'15', 'rank':{'phase':'off'}})
        self.assertIn('90 秒挑战进球', strings)
        self.assertIn('进球率', strings)
        self.assertNotIn('平均每颗', strings)
        self.assertNotIn('平均每颗用时', strings)

    def test_the_card_never_contains_the_desktop(self):
        """Only the app's own logo is used; no desktop icon artwork or names are drawn."""
        source = Path(card.__file__).read_text(encoding='utf-8')
        imports = [line for line in source.splitlines() if line.startswith(('import ', 'from '))]
        for line in imports:
            self.assertNotRegex(line, r'overlay|shell|\.ui|wallpaper', 'the card must not reach into the desktop')
        self.assertEqual(card.LOGO.name, 'desktop-icon-billiards.png')


class SharingTests(unittest.TestCase):
    def test_save_writes_a_png_with_a_timestamped_name(self):
        image = Image.new('RGB', (40, 50), 'white')
        with tempfile.TemporaryDirectory() as folder:
            path = card.save_card(image, Path(folder) / 'sub', when=time.strptime('2026-11-11 08:09:10', '%Y-%m-%d %H:%M:%S'))
            self.assertEqual(path.parent, Path(folder) / 'sub')
            self.assertEqual(path.name, '桌面图标台球-成绩-20261111-080910.png')
            with Image.open(path) as saved:
                self.assertEqual((saved.format, saved.size), ('PNG', (40, 50)))

    def test_clipboard_bitmap_is_a_dib_without_the_file_header(self):
        data = card.dib_bytes(Image.new('RGB', (4, 3), (10, 20, 30)))
        header_size, width, height = struct.unpack('<Iii', data[:12])
        self.assertEqual((header_size, width, abs(height)), (40, 4, 3))
        self.assertFalse(data.startswith(b'BM'))

    def test_pictures_directory_is_inside_the_users_pictures_or_home(self):
        directory = card.pictures_directory()
        self.assertEqual(directory.name, 'DesktopIconBilliards')


class LeaderboardSharingTests(unittest.TestCase):
    def board(self):
        return {'entries': [
            {'rank': 1, 'avg_ms': 3500, 'is_me': False, 'source': 'generated',
             'display_name': '青柠汽水', 'skill_label': '高手', 'ip_address': '', 'os_name': ''},
            {'rank': 2, 'avg_ms': 6300, 'is_me': True, 'source': 'player',
             'display_name': '', 'skill_label': '', 'ip_address': '127.0.0.1', 'os_name': 'Windows 11'},
        ], 'me': {'rank': 2, 'avg_ms': 6300}, 'total_players': 2,
            'real_players': 1, 'generated_players': 1, 'generated_at': '2026-11-17T12:00:00Z'}

    def test_live_snapshot_renders_without_modifying_results(self):
        data = self.board()
        before = deepcopy(data)
        image = card.render_leaderboard_card(data)
        self.assertEqual((image.size, image.mode), ((1080, 1350), 'RGB'))
        self.assertEqual(data, before)

    def test_empty_board_renders_an_invitation_card(self):
        data = {**self.board(), 'entries': [], 'me': None, 'total_players': 0,
                'real_players': 0, 'generated_players': 0}
        self.assertEqual(card.render_leaderboard_card(data).size, (1080, 1350))

    def test_copy_contains_official_url_and_correct_personal_score(self):
        text = card.leaderboard_copy(self.board())
        self.assertIn('https://deskpools.com', text)
        self.assertIn('第 2 名，6.30 秒/颗', text)
        self.assertIn('1 位玩家、1 个电脑对手', text)
        self.assertIn('F1', text)

    def test_public_copy_does_not_invent_a_personal_result(self):
        data = {**self.board(), 'me': None}
        text = card.leaderboard_copy(data)
        self.assertNotIn('我的最佳：', text)
        self.assertIn(card.SHARE_URL, text)

    def test_leaderboard_filename_is_distinct_from_result_card(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Image.new('RGB', (40, 50))
            result = card.save_card(image, folder, when=WHEN)
            ranking = card.save_card(image, folder, when=WHEN, kind='排行榜')
            self.assertNotEqual(result, ranking)
            self.assertTrue(result.exists() and ranking.exists())


if __name__ == '__main__':
    unittest.main()

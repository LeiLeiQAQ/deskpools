from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ranking_server.server import RankingStore
from tools.seed_rankings import identities, manage, records


class RankingImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.db = self.base / 'ranking.db'
        self.store = RankingStore(self.db)
        self.store.init()
        self.player, _ = self.store.register('a' * 64)
        self.store.finish_round(self.player, self.store.start_round(self.player), 90000, 15, 18,
                                ip_address='127.0.0.1', os_name='Windows 11')

    def action(self, action):
        return manage(self.db, action, 'example-batch', 60, self.base / 'backups')

    def test_refresh_preserves_real_scores_and_rollback_restores_original_board(self):
        original = self.store.rankings(self.player)
        seeded = self.action('seed')
        self.assertEqual(seeded['changed'], 60)
        with closing(sqlite3.connect(self.db)) as db:
            # Reproduce a batch imported by the previous labelled generator.
            db.execute("UPDATE scores SET os_name='预置成绩 · Windows 11',source='player' WHERE device_id!=?", (self.player,))
            db.commit()
        refreshed = self.action('refresh')
        self.assertEqual(refreshed['changed'], 60)
        self.assertEqual(refreshed['overview']['real_players'], 1)
        self.assertEqual(refreshed['overview']['generated_players'], 60)
        self.assertEqual(self.action('seed')['changed'], 0)
        with closing(sqlite3.connect(refreshed['backup'])) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM scores').fetchone()[0], 61)
        self.assertEqual(self.action('remove')['changed'], 60)
        final = self.store.rankings(self.player)
        for data in (original, final):
            data.pop('generated_at')
        self.assertEqual(original, final)

    def test_unidentified_record_is_not_rewritten(self):
        self.action('seed')
        device, _, _ = identities('example-batch', 5)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE scores SET source='player',os_name='Windows 11' WHERE device_id=?", (device,))
            db.commit()
        before = self.store.admin_scores(limit=100)
        with self.assertRaisesRegex(ValueError, 'not identified as generated'):
            self.action('refresh')
        self.assertEqual(before, self.store.admin_scores(limit=100))

    def test_identity_migration_removes_fictitious_network_data_and_keeps_results(self):
        self.action('seed')
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            db.execute("UPDATE scores SET ip_address='192.0.2.1',os_name='Windows 11' WHERE source='generated'")
            db.commit()
            before = {row['id']: dict(row) for row in db.execute('SELECT * FROM scores')}
        result = self.action('identities')
        self.assertEqual(result['changed'], 60)
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            after = {row['id']: dict(row) for row in db.execute('SELECT * FROM scores')}
        names = []
        for key, row in after.items():
            if row['device_id'] == self.player:
                self.assertEqual(row, before[key])
            else:
                self.assertEqual((row['ip_address'], row['os_name']), ('', ''))
                names.append(row['display_name'])
                self.assertIn(row['skill_label'], ('大师', '高手', '熟练', '进阶', '入门'))
                for field in ('id', 'round_id', 'total_ms', 'icon_count', 'shots', 'avg_ms', 'created_at'):
                    self.assertEqual(row[field], before[key][field])
        self.assertEqual(len(set(names)), 60)

    def test_generated_scores_are_varied_valid_and_disclose_source(self):
        now = datetime.now(timezone.utc)
        data = records('example-batch', 60, now)
        # Occasional rounded ties are natural; a large pile-up at a clamp is not.
        self.assertGreaterEqual(len({round(row['avg_ms'] / 10) for row in data}), 54)
        self.assertGreater(max(row['avg_ms'] for row in data), min(row['avg_ms'] for row in data) * 3)
        for row in data:
            self.assertNotIn('预置', row['os_name'])
            self.assertEqual(row['source'], 'generated')
            self.assertEqual((row['ip_address'], row['os_name']), ('', ''))
            self.assertTrue(row['display_name'])
            self.assertEqual(row['total_ms'] / row['icon_count'], row['avg_ms'])
            self.assertAlmostEqual(row['finished_at'] - row['started_at'], row['total_ms'] / 1000, places=3)
            self.assertLess(row['finished_at'], now.timestamp())


if __name__ == '__main__':
    unittest.main()

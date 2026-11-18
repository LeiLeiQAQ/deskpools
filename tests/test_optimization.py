"""Regression coverage for physical contact, competitive rules and durable results."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from dib.table import BilliardsMotion
from dib.history import LocalScores
from dib.health import HealthPulse,UI,RUNNER,stalled_reason
from dib.hotkeys import parse_binding
from dib.modes import rules_for,challenge_day
from ranking_server.server import RankingStore,ApiError


class OptimizedPhysicsTests(unittest.TestCase):
    def test_physical_radius_matches_artwork_at_all_ball_counts(self):
        for count in (15,21,36,45):
            game=BilliardsMotion((0,0,1920,1040),[(48,48)]*count)
            self.assertEqual(game.radius*2,game.poses[0][2])
            a,b=game.icons[:2]
            a.x,a.y=400,400; b.x,b.y=400+game.tile+1,400
            a.vx=500
            game._collide(a,b)
            self.assertEqual(b.vx,0)

    def test_ranked_table_is_two_to_one_on_different_monitors(self):
        for work in ((0,0,1920,1040),(0,0,3440,1400),(0,0,1280,720)):
            game=BilliardsMotion(work,[(48,48)]*15,mode='standard')
            self.assertAlmostEqual(game.width/game.height,2)
            self.assertAlmostEqual(game.radius*2,game.tile)

    def test_daily_rack_is_repeatable(self):
        a=BilliardsMotion((0,0,1920,1040),[(48,48)]*15,mode='daily',day_key='2026-11-17')
        b=BilliardsMotion((0,0,1920,1040),[(48,48)]*15,mode='daily',day_key='2026-11-17')
        c=BilliardsMotion((0,0,1920,1040),[(48,48)]*15,mode='daily',day_key='2026-11-18')
        self.assertEqual(a.rack,b.rack)
        self.assertNotEqual(a.rack,c.rack)

    def test_sprint_expires_and_competitive_pause_keeps_wall_clock(self):
        game=BilliardsMotion((0,0,1920,1040),[(48,48)]*15,mode='sprint')
        game.shoot(.5,100)
        game.step(.01,101)
        game.pause(101);game.resume(195);game.step(.01,195)
        self.assertEqual((game.state,game.play_ms,game.finish_reason),('finished',90000,'timeout'))

    def test_hotkey_parser_rejects_unmodified_letters_and_normalizes(self):
        self.assertEqual(parse_binding('ctrl+alt+p'),(0x4003,ord('P'),'Ctrl+Alt+P'))
        with self.assertRaises(ValueError):parse_binding('P')
        with self.assertRaises(ValueError):parse_binding('Ctrl+Ctrl+P')

    def test_independent_health_mapping_detects_stalls(self):
        import uuid
        a=HealthPulse(uuid.uuid4().hex,create=True)
        self.addCleanup(a.close)
        a.arm();a.beat(UI);a.beat(RUNNER)
        self.assertEqual(stalled_reason(a,time.monotonic()),'')
        self.assertEqual(stalled_reason(a,time.monotonic()+16),'ui_heartbeat_timeout')


class LocalRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'scores.db';self.store=LocalScores(self.path)

    def test_local_results_survive_reopening(self):
        game=BilliardsMotion((0,0,1920,1040),[(48,48)]*15)
        self.store.record('one',game);self.store.record('one',game)
        self.assertEqual(len(LocalScores(self.path).recent()),1)

    def test_outbox_is_durable_idempotent_and_cancellable(self):
        payload={'round_id':'one','total_ms':30000}
        self.store.enqueue('server','device',payload);self.store.enqueue('server','device',payload)
        reopened=LocalScores(self.path)
        self.assertEqual(len(reopened.pending('server','device')),1)
        reopened.cancel('server')
        self.assertEqual(reopened.pending('server','device'),[])

    def test_expired_outbox_is_not_replayed(self):
        self.store.enqueue('server','device',{'round_id':'one'})
        with self.store.connection() as db:db.execute('UPDATE outbox SET created_at=?',(time.time()-21601,))
        self.assertEqual(self.store.pending('server','device'),[])


class CompetitiveBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=RankingStore(Path(self.temp.name)/'ranking.db');self.store.init()
        self.device,self.secret=self.store.register('1'*64)

    def start(self,board='standard'):
        round_id=self.store.start_round(self.device,board=board,rules_version=3,
                                       day_key=challenge_day() if board=='daily' else '')
        with self.store._connection() as db:db.execute('UPDATE rounds SET started_at=started_at-120 WHERE round_id=?',(round_id,))
        return round_id

    def test_replayed_finish_returns_original_response_without_duplicate(self):
        rid=self.start()
        first=self.store.finish_round(self.device,rid,30000,15,6,pocketed=15)
        again=self.store.finish_round(self.device,rid,30000,15,6,pocketed=15)
        self.assertEqual(first,again)
        with self.store._connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM scores').fetchone()[0],1)

    def test_server_rejects_impossible_elapsed_time(self):
        rid=self.store.start_round(self.device,board='standard',rules_version=3)
        with self.assertRaises(ApiError):self.store.finish_round(self.device,rid,30000,15,6,pocketed=15)

    def test_human_and_computer_ranks_are_separate(self):
        rid=self.start();self.store.finish_round(self.device,rid,30000,15,6,pocketed=15)
        with self.store._connection() as db:db.execute("UPDATE scores SET source='generated',display_name='电脑',skill_label='高手'")
        self.assertEqual(self.store.rankings(board='standard')['total_players'],0)
        self.assertEqual(self.store.rankings(board='computer')['total_players'],1)

    def test_sprint_ranking_prioritizes_pocket_count(self):
        rid=self.start('sprint');self.store.finish_round(self.device,rid,90000,15,9,pocketed=5)
        rid=self.start('sprint');self.store.finish_round(self.device,rid,90000,15,12,pocketed=8)
        self.assertEqual(self.store.rankings(board='sprint')['entries'][0]['pocketed'],8)

    def test_sprint_personal_best_uses_balls_then_time_and_shots(self):
        def finish(balls,shots):
            return self.store.finish_round(self.device,self.start('sprint'),90000,15,shots,pocketed=balls)
        self.assertTrue(finish(5,9)['is_personal_best'])
        self.assertTrue(finish(8,12)['is_personal_best'])
        self.assertFalse(finish(6,8)['is_personal_best'])
        self.assertTrue(finish(8,10)['is_personal_best'])
        self.assertFalse(finish(8,10)['is_personal_best'])

    def test_same_player_has_independent_scores_by_mode(self):
        rid=self.start();self.store.finish_round(self.device,rid,30000,15,6,pocketed=15)
        self.assertEqual(self.store.rankings(board='daily',day_key=challenge_day())['entries'],[])

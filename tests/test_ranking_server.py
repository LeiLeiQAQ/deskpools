import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from dib.rank import RankApi, RankError, current_system
from ranking_server import server


class RankingServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.httpd = server.create_server('127.0.0.1', 0, Path(self.temp.name) / 'rank.db')
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.httpd.server_port}'
        self.api = RankApi(self.url, '0.2.1', timeout=2)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        payload = None if body is None else json.dumps(body, separators=(',', ':')).encode('utf-8')
        request = Request(self.url + path, data=payload, headers=headers or {}, method=method)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_client_round_trip_and_public_leaderboard(self):
        first = self.api.register('a' * 64)
        self.assertEqual(first, self.api.register('a' * 64))
        round_id = self.api.start_round({'device_id': first[0], 'secret': first[1]})
        result = self.api.finish_round(
            {'device_id': first[0], 'secret': first[1]}, round_id, 60_000, 15, 20,
        )
        self.assertEqual(result['avg_ms'], 4_000)
        self.assertTrue(result['is_personal_best'])
        rankings = self.api.rankings({'device_id': first[0], 'secret': first[1]})
        self.assertEqual(rankings['me']['rank'], 1)
        self.assertEqual(rankings['entries'][0]['avg_ms'], 4_000)
        status, public = self.request('/v1/leaderboard?limit=50')
        self.assertEqual(status, 200)
        self.assertEqual(public['entries'][0]['is_me'], False)
        self.assertEqual(public['entries'][0]['ip_address'], '127.0.0.1')
        self.assertEqual(public['entries'][0]['os_name'], current_system())

    def test_score_keeps_the_system_snapshot_of_the_best_round(self):
        device_id, secret = self.api.register('9' * 64)
        creds = {'device_id': device_id, 'secret': secret}
        with patch('dib.rank.current_system', return_value='Windows 10 (build 19045)'):
            self.api.finish_round(creds, self.api.start_round(creds), 60_000, 15, 12)
        with patch('dib.rank.current_system', return_value='Windows 11 (build 26100)'):
            self.api.finish_round(creds, self.api.start_round(creds), 90_000, 15, 12)
        self.assertEqual(self.api.rankings(creds)['me']['os_name'], 'Windows 10 (build 19045)')
        rows = self.httpd.RequestHandlerClass.store.admin_scores()['entries']
        self.assertEqual(rows[0]['os_name'], 'Windows 11 (build 26100)')

    def test_forwarded_ip_is_ignored_without_a_trusted_proxy(self):
        device_id, secret = self.api.register('f' * 64)
        creds = {'device_id': device_id, 'secret': secret}
        payload = {'round_id': self.api.start_round(creds), 'total_ms': 60000, 'icon_count': 15,
                   'shots': 12, 'os_name': 'Windows 11', 'ip_address': '203.0.113.99'}
        body = json.dumps(payload, separators=(',', ':')).encode()
        stamp = str(int(time.time()))
        status, _ = self.request('/v1/round/finish', 'POST', payload, {
            'X-Device-Id': device_id, 'X-Timestamp': stamp,
            'X-Signature': server.sign(secret, 'POST', '/v1/round/finish', stamp, body),
            'X-Forwarded-For': '203.0.113.99'})
        self.assertEqual(status, 200)
        self.assertEqual(self.api.leaderboard()['entries'][0]['ip_address'], '127.0.0.1')

    def test_invalid_system_metadata_is_rejected_without_consuming_round(self):
        device_id, secret = self.api.register('0' * 64)
        creds = {'device_id': device_id, 'secret': secret}
        round_id = self.api.start_round(creds)
        for label in (None, 42, 'x' * 81, 'Windows\n11'):
            with patch('dib.rank.current_system', return_value=label):
                with self.assertRaises(RankError) as caught:
                    self.api.finish_round(creds, round_id, 60000, 15, 12)
            self.assertEqual((caught.exception.code, caught.exception.status), ('bad_request', 400))
        self.api.finish_round(creds, round_id, 60000, 15, 12)

    def test_schema_upgrade_preserves_existing_scores_and_is_idempotent(self):
        device_id, secret = self.api.register('1' * 64)
        creds = {'device_id': device_id, 'secret': secret}
        self.api.finish_round(creds, self.api.start_round(creds), 60000, 15, 12)
        store = self.httpd.RequestHandlerClass.store
        with store._connection() as db:
            db.execute('ALTER TABLE scores DROP COLUMN ip_address')
            db.execute('ALTER TABLE scores DROP COLUMN os_name')
        store.init()
        store.init()
        entry = self.api.leaderboard()['entries'][0]
        self.assertEqual((entry['rank'], entry['avg_ms'], entry['ip_address'], entry['os_name']), (1, 4000, '', ''))


    def test_faster_device_reorders_and_slow_device_gets_personal_best(self):
        slow_id, slow_secret = self.api.register('b' * 64)
        slow_round = self.api.start_round({'device_id': slow_id, 'secret': slow_secret})
        self.api.finish_round({'device_id': slow_id, 'secret': slow_secret}, slow_round, 90_000, 15, 30)
        fast_id, fast_secret = self.api.register('c' * 64)
        fast_round = self.api.start_round({'device_id': fast_id, 'secret': fast_secret})
        self.api.finish_round({'device_id': fast_id, 'secret': fast_secret}, fast_round, 30_000, 15, 8)
        slower_round = self.api.start_round({'device_id': slow_id, 'secret': slow_secret})
        repeat = self.api.finish_round(
            {'device_id': slow_id, 'secret': slow_secret}, slower_round, 105_000, 15, 20,
        )
        self.assertFalse(repeat['is_personal_best'])
        rankings = self.api.rankings({'device_id': slow_id, 'secret': slow_secret})
        self.assertEqual([entry['avg_ms'] for entry in rankings['entries']], [2_000, 6_000])
        self.assertEqual(rankings['me']['rank'], 2)

    def test_authentication_and_validation_errors_are_protocol_codes(self):
        device_id, secret = self.api.register('d' * 64)
        credentials = {'device_id': device_id, 'secret': secret}
        round_id = self.api.start_round(credentials)
        with self.assertRaises(RankError) as caught:
            self.api.finish_round(credentials, round_id, 1_000, 15, 5)
        self.assertEqual((caught.exception.code, caught.exception.status), ('too_fast', 422))
        round_id = self.api.start_round(credentials)
        original = self.api.finish_round(credentials, round_id, 60_000, 15, 5)
        replay = self.api.finish_round(credentials, round_id, 60_000, 15, 5)
        self.assertEqual(original, replay)
        with self.assertRaises(RankError) as caught:
            self.api.finish_round(credentials, round_id, 60_001, 15, 5)
        self.assertEqual(caught.exception.code, 'round_invalid')
        status, body = self.request('/v1/rankings')
        self.assertEqual(status, 200)
        self.assertIsNone(body['me'])
        status, body = self.request('/v1/rankings', headers={'X-Device-Id': device_id})
        self.assertEqual(status, 401)
        self.assertEqual(body['error'], 'unauthorized')

    def test_signed_board_query_authenticates_full_request_target(self):
        device_id, secret = self.api.register('f' * 64)
        credentials = {'device_id': device_id, 'secret': secret}
        for board in ('standard', 'sprint', 'daily', 'legacy', 'computer'):
            result = self.api.rankings(credentials, board=board)
            self.assertEqual(result['board'], board)

    def test_stale_signed_request_is_rejected(self):
        device_id, secret = self.api.register('e' * 64)
        body = b'{}'
        timestamp = str(int(time.time()) - server.TIMESTAMP_TOLERANCE - 1)
        headers = {
            'Content-Type': 'application/json',
            'X-Device-Id': device_id,
            'X-Timestamp': timestamp,
            'X-Signature': server.sign(secret, 'POST', '/v1/round/start', timestamp, body),
        }
        status, payload = self.request('/v1/round/start', 'POST', {}, headers)
        self.assertEqual(status, 401)
        self.assertEqual(payload['error'], 'stale_timestamp')

    def test_invalid_leaderboard_limit_is_a_client_error(self):
        status, payload = self.request('/v1/leaderboard?limit=not-a-number')
        self.assertEqual(status, 400)
        self.assertEqual(payload['error'], 'bad_request')

    def test_tied_finish_rank_matches_personal_and_public_ranking(self):
        for machine_hash, expected_rank in (('6' * 64, 1), ('7' * 64, 2)):
            device_id, secret = self.api.register(machine_hash)
            credentials = {'device_id': device_id, 'secret': secret}
            result = self.api.finish_round(credentials, self.api.start_round(credentials), 60_000, 15, 12)
            rankings = self.api.rankings(credentials)
            self.assertEqual(result['round']['rank'], expected_rank)
            self.assertEqual(result['best']['rank'], rankings['me']['rank'])
            self.assertEqual(result['best']['rank'], expected_rank)

    def test_slower_round_does_not_compete_against_own_personal_best(self):
        device_id, secret = self.api.register('8' * 64)
        credentials = {'device_id': device_id, 'secret': secret}
        self.api.finish_round(credentials, self.api.start_round(credentials), 60_000, 15, 12)
        slower = self.api.finish_round(credentials, self.api.start_round(credentials), 90_000, 15, 12)
        self.assertEqual(slower['total_players'], 1)
        self.assertEqual(slower['round']['rank'], 1)
        self.assertEqual(slower['best']['rank'], 1)

    def test_generated_opponents_have_separate_counts_and_personal_next_target(self):
        player, secret = self.api.register('1' * 64)
        opponent, opponent_secret = self.api.register('2' * 64)
        creds = {'device_id': player, 'secret': secret}
        bot = {'device_id': opponent, 'secret': opponent_secret}
        self.api.finish_round(creds, self.api.start_round(creds), 90_000, 15, 18)
        self.api.finish_round(bot, self.api.start_round(bot), 60_000, 15, 12)
        store = self.httpd.RequestHandlerClass.store
        with store._connection() as connection:
            connection.execute("UPDATE scores SET source='generated',display_name='青柠汽水',skill_label='高手' WHERE device_id=?", (opponent,))
        board = self.api.rankings(creds)
        self.assertEqual((board['total_players'], board['real_players'], board['generated_players']), (1, 1, 0))
        self.assertEqual(board['me']['source'], 'player')
        self.assertEqual(board['entries'][0]['source'], 'player')
        self.assertEqual(self.api.rankings(board='computer')['entries'][0]['display_name'], '青柠汽水')
        self.assertIsNone(board['next_target'])
        self.assertIsNone(self.api.rankings(bot)['next_target'])


class ProxyAddressTests(unittest.TestCase):
    def test_trusted_chain_uses_first_untrusted_hop_from_the_right(self):
        trusted = server.trusted_proxy_networks('127.0.0.1/32,10.0.0.0/8,::1/128')
        self.assertEqual(server.connection_ip('127.0.0.1', {
            'X-Forwarded-For': '192.0.2.99,203.0.113.8,10.1.1.1'}, trusted), '203.0.113.8')
        self.assertEqual(server.connection_ip('::1', {'X-Forwarded-For': '2001:db8::1'}, trusted), '2001:db8::1')
        with self.assertRaises(server.ApiError):
            server.connection_ip('127.0.0.1', {'X-Forwarded-For': 'not-an-ip'}, trusted)


if __name__ == '__main__':
    unittest.main()

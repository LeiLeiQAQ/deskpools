"""Exercise the real unified HTTP backend with an independent ranking database.

Run from the project root:
    .venv\\Scripts\\python.exe tools/verify_ranking_sandbox.py --keep-running
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dib.rank import RankApi, RankError, current_system, sign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8072)
    parser.add_argument('--keep-running', action='store_true')
    args = parser.parse_args()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    directory = ROOT / 'build' / 'ranking-sandbox' / time.strftime('%Y%m%d-%H%M%S')
    directory.mkdir(parents=True, exist_ok=False)
    origin = f'http://127.0.0.1:{args.port}'
    rank_path = directory / 'ranking.db'
    report_path = directory / 'report.json'
    results = []
    latencies = []
    process = None
    server_log = (directory / 'server.log').open('a', encoding='utf-8')
    before = json.loads(urlopen('http://127.0.0.1:8070/v1/leaderboard', timeout=3).read())
    before.pop('generated_at')
    (directory / 'checkpoint.json').write_text(json.dumps({
        'origin': origin, 'directory': str(directory), 'live_before': before,
        'python': sys.version, 'source_sha256': hashlib.sha256((ROOT / 'ranking_server/server.py').read_bytes()).hexdigest(),
    }, indent=2), encoding='utf-8')

    def request(path, payload=None, headers=None, method=None):
        body = None if payload is None else json.dumps(payload, separators=(',', ':')).encode()
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        req = Request(origin + path, data=body, headers=request_headers, method=method)
        started = time.perf_counter()
        try:
            with urlopen(req, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        latencies.append((time.perf_counter() - started) * 1000)
        return status, json.loads(raw)

    def admin(path, payload=None):
        token = (directory / 'admin-token.txt').read_text(encoding='utf-8').strip()
        status, data = request(path, payload, {'X-Admin-Token': token})
        assert status == 200, (path, status, data)
        return data

    def check(name, function):
        started = time.perf_counter()
        try:
            function()
        except Exception as error:
            results.append({'check': name, 'status': 'FAIL', 'detail': str(error)})
            raise
        results.append({'check': name, 'status': 'PASS', 'elapsed_ms': round((time.perf_counter()-started)*1000, 2)})
        print('PASS:', name, flush=True)

    def require(condition, detail='assertion failed'):
        assert condition, detail

    def stop():
        nonlocal process
        if process is not None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            else:
                process.terminate()
            process.wait(timeout=10)
            process = None

    def start():
        nonlocal process
        env = {key: value for key, value in os.environ.items() if key != 'DIB_SITE_ADMIN_TOKEN'}
        process = subprocess.Popen([
            sys.executable, '-u', '-m', 'website.server', '--port', str(args.port),
            '--db', str(directory / 'site.db'), '--rank-db', str(rank_path),
        ], cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        (directory / 'server.pid').write_text(str(process.pid), encoding='utf-8')
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError(f'Backend exited: see {directory / "server.log"}')
            try:
                if request('/healthz')[0] == 200:
                    return
            except (URLError, OSError):
                time.sleep(.1)
        raise RuntimeError('Backend readiness timeout')

    api = RankApi(origin, '0.2.1', timeout=15)
    devices = []
    def finish(index, total_ms=60_000, count=15, shots=12):
        creds = devices[index]
        return api.finish_round(creds, api.start_round(creds), total_ms, count, shots)

    def expect_error(function, code, status):
        try:
            function()
        except RankError as error:
            require((error.code, error.status) == (code, status), (error.code, error.status))
            return
        raise AssertionError(f'Expected {code} / {status}')

    succeeded = False
    try:
        start()
        check('empty leaderboard', lambda: require(api.leaderboard()['total_players'] == 0))
        for i in range(60):
            device_id, secret = api.register(hashlib.sha256(f'sandbox-player-{i}'.encode()).hexdigest())
            devices.append({'device_id': device_id, 'secret': secret})
        check('60 device registrations', lambda: require(len({d['device_id'] for d in devices}) == 60))
        first = finish(0)
        second = finish(1)
        check('tied scores: result matches leaderboard', lambda: require(
            (first['round']['rank'], second['round']['rank'], api.rankings(devices[1])['me']['rank']) == (1, 2, 2)))
        slow = finish(0, 90_000)
        check('slower round excludes own best from opponents', lambda: require(
            (slow['round']['rank'], slow['best']['rank'], slow['is_personal_best']) == (2, 1, False)))
        fast = finish(0, 45_000)
        check('improved personal best', lambda: require(fast['best']['avg_ms'] == 3000 and fast['is_personal_best']))

        started = time.perf_counter()
        def concurrent_round(i):
            worker = RankApi(origin, '0.2.1', timeout=15)
            creds = devices[i]
            return worker.finish_round(creds, worker.start_round(creds), 30_000 + i * 1500, 15, 12)
        with ThreadPoolExecutor(max_workers=8) as pool:
            submitted = list(pool.map(concurrent_round, range(2, 60)))
        concurrent_ms = round((time.perf_counter() - started) * 1000, 2)
        check('58 round submissions with 8 concurrent workers', lambda: require(len(submitted) == 58))
        board = api.leaderboard(50)
        check('TOP 50 limit and 60 participants', lambda: require(len(board['entries']) == 50 and board['total_players'] == 60))
        check('ranking uses observed connection IP', lambda: require(
            all(entry['ip_address'] == '127.0.0.1' for entry in board['entries'])))
        check('ranking includes actual client operating system', lambda: require(
            all(entry['os_name'] == current_system() for entry in board['entries'])))
        check('ascending scores and stable ordinal ranks', lambda: require(
            [r['avg_ms'] for r in board['entries']] == sorted(r['avg_ms'] for r in board['entries'])
            and [r['rank'] for r in board['entries']] == list(range(1, 51))))
        check('personal rank outside TOP 50', lambda: require(api.rankings(devices[59])['me']['rank'] == 60))
        check('oversized limit is capped', lambda: require(len(api.leaderboard(999)['entries']) == 50))
        check('malformed limit returns 400', lambda: require(request('/v1/leaderboard?limit=invalid')[0] == 400))
        pages = [admin(f'/api/admin/ranking?limit=25&offset={offset}') for offset in (0, 25, 50)]
        ids = [item['id'] for page in pages for item in page['entries']]
        check('admin pagination: 25 / 25 / 12, no overlap', lambda: require(
            [len(p['entries']) for p in pages] == [25, 25, 12] and len(set(ids)) == 62))
        check('admin authentication required', lambda: require(request('/api/admin/ranking')[0] == 401))

        invalid_round = api.start_round(devices[2])
        check('cross-device round rejected', lambda: expect_error(
            lambda: api.finish_round(devices[3], invalid_round, 60_000, 15, 12), 'round_invalid', 422))
        check('too-fast result rejected', lambda: expect_error(
            lambda: api.finish_round(devices[2], invalid_round, 1000, 15, 12), 'too_fast', 422))
        for name, values in [('negative time', (-1, 15, 12)), ('too few icons', (60_000, 14, 12)),
                             ('too many icons', (60_000, 46, 12)), ('zero shots', (60_000, 15, 0))]:
            check(name + ' rejected', lambda values=values: expect_error(
                lambda: api.finish_round(devices[2], invalid_round, *values), 'implausible_time', 422))
        raw_payload = {'round_id': invalid_round, 'total_ms': True, 'icon_count': 15, 'shots': 12}
        body = json.dumps(raw_payload, separators=(',', ':')).encode()
        ts = str(int(time.time()))
        headers = {'X-Device-Id': devices[2]['device_id'], 'X-Timestamp': ts,
                   'X-Signature': sign(devices[2]['secret'], 'POST', '/v1/round/finish', ts, body)}
        check('boolean time rejected', lambda: require(request('/v1/round/finish', raw_payload, headers)[0] == 422))
        check('invalid signature rejected', lambda: require(request('/v1/round/start', {}, {
            'X-Device-Id': devices[0]['device_id'], 'X-Timestamp': ts, 'X-Signature': '0' * 64})[0] == 401))
        stale = str(int(time.time()) - 600)
        check('stale timestamp rejected', lambda: require(request('/v1/round/start', {}, {
            'X-Device-Id': devices[0]['device_id'], 'X-Timestamp': stale,
            'X-Signature': sign(devices[0]['secret'], 'POST', '/v1/round/start', stale, b'{}')})[0] == 401))
        check('invalid requests save no scores', lambda: require(admin('/api/admin/overview')['ranking']['scores'] == 62))

        expired = api.start_round(devices[4])
        with sqlite3.connect(rank_path) as db:
            db.execute('UPDATE rounds SET expires_at=? WHERE round_id=?', (time.time() - 1, expired))
        check('expired round rejected', lambda: expect_error(
            lambda: api.finish_round(devices[4], expired, 60_000, 15, 12), 'round_invalid', 422))
        old_round = api.start_round(devices[5])
        api.start_round(devices[5])
        check('superseded round rejected', lambda: expect_error(
            lambda: api.finish_round(devices[5], old_round, 60_000, 15, 12), 'round_invalid', 422))

        race_round = api.start_round(devices[58])
        def replay(_):
            worker = RankApi(origin, '0.2.1', timeout=15)
            try:
                worker.finish_round(devices[58], race_round, 90_000, 15, 12)
                return 200
            except RankError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            race = list(pool.map(replay, range(2)))
        check('concurrent duplicate: one accepted, one rejected', lambda: require(sorted(race) == [200, 422]))
        check('no duplicate row saved', lambda: require(admin('/api/admin/overview')['ranking']['scores'] == 63))

        all_scores = admin('/api/admin/ranking?limit=100')['entries']
        best = min(all_scores, key=lambda r: r['avg_ms'])
        admin('/api/admin/ranking/score', {'score_id': best['id'], 'hidden': True})
        check('hidden score removed from ranking', lambda: require(api.leaderboard()['total_players'] == 59))
        admin('/api/admin/ranking/score', {'score_id': best['id'], 'hidden': False})
        check('restored score reappears', lambda: require(api.leaderboard()['total_players'] == 60))
        admin('/api/admin/ranking/device', {'device_id': devices[59]['device_id'], 'disabled': True})
        check('disabled player excluded', lambda: require(api.leaderboard()['total_players'] == 59))
        check('disabled player cannot register again', lambda: expect_error(lambda: api.register(
            hashlib.sha256(b'sandbox-player-59').hexdigest()), 'device_disabled', 403))
        admin('/api/admin/ranking/device', {'device_id': devices[59]['device_id'], 'disabled': False})
        check('enabled player restored', lambda: require(api.rankings(devices[59])['me']['rank'] == 60))

        prior = api.leaderboard(); prior.pop('generated_at')
        stop(); start()
        restored = api.leaderboard(); restored.pop('generated_at')
        check('rankings persist across process restart', lambda: require(restored == prior))
        check('credentials remain valid after restart', lambda: require(api.rankings(devices[59])['me']['rank'] == 60))
        live = json.loads(urlopen('http://127.0.0.1:8070/v1/leaderboard', timeout=3).read()); live.pop('generated_at')
        check('live leaderboard unchanged', lambda: require(live == before))
        succeeded = True
        report = {'status': 'PASS', 'origin': origin, 'results': results, 'players': 60, 'scores': 63,
                  'concurrent_workers': 8, 'concurrent_rounds': 58, 'concurrent_elapsed_ms': concurrent_ms,
                  'live_board_unchanged': True, 'server_pid': process.pid,
                  'http_latency_samples': len(latencies), 'http_latency_p95_ms': round(sorted(latencies)[int(len(latencies)*.95)], 2)}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (ROOT / 'build' / 'ranking-sandbox' / 'latest.txt').write_text(str(directory), encoding='utf-8')
        print(json.dumps({'status': 'PASS', 'checks': len(results), 'origin': origin, 'report': str(report_path)}), flush=True)
    finally:
        if not (succeeded and args.keep_running):
            stop()
        if not succeeded:
            report_path.write_text(json.dumps({'status': 'FAIL', 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
        server_log.close()


if __name__ == '__main__':
    main()

"""Manage generated computer opponents in an existing ranking database.

Uses the application's SQLite schema and ranking rules without changing the
public ranking rules. The source column identifies generated records; clients
show the number of computer opponents once. Computer opponents have stable
nicknames and difficulty levels; they have no invented IP or operating system.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import secrets
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ranking_server.server import MIN_AVG_MS, MIN_BALLS, MAX_BALLS, MAX_TOTAL_MS, RankingStore

BATCH_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
PREFIX = 'deskpools-preset-ranking-v1'
LEGACY_LABEL = '预置成绩 · '
NICKNAMES = (
    '青柠汽水', '半杯可乐', '晚风', '纸飞机', '七号球', '橘子海', '松间月', '不加糖',
    '一杆入魂', '午后拿铁', '海盐饼干', '路过的猫', '山野来信', '下班再打', '慢半拍', '向晚',
    '半岛铁盒', '薄荷蓝', '小满', '远山', '落日邮差', '白日梦', '三分热度', '球不听话',
    '小熊软糖', '乌龙茶', '月亮不营业', '木棉', '摸鱼选手', '听雨', '空山新雨', '咸鱼翻身',
    '银河漫游', '风也温柔', '南栀', '认真瞄一杆', '芝士年糕', '一口气', '云边散步', '桔梗',
    '北岛', '冰镇西瓜', '日落之前', '不急慢慢来', '星河入梦', '阿栗', '清风入怀', '再来一局',
    '路灯下', '冬日热可可', '轻舟', '晴空', '没瞄准', '一颗柚子', '灯火可亲', '随风',
    '想起个名字', '清台就下班', '糖炒栗子', '周末愉快',
)


def skill_label(avg_ms):
    for limit, label in ((3000, '大师'), (5000, '高手'), (8000, '熟练'), (12000, '进阶')):
        if avg_ms < limit:
            return label
    return '入门'


def nickname(batch, index):
    names = list(NICKNAMES)
    random.Random(f'{PREFIX}:names:{batch}').shuffle(names)
    suffix = f'·{index // len(names) + 1:02d}' if index >= len(names) else ''
    return names[index % len(names)] + suffix


def identities(batch, index):
    key = f'{PREFIX}:{batch}:{index}'
    return (str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
            hashlib.sha256(key.encode()).hexdigest(),
            str(uuid.uuid5(uuid.NAMESPACE_URL, key + ':round')))


def records(batch, count, now):
    rng = random.Random(f'{PREFIX}:natural-v2:{batch}')
    result = []
    for index in range(count):
        device_id, machine_hash, round_id = identities(batch, index)
        icons = rng.choices((15, 20, 24, 30, 35, 40, 45), weights=(4, 6, 8, 17, 20, 20, 25))[0]
        # Skill controls misses and thinking time together. Summing individual
        # shots avoids a hard minimum that crowds many records onto one time.
        skill = rng.betavariate(2.2, 2.6)
        shots = max(5, round(icons * (.50 + 1.85 * (1 - skill)) * rng.uniform(.88, 1.12)))
        aiming = 450 + (1 - skill) ** 2 * 5300
        total_ms = 0
        for shot in range(shots):
            total_ms += round(rng.triangular(650, 2600, 1500))  # flight and settling
            if shot:
                total_ms += round(rng.triangular(aiming * .45, aiming * 1.8, aiming))
                total_ms += rng.randint(250, 1100)  # charging
                if rng.random() < .07:
                    total_ms += rng.randint(2000, 11000)  # occasional hesitation
        # Recent activity follows local waking hours, with occasional late rounds.
        day = rng.choices((0, 1, 2), weights=(5, 3, 2))[0]
        hour = rng.choices(tuple(range(24)), weights=(1,1,1,1,1,1,2,3,5,6,7,8,9,7,9,10,9,10,12,12,10,8,5,3))[0]
        local = now + timedelta(hours=8)
        local = local.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60)) - timedelta(days=day)
        if local >= now + timedelta(hours=8):
            local -= timedelta(days=1)
        finished = local - timedelta(hours=8)
        created = finished.isoformat(timespec='seconds').replace('+00:00', 'Z')
        assert MIN_BALLS <= icons <= MAX_BALLS
        assert MIN_AVG_MS <= total_ms / icons and total_ms <= MAX_TOTAL_MS
        result.append({
            'device_id': device_id, 'machine_hash': machine_hash, 'round_id': round_id,
            'total_ms': total_ms, 'icon_count': icons, 'shots': shots,
            'avg_ms': total_ms / icons, 'created_at': created,
            'started_at': finished.timestamp() - total_ms / 1000,
            'finished_at': finished.timestamp(), 'ip_address': '', 'os_name': '',
            'display_name': nickname(batch, index), 'skill_label': skill_label(total_ms / icons),
            'source': 'generated',
        })
    return result


def batch_rows(connection, batch, count):
    rows = []
    for index in range(count):
        device_id, machine_hash, round_id = identities(batch, index)
        device = connection.execute('SELECT machine_hash FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if device is not None and device[0] != machine_hash:
            raise ValueError('Preset device ID belongs to a different machine hash')
        if device is not None:
            score = connection.execute(
                'SELECT os_name,source FROM scores WHERE device_id=? AND round_id=?', (device_id, round_id)
            ).fetchone()
            if score is None:
                continue
            if not (score[0].startswith(LEGACY_LABEL) or score[1] == 'generated'):
                raise ValueError('Existing record is not identified as generated')
            rows.append((device_id, round_id))
    return rows


def manage(db, action, batch, count, backup_dir):
    db = Path(db).resolve(strict=True)
    if not db.is_file():
        raise ValueError('Expected an existing ranking database file')
    if not BATCH_RE.fullmatch(batch) or not 1 <= count <= 500:
        raise ValueError('Use a simple batch name and a count between 1 and 500')
    connection = sqlite3.connect(db, timeout=15)
    connection.execute('PRAGMA busy_timeout=15000')
    connection.execute('PRAGMA foreign_keys=ON')
    backup = None
    now = datetime.now(timezone.utc)
    try:
        # Back up via SQLite, including committed WAL pages, before any mutation.
        if action != 'status':
            backup_dir = Path(backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f'rankings-before-{action}-{batch}-{now.strftime("%Y%m%dT%H%M%S%fZ")}.db'
            with closing(sqlite3.connect(backup)) as target:
                connection.backup(target)
        connection.execute('BEGIN IMMEDIATE' if action != 'status' else 'BEGIN')
        existing = batch_rows(connection, batch, count)
        changed = 0
        if action in ('seed', 'refresh', 'identities'):
            if action in ('refresh', 'identities') and len(existing) != count:
                raise ValueError('Refresh requires a complete, identified batch')
            for entry in records(batch, count, now):
                if (entry['device_id'], entry['round_id']) in existing:
                    if action == 'identities':
                        # Preserve each existing result and its timestamps; only
                        # replace fictitious identity fields in the identified batch.
                        avg = connection.execute(
                            'SELECT avg_ms FROM scores WHERE device_id=? AND round_id=?',
                            (entry['device_id'], entry['round_id'])).fetchone()[0]
                        connection.execute(
                            "UPDATE scores SET ip_address='',os_name='',display_name=?,skill_label=?,source='generated' "
                            'WHERE device_id=? AND round_id=?',
                            (entry['display_name'], skill_label(avg), entry['device_id'], entry['round_id']))
                        changed += 1
                    if action == 'refresh':
                        connection.execute(
                            'UPDATE scores SET total_ms=:total_ms,icon_count=:icon_count,shots=:shots,'
                            'avg_ms=:avg_ms,created_at=:created_at,ip_address=:ip_address,os_name=:os_name,source=:source,'
                            'display_name=:display_name,skill_label=:skill_label '
                            'WHERE device_id=:device_id AND round_id=:round_id', entry)
                        connection.execute(
                            'UPDATE rounds SET started_at=:started_at,finished_at=:finished_at,'
                            'expires_at=:finished_at+3600 WHERE device_id=:device_id AND round_id=:round_id', entry)
                        connection.execute(
                            'UPDATE devices SET created_at=:created_at,last_seen_at=:created_at '
                            'WHERE device_id=:device_id', entry)
                        changed += 1
                    continue
                connection.execute(
                    'INSERT INTO devices(device_id,machine_hash,secret,created_at,last_seen_at) VALUES (?,?,?,?,?)',
                    (entry['device_id'], entry['machine_hash'], secrets.token_hex(32), entry['created_at'], entry['created_at']))
                connection.execute(
                    'INSERT INTO rounds(round_id,device_id,started_at,expires_at,finished_at) VALUES (?,?,?,?,?)',
                    (entry['round_id'], entry['device_id'], entry['started_at'], entry['finished_at'] + 3600, entry['finished_at']))
                connection.execute(
                    'INSERT INTO scores(round_id,device_id,total_ms,icon_count,shots,avg_ms,created_at,ip_address,os_name,source,display_name,skill_label) '
                    'VALUES (:round_id,:device_id,:total_ms,:icon_count,:shots,:avg_ms,:created_at,:ip_address,:os_name,:source,:display_name,:skill_label)', entry)
                changed += 1
        elif action == 'remove':
            for device_id, round_id in existing:
                connection.execute('DELETE FROM scores WHERE device_id=? AND round_id=?', (device_id, round_id))
                connection.execute('DELETE FROM rounds WHERE device_id=? AND round_id=?', (device_id, round_id))
                # Keep the device if any other data was subsequently associated with it.
                connection.execute(
                    'DELETE FROM devices WHERE device_id=? '
                    'AND NOT EXISTS(SELECT 1 FROM rounds WHERE device_id=?) '
                    'AND NOT EXISTS(SELECT 1 FROM scores WHERE device_id=?)', (device_id, device_id, device_id))
                changed += 1
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
        remaining = len(batch_rows(connection, batch, count))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    store = RankingStore(db)
    return {'status': 'ok', 'action': action, 'batch': batch, 'changed': changed,
            'batch_players': remaining, 'backup': str(backup) if backup else None,
            'data_source': 'generated computer opponents, source is retained and counts disclosed separately',
            'overview': store.admin_overview(),
            'top_entries': store.rankings(limit=3)['entries']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--action', choices=('seed', 'refresh', 'identities', 'status', 'remove'), default='seed')
    parser.add_argument('--batch', default='launch-20261117')
    parser.add_argument('--count', type=int, default=60)
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    backup_dir = args.backup_dir or args.db.parent / 'ranking-import-backups'
    print(json.dumps(manage(args.db, args.action, args.batch, args.count, backup_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

"""Shared, versioned game rules. This module has no Windows dependencies."""
from datetime import datetime, timedelta, timezone

RULES_VERSION = 3
CHALLENGE_BALLS = 15
SPRINT_MS = 90_000
MODES = {
    'practice': ('自由练习', '自由清台，可暂停停表；成绩保存在本机。'),
    'standard': ('标准排位', '15 球标准台面，清台计时；暂停期间继续计时。'),
    'sprint': ('90 秒挑战', '90 秒内击入更多球；同分比较用时和杆数，暂停继续计时。'),
    'daily': ('每日挑战', '每天统一的 15 球球阵，独立日榜；暂停继续计时。'),
}
BOARDS = {'standard': '标准排位', 'sprint': '90 秒挑战', 'daily': '每日挑战',
          'legacy': '旧版清台', 'computer': '电脑挑战'}
CUE_SKINS = {'wood': '经典木纹', 'jade': '翡翠绿', 'midnight': '午夜蓝'}


def challenge_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def rules_for(mode, day=None):
    if mode not in MODES:
        raise ValueError('Unknown game mode')
    return {'mode': mode, 'rules_version': RULES_VERSION,
            'day_key': (day or challenge_day()) if mode == 'daily' else ''}

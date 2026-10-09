"""Which notifications reach a person, and on which channel. One list, read at delivery.

CEO, 30 September 2026: "All the switches must work as listed or shown in
their profiles. If they put on or off something for discord or push or email
or in app, it must work as each user set it."

Until then the grid on Settings > Notifications was decorative: the server read
none of it. Every notification landed in the inbox, went to Discord for anybody
connected, never went by email and never by push, whatever the switches said.
Some rows (followers, direct messages, newsletter) had nothing behind them and
some real notifications (team invites, payouts, identity checks) had no row.

So:
  ROWS              what the settings screen draws, in order; the screen reads
                    it from the server, so the two cannot drift
  CATEGORY_ROW      every category `create_notification` is called with, to
                    the row that governs it (tools/check-notification-rows.py
                    fails on a category with no row)
  wants()           the one question delivery asks: may this person be sent
                    this category on this channel?

Stored in UserSetting.data['notifications'] as `<row>__<channel>` booleans,
which is what the screen already saved. A key never set falls back to the
row's default. Keys from the old grid are read through LEGACY so nobody's
earlier choice is lost.
"""
from django.conf import settings

CHANNELS = ('in_app', 'email', 'push', 'discord')
# Shown, never switchable: there is no SMS sender yet.
COMING_SOON_CHANNELS = ('sms',)

# id, default per channel, locked channels (always on), module flag (hidden
# while that module is closed).
ROWS = [
    {'id': 'tournaments', 'defaults': {'in_app': True, 'email': True, 'push': True, 'discord': False}},
    {'id': 'teams', 'defaults': {'in_app': True, 'email': True, 'push': True, 'discord': False}},
    {'id': 'events', 'defaults': {'in_app': True, 'email': True, 'push': True, 'discord': False}},
    {'id': 'wallet', 'defaults': {'in_app': True, 'email': True, 'push': True, 'discord': False}},
    {'id': 'mentions', 'defaults': {'in_app': True, 'email': False, 'push': True, 'discord': False}},
    {'id': 'followers', 'defaults': {'in_app': True, 'email': False, 'push': False, 'discord': False}},
    {'id': 'dms', 'defaults': {'in_app': True, 'email': False, 'push': True, 'discord': False}},
    {'id': 'marketplace', 'module': 'MARKETPLACE_ENABLED',
     'defaults': {'in_app': True, 'email': True, 'push': True, 'discord': False}},
    {'id': 'anime', 'module': 'ANIME_ENABLED',
     'defaults': {'in_app': True, 'email': False, 'push': True, 'discord': False}},
    # Security and account notices: somebody must always be able to find out
    # that their payout was refused or their account was signed into.
    {'id': 'account', 'locked': ('in_app', 'email'),
     'defaults': {'in_app': True, 'email': True, 'push': True, 'discord': False}},
]
ROW_IDS = [r['id'] for r in ROWS]
_ROW = {r['id']: r for r in ROWS}

CATEGORY_ROW = {
    'tournament': 'tournaments', 'match': 'tournaments', 'dispute': 'tournaments',
    'prizes': 'tournaments',
    'team': 'teams',
    'event': 'events',
    'wallet': 'wallet',
    # Decisions about somebody's money or identity: always told (account row).
    'payout': 'account', 'kyc': 'account',
    'mention': 'mentions',
    'follower': 'followers',
    'dm': 'dms',
    'marketplace': 'marketplace',
    'anime_room': 'anime', 'anime_promo': 'anime', 'anime_chapter': 'anime',
    'system': 'account',
    # "Tell me when it opens" (inbox 421): asked for by the person, one press,
    # and stopped from the same page, so it rides with account notices.
    'roadmap': 'account',
}

# The old grid's rows, so a choice made before 30 September still counts.
LEGACY = {
    'tournament_invite': 'tournaments', 'tournament_result': 'tournaments',
    'event_reminder': 'events', 'wallet_activity': 'wallet',
    'marketplace_orders': 'marketplace', 'dms': 'dms', 'mentions': 'mentions',
    'followers': 'followers',
}


def row_for(category):
    """The row that governs a category. Unknown categories are account notices."""
    return CATEGORY_ROW.get(str(category or ''), 'account')


def row_open(row):
    flag = row.get('module')
    return not flag or bool(getattr(settings, flag, False))


def _stored(user):
    from .models import UserSetting
    obj = UserSetting.objects.filter(user=user).only('data').first()
    return dict(((obj.data or {}) if obj else {}).get('notifications') or {})


def choice(stored, row_id, channel):
    """What this person chose for one row and channel, with the defaults filled in."""
    row = _ROW[row_id]
    if channel in row.get('locked', ()):
        return True
    key = '%s__%s' % (row_id, channel)
    if key in stored:
        return bool(stored[key])
    # A choice made on the old grid: any old row that maps here and was set.
    old = [bool(stored['%s__%s' % (legacy, channel)]) for legacy, new in LEGACY.items()
           if new == row_id and '%s__%s' % (legacy, channel) in stored]
    if old:
        return any(old)
    return bool(row['defaults'].get(channel, False))


def wants(user, category, channel, stored=None):
    """May `user` be sent a `category` notification on `channel`?"""
    row_id = row_for(category)
    row = _ROW[row_id]
    if not row_open(row) or channel not in CHANNELS:
        return False
    return choice(_stored(user) if stored is None else stored, row_id, channel)


def matrix_for(user):
    """Every row and channel as this person has it, for the settings screen."""
    stored = _stored(user)
    rows = []
    for row in ROWS:
        if not row_open(row):
            continue
        rows.append({
            'id': row['id'],
            'locked': list(row.get('locked', ())),
            'channels': {c: choice(stored, row['id'], c) for c in CHANNELS},
        })
    return {'channels': list(CHANNELS), 'coming_soon': list(COMING_SOON_CHANNELS), 'rows': rows}

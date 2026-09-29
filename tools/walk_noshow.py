#!/usr/bin/env python
"""Fixtures for walking the no-show refund choice, every role (inbox 330, 338).

`--setup` makes, on the LOCAL sqlite database only:
  * accounts, all `walk_ns_*`, password `walk-con-2026`, PIN 2468: an
    organiser, three players with 100 VENT COINS each, and a stranger;
  * two paid tournaments (10 VC), check-in open now (60 minutes before a start
    30 minutes away), forfeit on:
      walk-noshow-keep     the default: a no-show keeps no refund
      walk-noshow-refund   the organiser refunds no-shows
    Players one and two have joined and paid through the real join endpoint,
    so their ledger lines are real. Player three has not joined, to walk the
    registration review and receipt.

Everything is named walk_ns_* / walk-noshow-* so it can be found and removed.

  DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_noshow.py --setup
"""
import os
import sys
import uuid
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from vent_auth.models import Games, Users, UserWallet  # noqa: E402
from vent_tournament.models import Tournament  # noqa: E402

PASSWORD = 'walk-con-2026'  # local dev accounts only
PIN = '2468'


def refuse_production():
    engine = settings.DATABASES['default']['ENGINE']
    if 'sqlite' not in engine or not settings.DEBUG:
        sys.exit('walk_noshow.py runs against the local sqlite database with DEBUG only.')


def person(handle, coins=0):
    username = 'walk_ns_%s' % handle
    user = Users.objects.filter(username=username).first()
    if user is None:
        user = Users.objects.create(
            username=username, email='%s@walk.test' % username,
            full_name=handle.replace('_', ' ').title(), is_active=True)
    user.set_password(PASSWORD)
    user.login_session_token = ('wn%s' % uuid.uuid4().hex)[:16]
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user)
    wallet.wallet_balance = coins
    wallet.pin_hash = make_password(PIN)
    wallet.kyc_verified = True
    wallet.save()
    return user


def cup(slug, title, organiser, refund):
    # By title as well: a cup renamed or re-slugged since the last run keeps
    # its old registrations otherwise, and trips the overlap warning.
    Tournament.objects.filter(slug=slug).delete()
    Tournament.objects.filter(tournament_title=title, tournament_creator=organiser).delete()
    game = Games.objects.get_or_create(game_title='eFootball 2026')[0]
    now = timezone.now()
    t = Tournament.objects.create(
        tournament_title=title, tournament_game=game, tournament_creator=organiser,
        tournament_type='online', tournament_access='individual',
        tournament_visibility='public', entry_fee='Paid', entry_fee_price=10,
        prize_type='no_prize', bracket_type='single_elimination',
        start_date_and_time=now + timedelta(minutes=30),
        end_date_and_time=now + timedelta(hours=4),
        is_draft=False, status='registration_open', max_number_of_teams=8,
        options={'check_in_minutes': 60, 'forfeit_without_check_in': True,
                 'refund_no_shows': refund},
    )
    if t.slug != slug:
        Tournament.objects.filter(pk=t.pk).update(slug=slug)
        t.refresh_from_db()
    return t


def join(user, t):
    c = APIClient(SERVER_NAME='127.0.0.1')
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    res = c.post('/tournament/join-tournament/', {'tournament_id': t.tournament_id, 'pin': PIN,
                                              'acknowledge_overlap': True},
                 format='json')
    if res.status_code != 201:
        sys.exit('join failed for %s: %s %s' % (user.username, res.status_code, res.content[:200]))


def setup():
    refuse_production()
    org = person('organiser')
    players = [person('player%d' % i, coins=100) for i in (1, 2, 3)]
    person('stranger')
    for slug, title, refund in (('walk-noshow-keep', 'No Show Keep Cup', False),
                                ('walk-noshow-refund', 'No Show Refund Cup', True)):
        t = cup(slug, title, org, refund)
        join(players[0], t)
        join(players[1], t)
        print('%s  id %s  refund_no_shows=%s' % (t.slug, t.tournament_id, refund))
    print('accounts: walk_ns_organiser, walk_ns_player1..3, walk_ns_stranger; password %s' % PASSWORD)


if __name__ == '__main__':
    if '--setup' in sys.argv:
        setup()
    else:
        print(__doc__)

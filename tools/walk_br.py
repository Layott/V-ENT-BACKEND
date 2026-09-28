#!/usr/bin/env python
"""Fixtures for walking battle royale and the placing formats, every role.

CEO, 28 September 2026 (inbox 298 to 304): "ensure every single thing is
properly tested from every user role pov, every button page, sub page,
feature, flow". Gates in V-ENT/gates/45-battle-royale-and-formats.md.

`--setup` makes, on the LOCAL sqlite database only:

  * accounts, all `walk_br_*`, password `walk-con-2026`: an organiser, a
    scorekeeper, an admin, a stranger, eight squad captains each with two
    squad members, and eight lone players;
  * one tournament per thing to walk:
      walk-br-cup            one-format battle royale, eight squads (Free Fire)
      walk-br-chain          two lobbies into a final lobby, with a head start
      walk-stepladder-cup    stepladder, five players
      walk-page-cup          page playoff, four players
      walk-wso-cup           winner stays on, four players, streak of two
      walk-every-place-cup   single elimination playing for every place, eight

`--tokens` prints each account's bearer token for driving the API by hand.

Everything is named walk_br_* / walk-* so it can be found and removed.

  DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_br.py --setup
"""
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402
from django.utils import timezone  # noqa: E402

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet  # noqa: E402
from vent_tournament import stage_settings  # noqa: E402
from vent_tournament.models import (Tournament, TournamentRegistration,  # noqa: E402
                                    TournamentStaff, TournamentStage)

PASSWORD = 'walk-con-2026'  # local dev accounts only
PIN = '2468'


def refuse_production():
    engine = settings.DATABASES['default']['ENGINE']
    if 'sqlite' not in engine or not settings.DEBUG:
        sys.exit('walk_br.py runs against the local sqlite database with DEBUG only.')


def person(handle, admin_role=None):
    username = 'walk_br_%s' % handle
    user = Users.objects.filter(username=username).first()
    if user is None:
        user = Users.objects.create(
            username=username, email='%s@walk.test' % username,
            full_name=handle.replace('_', ' ').title(),
            login_session_token=('wb%s' % uuid.uuid4().hex)[:16],
            login_session_created_at=timezone.now(), is_active=True,
            is_staff=admin_role is not None, admin_role=admin_role,
            role='admin' if admin_role else 'user')
    if not user.password or not user.has_usable_password():
        user.set_password(PASSWORD)
        user.save(update_fields=['password'])
    if admin_role:
        user.login_session_2fa_at = timezone.now()
        user.save(update_fields=['login_session_2fa_at'])
    user.login_session_token = ('wb%s' % uuid.uuid4().hex)[:16]
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_token', 'login_session_created_at'])
    if not UserWallet.objects.filter(user=user).exists():
        UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                                  wallet_balance=0, pin_hash=make_password(PIN))
    return user


def game(title):
    g = Games.objects.filter(game_title=title).first()
    return g or Games.objects.create(game_title=title)


def squad(i, fire):
    captain = person('cap%d' % i)
    members = [person('m%d%s' % (i, x)) for x in 'ab']
    name = 'Walk Squad %s' % 'ABCDEFGH'[i - 1]
    team = Teams.objects.filter(team_name=name).first()
    if team is None:
        team = Teams.objects.create(team_name=name, game=fire, description='Walk squad',
                                    team_creator=captain, team_owner=captain,
                                    penalty_points=0, number_of_members=3)
    for u, cap in [(captain, True)] + [(m, False) for m in members]:
        TeamMembers.objects.get_or_create(team=team, user=u, defaults={'is_captain': cap})
    return team


def tournament(slug, title, creator, g, bracket_type, access='individual', options=None):
    t = Tournament.objects.filter(tournament_title=title).first()
    if t is not None:
        t.stages.all().delete()
        t.bracket_matches.all().delete()
        t.registrations.all().delete()
        t.delete()
    now = timezone.now()
    t = Tournament.objects.create(
        tournament_title=title, tournament_creator=creator, tournament_game=g,
        tournament_type='online', tournament_access=access,
        tournament_visibility='public', entry_fee='Free', entry_fee_price=0,
        prize_type='no_prize', bracket_type=bracket_type,
        start_date_and_time=now + timezone.timedelta(hours=2),
        end_date_and_time=now + timezone.timedelta(days=1),
        is_draft=False, status='registration_open', options=options or {},
        tournament_description='A walk fixture for %s.' % bracket_type.replace('_', ' '),
        # No check-in window: the walk draws at once.
    )
    t.options = dict(t.options or {}, check_in_minutes=0)
    t.save(update_fields=['options'])
    return t


def enter(t, entrants, team=False):
    for seed, e in enumerate(entrants, start=1):
        kw = {'team': e} if team else {'user': e}
        TournamentRegistration.objects.create(tournament=t, status='confirmed', seed=seed, **kw)


def setup():
    refuse_production()
    org = person('organiser')
    keeper = person('keeper')
    person('stranger')
    person('admin', admin_role='super_admin')
    fire = game('Free Fire')
    efoot = game('eFootball')
    teams = [squad(i, fire) for i in range(1, 9)]
    solos = [person('solo%d' % i) for i in range(1, 9)]

    t = tournament('walk-br-cup', 'Walk BR Cup', org, fire, 'battle_royale', access='team')
    enter(t, teams, team=True)
    TournamentStaff.objects.get_or_create(tournament=t, user=keeper)

    t = tournament('walk-br-chain', 'Walk BR Chain', org, fire, 'battle_royale', access='team')
    enter(t, teams, team=True)
    TournamentStaff.objects.get_or_create(tournament=t, user=keeper)
    TournamentStage.objects.create(
        tournament=t, order=0, label='Lobbies', format='battle_royale', advances=2,
        settings=stage_settings.clean('battle_royale', {
            'lobby_size': 4, 'maps': 2, 'advance_by': 'lobby', 'carry_over': {'1': 5}}))
    TournamentStage.objects.create(
        tournament=t, order=1, label='Grand final lobby', format='battle_royale',
        settings=stage_settings.clean('battle_royale', {'lobby_size': 8, 'maps': 2,
                                                        'match_point': 30}))

    for title, fmt, n, opts in (
            ('Walk Stepladder Cup', 'stepladder', 5, {}),
            ('Walk Page Cup', 'page_playoff', 4, {}),
            ('Walk WSO Cup', 'winner_stays_on', 4, {'streak_target': 2}),
            ('Walk Every Place Cup', 'single_elimination', 8, {'every_place': True})):
        t = tournament(None, title, org, efoot, fmt, options=opts)
        enter(t, solos[:n])
        TournamentStaff.objects.get_or_create(tournament=t, user=keeper)

    for t in Tournament.objects.filter(tournament_title__startswith='Walk ').order_by('tournament_id'):
        if t.tournament_title in ('Walk BR Cup', 'Walk BR Chain', 'Walk Stepladder Cup', 'Walk Page Cup',
                                  'Walk WSO Cup', 'Walk Every Place Cup'):
            print('%-22s /tournaments/%s' % (t.tournament_title, t.slug))
    print('accounts: walk_br_organiser, walk_br_keeper, walk_br_admin, walk_br_stranger,')
    print('          walk_br_cap1..8 (+ walk_br_m1a, m1b ...), walk_br_solo1..8; password', PASSWORD)


def br_live():
    """A battle royale half played, for the phone: two lobbies, a room on
    MATCH 1, lobby 1's MATCH 1 entered, everything else open."""
    refuse_production()
    from rest_framework.test import APIClient
    from vent_tournament import br_engine, stage_engine
    from django.db import transaction

    org = person('organiser')
    keeper = person('keeper')
    fire = game('Free Fire')
    teams = [squad(i, fire) for i in range(1, 9)]
    t = tournament('walk-br-live', 'Walk BR Live', org, fire, 'battle_royale', access='team')
    enter(t, teams, team=True)
    TournamentStaff.objects.get_or_create(tournament=t, user=keeper)
    stage = br_engine.ensure_stage(t)
    stage.settings = stage_settings.clean('battle_royale', {'lobby_size': 4, 'maps': 3})
    stage.save(update_fields=['settings'])
    with transaction.atomic():
        stage_engine.draw(stage, org)
    lobby = stage.br_lobbies.get(number=1)
    m1 = lobby.maps.get(number=1)
    m1.room_code, m1.room_password, m1.map_name = '5512890', 'drop7', 'Bermuda'
    m1.save(update_fields=['room_code', 'room_password', 'map_name'])
    seated = [s.registration for s in lobby.seats.order_by('seat')]
    br_engine.enter_results(m1, [{'registration_id': r.id, 'placement': i + 1, 'kills': 4 - i}
                                 for i, r in enumerate(seated)], org)
    print('Walk BR Live           /tournaments/%s' % t.slug)


def tokens():
    refuse_production()
    for u in Users.objects.filter(username__startswith='walk_br_').order_by('username'):
        print('%-20s %s' % (u.username, u.login_session_token))


if __name__ == '__main__':
    if '--setup' in sys.argv:
        setup()
    elif '--br-live' in sys.argv:
        br_live()
    elif '--tokens' in sys.argv:
        tokens()
    else:
        print(__doc__)

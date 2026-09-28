#!/usr/bin/env python
"""Walk data for the second bracket walk (inbox 289, gates/44).

The first walk (tools/walk_stages.py) pressed groups into a playoff and Swiss
into double elimination. This makes the tournaments for everything it did not
press: GSL, home and away, one-format single and double elimination, invited
entrants, the other placements, Swiss by hand, a no-show on screen, a team
tournament and a paid one. Stages are left for the builder where the walk is
about the builder; the rest are planned here so the walk starts at the part
it is testing. Everything is named walk_*. Local sqlite only.

  DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_brackets_2.py --setup
  ... --state                            print slugs and who is who
"""
import json
import os
import sys
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')

import django  # noqa: E402

django.setup()

from django.utils import timezone  # noqa: E402

from vent_auth.models import Games, TeamMembers, Teams  # noqa: E402
from vent_tournament import stage_settings  # noqa: E402
from vent_tournament.models import (Tournament, TournamentRegistration,  # noqa: E402
                                    TournamentStaff, TournamentStage)

from walk_event import WALK_PASSWORD, person  # noqa: E402

STATE = os.path.join(ROOT, 'tools', '.walk-brackets-2-state.json')


def tournament(title, game, creator, bracket_type, *, access='individual',
               fee=0, players=(), teams=()):
    t = Tournament.objects.filter(tournament_title=title).first()
    now = timezone.now()
    if t is None:
        t = Tournament.objects.create(
            tournament_title=title, tournament_game=game, tournament_creator=creator,
            tournament_type='online', tournament_access=access,
            tournament_visibility='public',
            entry_fee='Paid' if fee else 'Free', entry_fee_price=fee,
            prize_type='no_prize', bracket_type=bracket_type,
            start_date_and_time=now - timedelta(minutes=5),
            end_date_and_time=now + timedelta(days=2),
            is_draft=False, status='registration_open',
            tournament_description='A walk tournament for the second bracket walk.')
    for i, user in enumerate(players, start=1):
        reg, _ = TournamentRegistration.objects.get_or_create(
            tournament=t, user=user, defaults={'status': 'confirmed', 'seed': i})
        if reg.status != 'confirmed' or reg.seed != i:
            reg.status, reg.seed = 'confirmed', i
            reg.save(update_fields=['status', 'seed'])
    for i, team in enumerate(teams, start=1):
        reg, _ = TournamentRegistration.objects.get_or_create(
            tournament=t, team=team, defaults={'status': 'confirmed', 'seed': i})
        if reg.status != 'confirmed' or reg.seed != i:
            reg.status, reg.seed = 'confirmed', i
            reg.save(update_fields=['status', 'seed'])
    # Everybody has checked in to the tournament, as they would have on the
    # day; the draw refuses while anybody entered has not.
    TournamentRegistration.objects.filter(tournament=t, checked_in_at__isnull=True).update(
        checked_in_at=now)
    return t


def players(prefix, n):
    return [person('%s_%02d' % (prefix, i)) for i in range(1, n + 1)]


def team(name, owner, members, game):
    t = Teams.objects.filter(team_name=name).first()
    if t is None:
        t = Teams.objects.create(team_name=name, team_owner=owner, team_creator=owner,
                                 game=game)
    for m in [owner] + members:
        TeamMembers.objects.get_or_create(
            team=t, user=m, defaults={'role': 'owner' if m == owner else 'member',
                                      'is_captain': m == owner})
    return t


def stage(t, order, fmt, label, *, advances=0, groups=0, settings=None,
          placement='cross'):
    s = t.stages.filter(order=order).first()
    if s is None:
        s = TournamentStage.objects.create(
            tournament=t, order=order, label=label, format=fmt, advances=advances,
            groups=groups, settings=stage_settings.clean(fmt, settings or {}),
            placement=placement)
    return s


def setup():
    org = person('stage_org')
    scorer = person('stage_scorer')
    stranger = person('stage_stranger')
    admin = person('stage_admin', admin_role='super_admin')
    fc = Games.objects.get_or_create(game_title='EA SPORTS FC Mobile')[0]
    efb = Games.objects.get_or_create(game_title='eFootball 2026')[0]

    made = {}
    # P1 + P4 + P5: the organiser builds GSL groups into a playoff, with one
    # invited entrant and "by record" placement, in the builder.
    made['gsl'] = tournament('GSL Walk Cup', fc, org, 'gsl', players=players('gsl', 9))
    # P2 + P5: home and away table into a two-leg knockout, "random", built too.
    made['home_away'] = tournament('Home And Away Walk', efb, org, 'round_robin',
                                   players=players('ha', 4))
    # P3: one format, generated from the console.
    made['se_one'] = tournament('Knockout One Format Walk', fc, org, 'single_elimination',
                                players=players('se', 8))
    made['de_one'] = tournament('Double Elim One Format Walk', efb, org, 'double_elimination',
                                players=players('de', 4))
    # P6: Swiss pressed by hand.
    sw = tournament('Swiss By Hand Walk', efb, org, 'swiss', players=players('sw', 8))
    stage(sw, 0, 'swiss', 'Swiss', settings={'rounds': 3})
    made['swiss_hand'] = sw
    # P7: a no-show that happens on screen. One-minute check-in.
    ns = tournament('No Show Walk', fc, org, 'single_elimination', players=players('ns', 2))
    stage(ns, 0, 'single_elimination', 'Final', settings={'check_in_minutes': 1})
    made['no_show'] = ns
    # P8: clubs, with an owner (captain) and a member each.
    teams = []
    for i in range(1, 5):
        owner = person('club_%d_owner' % i)
        member = person('club_%d_member' % i)
        teams.append(team('Walk Club %d' % i, owner, [member], fc))
    tt = tournament('Club Stages Walk', fc, org, 'round_robin', access='team', teams=teams)
    stage(tt, 0, 'round_robin', 'Groups', advances=1, groups=2)
    stage(tt, 1, 'single_elimination', 'Final')
    made['teams'] = tt
    # P10: a paid tournament with stages, entered in the browser.
    made['paid'] = tournament('Paid Stages Walk', fc, org, 'round_robin', fee=1,
                              players=players('pd', 3))
    for p in players('pd', 4):
        person(p.username[len('walk_'):], coins=50)

    # Re-walk of the fixes (second half of the walk).
    # A two-leg final, entered leg by leg; its result completes the tournament.
    tl = tournament('Two Leg Walk', fc, org, 'single_elimination', players=players('tl', 2))
    stage(tl, 0, 'single_elimination', 'Final', settings={'knockout_legs': 2})
    made['two_legs'] = tl
    # Groups into a playoff placed at random, with one entrant invited
    # straight into the playoff: the close list has to say both.
    cl = tournament('Close List Walk', efb, org, 'round_robin', players=players('cl', 5))
    invited = TournamentRegistration.objects.get(tournament=cl, user__username='walk_cl_05')
    stage(cl, 0, 'round_robin', 'Groups', advances=2)
    later = stage(cl, 1, 'single_elimination', 'Playoff', placement='random')
    if not later.direct_entrants:
        later.direct_entrants = [invited.id]
        later.save(update_fields=['direct_entrants'])
    made['close_list'] = cl
    # A player with coins and no identity check, for a paid entry.
    person('pd_05', coins=50)
    # A check-in whose time has passed with one entrant not in.
    ci = tournament('Check In Walk', fc, org, 'single_elimination', players=players('ci', 3))
    TournamentRegistration.objects.filter(tournament=ci, user__username='walk_ci_03').update(
        checked_in_at=None)
    made['check_in'] = ci

    for t in made.values():
        TournamentStaff.objects.get_or_create(
            tournament=t, user=scorer, defaults={'role': 'scorekeeper', 'added_by': org})

    state = {
        'password': WALK_PASSWORD,
        'people': {u.username: u.login_session_token for u in (org, scorer, stranger, admin)},
        'tournaments': {k: t.slug for k, t in made.items()},
    }
    with open(STATE, 'w') as fh:
        json.dump(state, fh, indent=1)
    print(json.dumps(state, indent=1))


if __name__ == '__main__':
    if '--setup' in sys.argv:
        setup()
    elif '--state' in sys.argv:
        print(open(STATE).read())
    else:
        print(__doc__)

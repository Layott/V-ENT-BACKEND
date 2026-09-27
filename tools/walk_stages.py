#!/usr/bin/env python
"""Set up and drive the linked-stages walk against the local database.

CEO, 27 September 2026 (inbox 275 to 280): an FC Mobile and eFootball event,
linked bracket types, every button walked as every role. Gates in
V-ENT/gates/42-linked-stages.md.

The Chrome walk presses the buttons. This makes the people and tournaments to
press them on, and can fill a stage's remaining results so a walk can reach
the later states (a stage ready to close, a grand final) without typing sixty
scores by hand. Everything it creates is named walk_* and never touches the
demo_* accounts. Local sqlite only.

  DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_stages.py --setup
  ... --fill <stage_id> [--leave N]      record every open match but the last N
  ... --state                            print slugs, stage ids and tokens
"""
import json
import os
import sys
import uuid
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from vent_auth.models import Games  # noqa: E402
from vent_tournament.models import (BracketMatch, Tournament,  # noqa: E402
                                    TournamentRegistration, TournamentStaff)
from vent_tournament import results  # noqa: E402

from walk_event import WALK_PASSWORD, person  # noqa: E402

STATE = os.path.join(ROOT, 'tools', '.walk-stages-state.json')


def say(*parts):
    print(' '.join(str(p) for p in parts), flush=True)


def tournament(title, game, creator, n_players, prefix, bracket_type):
    t = Tournament.objects.filter(tournament_title=title).first()
    now = timezone.now()
    if t is None:
        t = Tournament.objects.create(
            tournament_title=title, tournament_game=game, tournament_creator=creator,
            tournament_type='online', tournament_access='individual',
            tournament_visibility='public', entry_fee='Free', entry_fee_price=0,
            prize_type='no_prize', bracket_type=bracket_type,
            start_date_and_time=now + timedelta(hours=2),
            end_date_and_time=now + timedelta(days=2),
            is_draft=False, status='registration_open',
            tournament_description='A walk tournament for the linked-stages build.',
        )
    for i in range(1, n_players + 1):
        user = person('%s_%02d' % (prefix, i))
        reg, _ = TournamentRegistration.objects.get_or_create(
            tournament=t, user=user, defaults={'status': 'confirmed'})
        if reg.status != 'confirmed' or reg.seed != i:
            reg.status, reg.seed = 'confirmed', i
            reg.save(update_fields=['status', 'seed'])
    return t


def setup():
    organiser = person('stage_org')
    scorer = person('stage_scorer')
    stranger = person('stage_stranger')
    admin = person('stage_admin', admin_role='super_admin')
    fc = Games.objects.get_or_create(game_title='EA SPORTS FC Mobile')[0]
    efb = Games.objects.get_or_create(game_title='eFootball 2026')[0]

    cup = tournament('FC Mobile Walk Cup', fc, organiser, 16, 'fcm', 'round_robin')
    swiss = tournament('eFootball Swiss Walk', efb, organiser, 8, 'efb', 'swiss')
    league = tournament('FC Mobile One Format League', fc, organiser, 4, 'lg', 'round_robin')
    for t in (cup, swiss, league):
        TournamentStaff.objects.get_or_create(
            tournament=t, user=scorer, defaults={'role': 'scorekeeper', 'added_by': organiser})

    state = {
        'password': WALK_PASSWORD,
        'people': {u.username: u.login_session_token for u in
                   (organiser, scorer, stranger, admin)},
        'players': {
            'fcm_01': person('fcm_01').login_session_token,
            'fcm_02': person('fcm_02').login_session_token,
            'efb_01': person('efb_01').login_session_token,
        },
        'tournaments': {t.tournament_title: t.slug for t in (cup, swiss, league)},
    }
    with open(STATE, 'w') as fh:
        json.dump(state, fh, indent=1)
    say(json.dumps(state, indent=1))


def fill(stage_id, leave=0):
    """Record every playable match of a stage, the better seed winning, except
    the last `leave`, which are left for the walk to press."""
    from vent_tournament.models import TournamentStage
    stage = TournamentStage.objects.get(pk=stage_id)
    done = 0
    for _ in range(30):
        open_ = list(stage.matches.filter(
            status='scheduled', participant_1__isnull=False, participant_2__isnull=False)
            .select_related('participant_1', 'participant_2').order_by('round_number', 'match_number'))
        if len(open_) <= leave:
            break
        batch = open_[:len(open_) - leave] if leave else open_
        for m in batch:
            a, b = m.participant_1.seed or 99, m.participant_2.seed or 99
            with transaction.atomic():
                locked = BracketMatch.objects.select_for_update().get(pk=m.pk)
                s1, s2 = (2, 1) if a < b else (1, 2)
                if locked.best_of == 2:
                    s1, s2 = (2, 0) if a < b else (0, 2)
                if locked.best_of > 2:
                    need = locked.best_of // 2 + 1
                    s1, s2 = (need, 0) if a < b else (0, need)
                results.apply(locked, results.decide(locked, s1, s2))
            done += 1
        if leave:
            break
    say('recorded', done, 'result(s) in stage', stage_id, '(%s)' % stage.label)


def show():
    with open(STATE) as fh:
        state = json.load(fh)
    for title, slug in state['tournaments'].items():
        t = Tournament.objects.get(slug=slug)
        say(title, slug, t.status, [(s.id, s.label, s.format, s.status, bool(s.drawn_at))
                                    for s in t.stages.all()])


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--setup' in args:
        setup()
    elif '--fill' in args:
        stage = int(args[args.index('--fill') + 1])
        leave = int(args[args.index('--leave') + 1]) if '--leave' in args else 0
        fill(stage, leave)
    else:
        show()

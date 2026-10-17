#!/usr/bin/env python
"""Set up the P0 walk (correct results) against the local database.

CEO, 16 October 2026, agreeing the wrong results go first: "these are fine."
Two tournaments to press the buttons on, named walk_* and local sqlite only:

- "P0 Scoring Walk": a round robin group of four with every result in, where
  head to head and goal difference disagree about third, into a knockout. The
  organiser changes the stage's scoring and tie-breakers and the table follows.
- "P0 Prize Walk": an eight-entrant knockout, finished, the two semi-final
  losers sharing third, with prizes of 500, 250, 150 and 100 coins, so the
  prize confirmation shows 125 each, shared 3-4.

  DB_ENGINE=sqlite DEBUG=True ../V-ENT-BACKEND/venv/Scripts/python.exe tools/walk_p0.py
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

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from vent_auth.models import Games, UserWallet  # noqa: E402
from vent_tournament import results, stage_engine, stage_settings  # noqa: E402
from vent_tournament.models import (BracketMatch, Tournament,  # noqa: E402
                                    TournamentPrizeDistribution, TournamentRegistration,
                                    TournamentStaff, TournamentStage)
from vent_tournament.services import bracket as bracket_service  # noqa: E402
from walk_event import WALK_PASSWORD, person  # noqa: E402


def tournament(title, game, creator, n_players, prefix, bracket_type, prize_type='no_prize'):
    t = Tournament.objects.filter(tournament_title=title).first()
    now = timezone.now()
    if t is None:
        t = Tournament.objects.create(
            tournament_title=title, tournament_game=game, tournament_creator=creator,
            tournament_type='online', tournament_access='individual',
            tournament_visibility='public', entry_fee='Free', entry_fee_price=0,
            prize_type=prize_type, bracket_type=bracket_type,
            start_date_and_time=now - timedelta(hours=1),
            end_date_and_time=now + timedelta(days=2),
            is_draft=False, status='registration_open',
            tournament_description='A walk tournament for the P0 correct-results build.',
        )
    for i in range(1, n_players + 1):
        user = person('%s_%02d' % (prefix, i))
        reg, _ = TournamentRegistration.objects.get_or_create(
            tournament=t, user=user, defaults={'status': 'confirmed'})
        if reg.status != 'confirmed' or reg.seed != i:
            reg.status, reg.seed = 'confirmed', i
            reg.save(update_fields=['status', 'seed'])
    return t


def play(match, s1, s2):
    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        results.apply(locked, results.decide(locked, s1, s2))


def scoring_walk(organiser, co_manager, game):
    t = tournament('P0 Scoring Walk', game, organiser, 4, 'p0s', 'round_robin')
    TournamentStaff.objects.get_or_create(
        tournament=t, user=co_manager, defaults={'permissions': ['report'], 'added_by': organiser})
    group = t.stages.filter(order=0).first()
    if group is None:
        group = TournamentStage.objects.create(
            tournament=t, order=0, label='Group', format='round_robin', advances=2,
            settings=stage_settings.clean('round_robin', {}))
        TournamentStage.objects.create(
            tournament=t, order=1, label='Final', format='single_elimination',
            settings=stage_settings.clean('single_elimination', {}))
    if group.drawn_at is None:
        with transaction.atomic():
            stage_engine.draw(group, organiser)
        group.refresh_from_db()
        by_seeds = {(m.participant_1.seed, m.participant_2.seed): m
                    for m in group.matches.select_related('participant_1', 'participant_2')}
        wanted = {(1, 2): (1, 0), (1, 3): (0, 1), (1, 4): (5, 0),
                  (2, 3): (3, 0), (2, 4): (1, 0), (3, 4): (0, 1)}
        for (a, b), (sa, sb) in wanted.items():
            if (a, b) in by_seeds:
                play(by_seeds[(a, b)], sa, sb)
            else:
                play(by_seeds[(b, a)], sb, sa)
    return t


def prize_walk(organiser, game):
    t = tournament('P0 Prize Walk', game, organiser, 8, 'p0p', 'single_elimination',
                   prize_type='distributed')
    if not t.bracket_matches.exists():
        with transaction.atomic():
            bracket_service.generate(t, generated_by=organiser, seed_strategy='registration')
        for _ in range(10):
            ready = list(BracketMatch.objects.filter(
                tournament=t, status='scheduled', participant_1__isnull=False,
                participant_2__isnull=False).select_related('participant_1', 'participant_2'))
            if not ready:
                break
            for m in ready:
                better_first = (m.participant_1.seed or 99) < (m.participant_2.seed or 99)
                play(m, 2, 0) if better_first else play(m, 0, 2)
    for position, amount in ((1, 500), (2, 250), (3, 150), (4, 100)):
        TournamentPrizeDistribution.objects.get_or_create(
            tournament=t, position=position, defaults={'prize': amount})
    UserWallet.objects.filter(user=organiser).update(wallet_balance=5000)
    t.refresh_from_db()
    return t


def main():
    organiser = person('stage_org')
    co_manager = person('stage_scorer')
    stranger = person('stage_stranger')
    admin = person('stage_admin', admin_role='super_admin')
    game = Games.objects.get_or_create(game_title='EA SPORTS FC Mobile')[0]
    scoring = scoring_walk(organiser, co_manager, game)
    prize = prize_walk(organiser, game)
    print(json.dumps({
        'password': WALK_PASSWORD,
        'organiser': organiser.username, 'co_manager': co_manager.username,
        'stranger': stranger.username, 'admin': admin.username,
        'player': 'walk_p0s_04',
        'scoring_walk': scoring.slug, 'prize_walk': prize.slug,
        'prize_walk_status': prize.status,
        'prize_places': sorted(prize.registrations.values_list('final_position', flat=True)),
    }, indent=1))


if __name__ == '__main__':
    main()

"""Seeding by rating (P3 W3d, research catalogue 5.2): Elo from every finished
match on V-ENT in the tournament's game, strongest first.

CEO, 24 October 2026: "make all brackets and formats live today and function
properly". Ratings were one engine (ratings.py, P3 W3) with no bracket using
them; this is the bracket's door to it.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import options
from .models import BracketMatch
from .services import bracket as bracket_service
from .tests_stage_engine import field


def beat(t, winner, loser, minutes):
    return BracketMatch.objects.create(
        tournament=t, round_number=1, match_number=minutes,
        participant_1=winner, participant_2=loser, winner=winner, status='completed',
        completed_at=timezone.now() - timedelta(minutes=100 - minutes))


class RatingSeedTests(TestCase):
    def test_strongest_first_and_the_unplayed_start_level(self):
        t, creator, regs = field(6)
        s = {r.seed: r for r in regs}
        beat(t, s[4], s[1], 1)
        beat(t, s[4], s[2], 2)
        beat(t, s[3], s[1], 3)
        # 4: 1500 +20 +18.85; 3: +18.85; 5 and 6 untouched; 2 and 1 below.
        ordered = bracket_service.seed_registrations(regs, 'rating')
        self.assertEqual([r.seed for r in ordered], [4, 3, 5, 6, 2, 1])

    def test_another_game_does_not_count(self):
        t, creator, regs = field(4)
        other, _c, other_regs = field(4)
        from vent_auth.models import Games
        game, _ = Games.objects.get_or_create(game_title='Tekken 8')
        other.tournament_game = game
        other.save(update_fields=['tournament_game'])
        o = {r.seed: r for r in other_regs}
        beat(other, o[4], o[1], 1)
        ordered = bracket_service.seed_registrations(regs, 'rating')
        self.assertEqual([r.seed for r in ordered], [1, 2, 3, 4])

    def test_it_is_a_seeding_method_the_options_keep(self):
        self.assertEqual(options.clean({'seeding_method': 'rating'})['seeding_method'], 'rating')

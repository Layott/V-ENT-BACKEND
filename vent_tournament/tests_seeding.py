"""Seeding, automatic and by hand.

CEO: "There should be automatic seeding (based off result entry) and none
automated seeding."

The automatic one has to mean the RESULTS. It used to sort by an unset seed
field and then by name, which is alphabetical order wearing the word "ranked" -
and an organiser pressing a button labelled that had no way to know.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import (BracketMatch, LeagueRules, Tournament,
                     TournamentRegistration)
from .services.bracket import seed_registrations


def a_user(name):
    return Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True)


class SeedingTests(TestCase):
    def setUp(self):
        self.organiser = a_user('sd_org')
        game = Games.objects.create(game_title='EA FC SD')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Seed Probe', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin', is_draft=False)
        LeagueRules.objects.create(tournament=self.tournament,
                                   players_per_team=1)

        # Deliberately named so alphabetical order and result order differ.
        self.zoe = TournamentRegistration.objects.create(
            tournament=self.tournament, user=a_user('zoe'), status='confirmed')
        self.amara = TournamentRegistration.objects.create(
            tournament=self.tournament, user=a_user('amara'), status='confirmed')
        self.bisi = TournamentRegistration.objects.create(
            tournament=self.tournament, user=a_user('bisi'), status='confirmed')

    def names(self, ordered):
        return [r.user.username for r in ordered]

    # ------------------------------------------------------------ by hand

    def test_manual_order_is_obeyed_exactly(self):
        ordered = seed_registrations(
            [self.zoe, self.amara, self.bisi], 'manual_order',
            manual_order=[self.bisi.id, self.zoe.id, self.amara.id])
        self.assertEqual(self.names(ordered), ['bisi', 'zoe', 'amara'])

    def test_anybody_left_out_of_the_manual_order_still_gets_in(self):
        # Forgetting somebody must not drop them from the tournament.
        ordered = seed_registrations(
            [self.zoe, self.amara, self.bisi], 'manual_order',
            manual_order=[self.bisi.id])
        self.assertEqual(self.names(ordered)[0], 'bisi')
        self.assertEqual(len(ordered), 3)

    def test_registration_order_is_first_come_first_seeded(self):
        ordered = seed_registrations(
            [self.zoe, self.amara, self.bisi], 'registration')
        self.assertEqual(self.names(ordered), ['zoe', 'amara', 'bisi'])

    # --------------------------------------------------------- automatic

    def test_with_nothing_played_it_falls_back_and_stays_deterministic(self):
        first = self.names(seed_registrations(
            [self.zoe, self.amara, self.bisi], 'ranked'))
        again = self.names(seed_registrations(
            [self.bisi, self.zoe, self.amara], 'ranked'))
        # Same answer whatever order it was handed, because a bracket that
        # comes out differently on a retry is a bracket nobody can check.
        self.assertEqual(first, again)

    def test_the_organisers_own_seed_beats_the_alphabet(self):
        self.zoe.seed = 1
        self.zoe.save()
        ordered = seed_registrations([self.amara, self.bisi, self.zoe], 'ranked')
        self.assertEqual(self.names(ordered)[0], 'zoe')

    def test_results_beat_everything(self):
        # zoe is last alphabetically and has no seed. She wins her matches, so
        # she seeds first. This is the whole point of "based off result entry".
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.zoe, participant_2=self.amara,
            score_p1=3, score_p2=0, winner=self.zoe, status='completed')
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=2,
            participant_1=self.zoe, participant_2=self.bisi,
            score_p1=2, score_p2=1, winner=self.zoe, status='completed')

        ordered = seed_registrations(
            [self.amara, self.bisi, self.zoe], 'ranked')
        self.assertEqual(self.names(ordered)[0], 'zoe')

    def test_goal_difference_separates_two_on_the_same_points(self):
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.amara, participant_2=self.zoe,
            score_p1=5, score_p2=0, winner=self.amara, status='completed')
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=2,
            participant_1=self.bisi, participant_2=self.zoe,
            score_p1=1, score_p2=0, winner=self.bisi, status='completed')

        ordered = self.names(seed_registrations(
            [self.zoe, self.bisi, self.amara], 'ranked'))
        # Both won once; amara by five, bisi by one.
        self.assertEqual(ordered[0], 'amara')
        self.assertEqual(ordered[1], 'bisi')

    def test_random_still_returns_everybody(self):
        ordered = seed_registrations(
            [self.zoe, self.amara, self.bisi], 'random')
        self.assertEqual(sorted(self.names(ordered)), ['amara', 'bisi', 'zoe'])

    def test_an_unknown_strategy_does_not_lose_anybody(self):
        ordered = seed_registrations(
            [self.zoe, self.amara, self.bisi], 'by vibes')
        self.assertEqual(len(ordered), 3)

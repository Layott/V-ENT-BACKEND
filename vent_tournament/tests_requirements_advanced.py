"""Penalty points and ranking, as entry requirements.

Both columns already existed and nothing read either of them. `penalty_point`
has been on a profile and `penalty_points` on a team since the beginning; the
rankings page has been live for months. The spec asks for both as conditions of
entry, and the interesting part is not the check, it is that a team satisfies
them once per PLAYER: a squad whose fourth member is suspended is not eligible,
and the old shape would have admitted them because only the captain was asked.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from vent_auth.models import Organization, TeamMembers, Teams, UserProfile, Games

from . import requirements as req
from .models import BracketMatch, EntryRequirement
from .tests import client_for, make_tournament, make_user, register


def with_penalty(user, points):
    UserProfile.objects.create(user=user, penalty_point=points)
    return user


class CleanTests(TestCase):

    def test_penalty_limit_has_to_be_a_number_in_range(self):
        cleaned = req.clean({'kind': 'penalty_points', 'config': {'max_points': 3}})
        self.assertEqual(cleaned['config']['max_points'], 3)
        for bad in ('lots', None, -1, 5000):
            with self.assertRaises(req.RequirementError):
                req.clean({'kind': 'penalty_points', 'config': {'max_points': bad}})

    def test_ranking_takes_a_direction_and_a_position(self):
        cleaned = req.clean({'kind': 'ranking',
                             'config': {'mode': 'below', 'position': 50,
                                        'region': 'West Africa'}})
        self.assertEqual(cleaned['config'], {'mode': 'below', 'position': 50,
                                             'region': 'West Africa'})

    def test_ranking_refuses_a_direction_it_cannot_enforce(self):
        with self.assertRaises(req.RequirementError):
            req.clean({'kind': 'ranking', 'config': {'mode': 'sideways',
                                                     'position': 10}})
        with self.assertRaises(req.RequirementError):
            req.clean({'kind': 'ranking', 'config': {'mode': 'top',
                                                     'position': 0}})

    def test_an_empty_region_means_the_whole_platform(self):
        cleaned = req.clean({'kind': 'ranking',
                             'config': {'mode': 'top', 'position': 8}})
        self.assertEqual(cleaned['config']['region'], '')

    def test_a_region_nobody_knows_is_refused_not_ignored(self):
        """Ignoring it would silently mean the whole platform."""
        with self.assertRaises(req.RequirementError):
            req.clean({'kind': 'ranking',
                       'config': {'mode': 'top', 'position': 10,
                                  'region': 'Wst Africa'}})

    def test_the_catalogue_offers_the_regions_it_will_accept(self):
        by_kind = {row['kind']: row for row in req.kind_catalogue()}
        options = by_kind['ranking']['options']
        self.assertIn('West Africa', options['regions'])
        self.assertEqual(options['modes'], ['top', 'below'])
        self.assertEqual(by_kind['country']['options'], {})

    def test_the_catalogue_says_which_kinds_are_paid_for(self):
        by_kind = {row['kind']: row for row in req.kind_catalogue()}
        self.assertTrue(by_kind['penalty_points']['premium'])
        self.assertTrue(by_kind['ranking']['premium'])
        self.assertFalse(by_kind['country']['premium'])
        self.assertEqual(req.PREMIUM_KINDS, {'penalty_points', 'ranking'})


class PenaltyPointTests(TestCase):

    def setUp(self):
        self.creator = make_user(700)
        self.tournament = make_tournament(self.creator)
        self.rule = {'kind': 'penalty_points', 'required': True,
                     'config': {'max_points': 5}}

    def test_a_clean_player_passes(self):
        player = with_penalty(make_user(701), 0)
        met, _reason, _detail = req.check_automatic(
            self.rule, player, tournament=self.tournament)
        self.assertTrue(met)

    def test_a_player_over_the_limit_is_told_the_two_numbers(self):
        player = with_penalty(make_user(702), 7)
        met, reason, detail = req.check_automatic(
            self.rule, player, tournament=self.tournament)
        self.assertFalse(met)
        self.assertEqual(detail['code'], 'penalty_points')
        self.assertEqual(detail['params'], {'limit': 5, 'points': 7})
        # The sentence names what to do about it rather than saying "not
        # eligible", which sends somebody to support.
        self.assertIn('7', reason)

    def test_a_player_with_no_profile_row_is_not_refused(self):
        """A missing profile is zero penalties, not a refusal."""
        met, _reason, _detail = req.check_automatic(
            self.rule, make_user(703), tournament=self.tournament)
        self.assertTrue(met)

    def test_the_team_carries_its_own_penalties(self):
        game = Games.objects.create(game_title='Penalty Test FC')
        owner = with_penalty(make_user(704), 0)
        team = Teams.objects.create(
            team_name='Heavy Cards', game=game, description='',
            team_creator=owner, team_owner=owner, penalty_points=9,
            number_of_members=1)
        met, _reason, detail = req.check_automatic(
            self.rule, owner, tournament=self.tournament, team=team)
        self.assertFalse(met)
        self.assertEqual(detail['code'], 'penalty_points_team')

    def test_every_member_is_asked_not_only_the_captain(self):
        game = Games.objects.create(game_title='Penalty Test 2')
        captain = with_penalty(make_user(705), 0)
        suspended = with_penalty(make_user(706), 12)
        team = Teams.objects.create(
            team_name='Four Of Five', game=game, description='',
            team_creator=captain, team_owner=captain, penalty_points=0,
            number_of_members=2)
        TeamMembers.objects.create(team=team, user=captain, is_captain=True)
        TeamMembers.objects.create(team=team, user=suspended)

        met, reason, detail = req.check_for_team(
            self.rule, team, tournament=self.tournament)
        self.assertFalse(met)
        self.assertEqual(detail['code'], 'member_penalty_points')
        # And it names WHICH member, because "your team is not eligible" is
        # something nobody can act on.
        self.assertEqual(detail['params']['member'], suspended.username)
        self.assertIn(suspended.username, reason)

    def test_it_is_checked_once_per_member_through_evaluate(self):
        game = Games.objects.create(game_title='Penalty Test 3')
        captain = with_penalty(make_user(707), 0)
        suspended = with_penalty(make_user(708), 30)
        team = Teams.objects.create(
            team_name='Evaluate Team', game=game, description='',
            team_creator=captain, team_owner=captain, penalty_points=0,
            number_of_members=2)
        TeamMembers.objects.create(team=team, user=captain, is_captain=True)
        TeamMembers.objects.create(team=team, user=suspended)

        results = req.evaluate([self.rule], captain,
                               tournament=self.tournament, team=team)
        self.assertEqual(len(req.blocking(results)), 1)


class RankingTests(TestCase):
    """The rank enforced is the rank the rankings page shows."""

    def setUp(self):
        self.creator = make_user(800)
        self.game = Games.objects.create(game_title='Ranked Game')
        self.tournament = make_tournament(self.creator)
        self.tournament.tournament_game = self.game
        self.tournament.save(update_fields=['tournament_game'])

    def _played(self, wins):
        """A player with `wins` completed wins in this game."""
        player = make_user(810 + wins * 7 + RankingTests._n())
        past = make_tournament(self.creator)
        past.tournament_game = self.game
        past.save(update_fields=['tournament_game'])
        mine = register(past, player)
        for i in range(wins):
            other = register(past, make_user(900 + RankingTests._n()))
            BracketMatch.objects.create(
                tournament=past, round_number=1, match_number=i + 1,
                participant_1=mine, participant_2=other, winner=mine,
                status='completed')
        return player

    _counter = 0

    @classmethod
    def _n(cls):
        cls._counter += 1
        return cls._counter

    def test_the_top_player_passes_a_top_condition(self):
        best = self._played(5)
        rule = {'kind': 'ranking', 'required': True,
                'config': {'mode': 'top', 'position': 1, 'region': ''}}
        met, _reason, _detail = req.check_automatic(
            rule, best, tournament=self.tournament)
        self.assertTrue(met)

    def test_somebody_who_has_never_played_fails_a_top_condition(self):
        self._played(5)
        newcomer = make_user(880)
        rule = {'kind': 'ranking', 'required': True,
                'config': {'mode': 'top', 'position': 1, 'region': ''}}
        met, _reason, detail = req.check_automatic(
            rule, newcomer, tournament=self.tournament)
        self.assertFalse(met)
        # Unranked, which is a different sentence from "you are 40th".
        self.assertEqual(detail['code'], 'ranking_unranked')
        self.assertEqual(detail['params']['position'], 1)

    def test_a_newcomers_cup_refuses_the_top_player(self):
        best = self._played(5)
        rule = {'kind': 'ranking', 'required': True,
                'config': {'mode': 'below', 'position': 1, 'region': ''}}
        met, _reason, detail = req.check_automatic(
            rule, best, tournament=self.tournament)
        self.assertFalse(met)
        self.assertEqual(detail['code'], 'ranking_below')

    def test_being_ranked_but_too_low_says_the_position(self):
        best = self._played(5)
        second = self._played(1)
        rule = {'kind': 'ranking', 'required': True,
                'config': {'mode': 'top', 'position': 1, 'region': ''}}
        met, _reason, detail = req.check_automatic(
            rule, second, tournament=self.tournament)
        self.assertFalse(met)
        self.assertEqual(detail['code'], 'ranking_top')
        self.assertEqual(detail['params']['rank'], 2)
        self.assertTrue(best)

    def test_a_newcomers_cup_admits_somebody_unranked(self):
        self._played(5)
        rule = {'kind': 'ranking', 'required': True,
                'config': {'mode': 'below', 'position': 10, 'region': ''}}
        met, _reason, _detail = req.check_automatic(
            rule, make_user(881), tournament=self.tournament)
        self.assertTrue(met)


class PremiumGateTests(TestCase):

    def setUp(self):
        self.owner = make_user(600)
        self.tournament = make_tournament(self.owner)
        self.client = client_for(self.owner)
        self.url = '/tournament/%s/requirements/set/' % self.tournament.tournament_id

    def _put(self, kind, config):
        return self.client.put(
            self.url,
            {'requirements': [{'kind': kind, 'required': True, 'config': config}]},
            format='json')

    def test_a_free_organiser_is_refused_by_name(self):
        res = self._put('penalty_points', {'max_points': 5})
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.data['code'], 'PREMIUM_REQUIRED')
        self.assertEqual(res.data['data']['kinds'], ['penalty_points'])
        self.assertEqual(EntryRequirement.objects.count(), 0)

    def test_the_free_kinds_still_save(self):
        res = self._put('verified_email', {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(EntryRequirement.objects.count(), 1)

    def test_a_premium_owner_may_set_it(self):
        self.owner.is_premium = True
        self.owner.save(update_fields=['is_premium'])
        res = self._put('ranking', {'mode': 'top', 'position': 20})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(EntryRequirement.objects.get().kind, 'ranking')

    def test_the_organisation_carries_it_for_its_organisers(self):
        org = Organization.objects.create(
            org_name='Paying Org', org_creator=self.owner, org_owner=self.owner,
            is_premium=True)
        self.tournament.tournament_organization = org
        self.tournament.save(update_fields=['tournament_organization'])
        res = self._put('penalty_points', {'max_points': 2})
        self.assertEqual(res.status_code, 200)

    def test_the_reader_is_told_whether_it_may_use_them(self):
        res = self.client.get(
            '/tournament/%s/requirements/' % self.tournament.tournament_id)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['data']['has_premium'])
        by_kind = {row['kind']: row for row in res.data['data']['catalogue']}
        # Still OFFERED, and marked. Hiding what is for sale from the people who
        # might buy it is how nobody ever buys it.
        self.assertIn('ranking', by_kind)
        self.assertTrue(by_kind['ranking']['premium'])

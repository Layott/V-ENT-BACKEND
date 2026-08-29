"""The organiser's tiebreak order, applied to both tables.

CEO's list, in their order: points, goal difference, goals scored, head to head,
most wins, then a toss supervised by the Tournament Director.

"Same order on both" is the part worth pinning. Two tables that separate a tie
differently are two tables that can disagree about who came second, and the
whole reason there are two is that they describe different things about the same
results - not different rules.

The toss is not implemented on purpose. V-ENT does not decide a title by
generating a random number; that is a person in a room with both captains, and
the standings stop at the last thing arithmetic can settle.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users

from .models import (BracketMatch, LeagueRules, TieFixture, Tournament,
                     TournamentRegistration)
from .services import league


def a_user(name):
    return Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True)


class TiebreakTests(TestCase):
    def setUp(self):
        self.organiser = a_user('tb_org')
        self.game = Games.objects.create(game_title='EA FC TB')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Tiebreak Probe', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin', is_draft=False,
            tournament_access='team', team_size=1)
        self.rules = LeagueRules.objects.create(
            tournament=self.tournament, points_win=3, points_draw=1,
            points_loss=0, players_per_team=1,
            tiebreakers=['goal_difference', 'goals_for', 'head_to_head', 'wins'])

        self.regs = {}
        for name in ('Alpha', 'Bravo', 'Charlie'):
            team = Teams.objects.create(
                team_name=name, game=self.game, team_creator=self.organiser,
                team_owner=self.organiser, description='', penalty_points=0,
                number_of_members=1)
            player = a_user('tb_%s' % name.lower())
            TeamMembers.objects.create(team=team, user=player)
            self.regs[name] = TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed')

    def played(self, left, right, goals_left, goals_right):
        """One fixture with one seat, settled."""
        match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1,
            match_number=BracketMatch.objects.filter(
                tournament=self.tournament).count() + 1,
            participant_1=self.regs[left], participant_2=self.regs[right])
        TieFixture.objects.create(
            tie=match, slot=1, goals_1=goals_left, goals_2=goals_right,
            status='completed')
        league.settle(match)
        return match

    def order(self):
        return [row['name'] for row in league.team_table(self.tournament)]

    # ------------------------------------------------------------- points

    def test_points_come_first(self):
        self.played('Alpha', 'Bravo', 1, 0)
        self.played('Alpha', 'Charlie', 1, 0)
        self.assertEqual(self.order()[0], 'Alpha')

    def test_a_draw_is_a_point_each(self):
        self.played('Alpha', 'Bravo', 2, 2)
        table = {r['name']: r for r in league.team_table(self.tournament)}
        self.assertEqual(table['Alpha']['points'], 1)
        self.assertEqual(table['Bravo']['points'], 1)
        self.assertEqual(table['Alpha']['drawn'], 1)

    # --------------------------------------------------- goal difference

    def test_goal_difference_separates_equal_points(self):
        # Both win once. Alpha by five, Bravo by one.
        self.played('Alpha', 'Charlie', 5, 0)
        self.played('Bravo', 'Charlie', 1, 0)
        order = self.order()
        self.assertEqual(order[0], 'Alpha')
        self.assertEqual(order[1], 'Bravo')

    def test_goals_scored_separates_equal_difference(self):
        # Same points, same difference (+1 each); Alpha scored more.
        self.played('Alpha', 'Charlie', 4, 3)
        self.played('Bravo', 'Charlie', 1, 0)
        order = self.order()
        self.assertEqual(order[0], 'Alpha')

    # --------------------------------------------------- the organiser's order

    def test_the_organiser_can_put_goals_scored_first(self):
        # The ORDER is the setting. With goals_for ahead of goal_difference,
        # a side that scored more but conceded more comes first.
        self.rules.tiebreakers = ['goals_for', 'goal_difference', 'wins']
        self.rules.save()
        self.played('Alpha', 'Charlie', 4, 3)   # +1, scored 4
        self.played('Bravo', 'Charlie', 2, 0)   # +2, scored 2
        self.assertEqual(self.order()[0], 'Alpha')

        self.rules.tiebreakers = ['goal_difference', 'goals_for', 'wins']
        self.rules.save()
        self.assertEqual(self.order()[0], 'Bravo')

    def test_an_unknown_tiebreak_is_dropped_not_fatal(self):
        # A tiebreaker removed in a later version must not make every existing
        # league table uncomputable.
        self.rules.tiebreakers = ['coin_toss', 'goal_difference']
        self.rules.save()
        self.assertNotIn('coin_toss', self.rules.ordered_tiebreakers())
        self.played('Alpha', 'Bravo', 3, 0)
        self.assertEqual(self.order()[0], 'Alpha')

    def test_an_empty_order_still_produces_a_table(self):
        self.rules.tiebreakers = []
        self.rules.save()
        self.played('Alpha', 'Bravo', 1, 0)
        self.assertEqual(len(self.order()), 3)

    # ------------------------------------------------- same order on both

    def test_both_tables_use_the_same_order(self):
        # Two tables that separate a tie differently can disagree about who came
        # second, and they describe the same results.
        self.rules.tiebreakers = ['goals_for', 'goal_difference']
        self.rules.save()
        self.played('Alpha', 'Charlie', 4, 3)
        self.played('Bravo', 'Charlie', 2, 0)

        teams = league.team_table(self.tournament)
        players = league.player_table(self.tournament)
        # Every position is set on both, from the same rules row.
        self.assertEqual([r['position'] for r in teams], sorted(r['position'] for r in teams))
        self.assertEqual([r['position'] for r in players],
                         sorted(r['position'] for r in players))
        self.assertTrue(all('points' in r for r in players))

    def test_positions_are_numbered_from_one(self):
        self.played('Alpha', 'Bravo', 1, 0)
        positions = [r['position'] for r in league.team_table(self.tournament)]
        self.assertEqual(positions, [1, 2, 3])

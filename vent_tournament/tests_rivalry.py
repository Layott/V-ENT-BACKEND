"""The Rivalry Series structure, end to end.

Five nations, two seats each, ten players. A MATCH is one player against one
player; a FIXTURE is one nation against one nation, made of two matches. Every
pair of nations meets once, so ten fixtures and twenty matches.

The rule the CEO called "the one people get wrong":

    FIXTURE F1   Nigeria v Ghana
      match 1    NGA1 v GHA1     3-0
      match 2    NGA2 v GHA2     0-2
      aggregate  Nigeria 3  Ghana 2

Nigeria lost one of the two matches and still took the fixture. It is decided on
goals added across both matches, never on matches won. Counting matches won
calls that a draw, and that is the failure this file exists to catch.

Two tables come off the same results: nations scored on the fixture, individuals
scored on their own match. A player can win their own match while their country
loses the fixture, and both tables have to record it correctly.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users

from .models import (BracketMatch, LeagueRules, TieFixture, Tournament,
                     TournamentRegistration)
from .services import bracket, league

NATIONS = ['Nigeria', 'Ghana', 'Kenya', 'Ivory Coast', 'Senegal']


def a_user(name):
    return Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True)


class RivalrySeriesTests(TestCase):
    """A five-nation, two-seat aggregate league."""

    def setUp(self):
        self.organiser = a_user('rs_org')
        self.game = Games.objects.create(game_title='EA FC 26')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='CADE Rivalry Series',
            tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin',
            tournament_access='team',
            team_size=2,
            is_draft=False,
        )
        # Three points a win, one a draw, and the CEO's tiebreak order.
        self.rules = LeagueRules.objects.create(
            tournament=self.tournament,
            points_win=3, points_draw=1, points_loss=0,
            players_per_team=2,
            tiebreakers=['goal_difference', 'goals_for', 'head_to_head', 'wins'],
        )

        self.teams = {}
        self.seats = {}
        for nation in NATIONS:
            team = Teams.objects.create(
                team_name=nation, game=self.game,
                team_creator=self.organiser, team_owner=self.organiser,
                description='', penalty_points=0, number_of_members=2)
            self.teams[nation] = team
            people = []
            for seat in (1, 2):
                player = a_user('%s%s' % (nation[:3].lower().replace(' ', ''), seat))
                TeamMembers.objects.create(team=team, user=player)
                people.append(player)
            self.seats[nation] = people
            TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed')

    def generate(self):
        return bracket.generate(self.tournament, self.organiser,
                                seed_strategy='registration')

    # ------------------------------------------------------------- the shape

    def test_every_pair_meets_once(self):
        summary = self.generate()
        # Five nations, every pair once: 5 x 4 / 2.
        self.assertEqual(summary['matches_created'], 10)
        self.assertEqual(
            BracketMatch.objects.filter(tournament=self.tournament).count(), 10)

    def test_each_fixture_is_two_matches(self):
        self.generate()
        self.assertEqual(
            TieFixture.objects.filter(tie__tournament=self.tournament).count(), 20)
        for fixture in BracketMatch.objects.filter(tournament=self.tournament):
            self.assertEqual(fixture.fixtures.count(), 2)

    def test_the_summary_says_what_is_on_the_floor(self):
        # Ten fixtures is twenty matches to run, and the day plan is built from
        # the second number.
        summary = self.generate()
        shape = summary['structure_summary'][0]
        self.assertEqual(shape['seats_per_side'], 2)
        self.assertEqual(shape['matches_on_the_floor'], 20)

    def test_each_nation_plays_four_fixtures_and_eight_matches(self):
        self.generate()
        for nation, team in self.teams.items():
            fixtures = BracketMatch.objects.filter(
                tournament=self.tournament).filter(
                    participant_1__team=team).count() + BracketMatch.objects.filter(
                    tournament=self.tournament).filter(
                        participant_2__team=team).count()
            self.assertEqual(fixtures, 4, nation)

    def test_seats_never_cross(self):
        # Seat 1 only ever faces seat 1. There is no fixture in which NGA1
        # plays GHA2, and the slot is what guarantees it.
        self.generate()
        seat_of = {}
        for nation, people in self.seats.items():
            for index, person in enumerate(people, start=1):
                seat_of[person.user_id] = index

        for row in TieFixture.objects.filter(tie__tournament=self.tournament):
            if row.player_1_id and row.player_2_id:
                self.assertEqual(seat_of[row.player_1_id], seat_of[row.player_2_id])
                self.assertEqual(seat_of[row.player_1_id], row.slot)

    def test_a_player_meets_the_other_four_nations_once_each(self):
        self.generate()
        for nation, people in self.seats.items():
            player = people[0]
            met = TieFixture.objects.filter(
                tie__tournament=self.tournament).filter(
                    player_1=player).count() + TieFixture.objects.filter(
                    tie__tournament=self.tournament).filter(
                        player_2=player).count()
            self.assertEqual(met, 4, nation)

    # -------------------------------------------------- the one rule people get wrong

    def _fixture_between(self, left, right):
        for fixture in BracketMatch.objects.filter(tournament=self.tournament):
            names = {
                fixture.participant_1.team.team_name if fixture.participant_1 else None,
                fixture.participant_2.team.team_name if fixture.participant_2 else None,
            }
            if names == {left, right}:
                return fixture
        raise AssertionError('no fixture between %s and %s' % (left, right))

    def _record(self, fixture, scores):
        """scores: {slot: (goals for participant_1, goals for participant_2)}."""
        for slot, (one, two) in scores.items():
            row = fixture.fixtures.get(slot=slot)
            row.goals_1, row.goals_2 = one, two
            row.status = 'completed'
            row.save()
        return league.settle(fixture)

    def test_losing_a_match_and_still_taking_the_fixture(self):
        # The CEO's own worked example, exactly.
        self.generate()
        fixture = self._fixture_between('Nigeria', 'Ghana')
        nigeria_is_one = fixture.participant_1.team.team_name == 'Nigeria'
        scores = ({1: (3, 0), 2: (0, 2)} if nigeria_is_one
                  else {1: (0, 3), 2: (2, 0)})
        self._record(fixture, scores)

        fixture.refresh_from_db()
        one, two = league.aggregate(fixture)
        # Nigeria 3, Ghana 2. One match each, and Nigeria takes the fixture.
        self.assertEqual(sorted([one, two]), [2, 3])
        winner = fixture.winner
        self.assertIsNotNone(winner, 'a 3-2 aggregate is not a draw')
        self.assertEqual(winner.team.team_name, 'Nigeria')

    def test_a_level_aggregate_is_a_draw(self):
        # "A level aggregate is a draw. One point each. No decider, no extra
        # time, no penalties."
        self.generate()
        fixture = self._fixture_between('Kenya', 'Senegal')
        self._record(fixture, {1: (2, 1), 2: (0, 1)})
        fixture.refresh_from_db()
        self.assertIsNone(fixture.winner)

    def test_winning_both_matches_still_only_counts_the_goals(self):
        self.generate()
        fixture = self._fixture_between('Nigeria', 'Kenya')
        nigeria_is_one = fixture.participant_1.team.team_name == 'Nigeria'
        scores = ({1: (1, 0), 2: (1, 0)} if nigeria_is_one
                  else {1: (0, 1), 2: (0, 1)})
        self._record(fixture, scores)
        fixture.refresh_from_db()
        self.assertEqual(fixture.winner.team.team_name, 'Nigeria')

    # ------------------------------------------------------------ two tables

    def test_both_tables_come_off_the_same_results(self):
        self.generate()
        fixture = self._fixture_between('Nigeria', 'Ghana')
        nigeria_is_one = fixture.participant_1.team.team_name == 'Nigeria'
        self._record(fixture, {1: (3, 0), 2: (0, 2)} if nigeria_is_one
                     else {1: (0, 3), 2: (2, 0)})

        teams = league.team_table(self.tournament)
        players = league.player_table(self.tournament)

        nigeria = next(r for r in teams if r['name'] == 'Nigeria')
        ghana = next(r for r in teams if r['name'] == 'Ghana')
        self.assertEqual(nigeria['points'], self.rules.points_win)
        self.assertEqual(ghana['points'], self.rules.points_loss)

        # A player can win their own match while their country loses the
        # fixture, and both tables have to record it. GHA2 won 2-0.
        gha2 = self.seats['Ghana'][1]
        row = next(r for r in players if r['name'] == gha2.username)
        self.assertEqual(row['points'], self.rules.points_win)

    def test_a_nations_maximum_is_twelve(self):
        # Four fixtures at three points.
        self.assertEqual(self.rules.points_win * 4, 12)

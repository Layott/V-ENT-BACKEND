"""The aggregate 2v2 league: ties decided on goals, and two tables out of one set
of fixtures.

The first test is the CEO's worked example, transcribed:

    player 1 in team A beats player 1 in team B 3-0
    player 2 in team A loses 0-2 to player 2 in team B
    the overall score is Team A 3-2 Team B and team A wins

It is worth being explicit about why that is the interesting case: **each team
won one game.** Any scheme that counts games won calls it a draw. Only the
aggregate makes it a win for A, so this single example pins the whole design.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Teams, Users

from .models import (
    BracketMatch, LeagueRules, TieFixture, Tournament, TournamentRegistration,
)
from .services import league, schedule


def make_user(name):
    return Users.objects.create(
        username=f'{name}_{uuid.uuid4().hex[:5]}',
        email=f'{name}_{uuid.uuid4().hex[:5]}@vent.test',
        full_name=name.title(),
    )


def a_game():
    game, _ = Games.objects.get_or_create(game_title='EA FC 25')
    return game


def make_team(name, owner):
    return Teams.objects.create(
        game=a_game(),
        team_name=f'{name} {uuid.uuid4().hex[:4]}',
        slug=f'{name.lower()}-{uuid.uuid4().hex[:6]}',
        team_creator=owner,
        team_owner=owner,
    )


def make_tournament(creator):
    now = timezone.now()
    return Tournament.objects.create(
        tournament_title=f'EAFC League {uuid.uuid4().hex[:6]}',
        tournament_creator=creator,
        tournament_type='online',
        tournament_access='team',
        tournament_visibility='public',
        entry_fee='Free',
        entry_fee_price=0,
        prize_type='no_prize',
        bracket_type='aggregate_league',
        start_date_and_time=now + timedelta(days=1),
        end_date_and_time=now + timedelta(days=2),
        is_draft=False,
        status='registration_open',
    )


def enter(tournament, team):
    return TournamentRegistration.objects.create(
        tournament=tournament, team=team, status='confirmed',
    )


def play(tie, slot, p1, p2, goals_1, goals_2):
    """Record one player-versus-player game inside a tie."""
    fixture, _ = TieFixture.objects.get_or_create(tie=tie, slot=slot)
    fixture.player_1 = p1
    fixture.player_2 = p2
    fixture.goals_1 = goals_1
    fixture.goals_2 = goals_2
    fixture.status = 'completed'
    fixture.save()
    return fixture


class AggregateTieTests(TestCase):
    def setUp(self):
        self.owner = make_user('organiser')
        self.t = make_tournament(self.owner)
        self.team_a = make_team('Alpha', self.owner)
        self.team_b = make_team('Bravo', self.owner)
        self.reg_a = enter(self.t, self.team_a)
        self.reg_b = enter(self.t, self.team_b)
        self.a1, self.a2 = make_user('a_one'), make_user('a_two')
        self.b1, self.b2 = make_user('b_one'), make_user('b_two')
        self.tie = BracketMatch.objects.create(
            tournament=self.t, round_number=1, match_number=1,
            participant_1=self.reg_a, participant_2=self.reg_b,
        )

    def test_the_worked_example(self):
        """3-0 and 0-2 is 3-2 to A, even though each team won a game."""
        play(self.tie, 1, self.a1, self.b1, 3, 0)
        play(self.tie, 2, self.a2, self.b2, 0, 2)

        winner = league.settle(self.tie)
        self.tie.refresh_from_db()

        self.assertEqual((self.tie.score_p1, self.tie.score_p2), (3, 2))
        self.assertEqual(winner, self.reg_a)

    def test_level_on_aggregate_is_a_draw(self):
        """A league draw is a result, not something to break with a coin toss."""
        play(self.tie, 1, self.a1, self.b1, 2, 0)
        play(self.tie, 2, self.a2, self.b2, 1, 3)

        winner = league.settle(self.tie)
        self.tie.refresh_from_db()

        self.assertEqual((self.tie.score_p1, self.tie.score_p2), (3, 3))
        self.assertIsNone(winner)
        self.assertEqual(self.tie.status, 'completed')

    def test_a_half_played_tie_is_not_settled(self):
        play(self.tie, 1, self.a1, self.b1, 3, 0)
        TieFixture.objects.create(tie=self.tie, slot=2, status='scheduled')

        self.assertIsNone(league.settle(self.tie))
        self.tie.refresh_from_db()
        self.assertNotEqual(self.tie.status, 'completed')


class TwoTablesTests(TestCase):
    """Both tables, from the same fixtures, for the worked example."""

    def setUp(self):
        self.owner = make_user('organiser')
        self.t = make_tournament(self.owner)
        self.reg_a = enter(self.t, make_team('Alpha', self.owner))
        self.reg_b = enter(self.t, make_team('Bravo', self.owner))
        self.a1, self.a2 = make_user('a_one'), make_user('a_two')
        self.b1, self.b2 = make_user('b_one'), make_user('b_two')
        tie = BracketMatch.objects.create(
            tournament=self.t, round_number=1, match_number=1,
            participant_1=self.reg_a, participant_2=self.reg_b,
        )
        play(tie, 1, self.a1, self.b1, 3, 0)
        play(tie, 2, self.a2, self.b2, 0, 2)
        league.settle(tie)

    def test_the_team_table(self):
        table = league.team_table(self.t)
        top, bottom = table[0], table[1]

        self.assertEqual(top['registration_id'], self.reg_a.id)
        self.assertEqual(top['points'], 3)          # default win = 3
        self.assertEqual((top['goals_for'], top['goals_against']), (3, 2))
        self.assertEqual(top['goal_difference'], 1)
        self.assertEqual((top['won'], top['drawn'], top['lost']), (1, 0, 0))

        self.assertEqual(bottom['points'], 0)
        self.assertEqual((bottom['goals_for'], bottom['goals_against']), (2, 3))

    def test_the_player_table(self):
        """Each person judged on their own game, not their team's result."""
        rows = {r['user_id']: r for r in league.player_table(self.t)}

        self.assertEqual(rows[self.a1.pk]['won'], 1)
        self.assertEqual((rows[self.a1.pk]['goals_for'], rows[self.a1.pk]['goals_against']), (3, 0))

        # a2 lost, even though a2's team won the tie. That is the point of
        # publishing both tables.
        self.assertEqual(rows[self.a2.pk]['lost'], 1)
        self.assertEqual((rows[self.a2.pk]['goals_for'], rows[self.a2.pk]['goals_against']), (0, 2))

        self.assertEqual(rows[self.b1.pk]['lost'], 1)
        self.assertEqual(rows[self.b2.pk]['won'], 1)

    def test_the_player_table_leader_is_not_the_winning_team(self):
        """b2 won 2-0; a1 won 3-0. The individual table has its own story."""
        table = league.player_table(self.t)
        self.assertEqual(table[0]['user_id'], self.a1.pk)


class ScoringRulesTests(TestCase):
    def setUp(self):
        self.owner = make_user('organiser')
        self.t = make_tournament(self.owner)
        self.regs = [enter(self.t, make_team(f'club{i}', self.owner)) for i in range(3)]
        self.players = [(make_user(f'p{i}a'), make_user(f'p{i}b')) for i in range(3)]

    def _tie(self, i, j, goals):
        tie = BracketMatch.objects.create(
            tournament=self.t, round_number=1, match_number=i * 10 + j,
            participant_1=self.regs[i], participant_2=self.regs[j],
        )
        (a1, a2), (b1, b2) = self.players[i], self.players[j]
        play(tie, 1, a1, b1, goals[0], goals[1])
        play(tie, 2, a2, b2, goals[2], goals[3])
        league.settle(tie)
        return tie

    def test_points_are_the_organisers(self):
        """A win worth 2 and a draw worth 0 is a legitimate league."""
        LeagueRules.objects.create(
            tournament=self.t, points_win=2, points_draw=0, points_loss=0,
        )
        self._tie(0, 1, (3, 0, 0, 0))          # team 0 wins 3-0

        rows = {r['registration_id']: r for r in league.team_table(self.t)}
        self.assertEqual(rows[self.regs[0].id]['points'], 2)
        self.assertEqual(rows[self.regs[1].id]['points'], 0)

    def test_goal_difference_separates_teams_level_on_points(self):
        self._tie(0, 2, (5, 0, 0, 0))          # team 0 wins by 5
        self._tie(1, 2, (1, 0, 0, 0))          # team 1 wins by 1

        table = league.team_table(self.t)
        self.assertEqual(table[0]['registration_id'], self.regs[0].id)
        self.assertEqual(table[0]['goal_difference'], 5)
        self.assertEqual(table[1]['registration_id'], self.regs[1].id)

    def test_the_organiser_can_put_goals_for_ahead_of_goal_difference(self):
        """Order matters, so it has to be the organiser's order."""
        LeagueRules.objects.create(
            tournament=self.t, tiebreakers=['goals_for', 'goal_difference'],
        )
        # team 0: scores 4 concedes 3 (GD +1, GF 4). team 1: scores 2 concedes 0
        # (GD +2, GF 2). On goal difference team 1 leads; on goals for, team 0.
        self._tie(0, 2, (4, 3, 0, 0))
        self._tie(1, 2, (2, 0, 0, 0))

        table = league.team_table(self.t)
        self.assertEqual(table[0]['registration_id'], self.regs[0].id,
                         'goals_for was named first and must win')

    def test_an_unknown_tiebreaker_does_not_break_the_table(self):
        LeagueRules.objects.create(tournament=self.t, tiebreakers=['nonsense'])
        self._tie(0, 1, (2, 0, 0, 0))

        table = league.team_table(self.t)      # must not raise
        self.assertEqual(table[0]['registration_id'], self.regs[0].id)


class RoundRobinTests(TestCase):
    def setUp(self):
        self.owner = make_user('organiser')
        self.t = make_tournament(self.owner)

    def _enter(self, n):
        return [enter(self.t, make_team(f'side{i}', self.owner)) for i in range(n)]

    def test_five_teams_meet_everybody_once(self):
        """5 teams is 10 ties, and every pair exactly once."""
        regs = self._enter(5)
        schedule.build_league(self.t, players_per_team=2)

        real = BracketMatch.objects.filter(tournament=self.t).exclude(status='bye')
        self.assertEqual(real.count(), 10)

        pairs = {frozenset((t.participant_1_id, t.participant_2_id)) for t in real}
        self.assertEqual(len(pairs), 10, 'every pair must be unique')
        expected = {frozenset((a.id, b.id))
                    for i, a in enumerate(regs) for b in regs[i + 1:]}
        self.assertEqual(pairs, expected)

    def test_an_odd_field_rests_one_team_a_round_and_nobody_plays_twice(self):
        self._enter(5)
        schedule.build_league(self.t, players_per_team=2)

        for round_number in range(1, 6):
            ties = BracketMatch.objects.filter(tournament=self.t, round_number=round_number)
            playing = []
            for tie in ties.exclude(status='bye'):
                playing += [tie.participant_1_id, tie.participant_2_id]
            self.assertEqual(len(playing), len(set(playing)),
                             f'round {round_number} has somebody playing twice')
            self.assertEqual(ties.filter(status='bye').count(), 1)

    def test_every_tie_gets_a_fixture_per_player_slot(self):
        self._enter(4)
        schedule.build_league(self.t, players_per_team=2)

        for tie in BracketMatch.objects.filter(tournament=self.t).exclude(status='bye'):
            self.assertEqual(tie.fixtures.count(), 2)
            self.assertEqual({f.slot for f in tie.fixtures.all()}, {1, 2})

    def test_building_twice_does_not_duplicate_or_wipe_a_schedule(self):
        self._enter(4)
        first = schedule.build_league(self.t, players_per_team=2)
        again = schedule.build_league(self.t, players_per_team=2)

        self.assertEqual(len(first), len(again))
        self.assertEqual(BracketMatch.objects.filter(tournament=self.t).count(), len(first))


class StandingsApiTests(TestCase):
    """The endpoint a league page reads: both tables in one answer."""

    def setUp(self):
        self.owner = make_user('organiser')
        self.t = make_tournament(self.owner)
        self.reg_a = enter(self.t, make_team('Alpha', self.owner))
        self.reg_b = enter(self.t, make_team('Bravo', self.owner))
        self.a1, self.a2 = make_user('a_one'), make_user('a_two')
        self.b1, self.b2 = make_user('b_one'), make_user('b_two')
        self.tie = BracketMatch.objects.create(
            tournament=self.t, round_number=1, match_number=1,
            participant_1=self.reg_a, participant_2=self.reg_b,
        )
        for slot in (1, 2):
            TieFixture.objects.create(tie=self.tie, slot=slot, status='scheduled')

        self.admin = make_user('an_admin')
        self.admin.is_staff = True
        self.admin.admin_role = 'super_admin'
        self.admin.admin_session_token = 'league-admin-grant'
        self.admin.admin_session_created_at = timezone.now()
        self.admin.save()
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer league-admin-grant'}

    def _record(self, slot, g1, g2):
        return self.client.post(
            f'/tournament/tie/{self.tie.pk}/record/',
            {'slot': slot, 'goals_1': g1, 'goals_2': g2},
            content_type='application/json', **self.auth)

    def test_standings_are_public_and_carry_both_tables(self):
        res = self.client.get(f'/tournament/{self.t.pk}/standings/')

        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertIn('team_table', data)
        self.assertIn('player_table', data)
        self.assertEqual(data['rules']['points_win'], 3)

    def test_recording_both_slots_settles_the_tie_on_aggregate(self):
        first = self._record(1, 3, 0)
        self.assertEqual(first.status_code, 200)
        # One slot in: the tie must not be decided yet.
        self.assertNotEqual(first.json()['data']['tie_status'], 'completed')

        second = self._record(2, 0, 2)
        body = second.json()['data']

        self.assertEqual(body['aggregate'], {'participant_1': 3, 'participant_2': 2})
        self.assertEqual(body['tie_status'], 'completed')
        self.assertEqual(body['winner_registration_id'], self.reg_a.id)
        self.assertFalse(body['drawn'])

    def test_a_draw_is_reported_as_a_draw_not_a_missing_winner(self):
        self._record(1, 1, 1)
        body = self._record(2, 2, 2).json()['data']

        self.assertTrue(body['drawn'])
        self.assertIsNone(body['winner_registration_id'])

    def test_a_negative_score_is_refused(self):
        res = self._record(1, -1, 0)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'VALIDATION_FAILED')

    def test_recording_needs_an_admin(self):
        res = self.client.post(
            f'/tournament/tie/{self.tie.pk}/record/',
            {'slot': 1, 'goals_1': 1, 'goals_2': 0},
            content_type='application/json')
        self.assertIn(res.status_code, (400, 401, 403))

    def test_the_organiser_sets_the_points_and_the_table_uses_them(self):
        res = self.client.post(
            f'/tournament/{self.t.pk}/league-rules/',
            {'points_win': 2, 'points_draw': 0, 'tiebreakers': ['goals_for']},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['points_win'], 2)

        self._record(1, 3, 0)
        self._record(2, 0, 2)

        table = self.client.get(f'/tournament/{self.t.pk}/standings/').json()['data']['team_table']
        self.assertEqual(table[0]['points'], 2)

    def test_an_unknown_tiebreaker_is_dropped_from_the_echo(self):
        """The response says what will be applied, not what was sent."""
        res = self.client.post(
            f'/tournament/{self.t.pk}/league-rules/',
            {'tiebreakers': ['goals_for', 'astrology']},
            content_type='application/json', **self.auth)

        self.assertEqual(res.json()['data']['tiebreakers'], ['goals_for'])

"""The table, and the organiser's choices, through the endpoints.

The arithmetic is checked against the real spreadsheet in
`tests_league_stats.py`. This file asks a different question: can anybody
actually reach it.

That is a distinction this codebase has been caught by before - an endpoint
existed, was tested, and no screen called it, which from an organiser's side is
the same as it not existing. So these go through HTTP: a result is recorded,
the table moves, a setting is changed, the table moves differently.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Teams, Users
from vent_tournament.models import (
    BracketMatch, Tournament, TournamentRegistration)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name,
        login_session_token=('t-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class StandingsEndpointTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('leagueOwner')
        self.stranger, self.stranger_auth = a_user('leagueStranger')
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')

        self.tournament = Tournament.objects.create(
            tournament_title='Rivalry League',
            tournament_game=game, tournament_creator=self.owner,
            bracket_type='league',
            start_date_and_time=timezone.now() + timezone.timedelta(days=1),
            end_date_and_time=timezone.now() + timezone.timedelta(days=2),
            is_draft=False)

        self.entrants = {}
        for name in ('Alpha', 'Bravo', 'Charlie'):
            team = Teams.objects.create(
                team_name=name, game=game, team_creator=self.owner,
                team_owner=self.owner, description='', penalty_points=0,
                number_of_members=1)
            self.entrants[name] = TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed')

    def tie(self, home, away, hg=None, ag=None, state='completed', n=1):
        return BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=n,
            participant_1=self.entrants[home], participant_2=self.entrants[away],
            score_p1=hg or 0, score_p2=ag or 0, status=state)

    def standings(self):
        res = self.client.get(
            '/tournament/%d/standings/' % self.tournament.tournament_id)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def team_row(self, name):
        for row in self.standings()['team_table']:
            if row.get('name') == name:
                return row
        return None

    # ------------------------------------------------------- it is reachable

    def test_the_table_is_public(self):
        """A league table is the most shareable thing a tournament produces.
        Behind a sign-in it is a competition nobody can see."""
        self.tie('Alpha', 'Bravo', 3, 1)
        res = self.client.get(
            '/tournament/%d/standings/' % self.tournament.tournament_id)
        self.assertEqual(res.status_code, 200)

    def test_recording_a_result_moves_the_table_with_no_further_action(self):
        """Nothing is typed into the table. It is derived, every time."""
        self.assertEqual(self.team_row('Alpha')['played'], 0)
        self.tie('Alpha', 'Bravo', 3, 1)
        row = self.team_row('Alpha')
        self.assertEqual(row['points'], 3)
        self.assertEqual(row['goals_for'], 3)

    def test_the_extra_columns_are_there(self):
        """The whole point of the CADE sheet: what a league actually keeps."""
        self.tie('Alpha', 'Bravo', 3, 0)
        row = self.team_row('Alpha')
        for column in ('clean_sheets', 'average_goals_for', 'win_rate',
                       'biggest_win', 'biggest_loss', 'walkovers_given',
                       'walkovers_received', 'points_per_game', 'form_score'):
            self.assertIn(column, row, column)
        self.assertEqual(row['clean_sheets'], 1)
        self.assertEqual(row['biggest_win'], 3)

    def test_a_walkover_is_a_result(self):
        self.tie('Alpha', 'Bravo', state='walkover_p1')
        alpha = self.team_row('Alpha')
        bravo = self.team_row('Bravo')
        self.assertEqual(alpha['walkovers_received'], 1)
        self.assertEqual(bravo['walkovers_given'], 1)
        self.assertEqual(alpha['points'], 3)

    def test_a_cancelled_match_counts_for_nothing(self):
        self.tie('Alpha', 'Bravo', 5, 0, state='cancelled')
        row = self.team_row('Alpha')
        self.assertEqual(row['played'], 0)
        self.assertEqual(row['goals_for'], 0)

    def test_a_scheduled_match_is_not_a_nil_nil_draw(self):
        """The most expensive default in a league table: an unplayed fixture
        counted as a goalless draw gives everybody a point they did not earn."""
        self.tie('Alpha', 'Bravo', state='scheduled')
        row = self.team_row('Alpha')
        self.assertEqual(row['played'], 0)
        self.assertEqual(row['points'], 0)

    # ------------------------------------------------------- the choices

    def test_the_choices_are_described_with_the_table(self):
        """So the screen never keeps its own copy of the list and never
        drifts from what the API accepts."""
        data = self.standings()
        keys = {c['key'] for c in data['stat_choices']}
        self.assertIn('walkover_goals_count', keys)
        self.assertIn('win_rate_method', keys)
        for choice in data['stat_choices']:
            self.assertTrue(choice.get('label'))

    def test_the_organiser_can_change_how_a_metric_is_worked_out(self):
        self.tie('Alpha', 'Bravo', state='walkover_p1')
        self.assertEqual(self.team_row('Alpha')['clean_sheets'], 1)

        res = self.client.post(
            '/tournament/%d/stat-settings/' % self.tournament.tournament_id,
            data={'clean_sheet_includes_walkover': False},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

        self.assertEqual(self.team_row('Alpha')['clean_sheets'], 0)

    def test_an_invalid_choice_is_refused_and_named(self):
        res = self.client.post(
            '/tournament/%d/stat-settings/' % self.tournament.tournament_id,
            data={'win_rate_method': 'vibes'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_LEAGUE_SETTING')

    def test_a_stranger_cannot_change_the_scoring(self):
        res = self.client.post(
            '/tournament/%d/stat-settings/' % self.tournament.tournament_id,
            data={'form_window': 3},
            content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_a_signed_out_caller_cannot_change_the_scoring(self):
        res = self.client.post(
            '/tournament/%d/stat-settings/' % self.tournament.tournament_id,
            data={'form_window': 3}, content_type='application/json')
        self.assertIn(res.status_code, (401, 403))

    # ------------------------------------------------------- adjustments

    def test_a_deduction_reaches_the_table_and_keeps_its_reason(self):
        self.tie('Alpha', 'Bravo', 3, 0)
        self.assertEqual(self.team_row('Alpha')['points'], 3)

        res = self.client.post(
            '/tournament/%d/league-adjustment/' % self.tournament.tournament_id,
            data={'player': 'Alpha', 'metric': 'PTS', 'value': -3,
                  'reason': 'Left mid match'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

        row = self.team_row('Alpha')
        self.assertEqual(row['points'], 0)
        self.assertEqual(row['points_adjustment'], -3)
        self.assertIn('Left mid match', row['adjustments'][0]['reason'])

    def test_an_adjustment_without_a_reason_is_refused(self):
        """A deduction is a decision somebody defends weeks later, and
        "we all remember why" is not a record."""
        res = self.client.post(
            '/tournament/%d/league-adjustment/' % self.tournament.tournament_id,
            data={'player': 'Alpha', 'metric': 'PTS', 'value': -3},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

    def test_an_adjustment_to_something_that_is_not_a_metric_is_refused(self):
        res = self.client.post(
            '/tournament/%d/league-adjustment/' % self.tournament.tournament_id,
            data={'player': 'Alpha', 'metric': 'VIBES', 'value': -3,
                  'reason': 'x'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_cannot_deduct_points(self):
        res = self.client.post(
            '/tournament/%d/league-adjustment/' % self.tournament.tournament_id,
            data={'player': 'Alpha', 'metric': 'PTS', 'value': -3,
                  'reason': 'x'},
            content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    # ------------------------------------------------------- head to head

    def test_head_to_head_reads_only_the_matches_between_the_two(self):
        self.tie('Alpha', 'Bravo', 3, 1, n=1)
        self.tie('Alpha', 'Charlie', 9, 0, n=2)
        res = self.client.get(
            '/tournament/%d/head-to-head/?a=Alpha&b=Bravo'
            % self.tournament.tournament_id)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual(data['matches'], 1)
        self.assertEqual(data['Alpha']['goals_for'], 3)    # not 12

    def test_head_to_head_needs_two_different_people(self):
        res = self.client.get(
            '/tournament/%d/head-to-head/?a=Alpha&b=Alpha'
            % self.tournament.tournament_id)
        self.assertEqual(res.status_code, 400)

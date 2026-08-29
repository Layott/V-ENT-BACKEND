"""Taking the data out.

PRD: "Excel or CSV Formats: Exportable spreadsheets providing detailed results
and statistics."

The results sheet is the one with a decision in it. A row per MATCH rather than
per fixture: on an aggregate league a fixture is several matches, and collapsing
them throws away exactly the detail somebody exports results to look at.
"""
import csv
import io
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet

from .models import (BracketMatch, LeagueRules, TieFixture, Tournament,
                     TournamentRegistration)
from .services import league


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('x-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('xw%s' % name)[:10], user=user, wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def rows_of(response):
    """The CSV as it actually arrives, not as it was handed to the response.

    Reading `response.data` reads the string BEFORE rendering, which is how a
    DRF Response that JSON-encodes the whole sheet passed these tests while the
    downloaded file was a quoted string with escaped newlines. Decode the body.
    """
    return list(csv.reader(io.StringIO(response.content.decode('utf-8'))))


class ExportTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('ex_org')
        self.stranger, self.stranger_auth = a_user('ex_other')
        game = Games.objects.create(game_title='EA FC EX')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Export Probe', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin', is_draft=False,
            tournament_access='team', team_size=2)
        LeagueRules.objects.create(tournament=self.tournament,
                                   players_per_team=2)

        self.regs = []
        self.seats = {}
        for name in ('Home', 'Away'):
            team = Teams.objects.create(
                team_name=name, game=game, team_creator=self.organiser,
                team_owner=self.organiser, description='', penalty_points=0,
                number_of_members=2)
            people = []
            for seat in (1, 2):
                player = a_user('ex_%s%s' % (name.lower(), seat))[0]
                TeamMembers.objects.create(team=team, user=player)
                people.append(player)
            self.seats[name] = people
            self.regs.append(TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed'))

        self.tie = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.regs[0], participant_2=self.regs[1])
        # With the players seated, because the player table is built from them
        # and an empty one is what an unseated fixture correctly produces.
        TieFixture.objects.create(
            tie=self.tie, slot=1, goals_1=3, goals_2=0, status='completed',
            player_1=self.seats['Home'][0], player_2=self.seats['Away'][0])
        TieFixture.objects.create(
            tie=self.tie, slot=2, goals_1=0, goals_2=2, status='completed',
            player_1=self.seats['Home'][1], player_2=self.seats['Away'][1])
        league.settle(self.tie)

    def url(self, sheet):
        return '/tournament/%s/export/?sheet=%s' % (self.tournament.pk, sheet)

    def get(self, sheet, auth=None):
        return self.client.get(
            '/tournament/%s/export/' % self.tournament.pk, {'sheet': sheet},
            **(auth or self.auth))

    # ------------------------------------------------------- participants

    def test_participants_come_out_with_a_header(self):
        res = self.get('participants')
        self.assertEqual(res.status_code, 200)
        rows = rows_of(res)
        self.assertEqual(rows[0][:3], ['registration_id', 'type', 'name'])
        self.assertEqual(len(rows), 3)  # header plus two teams

    def test_it_downloads_rather_than_rendering(self):
        res = self.get('participants')
        self.assertIn('attachment', res['Content-Disposition'])
        self.assertIn('csv', res['Content-Type'])
        # The body is the sheet itself, not a JSON string containing it.
        body = res.content.decode('utf-8')
        self.assertTrue(body.startswith('registration_id'), body[:60])
        # Not JSON-encoded: a rendered DRF Response arrives as one quoted string
        # with its newlines escaped inside it, so it would both start and end
        # with a quote mark. Real CSV ends with a line break.
        self.assertFalse(body.rstrip().endswith('"'))

    def test_the_names_are_the_entrants(self):
        names = {r[2] for r in rows_of(self.get('participants'))[1:]}
        self.assertEqual(names, {'Home', 'Away'})

    # ------------------------------------------------------------ results

    def test_results_are_one_row_per_match_not_per_fixture(self):
        # The fixture is two matches. A sheet with one row would hide that Away
        # won one of them, which is the whole reason to export results.
        rows = rows_of(self.get('results'))
        self.assertEqual(len(rows), 3)  # header plus two seats
        seats = sorted(r[5] for r in rows[1:])
        self.assertEqual(seats, ['1', '2'])

    def test_the_scorelines_are_in_it(self):
        rows = rows_of(self.get('results'))[1:]
        scores = sorted((r[8], r[9]) for r in rows)
        self.assertEqual(scores, [('0', '2'), ('3', '0')])

    def test_the_day_and_position_travel_with_the_match(self):
        self.tie.day = timezone.now().date()
        self.tie.running_order = 4
        self.tie.save()
        row = rows_of(self.get('results'))[1]
        self.assertEqual(row[4], '4')
        self.assertTrue(row[3])

    def test_a_fixture_with_no_seats_still_exports(self):
        # A plain knockout has no TieFixture rows, and its result is on the
        # fixture itself. It must not vanish from the sheet.
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=2, match_number=1,
            participant_1=self.regs[0], participant_2=self.regs[1],
            score_p1=1, score_p2=0, status='completed')
        rows = rows_of(self.get('results'))
        self.assertEqual(len(rows), 4)

    # ---------------------------------------------------------- standings

    def test_both_tables_are_in_the_standings_sheet(self):
        rows = rows_of(self.get('standings'))
        kinds = {r[0] for r in rows[1:]}
        self.assertEqual(kinds, {'team', 'player'})

    def test_goal_difference_is_a_column(self):
        header = rows_of(self.get('standings'))[0]
        self.assertIn('goal_difference', header)

    # ----------------------------------------------------------- refusals

    def test_a_stranger_exports_nothing(self):
        # A participant list carries contact details somebody handed over to
        # enter a competition, not to be published.
        res = self.get('participants', auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_exports_nothing(self):
        res = self.client.get('/tournament/%s/export/' % self.tournament.pk,
                              {'sheet': 'participants'})
        self.assertIn(res.status_code, (400, 401, 403))

    def test_an_unknown_sheet_is_refused(self):
        res = self.get('everything')
        self.assertEqual(res.status_code, 400)

    def test_an_unknown_tournament_is_a_404(self):
        res = self.client.get('/tournament/999999/export/',
                              {'sheet': 'participants'}, **self.auth)
        self.assertEqual(res.status_code, 404)

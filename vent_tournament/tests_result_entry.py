"""Recording a result: full scorelines, and only between seats that face.

CEO: "the results desk must capture full scorelines from match one, not just
outcomes, and the standings overlay must show goal difference on screen rather
than behind a hover."

So the endpoint takes goals rather than a winner. Four matches is a very short
sample and goal difference will decide places, probably more than once - an
outcome-only entry throws away the number that settles the table.

And a seat only ever plays its opposite number. There is no slot 3 in a two-seat
tie, and asking to record one is a mistake worth refusing rather than silently
creating.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet

from .models import (BracketMatch, LeagueRules, TieFixture, Tournament,
                     TournamentRegistration)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('r-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('rw%s' % name)[:10], user=user, wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ResultEntryTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('re_org')
        self.stranger, self.stranger_auth = a_user('re_other')
        game = Games.objects.create(game_title='EA FC RE')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Result Probe', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin', is_draft=False,
            tournament_access='team', team_size=2)
        LeagueRules.objects.create(tournament=self.tournament,
                                   players_per_team=2)

        regs = []
        for name in ('Home', 'Away'):
            team = Teams.objects.create(
                team_name=name, game=game, team_creator=self.organiser,
                team_owner=self.organiser, description='', penalty_points=0,
                number_of_members=2)
            for seat in (1, 2):
                TeamMembers.objects.create(
                    team=team, user=a_user('re_%s%s' % (name.lower(), seat))[0])
            regs.append(TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed'))

        self.tie = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=regs[0], participant_2=regs[1])
        for slot in (1, 2):
            TieFixture.objects.create(tie=self.tie, slot=slot, status='scheduled')

    def url(self):
        return '/tournament/tie/%s/record/' % self.tie.pk

    def record(self, slot, one, two, auth=None):
        return self.client.post(
            self.url(), data={'slot': slot, 'goals_1': one, 'goals_2': two},
            content_type='application/json', **(auth or self.auth))

    # ------------------------------------------------------ full scorelines

    def test_a_scoreline_is_stored_not_just_an_outcome(self):
        res = self.record(1, 3, 1)
        self.assertEqual(res.status_code, 200, res.json())
        row = TieFixture.objects.get(tie=self.tie, slot=1)
        self.assertEqual((row.goals_1, row.goals_2), (3, 1))
        self.assertEqual(row.status, 'completed')

    def test_a_nil_nil_is_a_real_result(self):
        # Zero is a score, not a missing one.
        self.record(1, 0, 0)
        row = TieFixture.objects.get(tie=self.tie, slot=1)
        self.assertEqual(row.status, 'completed')

    def test_the_running_aggregate_comes_back_after_one_match(self):
        # Reporting 0-0 here would read as "that did not save".
        body = self.record(1, 3, 0).json()['data']
        self.assertEqual(body['aggregate']['participant_1'], 3)
        self.assertEqual(body['aggregate']['participant_2'], 0)

    def test_the_tie_only_settles_when_every_seat_is_in(self):
        self.record(1, 3, 0)
        self.tie.refresh_from_db()
        self.assertNotEqual(self.tie.status, 'completed')

        self.record(2, 0, 2)
        self.tie.refresh_from_db()
        self.assertEqual(self.tie.status, 'completed')
        # 3-2 on aggregate: the side that lost a match takes the fixture.
        self.assertEqual((self.tie.score_p1, self.tie.score_p2), (3, 2))

    def test_a_score_can_be_corrected(self):
        self.record(1, 3, 0)
        self.record(1, 2, 2)
        row = TieFixture.objects.get(tie=self.tie, slot=1)
        self.assertEqual((row.goals_1, row.goals_2), (2, 2))

    # ------------------------------------------------------------ refusals

    def test_a_slot_this_tie_does_not_have_is_refused(self):
        # Seats never cross, and there is no third seat to cross to.
        res = self.record(3, 1, 0)
        self.assertEqual(res.status_code, 404, res.json())
        self.assertEqual(res.json()['code'], 'FIXTURE_NOT_FOUND')

    def test_a_negative_score_is_refused(self):
        res = self.record(1, -1, 0)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(TieFixture.objects.get(tie=self.tie, slot=1).status,
                         'scheduled')

    def test_a_score_that_is_not_a_number_is_refused(self):
        res = self.client.post(
            self.url(), data={'slot': 1, 'goals_1': 'three', 'goals_2': 0},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_records_nothing(self):
        res = self.record(1, 5, 0, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(TieFixture.objects.get(tie=self.tie, slot=1).goals_1, 0)

    def test_an_unknown_tie_is_a_404(self):
        res = self.client.post(
            '/tournament/tie/999999/record/',
            data={'slot': 1, 'goals_1': 1, 'goals_2': 0},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)

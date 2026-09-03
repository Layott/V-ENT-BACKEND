"""Who may enter results: the organiser, a scorekeeper they named, an admin.

CEO, 3 September 2026: "there will be a place to input results on the website
inside the tournament and then only those given the access to, should be able
to. Then its based off those results inputted that the leaderboards and
production and everything else gets their data."

The last sentence was already true. This file pins the middle one: a
scorekeeper may record a knockout score and a league fixture, and may do
nothing else; a stranger may do neither; removal revokes at once; every
recorded result says who entered it; and `/access/` tells every screen what
the viewer may do, 200 for everybody.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet

from .models import (BracketMatch, LeagueRules, TieFixture, Tournament,
                     TournamentRegistration, TournamentStaff)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('k-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id=('kw%s' % name)[:10], user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ResultsAccessTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('ra_org')
        self.keeper, self.keeper_auth = a_user('ra_keeper')
        self.stranger, self.stranger_auth = a_user('ra_other')
        game = Games.objects.create(game_title='EA FC RA')
        now = timezone.now()

        # A league of two-seat ties, the Rivalry shape.
        self.league = Tournament.objects.create(
            tournament_title='Access League', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='aggregate_2v2', is_draft=False,
            tournament_access='team', team_size=2)
        LeagueRules.objects.create(tournament=self.league, players_per_team=2)
        regs = []
        for name in ('Home', 'Away'):
            team = Teams.objects.create(
                team_name='RA %s' % name, game=game, team_creator=self.organiser,
                team_owner=self.organiser, description='', penalty_points=0,
                number_of_members=2)
            for seat in (1, 2):
                TeamMembers.objects.create(
                    team=team, user=a_user('ra_%s%s' % (name.lower(), seat))[0])
            regs.append(TournamentRegistration.objects.create(
                tournament=self.league, team=team, status='confirmed'))
        self.tie = BracketMatch.objects.create(
            tournament=self.league, round_number=1, match_number=1,
            participant_1=regs[0], participant_2=regs[1])
        for slot in (1, 2):
            TieFixture.objects.create(tie=self.tie, slot=slot, status='scheduled')

        # A knockout with one match, for the other recording path.
        self.knockout = Tournament.objects.create(
            tournament_title='Access Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='individual', team_size=1)
        p1, _ = a_user('ra_p1')
        p2, _ = a_user('ra_p2')
        self.r1 = TournamentRegistration.objects.create(
            tournament=self.knockout, user=p1, status='confirmed')
        self.r2 = TournamentRegistration.objects.create(
            tournament=self.knockout, user=p2, status='confirmed')
        self.match = BracketMatch.objects.create(
            tournament=self.knockout, round_number=1, match_number=1,
            participant_1=self.r1, participant_2=self.r2)

    # ------------------------------------------------------------ helpers

    def ref(self, t):
        return t.slug or t.tournament_id

    def add_keeper(self, t, username='ra_keeper', auth=None):
        return self.client.post(
            '/tournament/%s/staff/' % self.ref(t), data={'username': username},
            content_type='application/json', **(auth or self.auth))

    def record_tie(self, auth):
        return self.client.post(
            '/tournament/tie/%s/record/' % self.tie.pk,
            data={'slot': 1, 'goals_1': 3, 'goals_2': 1},
            content_type='application/json', **auth)

    def record_match(self, auth):
        return self.client.post(
            '/tournament/update-bracket/%s/' % self.ref(self.knockout),
            data={'match_id': self.match.pk, 'score_p1': 2, 'score_p2': 0,
                  'winner_registration_id': self.r1.pk},
            content_type='application/json', **auth)

    def access(self, t, auth=None):
        return self.client.get('/tournament/%s/access/' % self.ref(t), **(auth or {}))

    # ------------------------------------------------------------- access

    def test_access_answers_everybody_and_says_the_truth(self):
        self.assertEqual(self.access(self.league).json()['data'],
                         {'role': None, 'can_manage': False, 'can_record_results': False})
        self.assertEqual(self.access(self.league, self.stranger_auth).json()['data'],
                         {'role': None, 'can_manage': False, 'can_record_results': False})
        self.assertEqual(self.access(self.league, self.auth).json()['data'],
                         {'role': 'organiser', 'can_manage': True, 'can_record_results': True})
        self.add_keeper(self.league)
        self.assertEqual(self.access(self.league, self.keeper_auth).json()['data'],
                         {'role': 'scorekeeper', 'can_manage': False, 'can_record_results': True})

    # -------------------------------------------------------------- staff

    def test_the_organiser_names_a_scorekeeper_by_username(self):
        res = self.add_keeper(self.league, username='@RA_Keeper')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['added'])
        self.assertEqual([r['username'] for r in res.json()['data']['staff']], ['ra_keeper'])
        self.assertTrue(TournamentStaff.objects.filter(
            tournament=self.league, user=self.keeper, role='scorekeeper').exists())

    def test_a_scorekeeper_is_named_per_tournament(self):
        self.add_keeper(self.league)
        self.assertEqual(self.access(self.knockout, self.keeper_auth).json()['data']['role'], None)

    def test_a_stranger_and_a_scorekeeper_may_not_name_anybody(self):
        self.assertEqual(self.add_keeper(self.league, auth=self.stranger_auth).status_code, 403)
        self.add_keeper(self.league)
        res = self.add_keeper(self.league, username='ra_other', auth=self.keeper_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_TOURNAMENT_ORGANIZER')

    def test_an_unknown_username_and_the_organiser_are_refused_plainly(self):
        self.assertEqual(self.add_keeper(self.league, username='nobody_here').status_code, 404)
        res = self.add_keeper(self.league, username='ra_org')
        self.assertEqual(res.json()['code'], 'ALREADY_ORGANISER')

    def test_removal_revokes_at_once(self):
        self.add_keeper(self.league)
        self.assertEqual(self.record_tie(self.keeper_auth).status_code, 200)
        res = self.client.delete('/tournament/%s/staff/%s/' % (self.ref(self.league), self.keeper.user_id),
                                 **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['staff'], [])
        # The next slot is refused; the first stays recorded.
        res = self.client.post(
            '/tournament/tie/%s/record/' % self.tie.pk,
            data={'slot': 2, 'goals_1': 0, 'goals_2': 0},
            content_type='application/json', **self.keeper_auth)
        self.assertEqual(res.status_code, 403)

    # ---------------------------------------------------------- recording

    def test_a_scorekeeper_records_a_league_fixture_and_a_stranger_cannot(self):
        self.assertEqual(self.record_tie(self.stranger_auth).status_code, 403)
        self.assertEqual(self.record_tie(self.keeper_auth).status_code, 403)
        self.add_keeper(self.league)
        res = self.record_tie(self.keeper_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['aggregate'], {'participant_1': 3, 'participant_2': 1})

    def test_a_scorekeeper_records_a_knockout_score_and_it_says_who(self):
        self.assertEqual(self.record_match(self.stranger_auth).status_code, 403)
        self.add_keeper(self.knockout)
        res = self.record_match(self.keeper_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.match.refresh_from_db()
        self.assertEqual((self.match.score_p1, self.match.score_p2), (2, 0))
        self.assertEqual(self.match.winner_id, self.r1.pk)
        self.assertEqual(self.match.recorded_by_id, self.keeper.user_id)
        self.assertIsNotNone(self.match.recorded_at)

    def test_the_organiser_records_and_it_says_them(self):
        self.assertEqual(self.record_match(self.auth).status_code, 200)
        self.match.refresh_from_db()
        self.assertEqual(self.match.recorded_by_id, self.organiser.user_id)

    # ------------------------------------------------ nothing else gained

    def test_a_scorekeeper_gains_nothing_else(self):
        self.add_keeper(self.league)
        ref = self.ref(self.league)
        # Not the studio.
        self.assertEqual(self.client.get('/tournament/%s/studio/sessions/' % ref,
                                         **self.keeper_auth).status_code, 403)
        # Not the overlays.
        self.assertEqual(self.client.get('/tournament/%s/overlays/' % ref,
                                         **self.keeper_auth).status_code, 403)
        # Not the staff list.
        self.assertEqual(self.client.get('/tournament/%s/staff/' % ref,
                                         **self.keeper_auth).status_code, 403)
        # Not the tournament itself.
        res = self.client.put('/tournament/edit-tournament/%d/' % self.league.tournament_id,
                              data={'tournament_title': 'Stolen'},
                              content_type='application/json', **self.keeper_auth)
        self.assertEqual(res.status_code, 403)
        # Not the league's points.
        res = self.client.post('/tournament/%s/league-rules/' % ref,
                               data={'points_win': 9}, content_type='application/json',
                               **self.keeper_auth)
        self.assertEqual(res.status_code, 403)

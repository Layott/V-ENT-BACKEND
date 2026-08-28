"""The match list an override picks from.

The override form asked for a match id and a winner registration id, typed by
hand, with a tooltip saying "find it on the bracket". Getting it wrong overwrites
a result somebody played and won, so the fix is to stop asking for numbers.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Teams, Users
from vent_tournament.models import (
    BracketMatch, Tournament, TournamentRegistration,
)


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('tk-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class MatchListTests(TestCase):
    def setUp(self):
        self.admin, self.auth = a_user('m_admin', is_staff=True, admin_role='super_admin')
        self.owner, _ = a_user('m_owner')
        self.p1, _ = a_user('ada')
        self.p2, _ = a_user('bola')
        game = Games.objects.get_or_create(game_title='Match Probe')[0]
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Match Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2), is_draft=False,
        )
        self.r1 = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.p1, status='confirmed')
        self.r2 = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.p2, status='confirmed')
        self.match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.r1, participant_2=self.r2, status='scheduled')

    def url(self):
        return '/auth/admin/tournaments/%s/matches/' % self.tournament.tournament_id

    def test_every_match_comes_back_named(self):
        """The whole point: names, not ids to copy off another tab."""
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        match = res.json()['data']['matches'][0]
        self.assertEqual(match['side_1']['name'], 'ada')
        self.assertEqual(match['side_2']['name'], 'bola')
        self.assertEqual(match['label'], 'Round 1, match 1')

    def test_it_carries_what_the_form_needs_to_submit(self):
        match = self.client.get(self.url(), **self.auth).json()['data']['matches'][0]
        self.assertEqual(match['id'], self.match.id)
        self.assertEqual(match['side_1']['registration_id'], self.r1.id)
        self.assertEqual(match['side_2']['registration_id'], self.r2.id)

    def test_the_current_score_comes_with_it(self):
        """So an override meaning to fix one number does not zero the other."""
        self.match.score_p1 = 3
        self.match.score_p2 = 1
        self.match.status = 'completed'
        self.match.winner = self.r1
        self.match.save()
        match = self.client.get(self.url(), **self.auth).json()['data']['matches'][0]
        self.assertEqual((match['score_1'], match['score_2']), (3, 1))
        self.assertEqual(match['winner_registration_id'], self.r1.id)

    def test_a_bye_is_listed_but_not_playable(self):
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=2,
            participant_1=self.r1, participant_2=None, status='bye')
        data = self.client.get(self.url(), **self.auth).json()['data']
        self.assertEqual(data['counts']['matches'], 2)
        self.assertEqual(data['counts']['playable'], 1)

    def test_a_moderator_may_read_it_and_a_finance_admin_may_not(self):
        """Correcting a result is moderation, not money."""
        _mod, mod_auth = a_user('m_mod', is_staff=True, admin_role='mod_admin')
        _fin, fin_auth = a_user('m_fin', is_staff=True, admin_role='finance_admin')
        self.assertEqual(self.client.get(self.url(), **mod_auth).status_code, 200)
        self.assertEqual(self.client.get(self.url(), **fin_auth).status_code, 403)

    def test_an_unknown_tournament_is_a_404(self):
        res = self.client.get('/auth/admin/tournaments/999999/matches/', **self.auth)
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_signed_out_caller_gets_nothing(self):
        res = self.client.get(self.url())
        self.assertIn(res.status_code, (400, 401), res.content)


class DisqualifyTests(TestCase):
    """Disqualifying used to flip a status and stop.

    The team stayed in the bracket, and its opponents were left waiting on a
    match that would never be played. "Even the edit tournament and dq dont do
    enough" was the report, and this is the half of it that was doing nothing.
    """

    def setUp(self):
        self.admin, self.auth = a_user('dq_admin', is_staff=True, admin_role='super_admin')
        owner, _ = a_user('dq_owner')
        self.p1, _ = a_user('cheater')
        self.p2, _ = a_user('honest')
        game = Games.objects.get_or_create(game_title='DQ Probe')[0]
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='DQ Probe Cup', tournament_creator=owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2), is_draft=False,
        )
        self.bad = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.p1, status='confirmed')
        self.good = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.p2, status='confirmed')
        self.upcoming = BracketMatch.objects.create(
            tournament=self.tournament, round_number=2, match_number=1,
            participant_1=self.bad, participant_2=self.good, status='scheduled')
        self.played = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.bad, participant_2=self.good,
            status='completed', winner=self.bad, score_p1=2, score_p2=0)

    def dq(self, **body):
        return self.client.post(
            '/auth/admin/tournaments/%s/disqualify/' % self.tournament.tournament_id,
            data=body, content_type='application/json', **self.auth)

    def test_an_upcoming_match_is_forfeited_to_the_opponent(self):
        res = self.dq(registration_id=self.bad.id, reason='Cheating')
        self.assertEqual(res.status_code, 200, res.content)
        self.upcoming.refresh_from_db()
        self.assertEqual(self.upcoming.status, 'completed')
        self.assertEqual(self.upcoming.winner_id, self.good.id)

    def test_a_match_already_played_is_left_alone(self):
        """It happened. Rewriting a result somebody won is not what this is for."""
        self.dq(registration_id=self.bad.id)
        self.played.refresh_from_db()
        self.assertEqual(self.played.winner_id, self.bad.id)
        self.assertEqual((self.played.score_p1, self.played.score_p2), (2, 0))

    def test_the_answer_says_how_many_were_forfeited(self):
        body = self.dq(registration_id=self.bad.id).json()
        self.assertEqual(len(body['data']['forfeited_matches']), 1)
        self.assertIn('forfeited', body['message'])

    def test_the_registration_is_marked(self):
        self.dq(registration_id=self.bad.id)
        self.bad.refresh_from_db()
        self.assertEqual(self.bad.status, 'disqualified')

    def test_the_participant_list_says_what_will_happen(self):
        """Worked out before the click rather than discovered after it."""
        res = self.client.get(
            '/auth/admin/tournaments/%s/matches/' % self.tournament.tournament_id,
            **self.auth)
        rows = {p['name']: p for p in res.json()['data']['participants']}
        self.assertEqual(rows['cheater']['live_matches'], 1)
        self.assertEqual(rows['cheater']['status'], 'confirmed')

    def test_a_name_that_matches_nobody_is_a_404(self):
        res = self.dq(team_name='Nobody At All')
        self.assertEqual(res.status_code, 404, res.content)

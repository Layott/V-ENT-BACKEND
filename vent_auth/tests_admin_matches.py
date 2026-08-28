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

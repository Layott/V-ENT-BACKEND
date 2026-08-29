"""An organiser asking a named player or a named team to enter.

CEO, 29 August 2026: "tournament organizers, should be able to invite people or
teams to their events."

The behaviours worth pinning:

- accepting is not registering, because registration is the path that checks
  entry requirements and takes the entry fee, and an invitation that quietly
  registered somebody would quietly charge them;
- asking twice is a reminder, not a second row, or the recipient's list fills
  up with the same tournament;
- a team's invitation is answered by whoever owns the team, because entering
  commits the roster;
- withdrawing marks it withdrawn rather than deleting it, so the notification
  the recipient already has still points at something.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Teams, Users
from vent_tournament.models import Tournament, TournamentInvitation


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name.title(),
        login_session_token=('tk-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class InvitationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Invite Probe')[0]
        self.organiser = a_user('inv_org')
        self.player = a_user('inv_player')
        self.captain = a_user('inv_captain')
        self.stranger = a_user('inv_stranger')

        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Invite Probe Cup', tournament_creator=self.organiser,
            tournament_game=self.game, tournament_type='online',
            tournament_access='team_and_individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            start_date_and_time=now + timezone.timedelta(days=3),
            end_date_and_time=now + timezone.timedelta(days=4),
            is_draft=False)

        self.team = Teams.objects.create(
            team_name='Probe Squad', game=self.game,
            team_creator=self.captain, team_owner=self.captain,
            description='x', penalty_points=0, number_of_members=1)

    def _as(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)

    def _key(self):
        return self.tournament.slug or self.tournament.tournament_id

    def _invite(self, **payload):
        self._as(self.organiser)
        return self.client.post('/tournament/%s/invitations/' % self._key(),
                                payload, format='json')

    def _respond(self, invitation_id, answer, as_user):
        self._as(as_user)
        return self.client.post(
            '/tournament/%s/invitations/%d/respond/' % (self._key(), invitation_id),
            {'answer': answer}, format='json')

    # ------------------------------------------------------------------ asking

    def test_a_player_can_be_invited(self):
        res = self._invite(username='inv_player', message='Come and play')
        self.assertEqual(res.status_code, 201, res.data)
        invitation = TournamentInvitation.objects.get()
        self.assertEqual(invitation.user_id, self.player.user_id)
        self.assertEqual(invitation.status, TournamentInvitation.PENDING)

    def test_a_team_can_be_invited(self):
        res = self._invite(team='Probe Squad')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(TournamentInvitation.objects.get().team_id,
                         self.team.team_id)

    def test_the_recipient_is_told(self):
        from vent_auth.models import Notification
        self._invite(username='inv_player')
        self.assertTrue(
            Notification.objects.filter(user=self.player).exists(),
            'the player was invited and never told')

    def test_a_team_invitation_tells_the_owner(self):
        from vent_auth.models import Notification
        self._invite(team='Probe Squad')
        self.assertTrue(Notification.objects.filter(user=self.captain).exists())

    def test_naming_neither_or_both_is_refused(self):
        self.assertEqual(self._invite().status_code, 400)
        self.assertEqual(
            self._invite(username='inv_player', team='Probe Squad').status_code, 400)

    def test_somebody_who_does_not_exist_is_refused(self):
        self.assertEqual(self._invite(username='nobody_here').status_code, 404)
        self.assertEqual(self._invite(team='No Such Team').status_code, 404)

    def test_asking_twice_is_a_reminder_not_a_second_row(self):
        self._invite(username='inv_player')
        again = self._invite(username='inv_player')
        self.assertEqual(again.status_code, 200, again.data)
        self.assertTrue(again.data['data']['reminded'])
        self.assertEqual(TournamentInvitation.objects.count(), 1)

    def test_asking_again_after_a_decline_reopens_the_same_row(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        self._respond(invitation.id, 'decline', self.player)
        self._invite(username='inv_player', message='Please reconsider')
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, TournamentInvitation.PENDING)
        self.assertEqual(TournamentInvitation.objects.count(), 1)

    def test_asking_somebody_who_accepted_is_refused(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        self._respond(invitation.id, 'accept', self.player)
        res = self._invite(username='inv_player')
        self.assertEqual(res.status_code, 409, res.data)

    # ---------------------------------------------------------------- answering

    def test_the_player_can_accept(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        res = self._respond(invitation.id, 'accept', self.player)
        self.assertEqual(res.status_code, 200, res.data)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, TournamentInvitation.ACCEPTED)

    def test_accepting_does_not_register_anybody(self):
        from vent_tournament.models import TournamentRegistration
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        res = self._respond(invitation.id, 'accept', self.player)
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament).exists(),
            'accepting an invitation registered somebody without charging them')
        # And the response says where registration happens.
        self.assertIn('register', res.data['data']['next'])

    def test_a_team_invitation_is_answered_by_its_owner(self):
        self._invite(team='Probe Squad')
        invitation = TournamentInvitation.objects.get()
        self.assertEqual(self._respond(invitation.id, 'accept', self.captain).status_code, 200)

    def test_somebody_else_cannot_answer_it(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        res = self._respond(invitation.id, 'accept', self.stranger)
        self.assertEqual(res.status_code, 403, res.data)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, TournamentInvitation.PENDING)

    def test_the_organiser_cannot_accept_on_their_behalf(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        self.assertEqual(
            self._respond(invitation.id, 'accept', self.organiser).status_code, 403)

    def test_a_nonsense_answer_is_refused(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        self.assertEqual(self._respond(invitation.id, 'maybe', self.player).status_code, 400)

    def test_the_organiser_is_told_the_answer(self):
        from vent_auth.models import Notification
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        Notification.objects.filter(user=self.organiser).delete()
        self._respond(invitation.id, 'accept', self.player)
        self.assertTrue(Notification.objects.filter(user=self.organiser).exists())

    # -------------------------------------------------------------- withdrawing

    def test_the_organiser_can_withdraw_one(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        self._as(self.organiser)
        res = self.client.delete(
            '/tournament/%s/invitations/%d/' % (self._key(), invitation.id))
        self.assertEqual(res.status_code, 200, res.data)
        invitation.refresh_from_db()
        # Marked, not deleted: the recipient already has a notification.
        self.assertEqual(invitation.status, TournamentInvitation.WITHDRAWN)
        self.assertTrue(TournamentInvitation.objects.filter(pk=invitation.pk).exists())

    def test_a_withdrawn_invitation_cannot_be_accepted(self):
        self._invite(username='inv_player')
        invitation = TournamentInvitation.objects.get()
        self._as(self.organiser)
        self.client.delete('/tournament/%s/invitations/%d/' % (self._key(), invitation.id))
        res = self._respond(invitation.id, 'accept', self.player)
        self.assertEqual(res.status_code, 409, res.data)

    # ------------------------------------------------------------- who may look

    def test_only_the_organiser_sees_the_list(self):
        self._invite(username='inv_player')
        self._as(self.stranger)
        res = self.client.get('/tournament/%s/invitations/' % self._key())
        self.assertEqual(res.status_code, 403, res.data)

    def test_the_organiser_sees_who_was_asked_and_what_they_said(self):
        self._invite(username='inv_player')
        self._invite(team='Probe Squad')
        self._as(self.organiser)
        rows = self.client.get(
            '/tournament/%s/invitations/' % self._key()).data['data']['invitations']
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(r['player'] for r in rows))
        self.assertTrue(any(r['team'] for r in rows))

    def test_signed_out_cannot_send_one(self):
        self.client.credentials()
        res = self.client.post('/tournament/%s/invitations/' % self._key(),
                               {'username': 'inv_player'}, format='json')
        self.assertEqual(res.status_code, 401, res.data)
        self.assertFalse(TournamentInvitation.objects.exists())

    def test_somebody_who_does_not_run_it_cannot_send_one(self):
        self._as(self.stranger)
        res = self.client.post('/tournament/%s/invitations/' % self._key(),
                               {'username': 'inv_player'}, format='json')
        self.assertEqual(res.status_code, 403, res.data)
        self.assertFalse(TournamentInvitation.objects.exists())

"""Check-in and match reminders.

Two things cost an entrant their place and both are silent: check-in closing,
and a fixture becoming theirs when somebody else finishes.

The parts worth pinning: a reminder skips people who have already done the
thing, a team reminder reaches every member rather than the captain, and a
match reminder names the opponent.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import (Games, Notification, TeamMembers, Teams, Users,
                              UserWallet)

from .models import (BracketMatch, Tournament, TournamentRegistration)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('r-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id=('rw%s' % name)[:10], user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ReminderBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('rm_org')
        self.stranger, self.stranger_auth = a_user('rm_other')
        self.game = Games.objects.create(game_title='EA FC RM')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Reminder Probe', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(minutes=20),
            end_date_and_time=now + timedelta(hours=4),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='team', team_size=2,
            options={'check_in_minutes': 30, 'forfeit_without_check_in': True})

        self.regs = {}
        self.members = {}
        for name in ('Home', 'Away'):
            team = Teams.objects.create(
                team_name=name, game=self.game, team_creator=self.organiser,
                team_owner=self.organiser, description='', penalty_points=0,
                number_of_members=2)
            people = []
            for seat in (1, 2):
                player = a_user('rm_%s%d' % (name.lower(), seat))[0]
                TeamMembers.objects.create(team=team, user=player)
                people.append(player)
            self.members[name] = people
            self.regs[name] = TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed')

    def remind(self, payload=None, auth=None):
        return self.client.post(
            '/tournament/%s/remind/' % self.tournament.pk,
            payload or {'kind': 'check_in'},
            content_type='application/json', **(auth or self.auth))

    def audience(self, auth=None):
        return self.client.get(
            '/tournament/%s/remind/audience/' % self.tournament.pk,
            **(auth or self.auth))


class CheckInReminderTests(ReminderBase):
    def test_it_reaches_every_member_of_a_team(self):
        # The captain is not reliably the person who turns up.
        res = self.remind()
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['people'], 4)

    def test_it_skips_whoever_already_checked_in(self):
        # Reminding people who already did it is how a reminder becomes
        # something entrants filter.
        reg = self.regs['Home']
        reg.checked_in_at = timezone.now()
        reg.save()
        res = self.remind()
        self.assertEqual(res.data['data']['entrants'], 1)
        self.assertEqual(res.data['data']['people'], 2)

    def test_the_notification_says_when_it_closes(self):
        self.remind()
        row = Notification.objects.filter(
            user=self.members['Home'][0], category='tournament').first()
        self.assertIn('Check in for', row.title)
        self.assertIn('closes at', row.body)

    def test_it_warns_about_the_forfeit_when_that_is_the_rule(self):
        self.remind()
        body = Notification.objects.filter(
            user=self.members['Home'][0]).first().body
        self.assertIn('substitute', body)

    def test_it_does_not_threaten_a_forfeit_that_is_not_the_rule(self):
        self.tournament.options = {'check_in_minutes': 30,
                                   'forfeit_without_check_in': False}
        self.tournament.save()
        self.remind()
        body = Notification.objects.filter(
            user=self.members['Home'][0]).first().body
        self.assertNotIn('substitute', body)

    def test_the_link_carries_the_slug_not_the_key(self):
        self.remind()
        link = Notification.objects.filter(
            user=self.members['Home'][0]).first().link
        self.assertNotIn('?id=', link)
        self.assertIn(self.tournament.slug, link)

    def test_a_tournament_without_check_in_refuses_the_reminder(self):
        self.tournament.options = {'check_in_minutes': 0}
        self.tournament.save()
        res = self.remind()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOT_REQUIRED')

    def test_a_pending_entrant_is_not_reminded(self):
        # They have no slot to lose yet.
        self.regs['Away'].status = 'pending'
        self.regs['Away'].save()
        res = self.remind()
        self.assertEqual(res.data['data']['entrants'], 1)


class MatchReminderTests(ReminderBase):
    def setUp(self):
        super().setUp()
        self.tie = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.regs['Home'], participant_2=self.regs['Away'],
            scheduled_at=timezone.now() + timedelta(hours=1))

    def test_both_sides_are_told(self):
        res = self.remind({'kind': 'match'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['entrants'], 2)
        self.assertEqual(res.data['data']['people'], 4)

    def test_the_reminder_names_the_opponent(self):
        # A reminder that does not say who you are playing sends the entrant
        # looking for the bracket.
        self.remind({'kind': 'match'})
        title = Notification.objects.filter(
            user=self.members['Home'][0]).first().title
        self.assertIn('Away', title)

    def test_the_time_travels_with_it(self):
        self.remind({'kind': 'match'})
        body = Notification.objects.filter(
            user=self.members['Home'][0]).first().body
        self.assertIn('Match 1 of round 1 at', body)

    def test_a_finished_fixture_reminds_nobody(self):
        self.tie.status = 'completed'
        self.tie.save()
        res = self.remind({'kind': 'match'})
        self.assertEqual(res.data['data']['entrants'], 0)

    def test_a_bye_reminds_nobody(self):
        self.tie.status = 'bye'
        self.tie.save()
        res = self.remind({'kind': 'match'})
        self.assertEqual(res.data['data']['entrants'], 0)

    def test_a_side_still_to_be_decided_is_skipped_not_crashed_on(self):
        self.tie.participant_2 = None
        self.tie.save()
        res = self.remind({'kind': 'match'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['entrants'], 1)
        title = Notification.objects.filter(
            user=self.members['Home'][0]).first().title
        self.assertIn('yet to be decided', title)


class ReminderRefusalTests(ReminderBase):
    def test_a_stranger_sends_nothing(self):
        res = self.remind(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Notification.objects.count(), 0)

    def test_signed_out_sends_nothing(self):
        res = self.client.post('/tournament/%s/remind/' % self.tournament.pk,
                               {'kind': 'check_in'},
                               content_type='application/json')
        self.assertIn(res.status_code, (400, 401, 403))

    def test_an_unknown_kind_is_refused(self):
        self.assertEqual(self.remind({'kind': 'everything'}).status_code, 400)

    def test_a_custom_message_needs_a_subject_and_a_body(self):
        self.assertEqual(
            self.remind({'kind': 'custom', 'subject': 'Hi'}).status_code, 400)

    def test_a_custom_message_reaches_every_confirmed_entrant(self):
        res = self.remind({'kind': 'custom', 'subject': 'Bring your own pad',
                           'body': 'The venue has four.'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['people'], 4)
        self.assertEqual(
            Notification.objects.filter(title='Bring your own pad').count(), 4)

    def test_the_sixth_reminder_today_is_refused(self):
        for _ in range(5):
            self.assertEqual(self.remind().status_code, 200)
        res = self.remind()
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data['code'], 'RATE_LIMITED')

    def test_a_batch_counts_as_one_reminder_not_one_per_person(self):
        # Four people were written to, and that is one reminder.
        self.remind()
        self.assertEqual(self.audience().data['data']['sent_today'], 1)

    def test_the_audience_is_counted_before_anything_is_written(self):
        res = self.audience()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['check_in']['entrants'], 2)
        self.assertTrue(res.data['data']['check_in']['used'])
        self.assertEqual(res.data['data']['custom']['entrants'], 2)

    def test_a_stranger_cannot_count_the_audience(self):
        self.assertEqual(self.audience(auth=self.stranger_auth).status_code, 403)

    def test_an_unknown_tournament_is_a_404(self):
        res = self.client.post('/tournament/999999/remind/', {'kind': 'check_in'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)

"""Reminders set now and sent later.

The decision worth pinning is the one about time: a scheduled reminder stores an
ANCHOR and an OFFSET, not a timestamp. An organiser means "an hour before
check-in opens", and that has to survive them moving the tournament - which is
the most common edit there is, and the one a computed timestamp gets wrong
silently.

The rest is about the command being safe to run on a cron: it sends what is
due, it sends it once, and when it will not send it says why on the row rather
than in a log nobody reads.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import (Games, Notification, TeamMembers, Teams, Users,
                              UserWallet)

from .models import ScheduledReminder, Tournament, TournamentRegistration


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('s-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id='w%09d' % user.user_id, user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ScheduledBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('sc_org')
        self.stranger, self.stranger_auth = a_user('sc_other')
        self.game = Games.objects.create(game_title='EA FC SC')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Scheduled Probe', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(hours=4),
            end_date_and_time=now + timedelta(hours=8),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='team', team_size=2,
            options={'check_in_minutes': 30, 'forfeit_without_check_in': True})

        team = Teams.objects.create(
            team_name='Alpha SC', game=self.game, team_creator=self.organiser,
            team_owner=self.organiser, description='', penalty_points=0,
            number_of_members=2)
        self.players = []
        for seat in (1, 2):
            player = a_user('sc_p%d' % seat)[0]
            TeamMembers.objects.create(team=team, user=player)
            self.players.append(player)
        self.registration = TournamentRegistration.objects.create(
            tournament=self.tournament, team=team, status='confirmed')

    def url(self):
        return '/tournament/%s/remind/scheduled/' % self.tournament.pk

    def schedule(self, payload=None, auth=None):
        return self.client.post(
            self.url(),
            payload if payload is not None else {'kind': 'check_in',
                                                 'anchor': 'check_in_opens',
                                                 'offset_minutes': 60},
            content_type='application/json', **(auth or self.auth))

    def listing(self, auth=None):
        return self.client.get(self.url(), **(auth or self.auth))

    def run_command(self, **kwargs):
        out = StringIO()
        call_command('send_due_reminders', stdout=out, **kwargs)
        return out.getvalue()


class SchedulingTests(ScheduledBase):
    def test_an_organiser_schedules_one(self):
        res = self.schedule()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(ScheduledReminder.objects.count(), 1)

    def test_it_appears_in_the_diary(self):
        self.schedule()
        res = self.listing()
        self.assertEqual(res.status_code, 200)
        rows = res.data['data']['scheduled']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['due_at'])
        self.assertTrue(rows[0]['schedulable'])

    def test_the_due_time_is_the_anchor_minus_the_offset(self):
        self.schedule({'kind': 'check_in', 'anchor': 'check_in_opens',
                       'offset_minutes': 60})
        row = ScheduledReminder.objects.get()
        # Check-in opens 30 minutes before the start, and this goes an hour
        # before that.
        expected = self.tournament.start_date_and_time - timedelta(minutes=90)
        self.assertLess(abs((row.due_at() - expected).total_seconds()), 2)

    def test_moving_the_tournament_moves_the_reminder(self):
        # The whole reason it is an anchor and not a timestamp.
        self.schedule()
        row = ScheduledReminder.objects.get()
        before = row.due_at()
        self.tournament.start_date_and_time += timedelta(days=2)
        self.tournament.save()
        row.refresh_from_db()
        after = row.due_at()
        self.assertEqual((after - before).days, 2)

    def test_a_negative_offset_means_after_the_anchor(self):
        self.schedule({'kind': 'check_in', 'anchor': 'check_in_opens',
                       'offset_minutes': -15})
        row = ScheduledReminder.objects.get()
        opens = self.tournament.start_date_and_time - timedelta(minutes=30)
        self.assertGreater(row.due_at(), opens)

    def test_a_fixed_time_is_stored_as_one(self):
        when = timezone.now() + timedelta(days=1)
        self.schedule({'kind': 'custom', 'anchor': 'fixed',
                       'fixed_at': when.isoformat(),
                       'subject': 'Bring a pad', 'body': 'The venue has four.'})
        row = ScheduledReminder.objects.get()
        self.assertEqual(row.anchor, 'fixed')
        self.assertLess(abs((row.due_at() - when).total_seconds()), 2)

    def test_a_fixed_time_in_the_past_is_refused(self):
        res = self.schedule({'kind': 'custom', 'anchor': 'fixed',
                             'fixed_at': (timezone.now() - timedelta(hours=1)).isoformat(),
                             'subject': 'a', 'body': 'b'})
        self.assertEqual(res.status_code, 400)

    def test_a_custom_reminder_needs_words(self):
        res = self.schedule({'kind': 'custom', 'anchor': 'check_in_opens',
                             'offset_minutes': 30})
        self.assertEqual(res.status_code, 400)

    def test_an_unknown_anchor_is_refused(self):
        res = self.schedule({'kind': 'check_in', 'anchor': 'the_vibes'})
        self.assertEqual(res.status_code, 400)

    def test_an_absurd_offset_is_refused(self):
        res = self.schedule({'kind': 'check_in', 'anchor': 'check_in_opens',
                             'offset_minutes': 60 * 24 * 30})
        self.assertEqual(res.status_code, 400)

    def test_a_tournament_with_no_check_in_can_still_be_scheduled_around(self):
        # It simply never comes due, and the screen is told so rather than the
        # organiser being refused for a setting they may add later.
        self.tournament.options = {'check_in_minutes': 0}
        self.tournament.save()
        res = self.schedule()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(res.data['data']['scheduled']['schedulable'])

    def test_ten_waiting_is_the_limit(self):
        for _ in range(10):
            self.assertEqual(self.schedule().status_code, 201)
        res = self.schedule()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'TOO_MANY_SCHEDULED')

    def test_a_cancelled_one_does_not_count_towards_the_limit(self):
        for _ in range(10):
            self.schedule()
        row = ScheduledReminder.objects.first()
        self.client.delete('%s%d/' % (self.url(), row.pk), **self.auth)
        self.assertEqual(self.schedule().status_code, 201)

    def test_a_stranger_schedules_nothing(self):
        res = self.schedule(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(ScheduledReminder.objects.count(), 0)

    def test_a_stranger_cannot_read_the_diary(self):
        self.assertEqual(self.listing(auth=self.stranger_auth).status_code, 403)


class CancellingTests(ScheduledBase):
    def test_the_organiser_calls_one_off(self):
        self.schedule()
        row = ScheduledReminder.objects.get()
        res = self.client.delete('%s%d/' % (self.url(), row.pk), **self.auth)
        self.assertEqual(res.status_code, 200, res.data)
        row.refresh_from_db()
        self.assertIsNotNone(row.cancelled_at)

    def test_cancelling_keeps_the_row(self):
        # The diary keeps its history; an organiser can see they changed their
        # mind rather than wondering whether they ever scheduled it.
        self.schedule()
        row = ScheduledReminder.objects.get()
        self.client.delete('%s%d/' % (self.url(), row.pk), **self.auth)
        self.assertEqual(ScheduledReminder.objects.count(), 1)

    def test_one_already_sent_cannot_be_cancelled(self):
        self.schedule()
        row = ScheduledReminder.objects.get()
        row.sent_at = timezone.now()
        row.save()
        res = self.client.delete('%s%d/' % (self.url(), row.pk), **self.auth)
        self.assertEqual(res.status_code, 409)

    def test_a_stranger_cancels_nothing(self):
        self.schedule()
        row = ScheduledReminder.objects.get()
        res = self.client.delete('%s%d/' % (self.url(), row.pk),
                                 **self.stranger_auth)
        self.assertEqual(res.status_code, 403)


class SendingTests(ScheduledBase):
    def due_now(self, **overrides):
        """One reminder whose moment has arrived."""
        fields = dict(tournament=self.tournament, kind='check_in',
                      anchor='fixed',
                      fixed_at=timezone.now() - timedelta(minutes=1))
        fields.update(overrides)
        return ScheduledReminder.objects.create(**fields)

    def test_a_due_reminder_is_sent(self):
        self.due_now()
        out = self.run_command()
        self.assertIn('Sent 1', out)
        self.assertEqual(
            Notification.objects.filter(category='tournament').count(), 2)

    def test_it_is_marked_sent_with_who_it_reached(self):
        row = self.due_now()
        self.run_command()
        row.refresh_from_db()
        self.assertIsNotNone(row.sent_at)
        self.assertEqual(row.people_reached, 2)

    def test_running_twice_sends_once(self):
        # The whole reason this is safe on a cron.
        self.due_now()
        self.run_command()
        self.run_command()
        self.assertEqual(
            Notification.objects.filter(category='tournament').count(), 2)

    def test_one_not_yet_due_waits(self):
        self.due_now(fixed_at=timezone.now() + timedelta(hours=2))
        out = self.run_command()
        self.assertIn('Nothing due', out)

    def test_a_cancelled_one_never_goes(self):
        self.due_now(cancelled_at=timezone.now())
        out = self.run_command()
        self.assertIn('Nothing due', out)

    def test_a_dry_run_changes_nothing(self):
        row = self.due_now()
        out = self.run_command(dry_run=True)
        self.assertIn('Would send', out)
        row.refresh_from_db()
        self.assertIsNone(row.sent_at)
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_refusal_is_recorded_on_the_row(self):
        # An organiser whose reminder was skipped is owed the reason on the
        # screen where they scheduled it, not in a log nobody reads.
        self.tournament.options = {'check_in_minutes': 0}
        self.tournament.save()
        row = self.due_now()
        out = self.run_command()
        self.assertIn('skipped 1', out)
        row.refresh_from_db()
        self.assertTrue(row.skipped_reason)
        self.assertIsNotNone(row.sent_at)

    def test_a_reminder_that_cannot_be_placed_on_the_clock_never_fires(self):
        # A check-in anchor on a tournament that uses no check-in. There is no
        # moment to measure from, so it waits rather than firing at some
        # invented time - and `start_date_and_time` is NOT NULL, so this is the
        # only way the anchor can genuinely be absent.
        self.tournament.options = {'check_in_minutes': 0}
        self.tournament.save()
        row = ScheduledReminder.objects.create(
            tournament=self.tournament, kind='check_in',
            anchor='check_in_opens', offset_minutes=60)
        self.assertIsNone(row.due_at())
        out = self.run_command()
        self.assertIn('Nothing due', out)

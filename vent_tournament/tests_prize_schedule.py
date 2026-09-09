"""Prizes paid on a timer, and the warning that has to come first.

The order is the promise: nothing is paid that the organiser was not told about,
unless they asked for no notice. So the warning pass and the paying pass are
tested as a sequence rather than one at a time.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Notification, UserWallet

from .models import PrizePayout, PrizeSchedule, TournamentRegistration
from .tests import PrizeDistributionTests, client_for, make_user


class PrizeScheduleBase(TestCase):
    """A finished tournament with two prize positions, ready to be paid."""

    def setUp(self):
        # Reuse the fixture that already builds a completed bracket with
        # final positions and prize rows, rather than writing a second one that
        # could drift from it: these tests are only meaningful if the money
        # they schedule is the money the manual path would pay.
        self.org, self.tournament = (
            PrizeDistributionTests()._completed_tournament_with_prizes())
        self.client = client_for(self.org)
        self.ref = self.tournament.tournament_id

    def _premium(self):
        self.org.is_premium = True
        self.org.save(update_fields=['is_premium'])


class PlanTests(PrizeScheduleBase):

    def test_the_plan_names_everybody_and_totals_it(self):
        res = self.client.get('/tournament/%s/prizes/plan/' % self.ref)
        self.assertEqual(res.status_code, 200)
        data = res.data['data']
        self.assertEqual(data['total'], 1500)
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['rows'][0]['position'], 1)
        self.assertEqual(data['rows'][0]['amount'], 1000)
        self.assertTrue(data['rows'][0]['name'])
        self.assertEqual(data['problems'], [])

    def test_the_plan_pays_nobody(self):
        self.client.get('/tournament/%s/prizes/plan/' % self.ref)
        self.assertEqual(PrizePayout.objects.count(), 0)

    def test_it_is_not_a_public_list(self):
        stranger = client_for(make_user(430))
        res = stranger.get('/tournament/%s/prizes/plan/' % self.ref)
        self.assertEqual(res.status_code, 403)

    def test_a_position_nobody_finished_is_named_rather_than_hidden(self):
        from .models import TournamentPrizeDistribution
        TournamentPrizeDistribution.objects.create(
            tournament=self.tournament, position=9, prize=250, extras='')
        res = self.client.get('/tournament/%s/prizes/plan/' % self.ref)
        rows = {r['position']: r for r in res.data['data']['rows']}
        self.assertEqual(rows[9]['problem'], 'nobody_finished_here')
        # And the money that is not going anywhere is not in the total.
        self.assertEqual(res.data['data']['total'], 1500)


class ScheduleTests(PrizeScheduleBase):

    def _post(self, **body):
        return self.client.post('/tournament/%s/prizes/schedule/' % self.ref,
                                body, format='json')

    def test_it_is_premium(self):
        res = self._post(run_at=(timezone.now() + timedelta(days=1)).isoformat())
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.data['code'], 'PREMIUM_REQUIRED')
        self.assertFalse(PrizeSchedule.objects.exists())

    def test_a_premium_organiser_can_set_one(self):
        self._premium()
        when = timezone.now() + timedelta(days=2)
        res = self._post(run_at=when.isoformat(), warn_hours=6)
        self.assertEqual(res.status_code, 200, res.data)
        schedule = PrizeSchedule.objects.get()
        self.assertEqual(schedule.warn_hours, 6)
        self.assertEqual(schedule.state, 'scheduled')
        self.assertEqual(schedule.warn_at(), schedule.run_at - timedelta(hours=6))

    def test_a_time_that_has_passed_is_refused(self):
        self._premium()
        res = self._post(run_at=(timezone.now() - timedelta(hours=1)).isoformat())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'IN_THE_PAST')

    def test_rescheduling_forgets_the_old_warning(self):
        """Somebody told about Friday has not been told about Saturday."""
        self._premium()
        self._post(run_at=(timezone.now() + timedelta(days=1)).isoformat())
        schedule = PrizeSchedule.objects.get()
        schedule.warned_at = timezone.now()
        schedule.state = 'warned'
        schedule.save(update_fields=['warned_at', 'state'])

        self._post(run_at=(timezone.now() + timedelta(days=3)).isoformat())
        schedule.refresh_from_db()
        self.assertIsNone(schedule.warned_at)
        self.assertEqual(schedule.state, 'scheduled')

    def test_it_can_be_called_off(self):
        self._premium()
        self._post(run_at=(timezone.now() + timedelta(days=1)).isoformat())
        res = self.client.delete('/tournament/%s/prizes/schedule/' % self.ref)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(PrizeSchedule.objects.get().state, 'cancelled')


class RunnerTests(PrizeScheduleBase):
    """The command cron runs."""

    def _schedule(self, *, run_in, warn_hours=24):
        return PrizeSchedule.objects.create(
            tournament=self.tournament,
            run_at=timezone.now() + run_in,
            warn_hours=warn_hours,
            created_by=self.org)

    def _run(self, *args):
        out = StringIO()
        call_command('pay_due_prizes', *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_nothing_due_does_nothing(self):
        self._schedule(run_in=timedelta(days=5))
        self._run()
        self.assertEqual(PrizePayout.objects.count(), 0)
        self.assertEqual(PrizeSchedule.objects.get().state, 'scheduled')

    def test_the_warning_comes_before_the_money(self):
        # Runs in an hour, wants a day of notice: the warning is overdue and
        # the payout is not.
        self._schedule(run_in=timedelta(hours=1), warn_hours=24)
        self._run()

        schedule = PrizeSchedule.objects.get()
        self.assertEqual(schedule.state, 'warned')
        self.assertIsNotNone(schedule.warned_at)
        self.assertEqual(PrizePayout.objects.count(), 0)

        note = Notification.objects.filter(category='prizes').first()
        self.assertIsNotNone(note)
        self.assertIn(self.tournament.tournament_title, note.title)
        self.assertEqual(note.metadata['total'], 1500)

    def test_a_due_payout_pays(self):
        self._schedule(run_in=timedelta(minutes=-5), warn_hours=0)
        self._run()

        schedule = PrizeSchedule.objects.get()
        self.assertEqual(schedule.state, 'paid')
        self.assertIsNotNone(schedule.ran_at)
        self.assertEqual(PrizePayout.objects.filter(tournament=self.tournament).count(), 2)
        # And it is recorded as the automatic one, which the manual path is not.
        self.assertTrue(all(p.auto_distributed for p in PrizePayout.objects.all()))

        champ = TournamentRegistration.objects.get(
            tournament=self.tournament, final_position=1)
        self.assertEqual(UserWallet.objects.get(user=champ.user).wallet_balance, 1000)

    def test_a_cancelled_schedule_is_left_alone(self):
        schedule = self._schedule(run_in=timedelta(minutes=-5))
        schedule.state = 'cancelled'
        schedule.save(update_fields=['state'])
        self._run()
        self.assertEqual(PrizePayout.objects.count(), 0)

    def test_it_never_pays_twice(self):
        self._schedule(run_in=timedelta(minutes=-5), warn_hours=0)
        self._run()
        self._run()
        self.assertEqual(PrizePayout.objects.filter(tournament=self.tournament).count(), 2)

    def test_a_dry_run_changes_nothing(self):
        self._schedule(run_in=timedelta(minutes=-5), warn_hours=0)
        output = self._run('--dry-run')
        self.assertIn('would be', output)
        self.assertEqual(PrizePayout.objects.count(), 0)
        self.assertEqual(PrizeSchedule.objects.get().state, 'scheduled')

    def test_a_tournament_that_cannot_pay_is_recorded_not_retried_for_ever(self):
        self.tournament.status = 'live'
        self.tournament.save(update_fields=['status'])
        self._schedule(run_in=timedelta(minutes=-5), warn_hours=0)
        self._run()

        schedule = PrizeSchedule.objects.get()
        self.assertEqual(schedule.state, 'failed')
        self.assertEqual(schedule.problem, 'tournament_not_completed')
        # And the organiser is told, rather than it failing quietly in a log.
        self.assertTrue(Notification.objects.filter(category='prizes').exists())

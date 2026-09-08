"""A charge that fails, failing in the open.

Gate B3. Three things have to be true at once, and the third is the one that is
usually missing: it retries on a written schedule, it tells the subscriber each
time, and it ends in a state rather than quietly continuing to grant access.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Notification, UserWallet
from vent_billing import dunning, lifecycle, states
from vent_billing.factories import a_plan, a_user
from vent_billing.models import Invoice, Subscription


def drain_wallet(user):
    UserWallet.objects.filter(user=user).update(wallet_balance=0)


class ScheduleTests(TestCase):
    def test_the_last_retry_lands_on_the_day_access_ends(self):
        # Not a coincidence and it must stay true: a final retry after access
        # has lapsed asks somebody to pay for time they did not get.
        self.assertEqual(dunning.total_days(), Subscription.GRACE_DAYS)

    def test_the_gaps_are_the_written_ones(self):
        at = timezone.now()
        days = []
        cursor = at
        for attempt in range(1, len(dunning.SCHEDULE) + 2):
            following = dunning.next_attempt_after(attempt, cursor)
            if following is None:
                break
            days.append((following - at).days)
            cursor = following
        self.assertEqual(days, [1, 3, 7])

    def test_it_runs_out_rather_than_retrying_for_ever(self):
        self.assertIsNone(dunning.next_attempt_after(len(dunning.SCHEDULE) + 1,
                                                     timezone.now()))


class FailedRenewalTests(TestCase):
    def setUp(self):
        self.organiser, _ = a_user('dn_org')
        self.member, self.auth = a_user('dn_member', coins=5)
        self.plan = a_plan(self.organiser, name='Dunning Plan', price_vc=5)
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)
        # Paid for the first period; the wallet is now empty, so the renewal
        # is the first charge that fails.
        drain_wallet(self.member)

    def renew_at(self, moment):
        return lifecycle.process(
            Subscription.objects.get(pk=self.sub.pk), at=moment)

    def test_a_failed_renewal_moves_to_past_due_and_writes_an_invoice(self):
        verb = self.renew_at(self.sub.period_end + timedelta(minutes=1))
        self.sub.refresh_from_db()
        self.assertEqual(verb, 'failed')
        self.assertEqual(self.sub.state, states.PAST_DUE)
        failed = self.sub.invoices.filter(state=Invoice.STATE_FAILED)
        self.assertEqual(failed.count(), 1)
        self.assertEqual(failed.first().failure_code, 'INSUFFICIENT_FUNDS')

    def test_the_subscriber_is_told_every_single_time(self):
        moment = self.sub.period_end + timedelta(minutes=1)
        for _ in range(len(dunning.SCHEDULE) + 1):
            self.renew_at(moment)
            self.sub.refresh_from_db()
            moment = (self.sub.next_attempt_at or moment) + timedelta(minutes=1)
        notes = Notification.objects.filter(user=self.member)
        self.assertEqual(notes.count(), len(dunning.SCHEDULE) + 1)
        # Every notice carries a translation CODE, never a sentence built in
        # Python. A sentence assembled here cannot be read in French.
        for note in notes:
            self.assertIn(note.metadata.get('code'),
                          (dunning.CODE_FAILED, dunning.CODE_LAST_TRY,
                           dunning.CODE_CANCELLED))
            self.assertEqual(note.metadata.get('subscription'), self.sub.token)

    def test_the_period_does_not_move_while_the_charge_is_failing(self):
        before = self.sub.period_end
        self.renew_at(before + timedelta(minutes=1))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.period_end, before)

    def test_access_survives_the_grace_window_and_stops_after_it(self):
        self.renew_at(self.sub.period_end + timedelta(minutes=1))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.has_access(self.sub.period_end + timedelta(days=3)))
        self.assertFalse(self.sub.has_access(
            self.sub.period_end + timedelta(days=Subscription.GRACE_DAYS, hours=1)))

    def test_it_ends_in_cancelled_rather_than_retrying_for_ever(self):
        moment = self.sub.period_end + timedelta(minutes=1)
        verbs = []
        for _ in range(len(dunning.SCHEDULE) + 1):
            verbs.append(self.renew_at(moment))
            self.sub.refresh_from_db()
            moment = (self.sub.next_attempt_at or moment) + timedelta(minutes=1)
        self.assertEqual(verbs[-1], 'cancelled')
        self.assertEqual(self.sub.state, states.CANCELLED)
        # Four attempts, all on the record. A failure that is not on an invoice
        # is a failure nobody can count.
        self.assertEqual(self.sub.invoices.filter(
            state=Invoice.STATE_FAILED).count(), len(dunning.SCHEDULE) + 1)
        self.assertFalse(self.sub.has_access(moment))

    def test_a_retry_that_works_puts_them_back_and_moves_the_period(self):
        first = self.sub.period_end
        self.renew_at(first + timedelta(minutes=1))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.PAST_DUE)

        UserWallet.objects.filter(user=self.member).update(wallet_balance=50)
        verb = self.renew_at(self.sub.next_attempt_at + timedelta(minutes=1))
        self.sub.refresh_from_db()
        self.assertEqual(verb, 'recovered')
        self.assertEqual(self.sub.state, states.ACTIVE)
        self.assertEqual(self.sub.dunning_attempt, 0)
        self.assertIsNone(self.sub.next_attempt_at)
        self.assertGreater(self.sub.period_end, first)
        # And exactly one period was paid for, not two.
        self.assertEqual(self.sub.invoices.filter(state=Invoice.STATE_PAID).count(), 2)

    def test_a_retry_is_not_due_before_its_day(self):
        self.renew_at(self.sub.period_end + timedelta(minutes=1))
        self.sub.refresh_from_db()
        UserWallet.objects.filter(user=self.member).update(wallet_balance=50)
        # An hour later is not a day later. Nothing should be charged.
        tally = lifecycle.run_renewals(at=self.sub.period_end + timedelta(hours=2))
        self.assertEqual(tally, {})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.PAST_DUE)

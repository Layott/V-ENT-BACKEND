"""Taking the money, renewing, and giving it back.

Gates B1, B2, B6 and D2's idempotency half.

The one to read is `RenewalIdempotencyTests`. "What if the cron fires twice" is
a question that should be answered by a test rather than by an argument, and
the answer here is that the second pass charges nobody because due-ness is a
date that moved inside the same transaction as the charge.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Transaction, UserWallet
from vent_billing import charging, clock, lifecycle, states
from vent_billing.factories import a_card, a_plan, a_user, balance, set_fee
from vent_billing.models import (BillingLedgerEntry, Invoice, Plan,
                                 Subscription)


class FirstChargeTests(TestCase):
    def setUp(self):
        self.organiser, _ = a_user('fc_org')
        self.member, self.auth = a_user('fc_member', coins=20)
        self.plan = a_plan(self.organiser, name='First Charge', price_vc=5)

    def test_subscribing_takes_the_money_and_writes_an_invoice(self):
        sub, invoice = lifecycle.subscribe(self.member, self.plan)
        self.assertEqual(sub.state, states.ACTIVE)
        self.assertEqual(invoice.state, Invoice.STATE_PAID)
        self.assertEqual(invoice.collected_vc, 5)
        self.assertEqual(balance(self.member), 15)
        self.assertEqual(invoice.source, Subscription.SOURCE_WALLET)
        self.assertTrue(invoice.provider_reference)

    def test_it_shows_on_the_wallet_statement(self):
        lifecycle.subscribe(self.member, self.plan)
        row = Transaction.objects.filter(wallet__user=self.member).first()
        self.assertEqual(row.type, 'deduction')
        self.assertEqual(row.amount, -5)
        self.assertEqual(row.status, 'completed')

    def test_no_money_means_no_subscription_at_all(self):
        broke, _ = a_user('fc_broke', coins=0)
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.subscribe(broke, self.plan)
        self.assertEqual(caught.exception.code, charging.INSUFFICIENT_FUNDS)
        # Nothing left behind. A subscription in no state with a failed
        # invoice hanging off it would appear on the organiser's list as
        # somebody who never paid and never joined.
        self.assertFalse(Subscription.objects.filter(subscriber=broke).exists())
        self.assertFalse(Invoice.objects.filter(
            subscription__subscriber=broke).exists())

    def test_a_draft_plan_cannot_be_subscribed_to(self):
        draft = a_plan(self.organiser, name='Draft Plan',
                       status=Plan.STATUS_DRAFT)
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.subscribe(self.member, draft)
        self.assertEqual(caught.exception.code, 'PLAN_NOT_AVAILABLE')

    def test_a_free_plan_takes_the_same_path_and_charges_nothing(self):
        free = a_plan(self.organiser, name='Free Tier', price_vc=0)
        sub, invoice = lifecycle.subscribe(self.member, free)
        self.assertEqual(sub.state, states.ACTIVE)
        self.assertEqual(invoice.state, Invoice.STATE_PAID)
        self.assertEqual(invoice.collected_vc, 0)
        self.assertEqual(balance(self.member), 20)

    def test_joining_twice_is_refused(self):
        lifecycle.subscribe(self.member, self.plan)
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.subscribe(self.member, self.plan)
        self.assertEqual(caught.exception.code, 'ALREADY_SUBSCRIBED')


class CardChargeTests(TestCase):
    """A card is charged by a request THIS SERVER made, never by a redirect."""

    def setUp(self):
        self.organiser, _ = a_user('cc_org')
        self.member, _ = a_user('cc_member', coins=0)
        self.card = a_card(self.member)
        self.plan = a_plan(self.organiser, name='Card Plan', price_vc=5)

    def _paystack(self, ok=True, message='Declined'):
        response = mock.Mock()
        response.json.return_value = (
            {'status': True, 'data': {'status': 'success'}} if ok
            else {'status': True, 'data': {'status': 'failed',
                                           'gateway_response': message}})
        return response

    @mock.patch('vent_billing.charging.http_requests.post')
    @mock.patch('vent_auth.paystack.configured', return_value=True)
    @mock.patch('vent_auth.paystack.headers', return_value={})
    def test_a_successful_card_charge_leaves_the_balance_where_it_was(
            self, _headers, _configured, post):
        post.return_value = self._paystack(ok=True)
        sub, invoice = lifecycle.subscribe(self.member, self.plan)
        self.assertEqual(invoice.state, Invoice.STATE_PAID)
        self.assertEqual(invoice.source, Subscription.SOURCE_CARD)
        # Charged in naira, credited as coins, then spent. The statement adds
        # up and the balance is where it started.
        self.assertEqual(balance(self.member), 0)
        rows = list(Transaction.objects.filter(
            wallet__user=self.member).order_by('id'))
        self.assertEqual([r.type for r in rows], ['top_up', 'deduction'])
        self.assertEqual(post.call_args.kwargs['json']['amount'],
                         self.plan.price_ngn * 100)

    @mock.patch('vent_billing.charging.http_requests.post')
    @mock.patch('vent_auth.paystack.configured', return_value=True)
    @mock.patch('vent_auth.paystack.headers', return_value={})
    def test_a_declined_card_charges_nothing_and_says_so_by_code(
            self, _headers, _configured, post):
        post.return_value = self._paystack(ok=False)
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.subscribe(self.member, self.plan)
        self.assertEqual(caught.exception.code, charging.CARD_DECLINED)
        self.assertEqual(balance(self.member), 0)
        self.assertFalse(Transaction.objects.filter(wallet__user=self.member).exists())

    @mock.patch('vent_auth.paystack.configured', return_value=False)
    def test_with_no_paystack_key_the_card_path_says_so(self, _configured):
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.subscribe(self.member, self.plan)
        self.assertEqual(caught.exception.code, charging.PAYMENTS_UNAVAILABLE)


class RenewalIdempotencyTests(TestCase):
    """Gate B2. Running it twice charges nobody twice."""

    def setUp(self):
        self.organiser, _ = a_user('rn_org')
        self.member, _ = a_user('rn_member', coins=100)
        self.plan = a_plan(self.organiser, name='Renewal Plan', price_vc=5)
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def test_a_second_run_in_the_same_period_charges_nobody(self):
        moment = self.sub.period_end + timedelta(minutes=1)
        first = lifecycle.run_renewals(at=moment)
        after_first = balance(self.member)
        second = lifecycle.run_renewals(at=moment)

        self.assertEqual(first.get('renewed'), 1)
        self.assertEqual(second, {})
        self.assertEqual(balance(self.member), after_first)
        self.assertEqual(self.sub.invoices.count(), 2)

    def test_five_runs_in_a_row_charge_once(self):
        moment = self.sub.period_end + timedelta(minutes=1)
        for _ in range(5):
            lifecycle.run_renewals(at=moment)
        self.assertEqual(balance(self.member), 90)
        self.assertEqual(Invoice.objects.filter(
            subscription=self.sub, state=Invoice.STATE_PAID).count(), 2)

    def test_the_command_itself_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO

        moment = (self.sub.period_end + timedelta(minutes=1)).isoformat()
        out = StringIO()
        call_command('run_renewals', now=moment, stdout=out)
        first = balance(self.member)
        out2 = StringIO()
        call_command('run_renewals', now=moment, stdout=out2)
        self.assertEqual(balance(self.member), first)
        self.assertIn('nothing due', out2.getvalue())

    def test_dry_run_changes_nothing(self):
        from django.core.management import call_command
        from io import StringIO

        moment = (self.sub.period_end + timedelta(minutes=1)).isoformat()
        out = StringIO()
        call_command('run_renewals', now=moment, dry_run=True, stdout=out)
        self.assertEqual(balance(self.member), 95)
        self.assertIn(self.sub.token, out.getvalue())

    def test_the_unique_constraint_is_the_backstop(self):
        from django.db import IntegrityError, transaction as db_transaction
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():
                Invoice.objects.create(
                    subscription=self.sub, plan=self.plan,
                    period_start=self.sub.period_start,
                    period_end=self.sub.period_end, attempt=1, amount_vc=5)


class ClockRenewalTests(TestCase):
    """Gate D2 on real subscriptions rather than on the pure function."""

    def setUp(self):
        self.organiser, _ = a_user('ck_org')
        self.member, _ = a_user('ck_member', coins=500)

    def _subscribe_at(self, moment, interval=clock.MONTHLY):
        plan = a_plan(self.organiser, name='Clock %s' % moment.date(),
                      price_vc=1, interval=interval)
        with mock.patch('django.utils.timezone.now', return_value=moment):
            sub, _ = lifecycle.subscribe(self.member, plan, at=moment)
        return sub

    def test_a_subscription_taken_out_on_the_31st_renews_in_a_30_day_month(self):
        start = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        sub = self._subscribe_at(start)
        self.assertEqual(sub.period_end.day, 30)      # September has 30
        self.assertEqual(sub.period_end.month, 9)

        lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))
        sub.refresh_from_db()
        # And back to the 31st in October, because the anchor never moved.
        self.assertEqual((sub.period_end.month, sub.period_end.day), (10, 31))

    def test_a_year_of_renewals_from_the_31st_charges_twelve_times(self):
        start = datetime(2026, 1, 31, 10, 0, tzinfo=dt_timezone.utc)
        sub = self._subscribe_at(start)
        for _ in range(12):
            sub.refresh_from_db()
            lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))
        sub.refresh_from_db()
        paid = Invoice.objects.filter(subscription=sub, state=Invoice.STATE_PAID)
        self.assertEqual(paid.count(), 13)            # the first plus twelve
        # No two invoices cover the same period.
        starts = [i.period_start for i in paid]
        self.assertEqual(len(starts), len(set(starts)))

    def test_a_yearly_subscription_crosses_a_leap_day_once(self):
        start = datetime(2028, 2, 29, 10, 0, tzinfo=dt_timezone.utc)
        sub = self._subscribe_at(start, interval=clock.YEARLY)
        self.assertEqual((sub.period_end.year, sub.period_end.month,
                          sub.period_end.day), (2029, 2, 28))
        years = []
        for _ in range(4):
            sub.refresh_from_db()
            lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))
            sub.refresh_from_db()
            years.append(sub.period_end.year)
        self.assertEqual(years, [2030, 2031, 2032, 2033])
        paid = Invoice.objects.filter(subscription=sub, state=Invoice.STATE_PAID)
        self.assertEqual(paid.count(), 5)
        self.assertEqual(len({i.period_start for i in paid}), 5)


class LedgerTests(TestCase):
    def setUp(self):
        set_fee(10)
        self.organiser, _ = a_user('lg_org')
        self.member, _ = a_user('lg_member', coins=100)
        self.plan = a_plan(self.organiser, name='Ledger Plan', price_vc=10)

    def test_a_charge_writes_the_lines_it_created(self):
        _sub, invoice = lifecycle.subscribe(self.member, self.plan)
        lines = list(invoice.ledger_entries.order_by('kind'))
        kinds = {line.kind: line.amount_vc for line in lines}
        self.assertEqual(kinds[BillingLedgerEntry.KIND_ORGANISER], 9)
        self.assertEqual(kinds[BillingLedgerEntry.KIND_PLATFORM], 1)
        self.assertEqual(lines[0].fee_pct, 10)

    def test_the_rate_is_stamped_at_the_charge(self):
        _sub, invoice = lifecycle.subscribe(self.member, self.plan)
        set_fee(50)
        line = invoice.ledger_entries.filter(
            kind=BillingLedgerEntry.KIND_ORGANISER).first()
        line.refresh_from_db()
        self.assertEqual(line.amount_vc, 9)      # last month is not rewritten
        self.assertEqual(line.fee_pct, 10)

    def test_settling_twice_pays_once(self):
        from vent_billing import ledger
        lifecycle.subscribe(self.member, self.plan)
        first = ledger.settle(self.plan, run_by=self.organiser)
        self.assertEqual(first.amount_vc, 9)
        self.assertEqual(balance(self.organiser), 9)
        second = ledger.settle(self.plan, run_by=self.organiser)
        self.assertEqual(second.lines_paid, 0)
        self.assertEqual(balance(self.organiser), 9)


class RefundTests(TestCase):
    """Gate B6. A refund writes lines rather than adjusting quietly."""

    def setUp(self):
        set_fee(10)
        self.organiser, _ = a_user('rf_org')
        self.member, _ = a_user('rf_member', coins=100)
        self.plan = a_plan(self.organiser, name='Refund Plan', price_vc=10)
        self.sub, self.invoice = lifecycle.subscribe(self.member, self.plan)

    def test_the_money_goes_back_and_shows_on_the_statement(self):
        charging.refund(self.invoice, reason='charged in error',
                        actor=self.organiser)
        self.assertEqual(balance(self.member), 100)
        row = Transaction.objects.filter(wallet__user=self.member,
                                         type='refund').first()
        self.assertEqual(row.amount, 10)

    def test_reversal_lines_are_written_never_edits(self):
        original = list(self.invoice.ledger_entries.all())
        charging.refund(self.invoice, reason='error', actor=self.organiser)
        for line in original:
            line.refresh_from_db()
            self.assertIsNotNone(line.reversed_by_id)
        reversals = self.invoice.ledger_entries.filter(
            kind=BillingLedgerEntry.KIND_REVERSAL)
        self.assertEqual(reversals.count(), 2)
        self.assertEqual(sum(r.amount_vc for r in reversals), -10)

    def test_a_refund_after_settlement_nets_against_the_next_run(self):
        from vent_billing import ledger
        ledger.settle(self.plan, run_by=self.organiser)
        self.assertEqual(balance(self.organiser), 9)

        charging.refund(self.invoice, reason='error', actor=self.organiser)
        figures = ledger.balances(self.plan)
        self.assertEqual(figures['owed_vc'], -9)

        # The run refuses to claw coins out of a wallet on its own, and leaves
        # the debt open so it nets against the next charge.
        run = ledger.settle(self.plan, run_by=self.organiser)
        self.assertEqual(run.lines_paid, 0)
        self.assertEqual(balance(self.organiser), 9)

        second, _ = a_user('rf_second', coins=100)
        lifecycle.subscribe(second, self.plan)
        self.assertEqual(ledger.balances(self.plan)['owed_vc'], 0)

    def test_refunding_ends_the_period_that_was_paid_for(self):
        charging.refund(self.invoice, reason='error', actor=self.organiser)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.CANCELLED)
        self.assertFalse(self.sub.has_access())
        self.assertEqual(self.sub.events.first().reason, states.REFUNDED)

    def test_a_goodwill_refund_can_keep_the_access(self):
        charging.refund(self.invoice, reason='goodwill', actor=self.organiser,
                        end_access=False)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.ACTIVE)
        self.assertTrue(self.sub.has_access())

    def test_refunding_twice_gives_the_money_back_once(self):
        charging.refund(self.invoice, reason='error', actor=self.organiser)
        charging.refund(self.invoice, reason='error again', actor=self.organiser)
        self.assertEqual(balance(self.member), 100)
        self.assertEqual(Transaction.objects.filter(
            wallet__user=self.member, type='refund').count(), 1)


class ProrationTests(TestCase):
    """Gate B5. The decision is 'the change starts next period', and it is
    tested rather than merely written down."""

    def setUp(self):
        self.organiser, _ = a_user('pr_org')
        self.member, _ = a_user('pr_member', coins=200)
        self.small = a_plan(self.organiser, name='Small Plan', price_vc=5)
        self.big = a_plan(self.organiser, name='Big Plan', price_vc=20)
        self.sub, _ = lifecycle.subscribe(self.member, self.small)

    def test_an_upgrade_charges_nothing_now(self):
        before = balance(self.member)
        lifecycle.change_plan(self.sub, self.big, actor=self.member)
        self.sub.refresh_from_db()
        self.assertEqual(balance(self.member), before)
        self.assertEqual(self.sub.plan_id, self.small.plan_id)
        self.assertEqual(self.sub.pending_plan_id, self.big.plan_id)

    def test_it_takes_effect_at_the_next_period_and_charges_the_new_price(self):
        lifecycle.change_plan(self.sub, self.big, actor=self.member)
        before = balance(self.member)
        lifecycle.run_renewals(at=self.sub.period_end + timedelta(minutes=1))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan_id, self.big.plan_id)
        self.assertIsNone(self.sub.pending_plan_id)
        self.assertEqual(balance(self.member), before - 20)

    def test_moving_to_another_organisers_plan_is_refused(self):
        other, _ = a_user('pr_other_org')
        theirs = a_plan(other, name='Somebody Elses Plan')
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.change_plan(self.sub, theirs, actor=self.member)
        self.assertEqual(caught.exception.code, 'DIFFERENT_SELLER')


class TrialTests(TestCase):
    def setUp(self):
        self.organiser, _ = a_user('tr_org')
        self.member, _ = a_user('tr_member', coins=100)
        self.plan = a_plan(self.organiser, name='Trial Plan', price_vc=5,
                           trial_days=14)

    def test_a_trial_charges_nothing_up_front(self):
        sub, invoice = lifecycle.subscribe(self.member, self.plan)
        self.assertEqual(sub.state, states.TRIALING)
        self.assertIsNone(invoice)
        self.assertEqual(balance(self.member), 100)
        self.assertTrue(sub.has_access())

    def test_the_first_charge_happens_when_the_trial_ends(self):
        sub, _ = lifecycle.subscribe(self.member, self.plan)
        lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.ACTIVE)
        self.assertEqual(balance(self.member), 95)

    def test_the_anchor_moves_to_the_day_the_trial_ends(self):
        start = datetime(2026, 1, 5, 10, 0, tzinfo=dt_timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=start):
            sub, _ = lifecycle.subscribe(self.member, self.plan, at=start)
        self.assertEqual(sub.period_end.day, 19)      # 5 January plus 14 days
        lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))
        sub.refresh_from_db()
        # A month from the 19th, not from the 5th. Billing a fortnight after a
        # trial started on the 5th is a month nobody agreed to.
        self.assertEqual((sub.period_end.month, sub.period_end.day), (2, 19))


class AccessTests(TestCase):
    """`has_access` is one question with one answer, and the API asks it."""

    def setUp(self):
        self.organiser, _ = a_user('ac_org')
        self.member, _ = a_user('ac_member', coins=100)
        self.plan = a_plan(self.organiser, name='Access Plan', price_vc=5)
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def test_an_active_member_is_in(self):
        self.assertTrue(self.sub.has_access())

    def test_an_expired_subscription_is_out_whatever_the_dates_say(self):
        self.sub.state = states.EXPIRED
        self.assertFalse(self.sub.has_access(self.sub.period_start))

    def test_an_active_subscription_the_cron_forgot_does_not_last_for_ever(self):
        far = self.sub.period_end + timedelta(days=30)
        self.assertFalse(self.sub.has_access(far))
        # But a cron running an hour late does not lock a paying member out.
        self.assertTrue(self.sub.has_access(self.sub.period_end + timedelta(hours=1)))

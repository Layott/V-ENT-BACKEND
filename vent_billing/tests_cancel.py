"""Cancelling, which was built and tested before anything could charge.

Gate B4. The requirement is one sentence and it is the whole reason this
feature was refused in the first place: the subscriber cancels at any time, in
one press, and keeps what they paid for until the end of the period.

Every test here is that sentence taken apart.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_billing import lifecycle, states
from vent_billing.factories import a_plan, a_user, balance
from vent_billing.models import Subscription


class CancelTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('cn_org')
        self.member, self.auth = a_user('cn_member', coins=100)
        self.plan = a_plan(self.organiser, price_vc=5)
        self.sub, self.invoice = lifecycle.subscribe(self.member, self.plan)

    def test_one_press_cancels_from_active(self):
        res = self.client.post('/billing/subscription/%s/cancel/' % self.sub.token,
                               {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['state'], states.CANCELLED)
        self.assertTrue(res.data['data']['cancel_at_period_end'])

    def test_a_cancelled_member_keeps_access_to_the_end_of_the_period(self):
        lifecycle.cancel(self.sub, actor=self.member)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.has_access())
        # The day before the period ends: still in.
        self.assertTrue(self.sub.has_access(self.sub.period_end - timedelta(hours=1)))
        # A minute after: out.
        self.assertFalse(self.sub.has_access(self.sub.period_end + timedelta(minutes=1)))

    def test_cancelling_does_not_move_the_period_end(self):
        before = self.sub.period_end
        lifecycle.cancel(self.sub, actor=self.member)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.period_end, before)

    def test_cancelling_refunds_nothing(self):
        before = balance(self.member)
        lifecycle.cancel(self.sub, actor=self.member)
        self.assertEqual(balance(self.member), before)

    def test_the_transition_is_recorded_with_a_reason_and_a_time(self):
        lifecycle.cancel(self.sub, actor=self.member, reason='too expensive')
        event = self.sub.events.first()
        self.assertEqual(event.reason, states.CANCELLED_BY_SUBSCRIBER)
        self.assertEqual(event.from_state, states.ACTIVE)
        self.assertEqual(event.to_state, states.CANCELLED)
        self.assertEqual(event.actor_id, self.member.user_id)
        self.assertIsNotNone(event.at)

    def test_cancelling_twice_is_not_an_error(self):
        lifecycle.cancel(self.sub, actor=self.member)
        res = self.client.post('/billing/subscription/%s/cancel/' % self.sub.token,
                               {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200)

    def test_a_cancelled_subscription_is_not_renewed_and_then_expires(self):
        lifecycle.cancel(self.sub, actor=self.member)
        after = self.sub.period_end + timedelta(minutes=1)
        tally = lifecycle.run_renewals(at=after)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.EXPIRED)
        self.assertEqual(tally.get('expired'), 1)
        self.assertFalse(self.sub.has_access(after))
        # And nothing was charged for the period nobody asked for.
        self.assertEqual(self.sub.invoices.count(), 1)

    def test_somebody_else_cannot_cancel_my_membership(self):
        _stranger, other_auth = a_user('cn_other')
        res = self.client.post('/billing/subscription/%s/cancel/' % self.sub.token,
                               {}, format='json', **other_auth)
        self.assertEqual(res.status_code, 404)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.ACTIVE)

    def test_a_signed_out_caller_cannot_cancel(self):
        res = self.client.post('/billing/subscription/%s/cancel/' % self.sub.token,
                               {}, format='json')
        self.assertEqual(res.status_code, 401)


class ResumeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, _ = a_user('rs_org')
        self.member, self.auth = a_user('rs_member', coins=100)
        self.plan = a_plan(self.organiser, name='Resume Plan', price_vc=5)
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def test_changing_your_mind_inside_the_period_costs_nothing(self):
        lifecycle.cancel(self.sub, actor=self.member)
        before = balance(self.member)
        res = self.client.post('/billing/subscription/%s/resume/' % self.sub.token,
                               {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.ACTIVE)
        self.assertFalse(self.sub.cancel_at_period_end)
        self.assertEqual(balance(self.member), before)

    def test_resuming_after_the_period_is_refused_rather_than_faked(self):
        lifecycle.cancel(self.sub, actor=self.member)
        Subscription.objects.filter(pk=self.sub.pk).update(
            period_end=timezone.now() - timedelta(days=1))
        self.sub.refresh_from_db()
        with self.assertRaises(lifecycle.SubscribeError) as caught:
            lifecycle.resume(self.sub, actor=self.member)
        self.assertEqual(caught.exception.code, 'PERIOD_OVER')


class OrganiserEndingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('oe_org')
        self.member, self.member_auth = a_user('oe_member', coins=100)
        self.plan = a_plan(self.organiser, name='Organiser Ends')
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def test_the_organiser_can_end_a_membership_but_not_take_the_period_back(self):
        before = self.sub.period_end
        res = self.client.post('/billing/subscription/%s/end/' % self.sub.token,
                               {'reason': 'broke the rules'}, format='json',
                               **self.org_auth)
        self.assertEqual(res.status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.CANCELLED)
        self.assertEqual(self.sub.period_end, before)
        self.assertTrue(self.sub.has_access())
        self.assertEqual(self.sub.events.first().reason,
                         states.CANCELLED_BY_ORGANISER)

    def test_a_stranger_cannot_end_somebody_elses_membership(self):
        _other, other_auth = a_user('oe_other')
        res = self.client.post('/billing/subscription/%s/end/' % self.sub.token,
                               {}, format='json', **other_auth)
        self.assertEqual(res.status_code, 403)


class CancelFromTrialAndPastDueTests(TestCase):
    """Gate D1: the state machine proven from `trialing` and from `past_due`.

    Every test above starts from `active`, which is the state where nothing
    surprising happens. The two that matter are the ones where cancelling and
    then changing your mind could quietly hand somebody something:

    * cancel during a TRIAL and resume, and the trial must still be a trial. It
      used to come back as `active`, which is a paid period nobody paid for.
    * cancel while a charge is FAILING and resume, and it must still be failing.
      It used to come back as `active`, which is access on an unpaid invoice.

    Both were one line: `resume()` wrote `state = ACTIVE` by hand, bypassing the
    state machine that is meant to be the only writer of `state`.
    """

    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('td_org')
        self.member, self.auth = a_user('td_member', coins=100)

    # ------------------------------------------------------------- trialing

    def a_trial(self):
        plan = a_plan(self.organiser, price_vc=5, trial_days=14)
        sub, _invoice = lifecycle.subscribe(self.member, plan)
        self.assertEqual(sub.state, states.TRIALING)
        return sub

    def test_cancelling_during_a_trial_is_allowed(self):
        sub = self.a_trial()
        res = self.client.post('/billing/subscription/%s/cancel/' % sub.token,
                               {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.CANCELLED)
        self.assertTrue(sub.cancel_at_period_end)

    def test_a_cancelled_trial_keeps_the_trial_to_its_end(self):
        """The trial is time somebody was given. Cancelling declines the charge
        at the end of it, and takes nothing back."""
        sub = self.a_trial()
        ends = sub.period_end
        lifecycle.cancel(sub, actor=self.member)
        sub.refresh_from_db()
        self.assertEqual(sub.period_end, ends)
        self.assertTrue(sub.has_access)

    def test_resuming_a_cancelled_trial_is_still_a_trial(self):
        sub = self.a_trial()
        lifecycle.cancel(sub, actor=self.member)
        lifecycle.resume(sub, actor=self.member)
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.TRIALING)
        self.assertFalse(sub.cancel_at_period_end)

    def test_the_resume_is_recorded_as_going_back_to_the_trial(self):
        sub = self.a_trial()
        lifecycle.cancel(sub, actor=self.member)
        lifecycle.resume(sub, actor=self.member)
        last = sub.events.order_by('-at', '-pk').first()
        self.assertEqual(last.reason, states.RESUMED_TO_TRIAL)
        self.assertEqual(last.from_state, states.CANCELLED)
        self.assertEqual(last.to_state, states.TRIALING)

    # ------------------------------------------------------------- past_due

    def a_failing_one(self):
        plan = a_plan(self.organiser, price_vc=5)
        sub, _invoice = lifecycle.subscribe(self.member, plan)
        states.move(sub, states.CHARGE_FAILED, note='no coins')
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.PAST_DUE)
        return sub

    def test_cancelling_while_a_payment_is_failing_is_allowed(self):
        """Somebody whose card is failing is exactly who wants out, and making
        them settle an invoice first to escape a subscription is a trap."""
        sub = self.a_failing_one()
        res = self.client.post('/billing/subscription/%s/cancel/' % sub.token,
                               {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.CANCELLED)

    def test_cancelling_while_failing_stops_the_retries(self):
        """Otherwise dunning keeps trying to charge somebody who has left."""
        sub = self.a_failing_one()
        lifecycle.cancel(sub, actor=self.member)
        sub.refresh_from_db()
        self.assertIsNone(sub.next_attempt_at)

    def test_resuming_from_past_due_does_not_grant_a_paid_period(self):
        sub = self.a_failing_one()
        lifecycle.cancel(sub, actor=self.member)
        lifecycle.resume(sub, actor=self.member)
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.PAST_DUE)

    def test_the_resume_from_past_due_is_recorded_as_such(self):
        sub = self.a_failing_one()
        lifecycle.cancel(sub, actor=self.member)
        lifecycle.resume(sub, actor=self.member)
        last = sub.events.order_by('-at', '-pk').first()
        self.assertEqual(last.reason, states.RESUMED_TO_PAST_DUE)
        self.assertEqual(last.to_state, states.PAST_DUE)

    # ------------------------------------------------- the ordinary case too

    def test_resuming_from_active_still_lands_on_active(self):
        plan = a_plan(self.organiser, price_vc=5)
        sub, _invoice = lifecycle.subscribe(self.member, plan)
        lifecycle.cancel(sub, actor=self.member)
        lifecycle.resume(sub, actor=self.member)
        sub.refresh_from_db()
        self.assertEqual(sub.state, states.ACTIVE)

    def test_every_state_change_went_through_the_machine(self):
        """`states.move` is meant to be the only writer of `state`. `resume()`
        wrote it by hand, so the history recorded a move to `active` that the
        transition table would have refused."""
        sub = self.a_trial()
        lifecycle.cancel(sub, actor=self.member)
        lifecycle.resume(sub, actor=self.member)
        for event in sub.events.all():
            self.assertIn(event.reason, states.TRANSITIONS,
                          '%s is not a transition the table knows' % event.reason)

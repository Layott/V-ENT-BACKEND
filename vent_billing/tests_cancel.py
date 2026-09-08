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

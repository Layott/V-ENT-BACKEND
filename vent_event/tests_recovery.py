"""Abandoned checkout recovery: the last GAP on the tix and selar research.

The two things these tests are really about are the two ways this feature goes
wrong. It must record enough to be actionable, and it must never become a
mailing list assembled out of a checkout: one reminder, ever, sent because a
person pressed a button, and the row swept after thirty days.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import AbandonedCheckout, Event, Ticket, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('r-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class AbandonedCheckoutTests(TestCase):

    def setUp(self):
        self.organiser, self.auth = a_user('rec_org')
        self.stranger, self.stranger_auth = a_user('rec_stranger')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Recovery Probe', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('5000'),
            quantity=100)

    # -------------------------------------------------------------- writing

    def _start(self, email='amara@example.test', quantity=1):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.post') as post:
            post.return_value.raise_for_status = lambda: None
            post.return_value.json = lambda: {
                'status': True,
                'data': {'authorization_url': 'https://paystack.test/pay/abc'}}
            return self.client.post(
                '/event/%s/guest-buy/' % self.event.event_id,
                data={'tier_id': self.tier.id, 'quantity': quantity,
                      'email': email},
                content_type='application/json')

    def test_reaching_the_payment_page_is_recorded(self):
        """The ORDER still lives only in the Paystack metadata. The fact that
        somebody tried is the one thing an organiser can act on."""
        res = self._start()
        self.assertEqual(res.status_code, 200, res.content)
        row = AbandonedCheckout.objects.get()
        self.assertEqual(row.email, 'amara@example.test')
        self.assertEqual(row.quantity, 1)
        self.assertEqual(row.reference, res.json()['data']['reference'])
        self.assertTrue(row.open)
        # No ticket. That is the whole point of the row existing.
        self.assertEqual(Ticket.objects.count(), 0)

    def test_a_free_ticket_records_nothing(self):
        """Nobody abandons a free ticket: it is issued on the spot."""
        free = TicketTier.objects.create(
            event=self.event, name='Free', price=Decimal('0'), quantity=10)
        res = self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': free.id, 'quantity': 1, 'email': 'a@b.test'},
            content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(AbandonedCheckout.objects.count(), 0)

    def _verify(self, reference, quantity=1):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.get') as get:
            get.return_value.raise_for_status = lambda: None
            get.return_value.json = lambda: {
                'status': True,
                'data': {'status': 'success',
                         'customer': {'email': 'amara@example.test'},
                         'metadata': {'event_id': self.event.event_id,
                                      'tier_id': self.tier.id,
                                      'quantity': quantity,
                                      'answers': {}, 'attendees': []}}}
            return self.client.post('/event/guest-verify/',
                                    data={'reference': reference},
                                    content_type='application/json')

    def test_coming_back_and_paying_closes_the_row(self):
        reference = self._start().json()['data']['reference']
        self._verify(reference)
        row = AbandonedCheckout.objects.get()
        self.assertIsNotNone(row.converted_at)
        self.assertFalse(row.open)

    def test_the_second_arrival_for_one_payment_also_closes_it(self):
        """The browser returning and Paystack calling back are two arrivals.
        The first issues; the second must still close the row, because on a
        slow connection it is often the one that lands."""
        reference = self._start().json()['data']['reference']
        self._verify(reference)
        AbandonedCheckout.objects.update(converted_at=None)
        again = self._verify(reference)
        self.assertTrue(again.json()['data']['already_issued'])
        self.assertIsNotNone(AbandonedCheckout.objects.get().converted_at)

    # -------------------------------------------------------------- reading

    def url(self):
        return '/event/%s/abandoned/' % self.event.event_id

    def test_the_organiser_sees_who_nearly_bought(self):
        self._start(email='one@example.test')
        self._start(email='two@example.test', quantity=3)
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['open'], 2)
        self.assertEqual(data['remindable'], 2)
        self.assertEqual({r['email'] for r in data['results']},
                         {'one@example.test', 'two@example.test'})
        # In money, because that is what somebody decides on.
        self.assertEqual(data['open_ngn'], 5000.0 + 15000.0)

    def test_a_recovered_one_leaves_the_list(self):
        reference = self._start().json()['data']['reference']
        self._verify(reference)
        data = self.client.get(self.url(), **self.auth).json()['data']
        self.assertEqual(data['open'], 0)
        self.assertEqual(data['recovered'], 1)
        self.assertEqual(data['results'], [])
        # And is still reachable, which is the only way to see a recovery rate.
        both = self.client.get(self.url() + '?include=all', **self.auth)
        self.assertEqual(len(both.json()['data']['results']), 1)

    def test_a_stranger_cannot_read_the_addresses(self):
        self._start()
        res = self.client.get(self.url(), **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_signed_out_cannot_read_the_addresses(self):
        self._start()
        self.assertIn(self.client.get(self.url()).status_code, (401, 403))

    # ------------------------------------------------------------ reminding

    def remind(self, **body):
        return self.client.post(
            '/event/%s/abandoned/remind/' % self.event.event_id,
            data=body, content_type='application/json', **self.auth)

    def test_the_organiser_sends_the_reminder(self):
        self._start()
        with patch('vent_auth.emails.send_checkout_unfinished',
                   return_value=True) as send:
            res = self.remind()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['sent'], 1)
        self.assertEqual(send.call_count, 1)
        self.assertIsNotNone(AbandonedCheckout.objects.get().reminded_at)

    def test_one_reminder_ever(self):
        """The address was given in order to pay for a ticket. One message
        about that same purchase is the most it was given for."""
        self._start()
        with patch('vent_auth.emails.send_checkout_unfinished',
                   return_value=True) as send:
            self.remind()
            second = self.remind()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(second.json()['data']['sent'], 0)

    def test_somebody_who_came_back_is_never_reminded(self):
        reference = self._start().json()['data']['reference']
        self._verify(reference)
        with patch('vent_auth.emails.send_checkout_unfinished',
                   return_value=True) as send:
            self.remind()
        self.assertEqual(send.call_count, 0)

    def test_a_mail_outage_leaves_the_row_remindable(self):
        """Stamping on a send that failed silently burns the one chance."""
        self._start()
        with patch('vent_auth.emails.send_checkout_unfinished',
                   return_value=False):
            res = self.remind()
        self.assertEqual(res.json()['data']['failed'], 1)
        self.assertIsNone(AbandonedCheckout.objects.get().reminded_at)

    def test_one_row_can_be_reminded_on_its_own(self):
        self._start(email='one@example.test')
        self._start(email='two@example.test')
        target = AbandonedCheckout.objects.get(email='one@example.test')
        with patch('vent_auth.emails.send_checkout_unfinished',
                   return_value=True) as send:
            self.remind(id=target.id)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][0].email, 'one@example.test')

    def test_a_stranger_cannot_send_mail_from_an_event_that_is_not_theirs(self):
        self._start()
        res = self.client.post(
            '/event/%s/abandoned/remind/' % self.event.event_id,
            data={}, content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertIsNone(AbandonedCheckout.objects.get().reminded_at)

    # -------------------------------------------------------------- sweeping

    def test_it_does_not_become_an_address_book(self):
        """Thirty days is longer than a reminder is worth sending and shorter
        than anybody would call a record."""
        self._start()
        AbandonedCheckout.objects.update(
            started_at=timezone.now() - timedelta(days=31))
        self.assertEqual(AbandonedCheckout.sweep(), 1)
        self.assertEqual(AbandonedCheckout.objects.count(), 0)

    def test_a_recent_one_survives_the_sweep(self):
        self._start()
        self.assertEqual(AbandonedCheckout.sweep(), 0)
        self.assertEqual(AbandonedCheckout.objects.count(), 1)

    def test_a_converted_row_is_swept_too(self):
        """It is still an address somebody typed. Keeping it because it might
        one day be useful is exactly how a checkout becomes a mailing list."""
        reference = self._start().json()['data']['reference']
        self._verify(reference)
        AbandonedCheckout.objects.update(
            started_at=timezone.now() - timedelta(days=31))
        self.assertEqual(AbandonedCheckout.sweep(), 1)

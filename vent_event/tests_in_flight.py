"""A seat somebody is paying for at Paystack is not for sale to the next guest.

Found by the walk on 12 September 2026: guest-buy checked the room before the
gateway and issued after it, and nothing held the seat in between, so two
guests paying for the last seat both got a ticket. The AbandonedCheckout row
written at the start of every paid checkout now counts as held for twenty
minutes. See vent_event/availability.py.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from . import availability
from .models import AbandonedCheckout, Event, TicketTier


class InFlightSeatTests(TestCase):

    def setUp(self):
        self.organiser = Users.objects.create(
            username='inflight_org', email='inflight@vent.test',
            login_session_token='inflight-org-tk'[:16], is_active=True)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Last Seat', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=6))
        self.tier = TicketTier.objects.create(
            event=self.event, name='Last', price=Decimal('5000'), quantity=1)

    def start(self, email):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.post') as post:
            post.return_value.raise_for_status = lambda: None
            post.return_value.json = lambda: {
                'status': True, 'data': {'authorization_url': 'https://paystack.test/pay/x'}}
            return self.client.post(
                '/event/%s/guest-buy/' % self.event.event_id,
                data={'tier_id': self.tier.id, 'quantity': 1, 'email': email},
                content_type='application/json')

    def test_the_seat_at_paystack_is_held(self):
        first = self.start('one@example.test')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(availability.tier_available(self.tier), 0)
        second = self.start('two@example.test')
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(second.json()['code'], 'SOLD_OUT')

    def test_a_hold_older_than_the_window_stops_counting(self):
        self.start('one@example.test')
        row = AbandonedCheckout.objects.get()
        AbandonedCheckout.objects.filter(pk=row.pk).update(
            started_at=timezone.now() - timedelta(minutes=availability.IN_FLIGHT_MINUTES + 1))
        self.assertEqual(availability.tier_available(self.tier), 1)

    def test_a_converted_checkout_stops_holding_as_soon_as_it_is_issued(self):
        self.start('one@example.test')
        row = AbandonedCheckout.objects.get()
        row.converted_at = timezone.now()
        row.save(update_fields=['converted_at'])
        # The ticket it became is counted by sold, not by the hold: no double count.
        self.assertEqual(availability.in_flight_on_tier(self.tier), 0)


class FreeGuestUnderTheLockTests(TestCase):

    def setUp(self):
        self.organiser = Users.objects.create(
            username='free_org', email='free@vent.test',
            login_session_token='free-org-tk-0001'[:16], is_active=True)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Free Seat', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=6))
        self.tier = TicketTier.objects.create(
            event=self.event, name='Free', price=0, quantity=1)

    def test_the_second_free_guest_is_refused_not_issued(self):
        one = self.client.post('/event/%s/guest-buy/' % self.event.event_id,
                               data={'tier_id': self.tier.id, 'quantity': 1,
                                     'email': 'a@example.test'},
                               content_type='application/json')
        self.assertEqual(one.status_code, 201, one.content)
        two = self.client.post('/event/%s/guest-buy/' % self.event.event_id,
                               data={'tier_id': self.tier.id, 'quantity': 1,
                                     'email': 'b@example.test'},
                               content_type='application/json')
        self.assertEqual(two.status_code, 409, two.content)
        self.assertEqual(two.json()['code'], 'SOLD_OUT')

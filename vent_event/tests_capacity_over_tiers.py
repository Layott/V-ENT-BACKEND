"""Ticket types that add up to more than the capacity: the capacity wins.

CEO, 27 October 2026 (inbox 564), on the wizard now showing "more tickets than
places": "check it let's know what we'll do". Checked: the signed-in checkout
and the free guest checkout already stopped at the capacity. Two doors did not.

- A paid guest at the card gateway held their seats against their own ticket
  type and not against the room. With types adding up to more than the
  capacity, a second guest on a different type was sent to pay for the same
  last places, and both were issued tickets when they came back.
- A ticket the organiser sends by email (comp) checked the type's stock and
  never the room, so comps could fill a venue past its capacity.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Users

from . import availability
from .models import AbandonedCheckout, Event, Ticket, TicketTier


def _user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name.title(),
        login_session_token=('tk-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class TiersOverCapacityTests(TestCase):

    def setUp(self):
        self.organiser = _user('over_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Over Capacity', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, capacity=2,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))

    def tiers(self, price):
        # Four tickets across two types for a room of two.
        self.a = TicketTier.objects.create(
            event=self.event, name='Regular', price=Decimal(price), quantity=2)
        self.b = TicketTier.objects.create(
            event=self.event, name='VIP', price=Decimal(price), quantity=2)

    def guest(self, tier, quantity, email):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.post') as post:
            post.return_value.raise_for_status = lambda: None
            post.return_value.status_code = 200
            post.return_value.json = lambda: {
                'status': True, 'data': {'authorization_url': 'https://paystack.test/pay/x'}}
            return self.client.post(
                '/event/%s/guest-buy/' % self.event.event_id,
                data={'tier_id': tier.id, 'quantity': quantity, 'email': email},
                content_type='application/json')

    def test_free_types_stop_at_the_capacity(self):
        self.tiers(0)
        one = self.guest(self.a, 2, 'one@example.test')
        self.assertEqual(one.status_code, 201, one.content)
        two = self.guest(self.b, 1, 'two@example.test')
        self.assertEqual(two.status_code, 409, two.content)
        self.assertEqual(two.json()['code'], 'EVENT_FULL')
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 2)

    def test_seats_at_the_gateway_are_held_against_the_room(self):
        self.tiers(5000)
        one = self.guest(self.a, 2, 'one@example.test')
        self.assertEqual(one.status_code, 200, one.content)
        self.assertEqual(AbandonedCheckout.objects.count(), 1)
        # The other type still has its own two, but the room has none.
        self.assertEqual(availability.tier_available(self.b), 2)
        self.assertEqual(availability.event_room(self.event), 0)
        self.assertEqual(availability.available(self.b), 0)
        two = self.guest(self.b, 1, 'two@example.test')
        self.assertEqual(two.status_code, 409, two.content)
        self.assertEqual(two.json()['code'], 'EVENT_FULL')

    def test_the_room_comes_back_when_the_gateway_window_passes(self):
        self.tiers(5000)
        self.guest(self.a, 2, 'one@example.test')
        AbandonedCheckout.objects.update(
            started_at=timezone.now() - timedelta(minutes=availability.IN_FLIGHT_MINUTES + 1))
        self.assertEqual(availability.event_room(self.event), 2)

    def test_a_converted_checkout_is_counted_once(self):
        self.tiers(5000)
        self.guest(self.a, 2, 'one@example.test')
        AbandonedCheckout.objects.update(converted_at=timezone.now())
        # The tickets it became are counted as sold; the row no longer holds.
        self.assertEqual(availability.in_flight_on_event(self.event), 0)

    def test_a_seat_at_the_gateway_on_one_day_leaves_the_other_day_alone(self):
        Event.objects.filter(pk=self.event.pk).update(capacity_mode='per_day')
        self.event.refresh_from_db()
        first = (self.event.start_date).date()
        second = first + timedelta(days=1)
        self.a = TicketTier.objects.create(
            event=self.event, name='Saturday', price=Decimal(5000), quantity=2, day=first)
        self.b = TicketTier.objects.create(
            event=self.event, name='Sunday', price=Decimal(5000), quantity=2, day=second)
        self.guest(self.a, 2, 'one@example.test')
        self.assertEqual(availability.event_room(self.event, first), 0)
        self.assertEqual(availability.event_room(self.event, second), 2)

    def test_comps_stop_at_the_capacity(self):
        self.tiers(5000)
        Ticket.objects.create(event=self.event, tier=self.b, code='SOLD-0001',
                              price_vc=0, price_ngn=0, attendee_email='x@example.test')
        TicketTier.objects.filter(pk=self.b.pk).update(sold=1)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.organiser.login_session_token)
        sent = client.post('/event/%s/comp-tickets/' % self.event.slug,
                           {'tier_id': self.a.id,
                            'emails': ['p@example.test', 'q@example.test']},
                           format='json')
        self.assertEqual(sent.status_code, 400, sent.content)
        self.assertEqual(sent.json()['code'], 'NOT_ENOUGH_LEFT')
        self.assertEqual(sent.json()['data']['remaining'], 1)
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 1)

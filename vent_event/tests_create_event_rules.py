"""The rules that configure an event are set when it is created.

CEO, 1 September 2026, on being sent to the console to find them: "if i am
clicking on any of these and its still taking me here with this ui, then the
flow is very bad". And on the capacity: "IF I SET 5000, AND I AM SETTING
TICKETS FRO DIFFERENT DAYS, I SHOULD BE ABLE TO PICK IF THAT DAY MEANS STARTING
AFRESH OR IT KEEPS CPUNTING... SO I SHOULD HAVE THE OPTION SET IT HOW I WANT."

`create_event` already took `ticket_types` and `capacity`. It did not take
`capacity_mode`, so every event started on the default and an organiser who
wanted the other one had to create the event, find the console, find the
Tickets tab and change it there. The creation wizard sent none of the three.

The split this file pins: a rule that CONFIGURES the event belongs at creation;
the things that manage LIVE ACTIVITY - sales, the door, messages, holds - stay
in the console.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event.models import Event, TicketTier


class CreateEventRulesTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='ruleOwner', email='ro@vent.test',
            login_session_token='rule-owner-tk'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}

    def create(self, **extra):
        start = (timezone.now() + timezone.timedelta(days=10))
        body = {
            'name': 'Rules Con',
            'event_type': 'physical',
            'description': 'x',
            'start_date': start.isoformat(),
            'end_date': (start + timezone.timedelta(days=1)).isoformat(),
            'location': 'Lagos',
        }
        body.update(extra)
        return self.client.post('/event/create-event/', data=body,
                                content_type='application/json', **self.auth)

    def test_capacity_and_its_mode_are_set_when_the_event_is(self):
        res = self.create(capacity=5000, capacity_mode='total')
        self.assertEqual(res.status_code, 201, res.content[:400])
        event = Event.objects.get()
        self.assertEqual(event.capacity, 5000)
        self.assertEqual(event.capacity_mode, 'total')

    def test_the_other_mode_is_kept_too(self):
        self.create(capacity=5000, capacity_mode='per_day')
        self.assertEqual(Event.objects.get().capacity_mode, 'per_day')

    def test_a_mode_nobody_offers_falls_back_rather_than_failing(self):
        """A bad value here must not lose the whole event: an organiser has
        just filled in five steps."""
        res = self.create(capacity=100, capacity_mode='whatever')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Event.objects.get().capacity_mode, 'per_day')

    def test_ticket_types_are_created_with_the_event(self):
        start = (timezone.now() + timezone.timedelta(days=10)).date()
        res = self.create(ticket_types=[
            {'name': 'General Admission', 'price': '2000', 'quantity': 400,
             'perks': 'All-day entry', 'day': start.isoformat(),
             'day_label': 'Day 1'},
            {'name': 'VIP', 'price': '9000', 'quantity': 40},
        ])
        self.assertEqual(res.status_code, 201, res.content[:400])
        tiers = TicketTier.objects.order_by('id')
        self.assertEqual([t.name for t in tiers], ['General Admission', 'VIP'])
        self.assertEqual(tiers[0].price, Decimal('2000'))
        self.assertEqual(tiers[0].quantity, 400)
        self.assertEqual(tiers[0].day_label, 'Day 1')
        self.assertEqual(tiers[1].quantity, 40)

    def test_a_tier_for_a_day_the_event_does_not_run_keeps_no_day(self):
        """A ticket for a day there is no event is a door nobody can walk
        through."""
        self.create(ticket_types=[
            {'name': 'Ghost day', 'price': '1000', 'quantity': 10,
             'day': '2030-01-01'},
        ])
        self.assertIsNone(TicketTier.objects.get().day)

    def test_a_tier_with_no_name_is_skipped_not_stored(self):
        self.create(ticket_types=[
            {'name': '', 'price': '1000', 'quantity': 10},
            {'name': 'Real', 'price': '1000', 'quantity': 10},
        ])
        self.assertEqual([t.name for t in TicketTier.objects.all()], ['Real'])

    def test_an_event_created_with_no_rules_still_works(self):
        """Somebody who does not sell tickets is not made to think about them."""
        res = self.create()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(TicketTier.objects.count(), 0)
        self.assertEqual(Event.objects.get().capacity_mode, 'per_day')

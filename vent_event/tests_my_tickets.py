""""My tickets" said I had none while I was holding one.

CEO, 31 August 2026: "I went to the tickets tab and it said I don't have a
ticket, please make sure the counters under my tickets all work and display
properly in real time."

Two faults, one symptom:

1. `_issue` set `user=None` unconditionally. The guest checkout is the only
   checkout an event with a card price has, so a signed-in member buying
   through it got a ticket attached to nobody.
2. `claim_for` ran only when somebody verified their email, which happens once
   and almost always before the ticket exists. Nothing ever attached it later.

The counters were the same bug seen from the other end: the page counted rows
it had, and it had none.
"""
import json
import uuid

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from .models import Event, Ticket, TicketTier


def a_user(name, email=None):
    tag = uuid.uuid4().hex[:5]
    user = Users.objects.create(
        username='%s_%s' % (name, tag),
        email=email or '%s_%s@vent.test' % (name, tag),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class MyTicketsTests(TestCase):
    def setUp(self):
        self.organiser, _ = a_user('organiser')
        self.buyer, self.buyer_auth = a_user('buyer')
        self.event = Event.objects.create(
            name='Lagos Free Fire Finals', creator=self.organiser,
            event_type='physical', desc='One night', entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            start_date=timezone.now().date(), start_time='18:00', end_time='23:00',
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)

    def _mine(self):
        res = self.client.get('/event/my-tickets/', **self.buyer_auth)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['data']

    def test_a_free_ticket_bought_while_signed_in_belongs_to_the_buyer(self):
        res = self.client.post('/event/%s/guest-buy/' % self.event.slug, data=json.dumps({
            'tier_id': self.tier.id, 'quantity': 1, 'email': self.buyer.email,
        }), content_type='application/json', **self.buyer_auth)
        self.assertEqual(res.status_code, 201, res.content)

        data = self._mine()
        self.assertEqual(data['count'], 1)
        self.assertEqual(Ticket.objects.get().user_id, self.buyer.user_id)

    def test_a_ticket_bought_at_this_address_before_signing_in_is_claimed_on_sight(self):
        """The one the CEO hit: bought as a guest, then looked at the tab."""
        Ticket.objects.create(
            event=self.event, tier=self.tier, user=None, code='ORPHAN%s' % uuid.uuid4().hex[:6].upper(),
            attendee_email=self.buyer.email.upper(),   # case must not matter
        )
        self.assertEqual(self._mine()['count'], 1)
        self.assertEqual(Ticket.objects.get().user_id, self.buyer.user_id)

    def test_somebody_elses_guest_ticket_is_never_claimed(self):
        Ticket.objects.create(
            event=self.event, tier=self.tier, user=None,
            code='OTHER%s' % uuid.uuid4().hex[:6].upper(),
            attendee_email='stranger@example.com',
        )
        self.assertEqual(self._mine()['count'], 0)

    def test_the_counters_are_computed_from_the_same_rows_as_the_list(self):
        for state in ('valid', 'valid', 'checked_in', 'refunded', 'cancelled'):
            Ticket.objects.create(
                event=self.event, tier=self.tier, user=self.buyer,
                code='T%s' % uuid.uuid4().hex[:8].upper(), status=state)

        data = self._mine()
        self.assertEqual(data['counts'], {
            'all': 5, 'active': 2, 'used': 1, 'refunded': 2,
        })
        self.assertEqual(data['counts']['all'], len(data['tickets']))
        self.assertEqual(
            data['counts']['active'] + data['counts']['used'] + data['counts']['refunded'],
            data['counts']['all'])

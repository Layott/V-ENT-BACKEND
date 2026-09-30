"""The public event payload lists only the public ticket types (inbox 364).

A type behind an access code, or locked to an influencer, is a presale. The
ticket endpoint always hid it; the event card and detail payload listed it,
name and price, to anybody reading the JSON.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event.models import Event, TicketTier


class PublicTiersTests(TestCase):
    def setUp(self):
        organiser = Users.objects.create(username='tiersOrg', email='to@vent.test', is_active=True)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Presale Walk', creator=organiser, event_type='physical', desc='x',
            entry_fee=Decimal('0'), reg_start_date=now, reg_end_date=now,
            event_date=now.date(), start_time=now.time(), end_time=now.time())
        TicketTier.objects.create(event=self.event, name='General', price=Decimal('2000'), quantity=10)
        TicketTier.objects.create(event=self.event, name='Members presale', price=Decimal('1000'),
                                  quantity=10, access_code='MEMBERS')

    def names(self, body):
        data = body['data']
        data = data.get('event', data)
        return [t['name'] for t in data['ticket_types']]

    def test_detail_leaves_the_presale_out(self):
        body = self.client.get('/event/view-event/%s/' % self.event.slug).json()
        self.assertEqual(self.names(body), ['General'])

    def test_the_ticket_endpoint_agrees(self):
        body = self.client.get('/event/%s/ticket-types/' % self.event.slug).json()
        self.assertEqual([t['name'] for t in body['data']['tiers']], ['General'])

    def test_the_code_still_opens_it(self):
        body = self.client.get('/event/%s/ticket-types/?code=MEMBERS' % self.event.slug).json()
        self.assertIn('Members presale', [t['name'] for t in body['data']['tiers']])

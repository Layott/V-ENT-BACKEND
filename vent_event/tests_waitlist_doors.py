"""Every door a returned ticket comes through offers the queue.

`offer_next` was right and only one door called it: leaving the queue. A
ticket voided by an admin, a hold released by the organiser, an allocation
raised, a new type added: each put tickets back on sale and offered nobody
anything (walk, 18 September 2026). These walk each door, not the helper.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Users

from .models import Event, Ticket, TicketHold, TicketTier, WaitlistEntry


def a_user(name, **extra):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class WaitlistDoorsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('wd_org')
        self.first, self.first_auth = a_user('wd_first')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Doors Probe %s' % uuid.uuid4().hex[:4], creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0, capacity=5,
            start_date=now + timedelta(days=5), end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=1, sold=1)
        self.sold = Ticket.objects.create(
            event=self.event, tier=self.tier, user=self.organiser, code='VT-WD0', price_vc=0)
        # Sold out, and somebody is waiting.
        res = self.client.post('/event/%s/waitlist/' % self.event.slug, data={},
                               format='json', **self.first_auth)
        self.assertEqual(res.status_code, 201, res.content)

    def status_of_first(self):
        return WaitlistEntry.objects.get(user=self.first).status

    def test_raising_the_allocation_offers_the_queue(self):
        res = self.client.patch('/event/%s/tiers/%s/' % (self.event.slug, self.tier.id),
                                data={'quantity': 2}, format='json',
                                **self.org_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.status_of_first(), 'offered')

    def test_a_new_type_offers_the_queue(self):
        res = self.client.post('/event/%s/tiers/' % self.event.slug,
                               data={'name': 'Late release', 'price': 0, 'quantity': 3},
                               format='json', **self.org_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(self.status_of_first(), 'offered')

    def test_releasing_a_hold_offers_the_queue(self):
        # Two more on the type, both held back, so nothing is sellable.
        self.tier.quantity = 3
        self.tier.save(update_fields=['quantity'])
        hold = TicketHold.objects.create(event=self.event, tier=self.tier, quantity=2,
                                         name='Sponsors')
        self.assertEqual(self.status_of_first(), 'waiting')
        res = self.client.post('/event/%s/holds/%s/release/' % (self.event.slug, hold.id),
                               data={}, format='json', **self.org_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.status_of_first(), 'offered')

    def test_an_admin_void_offers_the_queue(self):
        admin, admin_auth = a_user('wd_admin', is_staff=True, admin_role='super_admin')
        res = self.client.post('/auth/admin/tickets/%s/action/' % self.sold.code,
                               data={'action': 'void', 'reason': 'Chargeback'},
                               format='json', **admin_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.status_of_first(), 'offered')

    def test_an_offer_holds_the_seat_for_the_person_it_was_made_to(self):
        """The returned seat read "1 remaining" to everybody, and the first
        hand to reach it took it from the person at the head of the queue."""
        other, other_auth = a_user('wd_other')
        self.client.patch('/event/%s/tiers/%s/' % (self.event.slug, self.tier.id),
                          data={'quantity': 2}, format='json', **self.org_auth)
        self.assertEqual(self.status_of_first(), 'offered')

        def remaining(auth):
            res = self.client.get('/event/%s/ticket-types/' % self.event.slug, **auth)
            return res.json()['data']['tiers'][0]['remaining']

        # The holder sees the seat; everybody else sees none.
        self.assertEqual(remaining(self.first_auth), 1)
        self.assertEqual(remaining(other_auth), 0)
        self.assertEqual(remaining({}), 0)

        # And a stranger who tries anyway is refused, while the holder buys.
        from vent_auth.models import UserWallet
        for who in (other, self.first):
            UserWallet.objects.get_or_create(
                user=who, defaults={'user_wallet_id': ('w%s' % uuid.uuid4().hex)[:10],
                                    'wallet_balance': 0})
        res = self.client.post('/event/%s/buy-ticket/' % self.event.slug,
                               data={'tier_id': self.tier.id, 'quantity': 1},
                               format='json', **other_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'EVENT_FULL')
        res = self.client.post('/event/%s/buy-ticket/' % self.event.slug,
                               data={'tier_id': self.tier.id, 'quantity': 1},
                               format='json', **self.first_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(self.status_of_first(), 'taken')

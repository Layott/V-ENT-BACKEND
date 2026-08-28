"""An organiser managing ticket types on an event that already exists.

Tiers could only be created inside the creation wizard. After that the event was
fixed: no adding a VIP tier once the standard ones sold, no correcting a price
typed wrong, no raising an allocation when a tier sold out with room still in
the venue.

The tests that matter are the ones about numbers that must not be able to lie:
`sold` is not writable, an allocation cannot drop below what is sold, and a tier
somebody holds a ticket on cannot be deleted.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, Ticket, TicketTier


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('t-%s' % name)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class TierManagementTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('tier_owner')
        self.stranger, self.stranger_auth = a_user('tier_stranger')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Meetup', creator=self.owner, event_type='physical',
            desc='A meetup.', entry_fee=0,
            start_date=now + timedelta(days=10),
            end_date=now + timedelta(days=10, hours=6),
            reg_start_date=now, reg_end_date=now + timedelta(days=9),
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=5000, quantity=100)

    def url(self, suffix=''):
        return '/event/%s/tiers/%s' % (self.event.event_id, suffix)

    def post(self, body, auth=None):
        return self.client.post(self.url(), data=body,
                                content_type='application/json',
                                **(auth if auth is not None else self.auth))

    def patch(self, tier, body, auth=None):
        return self.client.patch('%s%s/' % (self.url(), tier.id), data=body,
                                 content_type='application/json',
                                 **(auth if auth is not None else self.auth))

    # ------------------------------------------------------------- creating

    def test_an_organiser_adds_a_tier_after_the_event_exists(self):
        """The case this is for: standard sold out, open a VIP tier."""
        res = self.post({'name': 'VIP', 'price': '20000', 'quantity': 20,
                         'perks': ['Front row', 'Meet the players']})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(self.event.ticket_tiers.count(), 2)
        tier = res.json()['data']['tier']
        self.assertEqual(tier['name'], 'VIP')
        self.assertEqual(tier['remaining'], 20)
        self.assertEqual(tier['perks'], ['Front row', 'Meet the players'])

    def test_it_appears_on_the_public_ticket_list_immediately(self):
        """The endpoint the buy screen reads, not the one that wrote it."""
        self.post({'name': 'VIP', 'price': '20000', 'quantity': 20})
        res = self.client.get('/event/%s/ticket-types/' % self.event.event_id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('VIP', [t['name'] for t in res.json()['data']['tiers']])

    def test_a_tier_needs_a_name(self):
        self.assertEqual(self.post({'name': '  '}).status_code, 400)

    def test_two_tiers_with_the_same_name_are_refused(self):
        self.assertEqual(self.post({'name': 'general'}).status_code, 409)

    def test_a_price_that_is_not_a_number_is_refused(self):
        res = self.post({'name': 'Weird', 'price': 'twenty thousand'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'price')

    def test_a_free_tier_is_allowed(self):
        res = self.post({'name': 'Free entry', 'price': '0', 'quantity': 50})
        self.assertEqual(res.status_code, 201, res.content)

    def test_a_stranger_cannot_add_a_tier(self):
        res = self.post({'name': 'Mine now'}, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(self.event.ticket_tiers.count(), 1)

    # ------------------------------------------------------------- editing

    def test_a_price_typed_wrong_can_be_corrected(self):
        res = self.patch(self.tier, {'price': '7500'})
        self.assertEqual(res.status_code, 200, res.content)
        self.tier.refresh_from_db()
        self.assertEqual(int(self.tier.price), 7500)

    def test_more_can_be_opened_when_a_tier_sells_out(self):
        self.tier.sold = 100
        self.tier.save(update_fields=['sold'])
        res = self.patch(self.tier, {'quantity': 150})
        self.assertEqual(res.status_code, 200, res.content)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.quantity, 150)

    def test_the_allocation_cannot_drop_below_what_is_already_sold(self):
        """Twenty people hold a ticket. Setting it to ten does not un-sell ten
        of them, it makes every number on the page a lie."""
        self.tier.sold = 20
        self.tier.save(update_fields=['sold'])
        res = self.patch(self.tier, {'quantity': 10})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'BELOW_SOLD')
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.quantity, 100)

    def test_sold_is_not_writable(self):
        """It is the count of tickets that exist. An organiser who could edit it
        could make a sold-out tier look open and oversell the room."""
        self.tier.sold = 20
        self.tier.save(update_fields=['sold'])
        self.patch(self.tier, {'sold': 0})
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.sold, 20)

    def test_a_patch_that_changes_nothing_says_so(self):
        res = self.patch(self.tier, {})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NO_FIELDS_TO_UPDATE')

    def test_a_stranger_cannot_edit_a_tier(self):
        res = self.patch(self.tier, {'price': '1'}, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    # ------------------------------------------------------------- removing

    def test_a_tier_nobody_bought_can_be_removed(self):
        res = self.client.delete('%s%s/delete/' % (self.url(), self.tier.id),
                                 **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.event.ticket_tiers.count(), 0)

    def test_a_tier_somebody_holds_a_ticket_on_cannot_be_removed(self):
        """Deleting it would take the ticket with it, and the ticket is what
        somebody shows at the door."""
        holder, _ = a_user('tier_holder')
        Ticket.objects.create(
            event=self.event, tier=self.tier, user=holder,
            code='TESTCODE1', price_vc=0)
        res = self.client.delete('%s%s/delete/' % (self.url(), self.tier.id),
                                 **self.auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'TIER_HAS_TICKETS')
        self.assertTrue(TicketTier.objects.filter(pk=self.tier.pk).exists())


class VenueCapacityTests(TestCase):
    """The venue's ceiling, which is a SECOND ceiling and the lower one wins.

    Nothing reconciled the two: an organiser could set the venue to 200 and then
    sell 150 standard plus 100 VIP, because each ticket type only ever checked
    itself. Eventbrite documents the same rule for the same reason.
    """

    def setUp(self):
        self.owner, _ = a_user('cap_owner')
        self.buyer, self.buyer_auth = a_user('cap_buyer')
        # Buying goes through the wallet even when the ticket is free.
        UserWallet.objects.create(
            user_wallet_id='capw1', user=self.buyer, wallet_balance=0)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Small Room', creator=self.owner, event_type='physical',
            desc='A small room.', entry_fee=0, capacity=3,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4),
        )
        # Two types that between them hold far more than the room.
        self.standard = TicketTier.objects.create(
            event=self.event, name='Standard', price=0, quantity=50)
        self.vip = TicketTier.objects.create(
            event=self.event, name='VIP', price=0, quantity=50)

    def buy(self, tier, quantity=1):
        return self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': tier.id, 'quantity': quantity},
            content_type='application/json', **self.buyer_auth)

    def fill(self, n):
        for i in range(n):
            Ticket.objects.create(event=self.event, tier=self.standard,
                                  user=self.buyer, code='FILL%s' % i, price_vc=0)

    def test_a_full_room_refuses_the_next_ticket_even_with_stock_in_the_tier(self):
        self.fill(3)
        res = self.buy(self.vip)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'EVENT_FULL')

    def test_it_counts_across_ticket_types_not_within_one(self):
        """The whole point: 50 standard plus 50 VIP in a room that holds 3."""
        self.fill(2)
        res = self.buy(self.vip, quantity=2)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertIn('1', res.json()['message'])

    def test_a_cancelled_ticket_gives_its_place_back(self):
        self.fill(3)
        Ticket.objects.filter(code='FILL0').update(status='cancelled')
        res = self.buy(self.vip)
        self.assertIn(res.status_code, (200, 201), res.content)

    def test_an_event_with_no_capacity_set_is_bounded_only_by_its_tiers(self):
        """Most events. The ceiling is optional and absent means no ceiling."""
        self.event.capacity = 0
        self.event.save(update_fields=['capacity'])
        self.fill(10)
        res = self.buy(self.vip)
        self.assertIn(res.status_code, (200, 201), res.content)

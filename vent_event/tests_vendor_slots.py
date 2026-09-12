"""Selling a pitch, buying one, and running the shop it makes.

CEO, 7 September 2026: "event owners should be able to sell vendor slots to
other people, they can list prices for vendor slots with their rules and
conditions and other users should be able to buy and use the site to run the
shop or they can invite people too."

Written on the CONSEQUENCE rather than on a 201. A test that checks the buy
endpoint answers 201 would pass against an endpoint that takes the money and
creates nothing, which is precisely the failure worth guarding: what proves it
works is that afterwards the buyer OWNS a stall and can put a product in it,
and that a refusal leaves them with their coins and no stall.
"""
import uuid
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Transaction, Users, UserWallet
from vent_event.models import (Event, Vendor, VendorProduct, VendorSlot,
                               VendorSlotPurchase)

PIN = '1234'


def a_user(name, coins=0, with_pin=True):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = timezone.now()
    u.save()
    UserWallet.objects.create(
        user_wallet_id=uuid.uuid4().hex[:10], user=u, wallet_balance=coins,
        pin_hash=make_password(PIN) if with_pin else None)
    return u


def auth(u):
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


class SlotBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser = a_user('org', coins=0)
        self.buyer = a_user('buyer', coins=5000)
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.event = Event.objects.create(
            name='Slot Fest %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.organiser, event_type='physical', desc='x',
            entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            start_date=timezone.now() + timezone.timedelta(days=10),
            end_date=timezone.now() + timezone.timedelta(days=10, hours=6),
        )
        self.slot = VendorSlot.objects.create(
            event=self.event, name='Food stall, 3x3m',
            description='Table, one power point.',
            price_ngn=Decimal('50000'), quantity=2,
            rules='No open flames. Clear your own waste.',
            requires_approval=False,
        )

    def buy(self, user=None, **body):
        payload = {'accept_rules': True, 'pin': PIN}
        payload.update(body)
        return self.client.post(
            '/event/%s/slots/%d/buy/' % (self.event.slug, self.slot.id),
            payload, format='json', **auth(user or self.buyer))


class ListingTests(SlotBase):
    def test_anybody_can_see_what_is_for_sale(self):
        """Public: somebody deciding whether to trade at an event has to be
        able to see the pitch and its price before making an account."""
        res = self.client.get('/event/%s/slots/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        rows = res.json()['data']['slots']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Food stall, 3x3m')
        self.assertEqual(rows[0]['remaining'], 2)
        self.assertIn('No open flames', rows[0]['rules'])

    def test_a_stranger_is_not_offered_management(self):
        res = self.client.get('/event/%s/slots/' % self.event.slug)
        self.assertIs(res.json()['data']['can_manage'], False)

    def test_the_organiser_is(self):
        res = self.client.get('/event/%s/slots/' % self.event.slug,
                              **auth(self.organiser))
        self.assertIs(res.json()['data']['can_manage'], True)

    def test_only_the_organiser_can_list_a_slot(self):
        res = self.client.post('/event/%s/slots/' % self.event.slug,
                               {'name': 'Sneaky', 'price_ngn': 1},
                               format='json', **auth(self.buyer))
        self.assertEqual(res.status_code, 403)

    def test_a_withdrawn_slot_is_hidden_from_buyers_and_not_from_the_organiser(self):
        self.slot.is_active = False
        self.slot.save(update_fields=['is_active'])
        self.assertEqual(
            len(self.client.get('/event/%s/slots/' % self.event.slug)
                .json()['data']['slots']), 0)
        self.assertEqual(
            len(self.client.get('/event/%s/slots/' % self.event.slug,
                                **auth(self.organiser)).json()['data']['slots']), 1)

    def test_a_sold_out_slot_still_says_so_rather_than_vanishing(self):
        """Somebody who was told about a pitch needs to know what happened to
        it. A row that disappears reads as a broken link."""
        self.buy()
        self.buy(user=a_user('second', coins=5000))
        rows = self.client.get('/event/%s/slots/' % self.event.slug).json()['data']['slots']
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]['is_sold_out'], True)
        self.assertEqual(rows[0]['remaining'], 0)


class BuyingTests(SlotBase):
    def test_buying_makes_a_stall_the_buyer_owns(self):
        """The consequence. A 201 alone would pass against an endpoint that
        took the money and created nothing."""
        res = self.buy()
        self.assertEqual(res.status_code, 201, res.content[:400])
        vendor = Vendor.objects.get(event=self.event, owner=self.buyer)
        self.assertEqual(vendor.status, 'approved')   # this slot needs none
        self.assertEqual(VendorSlotPurchase.objects.count(), 1)

    def test_the_buyer_can_then_actually_run_the_shop(self):
        """The whole point of the feature: buying gets you a working stall,
        not a row in somebody's list."""
        self.buy()
        vendor = Vendor.objects.get(owner=self.buyer)
        res = self.client.post(
            '/event/vendor/%s/products/' % vendor.slug,
            {'name': 'Jollof', 'price': 2000, 'stock': 40},
            format='json', **auth(self.buyer))
        self.assertEqual(res.status_code, 201, res.content[:400])
        self.assertEqual(VendorProduct.objects.filter(vendor=vendor).count(), 1)

    def test_the_money_reaches_the_organiser(self):
        before = UserWallet.objects.get(user=self.organiser).wallet_balance
        self.buy()
        after = UserWallet.objects.get(user=self.organiser).wallet_balance
        paid = VendorSlotPurchase.objects.get().price_vc
        self.assertGreater(paid, 0)
        self.assertEqual(after - before, paid)
        self.assertEqual(
            UserWallet.objects.get(user=self.buyer).wallet_balance, 5000 - paid)

    def test_both_sides_of_the_money_are_recorded(self):
        self.buy()
        self.assertTrue(Transaction.objects.filter(
            wallet__user=self.buyer, type='deduction').exists())
        self.assertTrue(Transaction.objects.filter(
            wallet__user=self.organiser, type='receive').exists())

    def test_what_they_agreed_to_is_stored_word_for_word(self):
        """Not a pointer to the organiser's live text: they can edit it
        afterwards, and then nobody can answer what was agreed."""
        self.buy()
        purchase = VendorSlotPurchase.objects.get()
        self.assertEqual(purchase.rules_accepted, 'No open flames. Clear your own waste.')
        self.assertEqual(purchase.rules_version_accepted, 1)
        self.assertIsNotNone(purchase.accepted_at)

        self.slot.rules = 'Completely different conditions.'
        self.slot.save()
        purchase.refresh_from_db()
        self.assertIn('No open flames', purchase.rules_accepted)
        self.assertEqual(self.slot.rules_version, 2)

    def test_a_slot_needing_approval_starts_pending(self):
        self.slot.requires_approval = True
        self.slot.save(update_fields=['requires_approval'])
        self.buy()
        self.assertEqual(Vendor.objects.get(owner=self.buyer).status, 'pending')

    def test_a_free_slot_needs_no_pin_and_no_wallet(self):
        """An organiser giving pitches away should not force people through a
        payment they are not making."""
        free = VendorSlot.objects.create(event=self.event, name='Community table',
                                         price_ngn=0, quantity=1, requires_approval=False)
        broke = a_user('broke', coins=0, with_pin=False)
        res = self.client.post('/event/%s/slots/%d/buy/' % (self.event.slug, free.id),
                               {}, format='json', **auth(broke))
        self.assertEqual(res.status_code, 201, res.content[:400])
        self.assertTrue(Vendor.objects.filter(owner=broke).exists())


class RefusalsTests(SlotBase):
    """Every refusal is by name, and none of them takes the money."""

    def coins(self, user):
        return UserWallet.objects.get(user=user).wallet_balance

    def test_not_accepting_the_rules_is_refused_and_costs_nothing(self):
        before = self.coins(self.buyer)
        res = self.buy(accept_rules=False)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'RULES_NOT_ACCEPTED')
        self.assertEqual(self.coins(self.buyer), before)
        self.assertFalse(Vendor.objects.filter(owner=self.buyer).exists())

    def test_a_wrong_pin_is_refused_and_costs_nothing(self):
        before = self.coins(self.buyer)
        res = self.buy(pin='9999')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_PIN')
        self.assertEqual(self.coins(self.buyer), before)
        self.assertFalse(Vendor.objects.filter(owner=self.buyer).exists())

    def test_too_few_coins_is_refused_by_name(self):
        poor = a_user('poor', coins=1)
        res = self.buy(user=poor)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INSUFFICIENT_BALANCE')
        self.assertFalse(Vendor.objects.filter(owner=poor).exists())

    def test_buying_twice_at_one_event_is_refused(self):
        self.buy()
        res = self.buy()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_A_VENDOR')
        self.assertEqual(Vendor.objects.filter(owner=self.buyer).count(), 1)

    def test_a_sold_out_slot_is_refused(self):
        self.buy()
        self.buy(user=a_user('b2', coins=5000))
        third = a_user('b3', coins=5000)
        res = self.buy(user=third)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'SOLD_OUT')
        self.assertFalse(Vendor.objects.filter(owner=third).exists())

    def test_a_withdrawn_slot_cannot_be_bought(self):
        self.slot.is_active = False
        self.slot.save(update_fields=['is_active'])
        res = self.buy()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'SLOT_WITHDRAWN')

    def test_a_stranger_cannot_buy(self):
        res = self.client.post(
            '/event/%s/slots/%d/buy/' % (self.event.slug, self.slot.id),
            {'accept_rules': True}, format='json')
        self.assertIn(res.status_code, (401, 403))


class OrganiserEditingTests(SlotBase):
    def patch(self, **body):
        return self.client.patch(
            '/event/%s/slots/%d/' % (self.event.slug, self.slot.id),
            body, format='json', **auth(self.organiser))

    def test_the_price_and_the_rules_can_be_changed(self):
        res = self.patch(price_ngn=75000, rules='New conditions.')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.price_ngn, Decimal('75000'))
        self.assertEqual(self.slot.rules_version, 2)

    def test_editing_something_else_does_not_bump_the_rules_version(self):
        """The version has to move exactly when the wording does, or a stored
        acceptance points at a version that means nothing."""
        self.patch(name='Food stall, 4x4m')
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.rules_version, 1)

    def test_the_count_cannot_drop_below_what_is_sold(self):
        """Otherwise `remaining` goes negative and, worse, it implies
        somebody's stall does not exist."""
        self.buy()
        res = self.patch(quantity=0)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'BELOW_SOLD')

    def test_deleting_an_unsold_slot_removes_it(self):
        res = self.client.delete(
            '/event/%s/slots/%d/' % (self.event.slug, self.slot.id),
            **auth(self.organiser))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(VendorSlot.objects.filter(id=self.slot.id).exists())

    def test_deleting_a_SOLD_slot_withdraws_it_instead(self):
        """Somebody paid for one and their stall points at it. Deleting the row
        would take their record of what they agreed to with it."""
        self.buy()
        self.client.delete('/event/%s/slots/%d/' % (self.event.slug, self.slot.id),
                           **auth(self.organiser))
        self.slot.refresh_from_db()
        self.assertFalse(self.slot.is_active)
        self.assertTrue(VendorSlotPurchase.objects.filter(slot=self.slot).exists())

    def test_a_buyer_cannot_edit_the_slot(self):
        res = self.client.patch(
            '/event/%s/slots/%d/' % (self.event.slug, self.slot.id),
            {'price_ngn': 1}, format='json', **auth(self.buyer))
        self.assertEqual(res.status_code, 403)


class BoughtAndInvitedAreTheSameTests(SlotBase):
    """However a vendor got in, they run the same shop.

    This is the gate that matters most for the feature: build the two routes
    apart and the invited vendor and the paying vendor get different products,
    and one of them stops being maintained.
    """

    def test_a_bought_stall_and_an_invited_stall_are_the_same_shape(self):
        self.buy()
        bought = Vendor.objects.get(owner=self.buyer)

        invited_user = a_user('invited', coins=0)
        invited = Vendor.objects.create(
            event=self.event, owner=invited_user, name='Invited stall',
            status='approved')

        for vendor, who in ((bought, self.buyer), (invited, invited_user)):
            res = self.client.post(
                '/event/vendor/%s/products/' % vendor.slug,
                {'name': 'Thing', 'price': 100, 'stock': 5},
                format='json', **auth(who))
            self.assertEqual(res.status_code, 201,
                             '%s could not list a product: %s'
                             % (vendor.name, res.content[:200]))

    def test_one_vendor_cannot_touch_another_stall(self):
        self.buy()
        other = a_user('other', coins=0)
        mine = Vendor.objects.get(owner=self.buyer)
        res = self.client.post('/event/vendor/%s/products/' % mine.slug,
                               {'name': 'Nope', 'price': 1, 'stock': 1},
                               format='json', **auth(other))
        self.assertEqual(res.status_code, 403)


class AddingAStallByHandTests(SlotBase):
    """`POST /event/<event>/vendors/create/`: the invited door, beside the
    bought one. It had no screen and no test until 12 September."""

    def add(self, user=None, **body):
        return self.client.post(
            '/event/%s/vendors/create/' % self.event.slug,
            body, format='json', **auth(user or self.organiser))

    def test_the_organiser_adds_a_stall_with_no_owner(self):
        res = self.add(name='Merch table', booth='B4')
        self.assertEqual(res.status_code, 201, res.data)
        stall = Vendor.objects.get(event=self.event, name='Merch table')
        self.assertIsNone(stall.owner)
        self.assertEqual(stall.status, 'approved')

    def test_somebody_on_vent_becomes_the_owner_by_username_or_email(self):
        res = self.add(name='Grill', owner='@' + self.buyer.username)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Vendor.objects.get(name='Grill').owner_id, self.buyer.user_id)
        other = a_user('cook')
        res = self.add(name='Smoothies', owner=other.email.upper())
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Vendor.objects.get(name='Smoothies').owner_id, other.user_id)

    def test_an_address_nobody_has_claimed_becomes_an_invitation(self):
        from vent_event.models import VendorInvite
        res = self.add(name='Prints', owner='artist@example.com', booth='C1')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(Vendor.objects.filter(name='Prints').exists())
        invite = VendorInvite.objects.get(event=self.event, email='artist@example.com')
        self.assertEqual((invite.name, invite.booth), ('Prints', 'C1'))
        # And the invitation becomes this same stall when they arrive.
        from vent_auth.invites import claim_pending
        newcomer = Users.objects.create(
            username='artist', email='artist@example.com',
            login_session_token='tkartist00000001', is_active=True)
        claim_pending(newcomer)
        stall = Vendor.objects.get(event=self.event, owner=newcomer)
        self.assertEqual((stall.name, stall.booth, stall.status), ('Prints', 'C1', 'approved'))

    def test_a_word_that_is_neither_is_refused_with_the_reason(self):
        res = self.add(name='Prints', owner='nobody-here')
        self.assertEqual(res.status_code, 404)
        self.assertIn('not an email address', res.data['message'])

    def test_a_stranger_may_not_add_one(self):
        res = self.add(user=self.buyer, name='Mine')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Vendor.objects.filter(name='Mine').exists())

    def test_a_name_is_required(self):
        res = self.add(name='   ')
        self.assertEqual(res.status_code, 400)

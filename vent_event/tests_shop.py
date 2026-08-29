"""The shop at an event: products, buying, and collecting.

PRD section 4: "As a premium organizer, I want to be able to add a temporary
product shop to my event. In which users can buy things."

The models and endpoints for this already existed and had NO tests, which for a
path that moves money is where a fault gets to live longest. Writing them found
one: the buyer's wallet was debited and the seller's was never credited, so
every purchase destroyed the money.

The rest of the suite is about the two things a shop must never get wrong:
overselling, and charging somebody twice.
"""
from datetime import time, timedelta

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Transaction, Users, UserWallet

from .models import Event, Vendor, VendorOrder, VendorProduct

PIN = '1234'


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('sh-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id='w%09d' % user.user_id, user=user,
        wallet_balance=balance, pin_hash=make_password(PIN))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ShopBase(TestCase):
    def setUp(self):
        self.organiser, self.org_auth = a_user('sh_org')
        self.buyer, self.buyer_auth = a_user('sh_buyer', balance=500)
        game = Games.objects.create(game_title='EA FC SH')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Shop Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=3),
            event_date=now.date(), start_time=time(18, 0), end_time=time(22, 0),
            location='Lagos')
        # The organiser's own shop is a vendor they own. There is no second
        # model for it, and there should not be: a stall is a stall.
        self.vendor = Vendor.objects.create(
            event=self.event, owner=self.organiser, name='Merch Table',
            status='open')
        self.shirt = VendorProduct.objects.create(
            vendor=self.vendor, name='Team shirt', price=10000, stock=5)

    def buy(self, items=None, pin=PIN, auth=None):
        # `is None` rather than falsy: an empty list is a real thing to send,
        # and `items or [default]` quietly turned that test into a valid order.
        if items is None:
            items = [{'product_id': self.shirt.id, 'quantity': 1}]
        body = {'items': items}
        if pin is not None:
            body['pin'] = pin
        return self.client.post(
            '/event/vendor/%s/order/' % self.vendor.id, body,
            content_type='application/json', **(auth or self.buyer_auth))

    def balance(self, user):
        return UserWallet.objects.get(user=user).wallet_balance


class BuyingTests(ShopBase):
    def test_a_shopper_buys_something(self):
        res = self.buy()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(VendorOrder.objects.count(), 1)

    def test_the_buyer_is_charged(self):
        before = self.balance(self.buyer)
        res = self.buy()
        cost = res.data['data']['order']['total_vc']
        self.assertGreater(cost, 0)
        self.assertEqual(self.balance(self.buyer), before - cost)

    def test_the_seller_is_paid(self):
        # The fault this suite was written to find. The buyer was debited and
        # nobody was credited, so the money simply stopped existing.
        before = self.balance(self.organiser)
        res = self.buy()
        cost = res.data['data']['order']['total_vc']
        self.assertEqual(self.balance(self.organiser), before + cost)

    def test_the_seller_gets_a_transaction_row(self):
        # A balance that changed with no record of why is unauditable.
        self.buy()
        rows = Transaction.objects.filter(wallet__user=self.organiser)
        self.assertEqual(rows.count(), 1)
        self.assertGreater(rows.first().amount, 0)

    def test_the_money_is_conserved(self):
        before = self.balance(self.buyer) + self.balance(self.organiser)
        self.buy()
        after = self.balance(self.buyer) + self.balance(self.organiser)
        self.assertEqual(before, after)

    def test_stock_goes_down_and_sold_goes_up(self):
        self.buy([{'product_id': self.shirt.id, 'quantity': 2}])
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 3)
        self.assertEqual(self.shirt.sold, 2)

    def test_it_cannot_be_oversold(self):
        res = self.buy([{'product_id': self.shirt.id, 'quantity': 6}])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_STOCK')
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 5)

    def test_a_refused_order_charges_nobody(self):
        before_buyer = self.balance(self.buyer)
        before_seller = self.balance(self.organiser)
        self.buy([{'product_id': self.shirt.id, 'quantity': 99}])
        self.assertEqual(self.balance(self.buyer), before_buyer)
        self.assertEqual(self.balance(self.organiser), before_seller)

    def test_the_wrong_pin_buys_nothing(self):
        res = self.buy(pin='9999')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INVALID_PIN')
        self.assertEqual(VendorOrder.objects.count(), 0)

    def test_too_little_money_buys_nothing(self):
        poor, poor_auth = a_user('sh_poor', balance=1)
        res = self.buy(auth=poor_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_BALANCE')

    def test_a_closed_stall_sells_nothing(self):
        self.vendor.status = 'closed'
        self.vendor.save()
        res = self.buy()
        self.assertEqual(res.status_code, 409)

    def test_a_product_from_another_stall_is_refused(self):
        other = Vendor.objects.create(event=self.event, owner=self.organiser,
                                      name='Other', status='open')
        stray = VendorProduct.objects.create(vendor=other, name='Cap',
                                             price=1000, stock=5)
        res = self.buy([{'product_id': stray.id, 'quantity': 1}])
        self.assertEqual(res.status_code, 404)

    def test_a_free_product_needs_no_pin(self):
        free = VendorProduct.objects.create(vendor=self.vendor, name='Sticker',
                                            price=0, stock=10)
        res = self.buy([{'product_id': free.id, 'quantity': 1}], pin=None)
        self.assertEqual(res.status_code, 201, res.data)

    def test_a_free_product_moves_no_money(self):
        free = VendorProduct.objects.create(vendor=self.vendor, name='Sticker',
                                            price=0, stock=10)
        before = self.balance(self.organiser)
        self.buy([{'product_id': free.id, 'quantity': 1}], pin=None)
        self.assertEqual(self.balance(self.organiser), before)

    def test_buying_from_your_own_stall_moves_nothing(self):
        # The organiser owns this stall. Debiting and crediting the same wallet
        # is a no-op that should still not produce two misleading rows.
        before = self.balance(self.organiser)
        res = self.buy(auth=self.org_auth)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self.balance(self.organiser), before)

    def test_zero_quantity_is_refused(self):
        res = self.buy([{'product_id': self.shirt.id, 'quantity': 0}])
        self.assertEqual(res.status_code, 400)

    def test_an_empty_order_is_refused(self):
        res = self.buy([])
        self.assertEqual(res.status_code, 400)


class CollectingTests(ShopBase):
    def order(self):
        res = self.buy()
        return res.data['data']['order']['code']

    def test_the_stall_marks_an_order_collected(self):
        code = self.order()
        res = self.client.post('/event/vendor/order/%s/collect/' % code, {},
                               content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNotNone(VendorOrder.objects.get(code=code).collected_at)

    def test_it_can_only_be_collected_once(self):
        code = self.order()
        self.client.post('/event/vendor/order/%s/collect/' % code, {},
                         content_type='application/json', **self.org_auth)
        again = self.client.post('/event/vendor/order/%s/collect/' % code, {},
                                 content_type='application/json', **self.org_auth)
        self.assertIn(again.status_code, (400, 409))

    def test_a_stranger_collects_nothing(self):
        code = self.order()
        _other, other_auth = a_user('sh_other')
        res = self.client.post('/event/vendor/order/%s/collect/' % code, {},
                               content_type='application/json', **other_auth)
        self.assertEqual(res.status_code, 403)
        self.assertIsNone(VendorOrder.objects.get(code=code).collected_at)

    def test_an_unknown_code_is_a_404(self):
        res = self.client.post('/event/vendor/order/NOSUCHCODE/collect/', {},
                               content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 404)

    def test_the_buyer_can_see_their_own_orders(self):
        self.order()
        res = self.client.get('/event/vendor-orders/', **self.buyer_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['data']['orders']), 1)


class ProductTests(ShopBase):
    def test_the_owner_lists_a_product(self):
        res = self.client.post(
            '/event/vendor/%s/products/' % self.vendor.id,
            {'name': 'Hoodie', 'price': 25000, 'stock': 3},
            content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(VendorProduct.objects.filter(name='Hoodie').count(), 1)

    def test_a_stranger_lists_nothing(self):
        res = self.client.post(
            '/event/vendor/%s/products/' % self.vendor.id,
            {'name': 'Hoodie', 'price': 25000, 'stock': 3},
            content_type='application/json', **self.buyer_auth)
        self.assertEqual(res.status_code, 403)

    def test_the_shop_is_readable_without_an_account(self):
        # Somebody deciding whether to come to the event should be able to see
        # what is on sale.
        res = self.client.get('/event/%s/vendors/' % self.event.event_id)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['data']['vendors'])

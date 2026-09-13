"""A fee on everything a stall sells, and the stallholder chooses who bears it.

CEO, 13 September 2026: "The organizer decides if they want to handle the cost
or they want people buying the tickets to, same for vendors, should have a fee
on everything sold."

The rule is the tickets' rule (5% + 100 naira per unit sold, both admin
settings) through the same functions in vent_event/ledger.py. A wallet pays
whole coins, so with the fee on the buyer the whole coins of it go on top and
the part under a coin comes off the stallholder; the stall is paid the whole
coins its naira has reached at each order and carries the rest.
"""
from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Transaction, UserWallet

from .models import Event, Vendor, VendorOrder, VendorProduct
from .tests_ledger import set_fee
from .tests_shop import PIN, a_user


class StallFeeBase(TestCase):
    def setUp(self):
        self.holder, self.holder_auth = a_user('vf_holder')
        self.buyer, self.buyer_auth = a_user('vf_buyer', balance=500)
        game = Games.objects.create(game_title='EA FC VF')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Fee Probe', game=game, creator=self.holder,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=3),
            event_date=now.date(), start_time=time(18, 0), end_time=time(22, 0),
            location='Lagos')
        self.stall = Vendor.objects.create(
            event=self.event, owner=self.holder, name='Suya Corner', status='live')
        self.plate = VendorProduct.objects.create(
            vendor=self.stall, name='Plate', price=2000, stock=50)
        self.hoodie = VendorProduct.objects.create(
            vendor=self.stall, name='Hoodie', price=25000, stock=5)
        set_fee(5, 100)

    def order(self, items, auth=None):
        return self.client.post(
            '/event/vendor/%s/order/' % self.stall.slug,
            {'items': items, 'pin': PIN}, content_type='application/json',
            **(auth or self.buyer_auth))

    def balance(self, user):
        return UserWallet.objects.get(user=user).wallet_balance


class FeeOnEverythingSoldTests(StallFeeBase):
    def test_five_per_cent_plus_a_hundred_on_every_unit(self):
        # Two plates at 2,000: 2 x (100 + 100) = 400. One hoodie at 25,000:
        # 1,250 + 100. Together 1,750.
        res = self.order([{'product_id': self.plate.id, 'quantity': 2},
                          {'product_id': self.hoodie.id, 'quantity': 1}])
        self.assertEqual(res.status_code, 201, res.content[:300])
        order = res.json()['data']['order']
        self.assertEqual(order['items_ngn'], 29000.0)
        self.assertEqual(order['fee_ngn'], 1750.0)
        self.assertEqual(order['fee_pct'], 5)
        self.assertEqual(order['fee_flat_ngn'], 100.0)

    def test_by_default_the_stallholder_absorbs_it_and_is_paid_the_whole_coins(self):
        before = self.balance(self.holder)
        res = self.order([{'product_id': self.plate.id, 'quantity': 2}])
        order = res.json()['data']['order']
        # The buyer pays the price, 4 coins, and nothing on top.
        self.assertEqual(order['total_vc'], 4)
        self.assertEqual(order['buyer_fee_vc'], 0)
        self.assertEqual(order['fee_bearer'], 'vendor')
        # The stall earned 4,000 less 400: 3 coins now, 600 carried.
        self.assertEqual(order['vendor_ngn'], 3600.0)
        self.assertEqual(order['vendor_paid_vc'], 3)
        self.assertEqual(self.balance(self.holder), before + 3)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.carry_ngn, Decimal('600.00'))

    def test_the_carry_pays_out_the_moment_it_reaches_a_coin(self):
        self.order([{'product_id': self.plate.id, 'quantity': 2}])   # carry 600
        before = self.balance(self.holder)
        res = self.order([{'product_id': self.plate.id, 'quantity': 2}])
        order = res.json()['data']['order']
        # 600 carried + 3,600 earned = 4,200: 4 coins now, 200 carried.
        self.assertEqual(order['vendor_paid_vc'], 4)
        self.assertEqual(self.balance(self.holder), before + 4)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.carry_ngn, Decimal('200.00'))

    def test_the_stallholder_can_put_it_on_the_buyer(self):
        res = self.client.patch('/event/my-stalls/%s/' % self.stall.slug,
                                {'fee_bearer': 'buyer'}, content_type='application/json',
                                **self.holder_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.fee_bearer, 'buyer')
        # And back. Nonsense is refused.
        res = self.client.patch('/event/my-stalls/%s/' % self.stall.slug,
                                {'fee_bearer': 'the moon'}, content_type='application/json',
                                **self.holder_auth)
        self.assertEqual(res.status_code, 400)

    def test_on_the_buyer_a_wallet_pays_the_whole_coins_of_it_on_top(self):
        self.stall.fee_bearer = 'buyer'
        self.stall.save(update_fields=['fee_bearer'])
        before_buyer, before_holder = self.balance(self.buyer), self.balance(self.holder)
        res = self.order([{'product_id': self.hoodie.id, 'quantity': 1}])
        order = res.json()['data']['order']
        # 25,000 hoodie, fee 1,350: the buyer pays 25 + 1 coins, the 350 under a
        # coin comes off the stall, which earns 24,650: 24 coins now, 650 carried.
        self.assertEqual(order['fee_ngn'], 1350.0)
        self.assertEqual(order['buyer_fee_vc'], 1)
        self.assertEqual(order['total_vc'], 26)
        self.assertEqual(order['vendor_ngn'], 24650.0)
        self.assertEqual(order['vendor_paid_vc'], 24)
        self.assertEqual(self.balance(self.buyer), before_buyer - 26)
        self.assertEqual(self.balance(self.holder), before_holder + 24)

    def test_on_the_buyer_a_fee_under_a_coin_still_comes_off_the_stall(self):
        self.stall.fee_bearer = 'buyer'
        self.stall.save(update_fields=['fee_bearer'])
        res = self.order([{'product_id': self.plate.id, 'quantity': 1}])
        order = res.json()['data']['order']
        self.assertEqual(order['fee_ngn'], 200.0)
        self.assertEqual(order['buyer_fee_vc'], 0)
        self.assertEqual(order['total_vc'], 2)
        self.assertEqual(order['vendor_ngn'], 1800.0)

    def test_the_stallholder_taking_their_own_stock_pays_no_fee(self):
        res = self.order([{'product_id': self.plate.id, 'quantity': 1}], auth=self.holder_auth)
        order = res.json()['data']['order']
        self.assertEqual(order['total_vc'], 0)
        self.assertEqual(order['fee_ngn'], 0.0)
        self.assertEqual(order['vendor_ngn'], 0.0)

    def test_the_orders_endpoint_totals_the_naira(self):
        self.order([{'product_id': self.plate.id, 'quantity': 2}])
        res = self.client.get('/event/my-stalls/%s/orders/' % self.stall.slug, **self.holder_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual(data['fees_ngn'], 400.0)
        self.assertEqual(data['kept_ngn'], 3600.0)
        self.assertEqual(data['paid_vc'], 3)
        self.assertEqual(data['carry_ngn'], 600.0)

    def test_the_stall_payload_says_who_bears_it_and_the_rule(self):
        res = self.client.get('/event/%s/vendor/%s/' % (self.event.slug, self.stall.slug))
        v = res.json()['data']['vendor']
        self.assertEqual(v['fee_bearer'], 'vendor')
        self.assertEqual((v['fee_pct'], v['fee_flat_ngn']), (5, 100.0))

    def test_the_numbers_are_stamped_so_a_later_change_rewrites_nothing(self):
        res = self.order([{'product_id': self.plate.id, 'quantity': 1}])
        set_fee(12, 500)
        order = VendorOrder.objects.get(code=res.json()['data']['order']['code'])
        self.assertEqual((order.fee_pct, order.fee_flat_ngn, order.fee_ngn),
                         (5, Decimal('100.00'), Decimal('200.00')))


class QuoteTests(StallFeeBase):
    def test_the_cart_is_told_the_fee_before_the_pin(self):
        self.stall.fee_bearer = 'buyer'
        self.stall.save(update_fields=['fee_bearer'])
        res = self.client.post('/event/vendor/%s/quote/' % self.stall.slug,
                               {'items': [{'product_id': self.hoodie.id, 'quantity': 1}]},
                               content_type='application/json', **self.buyer_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        q = res.json()['data']
        self.assertEqual(q['items_vc'], 25)
        self.assertEqual(q['fee_ngn'], 1350.0)
        self.assertEqual(q['buyer_fee_vc'], 1)
        self.assertEqual(q['total_vc'], 26)
        self.assertTrue(q['buyer_pays_fee'])
        # Nothing moved.
        self.assertEqual(self.balance(self.buyer), 500)
        self.assertEqual(VendorOrder.objects.count(), 0)

    def test_the_quote_and_the_order_agree(self):
        items = [{'product_id': self.plate.id, 'quantity': 3},
                 {'product_id': self.hoodie.id, 'quantity': 2}]
        q = self.client.post('/event/vendor/%s/quote/' % self.stall.slug, {'items': items},
                             content_type='application/json', **self.buyer_auth).json()['data']
        o = self.order(items).json()['data']['order']
        for key in ('total_vc', 'fee_ngn', 'buyer_fee_vc', 'vendor_ngn', 'items_ngn'):
            self.assertEqual(q[key], o[key], key)

    def test_signed_out_gets_no_quote(self):
        res = self.client.post('/event/vendor/%s/quote/' % self.stall.slug,
                               {'items': [{'product_id': self.plate.id, 'quantity': 1}]},
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)


class CancelledOrderTests(StallFeeBase):
    def cancel(self, code):
        return self.client.post('/event/my-stalls/%s/orders/%s/status/' % (self.stall.slug, code),
                                {'status': 'cancelled'}, content_type='application/json',
                                **self.holder_auth)

    def test_a_cancel_refunds_the_buyer_takes_back_the_stalls_coins_and_restocks(self):
        res = self.order([{'product_id': self.plate.id, 'quantity': 2}])
        code = res.json()['data']['order']['code']
        self.plate.refresh_from_db()
        self.assertEqual(self.plate.stock, 48)
        buyer_before, holder_before = self.balance(self.buyer), self.balance(self.holder)
        res = self.cancel(code)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.balance(self.buyer), buyer_before + 4)
        self.assertEqual(self.balance(self.holder), holder_before - 3)
        self.plate.refresh_from_db()
        self.assertEqual((self.plate.stock, self.plate.sold), (50, 0))
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.carry_ngn, Decimal('0.00'))
        self.assertEqual(Transaction.objects.filter(type='refund').count(), 2)

    def test_a_stall_that_has_spent_the_coins_cannot_cancel(self):
        res = self.order([{'product_id': self.hoodie.id, 'quantity': 1}])
        code = res.json()['data']['order']['code']
        wallet = UserWallet.objects.get(user=self.holder)
        wallet.wallet_balance = 0
        wallet.save(update_fields=['wallet_balance'])
        res = self.cancel(code)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'CANNOT_REFUND')
        self.assertEqual(VendorOrder.objects.get(code=code).status, 'paid')
        self.assertEqual(self.balance(self.buyer), 500 - 25)

    def test_a_cancelled_order_counts_for_nothing(self):
        code = self.order([{'product_id': self.plate.id, 'quantity': 2}]).json()['data']['order']['code']
        self.cancel(code)
        data = self.client.get('/event/my-stalls/%s/orders/' % self.stall.slug,
                               **self.holder_auth).json()['data']
        self.assertEqual((data['fees_ngn'], data['kept_ngn'], data['paid_vc']), (0.0, 0.0, 0))

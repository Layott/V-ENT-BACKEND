"""The event's own shop and the scanner card (inbox 550, 553, 554; 25 October 2026).

"As a premium organizer, I want to be able to add a temporary product shop to
my event. In which users can buy things (can be at a discount) and get them
only at the venue and only on the event day (Only physical products)."
Then: "when scanning in people, they should be able to see if that person has
bought things and what things, also if the person is an esports player ...
When scanned in, the person in charge of the store and those accounts manning
it on the site should be able to see that this particular person is around."

Each test checks what a door DOES, the coins and the rows, not only its status.
"""
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from vent_auth.models import UserWallet
from vent_event.models import (EventPromo, Ticket, TicketTier, Vendor, VendorOrder,
                               VendorProduct, VendorStaff)
from vent_event.tests_vendor_shop import PIN, ShopBase, a_user, auth


def balance(user):
    return UserWallet.objects.get(user=user).wallet_balance


class EventShopBase(ShopBase):
    def setUp(self):
        super().setUp()
        self.organiser.is_premium = True
        self.organiser.save(update_fields=['is_premium'])

    def open_shop(self, who=None):
        return self.client.post('/event/%s/shop/' % self.event.slug, {}, format='json',
                                **auth(who or self.organiser))

    def shop(self):
        return Vendor.objects.get(event=self.event, kind=Vendor.KIND_ORGANISER)

    def product(self, shop, **extra):
        values = dict(vendor=shop, name='Event hoodie', price=Decimal('30000'), stock=10)
        values.update(extra)
        return VendorProduct.objects.create(**values)

    def buy(self, shop, product, qty=1, who=None, **extra):
        body = {'items': [{'product_id': product.id, 'quantity': qty}], 'pin': PIN}
        body.update(extra)
        return self.client.post('/event/vendor/%s/order/' % shop.slug, body, format='json',
                                **auth(who or self.buyer))


class OpenTheShopTests(EventShopBase):
    def test_a_premium_organiser_opens_one_live_shop(self):
        res = self.open_shop()
        self.assertEqual(res.status_code, 201, res.content[:300])
        shop = self.shop()
        self.assertEqual(shop.status, 'live')
        self.assertEqual(shop.owner, self.organiser)
        again = self.open_shop()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(Vendor.objects.filter(event=self.event, kind='organiser').count(), 1)

    def test_without_premium_it_is_refused_with_a_code(self):
        self.organiser.is_premium = False
        self.organiser.save(update_fields=['is_premium'])
        res = self.open_shop()
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.json()['code'], 'PREMIUM_REQUIRED')
        self.assertFalse(Vendor.objects.filter(event=self.event, kind='organiser').exists())

    def test_a_stranger_cannot_open_it(self):
        res = self.open_shop(a_user('stranger'))
        self.assertEqual(res.status_code, 403)

    def test_the_shop_is_collected_at_the_venue_only(self):
        self.open_shop()
        shop = self.shop()
        hoodie = self.product(shop, can_deliver=True)
        res = self.buy(shop, hoodie, fulfilment='deliver',
                       delivery={'name': 'A', 'phone': '1', 'address': 'x'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'PICKUP_ONLY')
        self.assertFalse(VendorOrder.objects.exists())


class PriceTests(EventShopBase):
    def setUp(self):
        super().setUp()
        self.open_shop()
        self.s = self.shop()

    def test_a_discount_is_charged_and_quoted_alike(self):
        hoodie = self.product(self.s, discount_kind='percent', discount_value=Decimal('20'))
        quote = self.client.post('/event/vendor/%s/quote/' % self.s.slug,
                                 {'items': [{'product_id': hoodie.id, 'quantity': 1}]},
                                 format='json', **auth(self.buyer)).json()['data']
        before = balance(self.buyer)
        res = self.buy(self.s, hoodie)
        self.assertEqual(res.status_code, 201, res.content[:300])
        order = VendorOrder.objects.get()
        self.assertEqual(order.items.get().unit_vc, 24)
        self.assertEqual(before - balance(self.buyer), order.total_vc)
        self.assertEqual(quote['total_vc'], order.total_vc)
        self.assertEqual(order.discount_ngn, Decimal('6000.00'))

    def test_a_price_in_the_request_is_ignored(self):
        hoodie = self.product(self.s)
        res = self.buy(self.s, hoodie, price=1, total_vc=1)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(VendorOrder.objects.get().items.get().unit_vc, 30)

    def test_a_shop_code_takes_its_share_and_counts_its_use(self):
        hoodie = self.product(self.s)
        EventPromo.objects.create(event=self.event, code='HALLB10', kind='percent',
                                  value=Decimal('10'), scope='shop', max_tickets=5)
        res = self.buy(self.s, hoodie, qty=2, promo_code='hallb10')
        self.assertEqual(res.status_code, 201, res.content[:300])
        order = VendorOrder.objects.get()
        self.assertEqual(order.items.get().unit_vc, 27)
        self.assertEqual(order.promo.code, 'HALLB10')
        self.assertEqual(EventPromo.objects.get(code='HALLB10').used_tickets, 2)

    def test_a_ticket_code_does_nothing_at_the_shop_and_the_reverse(self):
        hoodie = self.product(self.s)
        EventPromo.objects.create(event=self.event, code='TIX', kind='percent',
                                  value=Decimal('50'), scope='tickets')
        res = self.buy(self.s, hoodie, promo_code='TIX')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'PROMO_UNKNOWN')
        from vent_event import promos
        EventPromo.objects.create(event=self.event, code='SHOPONLY', kind='percent',
                                  value=Decimal('50'), scope='shop')
        promo, why = promos.resolve(self.event, 'SHOPONLY')
        self.assertIsNone(promo)
        self.assertEqual(why, 'PROMO_UNKNOWN')

    def test_a_code_for_another_stall_is_refused(self):
        hoodie = self.product(self.s)
        EventPromo.objects.create(event=self.event, code='MAMAT', kind='percent',
                                  value=Decimal('10'), scope='shop', vendor=self.stall)
        res = self.buy(self.s, hoodie, promo_code='MAMAT')
        self.assertEqual(res.json()['code'], 'PROMO_WRONG_SHOP')


class PickupTests(EventShopBase):
    def setUp(self):
        super().setUp()
        self.open_shop()
        self.s = self.shop()
        self.hoodie = self.product(self.s)

    def test_the_window_must_sit_on_the_event_days(self):
        bad = self.client.patch('/event/my-stalls/%s/' % self.s.slug, {
            'pickup_opens_at': (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            'pickup_closes_at': (timezone.now() + timezone.timedelta(days=1, hours=2)).isoformat(),
        }, format='json', **auth(self.organiser))
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()['code'], 'PICKUP_OUTSIDE_EVENT')

    def test_collection_is_refused_before_the_window_opens(self):
        start = self.event.starts_at()
        self.s.pickup_opens_at = start
        self.s.pickup_closes_at = start + timezone.timedelta(hours=4)
        self.s.save()
        self.buy(self.s, self.hoodie)
        order = VendorOrder.objects.get()
        res = self.client.post('/event/my-stalls/%s/orders/%s/status/' % (self.s.slug, order.code),
                               {'status': 'collected'}, format='json', **auth(self.organiser))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'PICKUP_NOT_OPEN')
        with mock.patch('django.utils.timezone.now',
                        return_value=start + timezone.timedelta(hours=1)):
            ok = self.client.post('/event/my-stalls/%s/orders/%s/status/' % (self.s.slug, order.code),
                                  {'status': 'collected'}, format='json', **auth(self.organiser))
        self.assertEqual(ok.status_code, 200, ok.content[:300])
        self.assertEqual(VendorOrder.objects.get().status, 'collected')


class RefundTests(EventShopBase):
    def setUp(self):
        super().setUp()
        self.open_shop()
        self.s = self.shop()
        self.hoodie = self.product(self.s)

    def test_no_refunds_means_the_buyer_cannot_cancel(self):
        self.buy(self.s, self.hoodie)
        order = VendorOrder.objects.get()
        res = self.client.post('/event/vendor/order/%s/cancel/' % order.code, {}, format='json',
                               **auth(self.buyer))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'REFUNDS_OFF')

    def test_within_the_policy_the_buyer_is_refunded_and_stock_returns(self):
        self.s.refund_policy = Vendor.REFUND_UNTIL_COLLECTED
        self.s.save()
        before = balance(self.buyer)
        self.buy(self.s, self.hoodie, qty=2)
        order = VendorOrder.objects.get()
        res = self.client.post('/event/vendor/order/%s/cancel/' % order.code, {}, format='json',
                               **auth(self.buyer))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(VendorOrder.objects.get().status, 'cancelled')
        self.assertEqual(balance(self.buyer), before)
        self.assertEqual(VendorProduct.objects.get(pk=self.hoodie.pk).stock, 10)

    def test_until_the_event_starts_closes_at_the_start(self):
        self.s.refund_policy = Vendor.REFUND_UNTIL_START
        self.s.save()
        self.buy(self.s, self.hoodie)
        order = VendorOrder.objects.get()
        with mock.patch('django.utils.timezone.now',
                        return_value=self.event.starts_at() + timezone.timedelta(minutes=5)):
            res = self.client.post('/event/vendor/order/%s/cancel/' % order.code, {},
                                   format='json', **auth(self.buyer))
        self.assertEqual(res.json()['code'], 'REFUND_WINDOW_CLOSED')

    def test_nobody_else_cancels_a_buyers_order(self):
        self.s.refund_policy = Vendor.REFUND_UNTIL_COLLECTED
        self.s.save()
        self.buy(self.s, self.hoodie)
        order = VendorOrder.objects.get()
        res = self.client.post('/event/vendor/order/%s/cancel/' % order.code, {}, format='json',
                               **auth(a_user('stranger')))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(VendorOrder.objects.get().status, 'paid')


class StaffTests(EventShopBase):
    def setUp(self):
        super().setUp()
        self.helper = a_user('helper')
        self.client.post('/event/my-stalls/%s/staff/' % self.stall.slug,
                         {'username': self.helper.username}, format='json', **auth(self.vendor))
        self.fries = VendorProduct.objects.create(vendor=self.stall, name='Fries',
                                                  price=Decimal('2000'), stock=20)
        self.buy(self.stall, self.fries)
        self.order = VendorOrder.objects.get()

    def test_staff_see_orders_and_hand_them_over(self):
        self.assertTrue(VendorStaff.objects.filter(vendor=self.stall, user=self.helper).exists())
        res = self.client.get('/event/my-stalls/%s/orders/' % self.stall.slug, **auth(self.helper))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['orders'][0]['code'], self.order.code)
        # What the stall earns is the owner's, in the payload too.
        self.assertNotIn('vendor_ngn', res.json()['data']['orders'][0])
        self.assertEqual(res.json()['data']['role'], 'staff')
        done = self.client.post('/event/my-stalls/%s/orders/%s/status/'
                                % (self.stall.slug, self.order.code),
                                {'status': 'collected'}, format='json', **auth(self.helper))
        self.assertEqual(done.status_code, 200, done.content[:300])

    def test_staff_cannot_cancel_or_change_settings(self):
        res = self.client.post('/event/my-stalls/%s/orders/%s/status/'
                               % (self.stall.slug, self.order.code),
                               {'status': 'cancelled'}, format='json', **auth(self.helper))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'OWNER_ONLY')
        patch = self.client.patch('/event/my-stalls/%s/' % self.stall.slug,
                                  {'refund_policy': 'until_collected'}, format='json',
                                  **auth(self.helper))
        self.assertEqual(patch.status_code, 404)
        self.assertEqual(Vendor.objects.get(pk=self.stall.pk).refund_policy, 'none')

    def test_the_owner_removes_staff_and_access_ends(self):
        self.client.delete('/event/my-stalls/%s/staff/%s/' % (self.stall.slug, self.helper.username),
                           **auth(self.vendor))
        res = self.client.get('/event/my-stalls/%s/orders/' % self.stall.slug, **auth(self.helper))
        self.assertEqual(res.status_code, 404)

    def test_a_stranger_cannot_add_staff(self):
        res = self.client.post('/event/my-stalls/%s/staff/' % self.stall.slug,
                               {'username': 'anybody'}, format='json', **auth(a_user('x')))
        self.assertEqual(res.status_code, 404)


class LowStockTests(EventShopBase):
    def test_the_alert_is_sent_once_per_dip_and_cleared_by_a_restock(self):
        self.open_shop()
        s = self.shop()
        grips = self.product(s, name='Grips', price=Decimal('8000'), stock=6, low_stock_at=5)
        with mock.patch('vent_auth.views_notifications.create_notification') as told:
            self.buy(s, grips)
            self.buy(s, grips)
        titles = [c.kwargs.get('title', '') for c in told.call_args_list]
        self.assertEqual(sum('running low' in t for t in titles), 1)
        self.client.patch('/event/my-stalls/%s/products/%s/' % (s.slug, grips.id),
                          {'stock': 20}, format='json', **auth(self.organiser))
        self.assertIsNone(VendorProduct.objects.get(pk=grips.pk).low_stock_alerted_at)


class DoorCardTests(EventShopBase):
    def setUp(self):
        super().setUp()
        self.open_shop()
        self.s = self.shop()
        self.hoodie = self.product(self.s)
        self.vip = TicketTier.objects.create(event=self.event, name='VIP', price=Decimal('0'),
                                             quantity=10, perks='Front row')
        self.ticket = Ticket.objects.create(event=self.event, tier=self.vip, user=self.buyer,
                                            code='VIPDOOR123', attendee_name='Ada O.',
                                            answers={'comped_by': self.organiser.username})
        self.buy(self.s, self.hoodie)

    def test_the_lookup_says_how_the_ticket_was_got_and_what_was_bought(self):
        res = self.client.get('/event/ticket/%s/lookup/' % self.ticket.code, **auth(self.organiser))
        self.assertEqual(res.status_code, 200, res.content[:300])
        story = res.json()['data']['story']
        self.assertEqual(story['origin']['kind'], 'complimentary')
        self.assertEqual(story['tier'], 'VIP')
        self.assertEqual(story['orders'][0]['items'][0]['name'], 'Event hoodie')
        self.assertTrue(story['orders'][0]['waiting'])

    def test_checking_in_tells_the_shop_and_marks_the_order_here(self):
        with mock.patch('vent_auth.views_notifications.create_notification') as told:
            res = self.client.post('/event/ticket/%s/check-in/' % self.ticket.code, {},
                                   format='json', **auth(self.organiser))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIn('story', res.json()['data'])
        titles = [c.kwargs.get('title', '') for c in told.call_args_list]
        self.assertIn('Ada O. is here', titles)
        rows = self.client.get('/event/my-stalls/%s/orders/' % self.s.slug,
                               **auth(self.organiser)).json()['data']['orders']
        self.assertTrue(rows[0]['buyer_here'])

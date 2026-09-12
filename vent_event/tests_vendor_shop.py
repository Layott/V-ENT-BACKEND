"""Running a stall: stock it, sell from it, and get the thing to the buyer.

CEO, 7 September 2026: "run the vendor UI properly. build all screens."

The stall was built the day before and `tools/endpoint-callers.py` then said
what nobody had noticed: nothing on the site called any of it. Somebody who
bought a pitch got a stall they could not stock.

Three things were missing before a screen could exist at all - a way to find
your own stalls, a way to change a product, and a way to fulfil an order - and
all three are tested here on what they DO rather than on the status code they
answer with.
"""
import base64
import uuid
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users, UserWallet
from vent_event.models import (Event, Vendor, VendorOrder, VendorProduct)

PIN = '1234'


def a_user(name, coins=0):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = timezone.now()
    u.save()
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=u,
                              wallet_balance=coins, pin_hash=make_password(PIN))
    return u


def auth(u):
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


class ShopBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser = a_user('org')
        self.vendor = a_user('vend')
        self.buyer = a_user('buy', coins=100000)
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.event = Event.objects.create(
            name='Shop Fest %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.organiser, event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=3),
            start_date=timezone.now() + timezone.timedelta(days=9),
            end_date=timezone.now() + timezone.timedelta(days=9, hours=6),
        )
        self.stall = Vendor.objects.create(
            event=self.event, owner=self.vendor, name='Mama T Kitchen',
            status='approved')


class MyStallsTests(ShopBase):
    def test_a_vendor_can_find_their_own_stall(self):
        """There was no address on the platform that listed it."""
        res = self.client.get('/event/my-stalls/', **auth(self.vendor))
        self.assertEqual(res.status_code, 200, res.content[:300])
        rows = res.json()['data']['stalls']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['slug'], self.stall.slug)
        self.assertEqual(rows[0]['event']['slug'], self.event.slug)

    def test_somebody_elses_stall_is_not_listed(self):
        other = a_user('other')
        self.assertEqual(
            self.client.get('/event/my-stalls/', **auth(other))
            .json()['data']['stalls'], [])

    def test_a_stranger_gets_401(self):
        self.assertIn(self.client.get('/event/my-stalls/').status_code, (401, 403))

    def test_the_summary_counts_what_a_stallholder_opens_this_to_see(self):
        VendorOrder.objects.create(vendor=self.stall, buyer=self.buyer,
                                   code='OPEN01', total_vc=10, status='paid')
        VendorOrder.objects.create(vendor=self.stall, buyer=self.buyer,
                                   code='DONE01', total_vc=10, status='collected')
        row = self.client.get('/event/my-stalls/', **auth(self.vendor)) \
            .json()['data']['stalls'][0]
        self.assertEqual(row['open_orders'], 1)


class ProductTests(ShopBase):
    def setUp(self):
        super().setUp()
        self.product = VendorProduct.objects.create(
            vendor=self.stall, name='Jollof', price=Decimal('2500'), stock=10)

    def patch(self, **body):
        return self.client.patch(
            '/event/my-stalls/%s/products/%d/' % (self.stall.slug, self.product.id),
            body, format='json', **auth(self.vendor))

    def test_the_price_and_stock_can_be_changed(self):
        """Most of running a stall: restocking and changing a price."""
        res = self.patch(price=3000, stock=42)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('3000'))
        self.assertEqual(self.product.stock, 42)

    def test_variants_can_be_set_from_a_comma_separated_line(self):
        """What somebody types into one box, rather than a list widget."""
        res = self.patch(variants='Small, Medium, Large')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.product.refresh_from_db()
        self.assertEqual(self.product.variants, ['Small', 'Medium', 'Large'])

    def test_a_negative_price_is_refused(self):
        self.assertEqual(self.patch(price=-5).status_code, 400)

    def test_something_that_is_not_a_number_is_refused_by_name(self):
        self.assertEqual(self.patch(stock='lots').json()['code'], 'VALIDATION_ERROR')

    def test_an_unsold_product_is_deleted(self):
        res = self.client.delete(
            '/event/my-stalls/%s/products/%d/' % (self.stall.slug, self.product.id),
            **auth(self.vendor))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(VendorProduct.objects.filter(id=self.product.id).exists())

    def test_a_SOLD_product_is_hidden_rather_than_deleted(self):
        """Past orders point at it; deleting the row leaves a receipt naming
        nothing."""
        self.product.sold = 3
        self.product.save(update_fields=['sold'])
        self.client.delete(
            '/event/my-stalls/%s/products/%d/' % (self.stall.slug, self.product.id),
            **auth(self.vendor))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

    def test_another_vendor_cannot_touch_it(self):
        intruder = a_user('intruder')
        res = self.client.patch(
            '/event/my-stalls/%s/products/%d/' % (self.stall.slug, self.product.id),
            {'price': 1}, format='json', **auth(intruder))
        self.assertEqual(res.status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('2500'))


class ProductCreateCarriesEverythingTests(ShopBase):
    """The stall screen sends the whole product in one multipart call. Until
    12 September the create endpoint read name, price and stock only, so the
    picture was dropped and the screen PATCHed the choices in afterwards."""

    # A 1x1 PNG, base64 so no escape sequence has to survive a shell.
    PNG = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4DwABAQEAWDs'
        'IsAAAAABJRU5ErkJggg==')

    def create(self, **fields):
        return self.client.post('/event/vendor/%s/products/' % self.stall.slug,
                                fields, format='multipart', **auth(self.vendor))

    def test_choices_deliverability_and_the_picture_land_on_create(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        res = self.create(name='Hoodie', price='25000', stock='3',
                          variants='S, M, L', can_deliver='true',
                          image=SimpleUploadedFile('h.png', self.PNG, content_type='image/png'))
        self.assertEqual(res.status_code, 201, res.content[:300])
        product = VendorProduct.objects.get(name='Hoodie')
        self.assertEqual(product.variants, ['S', 'M', 'L'])
        self.assertTrue(product.can_deliver)
        self.assertTrue(product.image, 'the picture sent with the product was dropped')
        self.assertEqual(res.json()['data']['product']['variants'], ['S', 'M', 'L'])

    def test_a_product_with_no_choices_is_still_fine(self):
        res = self.create(name='Plate', price='2000', stock='40')
        self.assertEqual(res.status_code, 201, res.content[:300])
        product = VendorProduct.objects.get(name='Plate')
        self.assertEqual(product.variants, [])
        self.assertFalse(product.can_deliver)


class StallPageBySlugTests(ShopBase):
    """The event page links a stall as /event/<event slug>/vendor/<stall slug>/.
    That address answered 500 until 12 September: the view filtered
    `event_id=<slug>` and Django refused the string as a number."""

    def test_the_stall_opens_under_the_event_slug(self):
        res = self.client.get('/event/%s/vendor/%s/' % (self.event.slug, self.stall.slug))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['vendor']['slug'], self.stall.slug)

    def test_and_under_the_event_id_for_links_shared_before(self):
        res = self.client.get('/event/%s/vendor/%s/' % (self.event.event_id, self.stall.slug))
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_a_stall_from_another_event_is_not_found_here(self):
        other = Event.objects.create(
            name='Other Fest', game=self.event.game, creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            start_date=timezone.now(), end_date=timezone.now())
        res = self.client.get('/event/%s/vendor/%s/' % (other.slug, self.stall.slug))
        self.assertEqual(res.status_code, 404)


class ContactTheStallTests(ShopBase):
    """The Contact box on a stall page showed "Message sent" with no request
    behind it until 12 September. Now it is a notification to the
    stallholder, from a signed-in person, once a minute."""

    def send(self, who, message='Do you have jollof on day 2?', **extra):
        return self.client.post('/event/vendor/%s/contact/' % self.stall.slug,
                                {'message': message}, format='json', **extra)

    def test_the_stallholder_gets_a_notification_with_the_username(self):
        from vent_auth.models import Notification
        res = self.send(self.buyer, **auth(self.buyer))
        self.assertEqual(res.status_code, 201, res.content[:300])
        row = Notification.objects.get(user=self.vendor)
        self.assertIn(self.buyer.username, row.body)
        self.assertIn('jollof', row.body)
        self.assertEqual(row.metadata['kind'], 'vendor_message')
        self.assertEqual(row.metadata['stall'], self.stall.slug)

    def test_a_second_one_within_a_minute_waits(self):
        self.send(self.buyer, **auth(self.buyer))
        res = self.send(self.buyer, 'And on day 1?', **auth(self.buyer))
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()['code'], 'TOO_SOON')

    def test_signed_out_is_refused_and_empty_is_refused(self):
        self.assertEqual(self.send(None).status_code, 401)
        res = self.send(self.buyer, '   ', **auth(self.buyer))
        self.assertEqual(res.status_code, 400)

    def test_the_owner_cannot_message_their_own_stall(self):
        res = self.send(self.vendor, **auth(self.vendor))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'OWN_STALL')


class VariantOrderTests(ShopBase):
    """A choice asked for at checkout reaches the person packing it."""

    def setUp(self):
        super().setUp()
        self.shirt = VendorProduct.objects.create(
            vendor=self.stall, name='Tee', price=Decimal('5000'), stock=10,
            variants=['S', 'M', 'L'], can_deliver=True)

    def order(self, **extra):
        body = {'items': [dict({'product_id': self.shirt.id, 'quantity': 1},
                               **extra.pop('item', {}))], 'pin': PIN}
        body.update(extra)
        return self.client.post('/event/vendor/%s/order/' % self.stall.slug,
                                body, format='json', **auth(self.buyer))

    def test_the_chosen_size_is_written_on_the_order(self):
        res = self.order(item={'variant': 'M'})
        self.assertEqual(res.status_code, 201, res.content[:400])
        order = VendorOrder.objects.get()
        self.assertEqual(order.items.first().variant, 'M')

    def test_not_choosing_one_is_refused_by_name(self):
        """A stallholder reads this while packing and cannot ask."""
        res = self.order()
        self.assertEqual(res.json()['code'], 'VARIANT_REQUIRED')

    def test_a_size_the_stall_does_not_sell_is_refused(self):
        res = self.order(item={'variant': 'XXL'})
        self.assertEqual(res.json()['code'], 'VARIANT_UNKNOWN')

    def test_a_product_with_no_choices_needs_none(self):
        plate = VendorProduct.objects.create(vendor=self.stall, name='Jollof',
                                             price=Decimal('2000'), stock=5)
        res = self.client.post('/event/vendor/%s/order/' % self.stall.slug,
                               {'items': [{'product_id': plate.id, 'quantity': 1}],
                                'pin': PIN}, format='json', **auth(self.buyer))
        self.assertEqual(res.status_code, 201, res.content[:400])


class DeliveryTests(ShopBase):
    """CEO row 146: "if they re doing delivery there has to be a way for these
    thingd to works"."""

    def setUp(self):
        super().setUp()
        self.postable = VendorProduct.objects.create(
            vendor=self.stall, name='Tee', price=Decimal('5000'), stock=10,
            can_deliver=True)
        self.hot = VendorProduct.objects.create(
            vendor=self.stall, name='Jollof', price=Decimal('2000'), stock=10)

    def order(self, product, **extra):
        body = {'items': [{'product_id': product.id, 'quantity': 1}], 'pin': PIN}
        body.update(extra)
        return self.client.post('/event/vendor/%s/order/' % self.stall.slug,
                                body, format='json', **auth(self.buyer))

    def address(self):
        return {'name': 'Bisi Adeleke', 'phone': '08030000000',
                'address': '12 Awolowo Road, Ikoyi, Lagos'}

    def test_an_order_can_be_for_delivery_and_keeps_the_address(self):
        res = self.order(self.postable, fulfilment='deliver',
                         delivery=self.address())
        self.assertEqual(res.status_code, 201, res.content[:400])
        order = VendorOrder.objects.get()
        self.assertEqual(order.fulfilment, 'deliver')
        self.assertIn('Awolowo', order.delivery_address)

    def test_a_delivery_with_no_address_is_refused_and_names_what_is_missing(self):
        res = self.order(self.postable, fulfilment='deliver',
                         delivery={'name': 'Bisi'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'DELIVERY_DETAILS_REQUIRED')
        self.assertIn('address', res.json()['data']['missing'])

    def test_something_that_cannot_be_posted_is_refused_BEFORE_paying(self):
        """Finding out after paying that hot food cannot be posted is worse."""
        before = UserWallet.objects.get(user=self.buyer).wallet_balance
        res = self.order(self.hot, fulfilment='deliver', delivery=self.address())
        self.assertEqual(res.json()['code'], 'NOT_DELIVERABLE')
        self.assertEqual(UserWallet.objects.get(user=self.buyer).wallet_balance,
                         before)

    def test_collecting_needs_no_address_at_all(self):
        """Asking everybody for a postal address when most are walking ten
        metres to a table is how a checkout loses people."""
        res = self.order(self.hot)
        self.assertEqual(res.status_code, 201, res.content[:400])
        self.assertEqual(VendorOrder.objects.get().fulfilment, 'collect')


class ARefusalNeverKeepsTheMoneyTests(ShopBase):
    """`return` inside `transaction.atomic()` COMMITS.

    Every refusal after the wallet debit in `create_order` therefore kept the
    money. The buyer was charged, the response said "Nothing has been taken
    from your wallet", and that was false. It had been true of the seller
    -has-no-wallet branch since that branch was written; the delivery check
    added on 7 September 2026 landed in the same place and a test caught it,
    2 VC missing from a refused order.

    Refusals raise now, so the transaction unwinds by construction rather than
    by whoever writes the next check remembering to put it above the debit.
    This test is the guard on the CLASS, not on the one branch.
    """

    def setUp(self):
        super().setUp()
        self.hot = VendorProduct.objects.create(
            vendor=self.stall, name='Jollof', price=Decimal('2000'), stock=10)

    def coins(self):
        return UserWallet.objects.get(user=self.buyer).wallet_balance

    def test_a_late_refusal_leaves_the_wallet_exactly_as_it_was(self):
        before = self.coins()
        res = self.client.post(
            '/event/vendor/%s/order/' % self.stall.slug,
            {'items': [{'product_id': self.hot.id, 'quantity': 1}], 'pin': PIN,
             'fulfilment': 'deliver',
             'delivery': {'name': 'B', 'phone': '0803', 'address': 'Ikoyi'}},
            format='json', **auth(self.buyer))
        self.assertEqual(res.json()['code'], 'NOT_DELIVERABLE')
        self.assertEqual(self.coins(), before)

    def test_a_late_refusal_writes_no_transaction_row(self):
        """A debit that is rolled back must not leave a ledger entry saying it
        happened, or the books disagree with the balance."""
        from vent_auth.models import Transaction
        self.client.post(
            '/event/vendor/%s/order/' % self.stall.slug,
            {'items': [{'product_id': self.hot.id, 'quantity': 1}], 'pin': PIN,
             'fulfilment': 'deliver',
             'delivery': {'name': 'B', 'phone': '0803', 'address': 'Ikoyi'}},
            format='json', **auth(self.buyer))
        self.assertEqual(
            Transaction.objects.filter(wallet__user=self.buyer).count(), 0)

    def test_a_late_refusal_leaves_no_order_and_no_stock_movement(self):
        self.client.post(
            '/event/vendor/%s/order/' % self.stall.slug,
            {'items': [{'product_id': self.hot.id, 'quantity': 1}], 'pin': PIN,
             'fulfilment': 'deliver',
             'delivery': {'name': 'B', 'phone': '0803', 'address': 'Ikoyi'}},
            format='json', **auth(self.buyer))
        self.assertEqual(VendorOrder.objects.count(), 0)
        self.hot.refresh_from_db()
        self.assertEqual(self.hot.stock, 10)
        self.assertEqual(self.hot.sold, 0)


class FulfilmentTests(ShopBase):
    def setUp(self):
        super().setUp()
        self.order = VendorOrder.objects.create(
            vendor=self.stall, buyer=self.buyer, code='ORD001', total_vc=50,
            status='paid', fulfilment='deliver',
            delivery_name='Bisi', delivery_phone='0803', delivery_address='Ikoyi')

    def move(self, status, **extra):
        return self.client.post(
            '/event/my-stalls/%s/orders/%s/status/' % (self.stall.slug, self.order.code),
            dict({'status': status}, **extra), format='json', **auth(self.vendor))

    def test_an_order_can_be_marked_sent_with_a_tracking_number(self):
        res = self.move('sent', tracking='NIPOST-123')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'sent')
        self.assertEqual(self.order.tracking, 'NIPOST-123')
        self.assertIsNotNone(self.order.sent_at)

    def test_sent_then_delivered(self):
        self.move('sent')
        res = self.move('delivered')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.delivered_at)

    def test_delivered_before_sent_is_refused(self):
        """A stallholder can only ever know that they posted it."""
        res = self.move('delivered')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'BAD_TRANSITION')

    def test_marking_it_twice_is_not_an_error(self):
        """Two taps on a slow connection is one intention, and a red toast for
        the second is a lie about what happened."""
        self.move('sent')
        res = self.move('sent')
        self.assertEqual(res.status_code, 200)

    def test_a_collection_order_cannot_be_marked_sent(self):
        self.order.fulfilment = 'collect'
        self.order.save(update_fields=['fulfilment'])
        self.assertEqual(self.move('sent').json()['code'], 'NOT_A_DELIVERY')

    def test_another_vendor_cannot_move_it(self):
        intruder = a_user('intruder')
        res = self.client.post(
            '/event/my-stalls/%s/orders/%s/status/' % (self.stall.slug, self.order.code),
            {'status': 'collected'}, format='json', **auth(intruder))
        self.assertEqual(res.status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'paid')

    def test_the_order_list_carries_the_address_only_for_deliveries(self):
        rows = self.client.get('/event/my-stalls/%s/orders/' % self.stall.slug,
                               **auth(self.vendor)).json()['data']['orders']
        self.assertIsNotNone(rows[0]['delivery'])
        self.order.fulfilment = 'collect'
        self.order.save(update_fields=['fulfilment'])
        rows = self.client.get('/event/my-stalls/%s/orders/' % self.stall.slug,
                               **auth(self.vendor)).json()['data']['orders']
        self.assertIsNone(rows[0]['delivery'])


class StallOpeningTests(ShopBase):
    def patch(self, **body):
        return self.client.patch('/event/my-stalls/%s/' % self.stall.slug,
                                 body, format='json', **auth(self.vendor))

    def test_a_stallholder_can_close_and_reopen(self):
        self.assertEqual(self.patch(status='closed').status_code, 200)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.status, 'closed')
        self.patch(status='live')
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.status, 'live')

    def test_a_stall_awaiting_approval_cannot_open_itself(self):
        self.stall.status = 'pending'
        self.stall.save(update_fields=['status'])
        res = self.patch(status='live')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'NOT_APPROVED')

    def test_the_name_and_description_can_be_edited(self):
        self.patch(name='Mama T Kitchen and Grill', description='Home cooking.')
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.name, 'Mama T Kitchen and Grill')

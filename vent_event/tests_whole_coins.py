"""A naira price that is not whole coins is refused where it is set.

Found by the walk on 12 September 2026: a 1,500 naira ticket type charged ONE
coin from a wallet, because the conversion floors and a coin is 1,000 naira.
See vent_event/pricing.py.
"""
import json
import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users, UserWallet
from vent_event.models import Event, TicketTier, Vendor, VendorProduct, VendorSlot

from .pricing import whole_coins


class WholeCoinsRuleTests(TestCase):
    def test_whole_and_not_whole(self):
        self.assertEqual(whole_coins(0), (True, ''))
        self.assertEqual(whole_coins(2000), (True, ''))
        self.assertEqual(whole_coins('9000.00'), (True, ''))
        ok, why = whole_coins(1500)
        self.assertFalse(ok)
        self.assertIn('1,000 naira', why)
        self.assertIn('1,500', why)
        self.assertIn('2,000', why)
        self.assertFalse(whole_coins(999)[0])
        self.assertFalse(whole_coins(-1000)[0])
        self.assertFalse(whole_coins('cheap')[0])


class WholeCoinsAtEveryDoorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = Users.objects.create(
            username='wc_owner_%s' % uuid.uuid4().hex[:5], email='wc@vent.test',
            login_session_token=('wc%s' % uuid.uuid4().hex)[:16], is_active=True,
            login_session_created_at=timezone.now())
        UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=self.owner)
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % self.owner.login_session_token}
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        start = timezone.now() + timezone.timedelta(days=10)
        self.event = Event.objects.create(
            name='Coins Con', game=game, creator=self.owner, event_type='physical',
            desc='x', entry_fee=0, reg_start_date=timezone.now(),
            reg_end_date=start, start_date=start,
            end_date=start + timezone.timedelta(hours=6))
        self.tier = TicketTier.objects.create(event=self.event, name='GA', price=2000, quantity=10)

    def test_create_event_refuses_a_tier_that_is_not_whole_coins(self):
        start = timezone.now() + timezone.timedelta(days=10)
        res = self.client.post('/event/create-event/', data=json.dumps({
            'name': 'Odd Con', 'event_type': 'physical', 'description': 'x',
            'start_date': start.isoformat(),
            'end_date': (start + timezone.timedelta(days=1)).isoformat(),
            'location': 'Lagos',
            'ticket_types': [{'name': 'Day pass', 'price': '1500', 'quantity': 10}],
        }), content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PRICE_NOT_WHOLE_COINS')
        self.assertIn('Day pass', res.json()['message'])
        # And nothing was half-created.
        self.assertFalse(Event.objects.filter(name='Odd Con').exists())

    def test_a_tier_price_that_is_not_whole_coins_is_refused(self):
        res = self.client.post('/event/%s/tiers/' % self.event.slug,
                               data=json.dumps({'name': 'Odd', 'price': '1500'}),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PRICE_NOT_WHOLE_COINS')
        res = self.client.patch('/event/%s/tiers/%s/' % (self.event.slug, self.tier.id),
                                data=json.dumps({'group_min': 4, 'group_price': '1600'}),
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PRICE_NOT_WHOLE_COINS')
        res = self.client.patch('/event/%s/tiers/%s/' % (self.event.slug, self.tier.id),
                                data=json.dumps({'early_bird_quantity': 5, 'early_bird_price': '2500'}),
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        # Whole coins pass.
        res = self.client.patch('/event/%s/tiers/%s/' % (self.event.slug, self.tier.id),
                                data=json.dumps({'group_min': 4, 'group_price': '1000'}),
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_vendor_pitch_price_that_is_not_whole_coins_is_refused(self):
        res = self.client.post('/event/%s/slots/' % self.event.slug,
                               data=json.dumps({'name': 'Pitch', 'price_ngn': 20500, 'quantity': 1}),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PRICE_NOT_WHOLE_COINS')
        slot = VendorSlot.objects.create(event=self.event, name='P', price_ngn=20000, quantity=1)
        res = self.client.patch('/event/%s/slots/%s/' % (self.event.slug, slot.id),
                                data=json.dumps({'price_ngn': 20500}),
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_vendor_product_price_that_is_not_whole_coins_is_refused(self):
        stall = Vendor.objects.create(event=self.event, owner=self.owner, name='Stall', status='approved')
        res = self.client.post('/event/vendor/%s/products/' % stall.slug,
                               data=json.dumps({'name': 'Drink', 'price': 500, 'stock': 5}),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PRICE_NOT_WHOLE_COINS')
        self.assertIn('500', res.json()['message'])
        # The fields the screen's translation fills in, same as billing sends.
        self.assertEqual(res.json()['data']['nearest_lower'], 0)
        self.assertEqual(res.json()['data']['nearest_upper'], 1000)
        product = VendorProduct.objects.create(vendor=stall, name='Plate', price=2000, stock=5)
        res = self.client.patch('/event/my-stalls/%s/products/%s/' % (stall.slug, product.id),
                                data=json.dumps({'price': 1500}),
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PRICE_NOT_WHOLE_COINS')

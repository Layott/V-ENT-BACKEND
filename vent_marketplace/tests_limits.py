"""What a free account may have, and what premium lifts.

The spec is short and specific: "free users are limited to one active listing at
a time", bids run three days, and seven things are premium. All of it goes
through `vent_auth.premium`, so there is one idea of premium on the platform
rather than a second one growing in the marketplace.
"""
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Organization

from . import catalogue, listings as rules
from .models import Listing
from .tests_switch import client_for, make_user


@override_settings(MARKETPLACE_ENABLED=True)
class FreeLimitTests(TestCase):

    def setUp(self):
        self.user = make_user(20)
        self.client = client_for(self.user)

    def _make(self, title, publish=True):
        return self.client.post('/marketplace/listings/new/', {
            'kind': 'sale', 'category': 'merchandise', 'title': title,
            'price': 100, 'quantity': 1, 'publish': publish}, format='json')

    def test_one_live_listing_on_a_free_account(self):
        self.assertEqual(self._make('First thing').status_code, 201)
        second = self._make('Second thing')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['code'], 'LISTING_LIMIT')
        # And the refusal says what the limit IS, because "you have reached
        # your limit" tells nobody anything they can act on.
        self.assertIn('1', second.json()['message'])

    def test_a_draft_is_not_a_live_listing(self):
        self._make('First thing')
        self.assertEqual(self._make('A draft', publish=False).status_code, 201)
        self.assertEqual(Listing.objects.filter(status='draft').count(), 1)

    def test_pausing_one_frees_the_slot(self):
        first = self._make('First thing').json()['data']['listing']['slug']
        self.client.post('/marketplace/listings/%s/status/' % first,
                         {'status': 'paused'}, format='json')
        self.assertEqual(self._make('Second thing').status_code, 201)

    def test_premium_lifts_it(self):
        self.user.is_premium = True
        self.user.save(update_fields=['is_premium'])
        self.assertEqual(self._make('First thing').status_code, 201)
        self.assertEqual(self._make('Second thing').status_code, 201)
        self.assertEqual(self._make('Third thing').status_code, 201)

    def test_an_org_that_pays_does_not_lift_its_owner_s_own_limit(self):
        """Written down because it is the surprising half.

        `has_premium` lets an ORGANISATION carry the people acting for it, and
        that is right for a tournament, which belongs to an org. A listing
        belongs to a PERSON: it has no organisation on it, so an org's premium
        does not silently give its owner unlimited personal listings. If that
        is ever wanted, a listing needs an org first.
        """
        Organization.objects.create(
            org_name='A paying org', org_creator=self.user, org_owner=self.user,
            is_premium=True)
        self.assertEqual(self._make('First thing').status_code, 201)
        second = self._make('Second thing')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['code'], 'LISTING_LIMIT')


@override_settings(MARKETPLACE_ENABLED=True)
class PremiumFieldTests(TestCase):

    def setUp(self):
        self.user = make_user(21)
        self.client = client_for(self.user)

    def _make(self, **extra):
        body = {'kind': 'sale', 'category': 'merchandise', 'title': 'A thing',
                'price': 100, 'quantity': 1}
        body.update(extra)
        return self.client.post('/marketplace/listings/new/', body, format='json')

    def test_a_discount_code_is_refused_by_name(self):
        res = self._make(discount_code='EARLY', discount_percent=10)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['field'], 'discount_code')
        self.assertEqual(Listing.objects.count(), 0)

    def test_premium_may_set_one(self):
        self.user.is_premium = True
        self.user.save(update_fields=['is_premium'])
        res = self._make(discount_code='EARLY', discount_percent=10)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Listing.objects.get().discount_code, 'EARLY')

    def test_the_analytics_are_only_filled_in_for_premium(self):
        self._make(publish=True)
        free = self.client.get('/marketplace/mine/')
        self.assertIsNone(free.json()['data']['listings'][0]['analytics'])
        self.assertFalse(free.json()['data']['has_premium'])

        self.user.is_premium = True
        self.user.save(update_fields=['is_premium'])
        paid = self.client.get('/marketplace/mine/')
        self.assertIsNotNone(paid.json()['data']['listings'][0]['analytics'])

    def test_a_portfolio_upload_is_refused_without_premium(self):
        made = self._make(publish=True).json()['data']['listing']['slug']
        from django.core.files.uploadedfile import SimpleUploadedFile
        res = self.client.post(
            '/marketplace/listings/%s/media/' % made,
            {'file': SimpleUploadedFile('a.png', b'x', content_type='image/png'),
             'portfolio': 'true'})
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.json()['code'], 'PREMIUM_REQUIRED')

    def test_an_ordinary_picture_is_free(self):
        made = self._make(publish=True).json()['data']['listing']['slug']
        from django.core.files.uploadedfile import SimpleUploadedFile
        res = self.client.post(
            '/marketplace/listings/%s/media/' % made,
            {'file': SimpleUploadedFile('a.png', b'x', content_type='image/png')})
        self.assertEqual(res.status_code, 201, res.content)


@override_settings(MARKETPLACE_ENABLED=True)
class BidWindowTests(TestCase):

    def setUp(self):
        self.user = make_user(22)
        self.client = client_for(self.user)

    def _make(self, days):
        return self.client.post('/marketplace/listings/new/', {
            'kind': 'sale', 'category': 'merchandise', 'title': 'Bid thing',
            'price': 100, 'quantity': 1, 'publish': True,
            'bidding': True, 'bid_days': days}, format='json')

    def test_three_days_on_a_free_account(self):
        self.assertEqual(self._make(3).status_code, 201)
        listing = Listing.objects.get()
        self.assertTrue(listing.bidding)
        self.assertGreater(listing.bids_close_at, timezone.now())

    def test_longer_is_refused_by_name(self):
        res = self._make(10)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['field'], 'bid_days')
        self.assertIn('3', res.json()['message'])

    def test_premium_may_run_it_longer(self):
        self.user.is_premium = True
        self.user.save(update_fields=['is_premium'])
        self.assertEqual(self._make(14).status_code, 201)


class ExpiryTests(TestCase):

    def test_a_date_that_has_passed_closes_the_listing(self):
        """An expiry nothing enforces is a listing that says it ended and did not."""
        user = make_user(23)
        past = Listing.objects.create(
            seller=user, kind='sale', category='merchandise', title='Old',
            price=1, quantity=1, status='active',
            expires_at=timezone.now() - timezone.timedelta(hours=1))
        future = Listing.objects.create(
            seller=user, kind='sale', category='merchandise', title='Current',
            price=1, quantity=1, status='active',
            expires_at=timezone.now() + timezone.timedelta(days=1))

        self.assertEqual(rules.expire_due(), 1)
        past.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(past.status, 'expired')
        self.assertEqual(future.status, 'active')

"""Exchange rates: refreshing them, and refusing to write nonsense.

Rates decide what a price looks like to a reader in Accra. The seeded cedi rate
was 0.0098 when the real one was 0.00827 - a 15 per cent error on every price a
Ghanaian reader saw - which is why they are pulled from a feed rather than typed
once and forgotten.

The network is never touched here. `fetch_rates` is replaced, because a test
that depends on somebody else's uptime fails for reasons that have nothing to do
with this code.
"""
import uuid
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from .models import Currency, Users
from .rates import refresh_rates


def an_admin(role='super_admin', token='rates-grant'):
    user = Users.objects.create(
        username='adm_%s' % uuid.uuid4().hex[:6],
        email='adm_%s@vent.test' % uuid.uuid4().hex[:6],
        is_staff=True, admin_role=role, admin_session_token=token,
    )
    user.admin_session_created_at = timezone.now()
    user.save(update_fields=['admin_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % token}


class RefreshRatesTests(TestCase):
    def setUp(self):
        Currency.objects.update_or_create(
            code='NGN', defaults={'name': 'Naira', 'symbol': '₦', 'rate_from_ngn': 1})
        self.ghs, _ = Currency.objects.update_or_create(
            code='GHS', defaults={'name': 'Cedi', 'symbol': 'GH₵',
                                  'rate_from_ngn': Decimal('0.0098')})

    def test_a_good_feed_updates_the_rate(self):
        with mock.patch('vent_auth.rates.fetch_rates',
                        return_value=({'GHS': 0.00827}, None)):
            updated, skipped, error = refresh_rates()
        self.assertIsNone(error)
        self.assertEqual(updated, 1)
        self.ghs.refresh_from_db()
        self.assertEqual(self.ghs.rate_from_ngn, Decimal('0.00827000'))

    def test_the_base_is_never_written(self):
        """A feed claiming the naira is not 1 against itself is wrong."""
        with mock.patch('vent_auth.rates.fetch_rates',
                        return_value=({'NGN': 2, 'GHS': 0.00827}, None)):
            refresh_rates()
        self.assertEqual(Currency.objects.get(code='NGN').rate_from_ngn, Decimal('1'))

    def test_a_failed_fetch_leaves_every_rate_alone(self):
        """Stale is a slightly wrong guide; blank would make prices unreadable."""
        with mock.patch('vent_auth.rates.fetch_rates',
                        return_value=(None, 'the service is down')):
            updated, skipped, error = refresh_rates()
        self.assertEqual(updated, 0)
        self.assertIsNotNone(error)
        self.ghs.refresh_from_db()
        self.assertEqual(self.ghs.rate_from_ngn, Decimal('0.00980000'))

    def test_an_absurd_rate_is_refused_rather_than_stored(self):
        """A broken response should not overwrite figures people read prices from."""
        with mock.patch('vent_auth.rates.fetch_rates',
                        return_value=({'GHS': 999999999}, None)):
            updated, skipped, error = refresh_rates()
        self.assertEqual(updated, 0)
        self.assertTrue(any('GHS' in s for s in skipped))
        self.ghs.refresh_from_db()
        self.assertEqual(self.ghs.rate_from_ngn, Decimal('0.00980000'))

    def test_a_currency_missing_from_the_feed_is_skipped_not_zeroed(self):
        with mock.patch('vent_auth.rates.fetch_rates', return_value=({}, None)):
            updated, skipped, error = refresh_rates()
        self.assertEqual(updated, 0)
        self.ghs.refresh_from_db()
        self.assertEqual(self.ghs.rate_from_ngn, Decimal('0.00980000'))


class RateAdminTests(TestCase):
    def setUp(self):
        self.admin, self.auth = an_admin()
        Currency.objects.update_or_create(
            code='NGN', defaults={'name': 'Naira', 'symbol': '₦', 'rate_from_ngn': 1})
        Currency.objects.update_or_create(
            code='GHS', defaults={'name': 'Cedi', 'symbol': 'GH₵',
                                  'rate_from_ngn': Decimal('0.0098')})

    def test_an_admin_can_see_the_rates_and_their_age(self):
        res = self.client.get('/auth/admin/rates/', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('rate_updated', res.json()['data']['results'][0])

    def test_a_rate_can_be_set_by_hand(self):
        res = self.client.patch('/auth/admin/rates/GHS/',
                                data={'rate_from_ngn': 0.0083},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Currency.objects.get(code='GHS').rate_from_ngn,
                         Decimal('0.00830000'))

    def test_the_naira_cannot_be_moved_off_one(self):
        res = self.client.patch('/auth/admin/rates/NGN/',
                                data={'rate_from_ngn': 2},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(Currency.objects.get(code='NGN').rate_from_ngn, Decimal('1'))

    def test_the_naira_cannot_be_switched_off(self):
        """Everything is priced against it; without it nothing converts."""
        res = self.client.patch('/auth/admin/rates/NGN/',
                                data={'is_active': False},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_negative_rate_is_refused(self):
        res = self.client.patch('/auth/admin/rates/GHS/',
                                data={'rate_from_ngn': -1},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_moderator_cannot_touch_the_rates(self):
        """Rates are money-shaped: they belong with finance, not moderation."""
        _user, auth = an_admin(role='mod_admin', token='mod-rates')
        res = self.client.get('/auth/admin/rates/', **auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_a_refresh_failure_is_reported_rather_than_silent(self):
        with mock.patch('vent_auth.views_admin_rates.refresh_rates',
                        return_value=(0, [], 'the service is down')):
            res = self.client.post('/auth/admin/rates/refresh/', **self.auth)
        self.assertEqual(res.status_code, 502, res.content)
        self.assertEqual(res.json()['code'], 'RATES_FEED_UNAVAILABLE')

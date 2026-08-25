"""The daily location refresh.

The lookup itself is DB-IP's data and not ours to test; what matters here is
that the refresh writes when it should, stays quiet when it should, and can
never take a login down with it.
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_auth import geo


def request_from(ip):
    """A stand-in carrying the header nginx sets in production."""
    return mock.Mock(META={'HTTP_X_FORWARDED_FOR': f'{ip}, 10.0.0.1'})


class DailyLocationTests(TestCase):
    def setUp(self):
        self.user = Users.objects.create(
            username='geotest', email='geotest@vent.test', signup_type='normal')

    @mock.patch('vent_auth.geo.locate', return_value=('Nigeria', 'Lagos'))
    def test_writes_city_and_country_on_first_login(self, _locate):
        with self.settings(DEBUG=False):
            self.assertTrue(geo.refresh_daily_location(self.user, request_from('105.112.0.1')))
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, 'Nigeria')
        self.assertEqual(self.user.state, 'Lagos')
        self.assertEqual(self.user.last_login_ip, '105.112.0.1')
        self.assertIsNotNone(self.user.location_updated_at)

    @mock.patch('vent_auth.geo.locate', return_value=('Nigeria', 'Lagos'))
    def test_second_login_the_same_day_does_not_write(self, locate):
        with self.settings(DEBUG=False):
            geo.refresh_daily_location(self.user, request_from('105.112.0.1'))
            self.assertFalse(geo.refresh_daily_location(self.user, request_from('105.112.0.1')))
        self.assertEqual(locate.call_count, 1)

    @mock.patch('vent_auth.geo.locate', return_value=('Ghana', 'Accra'))
    def test_the_next_day_writes_again(self, _locate):
        self.user.location_updated_at = timezone.now() - timezone.timedelta(days=1)
        self.user.save()
        with self.settings(DEBUG=False):
            self.assertTrue(geo.refresh_daily_location(self.user, request_from('154.160.0.1')))
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, 'Ghana')
        self.assertEqual(self.user.state, 'Accra')

    @mock.patch('vent_auth.geo.locate')
    def test_private_address_is_skipped_without_a_lookup(self, locate):
        with self.settings(DEBUG=False):
            self.assertFalse(geo.refresh_daily_location(self.user, request_from('192.168.1.5')))
        locate.assert_not_called()

    @mock.patch('vent_auth.geo.locate', side_effect=RuntimeError('database on fire'))
    def test_a_broken_lookup_cannot_break_a_login(self, _locate):
        with self.settings(DEBUG=False):
            self.assertFalse(geo.refresh_daily_location(self.user, request_from('105.112.0.1')))
        self.user.refresh_from_db()
        self.assertIsNone(self.user.location_updated_at)

    @mock.patch('vent_auth.geo.locate', return_value=(None, None))
    def test_an_unknown_address_leaves_the_profile_alone(self, _locate):
        self.user.country = 'Nigeria'
        self.user.save()
        with self.settings(DEBUG=False):
            self.assertFalse(geo.refresh_daily_location(self.user, request_from('105.112.0.1')))
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, 'Nigeria')

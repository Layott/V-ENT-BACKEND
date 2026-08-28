"""The endpoints that answer without a key or a session.

The SSO consent screen has to draw a partner's name before anybody signs in, so
it answers AllowAny - and in doing so it confirms whether a client_id exists.
Unlimited, that is an enumeration tool with no cost attached.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from vent_auth.throttle import client_ip, too_many


class FakeRequest:
    def __init__(self, **meta):
        self.META = meta


class ClientIpTests(TestCase):
    def test_the_first_hop_of_x_forwarded_for_wins(self):
        """nginx sets it, and the client is the first entry. Anything a caller
        adds itself is appended, not prepended."""
        request = FakeRequest(HTTP_X_FORWARDED_FOR='41.58.1.9, 10.0.0.1',
                              REMOTE_ADDR='10.0.0.1')
        self.assertEqual(client_ip(request), '41.58.1.9')

    def test_it_falls_back_to_remote_addr(self):
        self.assertEqual(client_ip(FakeRequest(REMOTE_ADDR='41.58.1.9')), '41.58.1.9')

    def test_an_empty_forwarded_header_does_not_win(self):
        request = FakeRequest(HTTP_X_FORWARDED_FOR='', REMOTE_ADDR='41.58.1.9')
        self.assertEqual(client_ip(request), '41.58.1.9')

    def test_nothing_at_all_is_still_a_bucket(self):
        """A caller we cannot identify is still counted, together."""
        self.assertEqual(client_ip(FakeRequest()), 'unknown')


class TooManyTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_it_allows_the_allowance_and_refuses_the_next(self):
        request = FakeRequest(REMOTE_ADDR='41.58.1.9')
        for _ in range(3):
            self.assertFalse(too_many(request, 'probe', 3))
        self.assertTrue(too_many(request, 'probe', 3))

    def test_two_callers_do_not_share_an_allowance(self):
        first = FakeRequest(REMOTE_ADDR='41.58.1.9')
        second = FakeRequest(REMOTE_ADDR='41.58.1.10')
        for _ in range(3):
            too_many(first, 'probe', 3)
        self.assertTrue(too_many(first, 'probe', 3))
        self.assertFalse(too_many(second, 'probe', 3))

    def test_two_names_do_not_share_an_allowance(self):
        request = FakeRequest(REMOTE_ADDR='41.58.1.9')
        for _ in range(3):
            too_many(request, 'one', 3)
        self.assertTrue(too_many(request, 'one', 3))
        self.assertFalse(too_many(request, 'two', 3))

    def test_extra_narrows_the_bucket_further(self):
        """One noisy partner cannot spend everybody else's allowance."""
        request = FakeRequest(REMOTE_ADDR='41.58.1.9')
        for _ in range(3):
            too_many(request, 'probe', 3, extra='partner-a')
        self.assertTrue(too_many(request, 'probe', 3, extra='partner-a'))
        self.assertFalse(too_many(request, 'probe', 3, extra='partner-b'))

    def test_a_cache_outage_opens_the_door_rather_than_closing_it(self):
        """A limiter that fails shut turns one broken Redis into a site-wide
        outage, and what this protects is enumeration of public client ids."""
        request = FakeRequest(REMOTE_ADDR='41.58.1.9')
        with patch('vent_auth.throttle.cache.get_or_set', side_effect=OSError('down')):
            self.assertFalse(too_many(request, 'probe', 1))
            self.assertFalse(too_many(request, 'probe', 1))

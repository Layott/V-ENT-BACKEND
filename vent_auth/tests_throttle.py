"""The endpoints that answer without a key or a session.

The SSO consent screen has to draw a partner's name before anybody signs in, so
it answers AllowAny - and in doing so it confirms whether a client_id exists.
Unlimited, that is an enumeration tool with no cost attached.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

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


class LimitedEndpointsTests(TestCase):
    """Owner rule R59: the credential endpoints carry their own limiter.

    Off under the test runner by default (the cache outlives a test), so this
    switches it on and proves the (n + 1)th request in a minute is a 429 with
    a code the screen can translate.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def burst(self, path, body, times):
        last = None
        for _ in range(times):
            last = self.client.post(path, body, format='json',
                                    REMOTE_ADDR='41.58.1.9')
        return last

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_login_answers_429_after_its_allowance(self):
        body = {'username_or_email': 'nobody@example.com', 'password': 'wrong'}
        res = self.burst('/auth/login/', body, 20)
        self.assertNotEqual(res.status_code, 429)
        res = self.burst('/auth/login/', body, 1)
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data['code'], 'TOO_MANY_ATTEMPTS')
        self.assertEqual(res['Retry-After'], '60')

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_another_address_has_its_own_allowance(self):
        body = {'username_or_email': 'nobody@example.com', 'password': 'wrong'}
        self.burst('/auth/login/', body, 21)
        res = self.client.post('/auth/login/', body, format='json',
                               REMOTE_ADDR='41.58.1.10')
        self.assertNotEqual(res.status_code, 429)

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_the_mail_sending_endpoints_are_tighter(self):
        """Five a minute: every one of these sends an email, and a loop on
        them is a mail bill and a spam complaint."""
        for path, body in (
            ('/auth/resend-link/', {'email': 'nobody@example.com'}),
            ('/auth/send-code/', {'email': 'nobody@example.com'}),
            ('/auth/forgot-password/send-token/', {'email': 'nobody@example.com'}),
            ('/auth/resend-forgot-password-token/', {'email': 'nobody@example.com'}),
        ):
            cache.clear()
            res = self.burst(path, body, 6)
            self.assertEqual(res.status_code, 429, path)

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_the_code_endpoints_are_limited(self):
        for path, body in (
            ('/auth/signup/', {}),
            ('/auth/login/2fa/verify/', {'pending_token': 'x', 'code': '000000'}),
            ('/auth/forgot-password/verify-token/', {}),
            ('/auth/forgot-password/change-password/', {}),
            ('/auth/2fa/confirm/', {'code': '000000'}),
            ('/auth/2fa/disable/', {'code': '000000'}),
            ('/auth/verify-new-email/', {}),
            ('/auth/social-auth/', {'provider': 'google'}),
        ):
            cache.clear()
            res = self.burst(path, body, 21)
            self.assertEqual(res.status_code, 429, path)

    def test_off_under_the_test_runner_by_default(self):
        body = {'username_or_email': 'nobody@example.com', 'password': 'wrong'}
        res = self.burst('/auth/login/', body, 25)
        self.assertNotEqual(res.status_code, 429)

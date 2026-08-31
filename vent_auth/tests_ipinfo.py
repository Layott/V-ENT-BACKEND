"""ipinfo.io as the sharper provider, with the safety rails intact.

CEO, 31 August 2026: "wire up ipinfo".

What is worth testing is not that a successful call returns a city. It is
everything around that call, because a third-party lookup on a sign-in path is
exactly where a platform picks up a stall it never recovers from:

- with no token there is no network call at all, and the local file still works;
- a refusal, a timeout, a rate limit and a malformed body are all a quiet
  fallback rather than a failed sign-in;
- an address is asked about once, not once per sign-in, because the free tier
  is 50,000 distinct addresses and a busy month is not;
- a two-letter country code is turned into the NAME the rest of the platform
  compares against - a tournament open to "Nigeria" would match nothing at all
  against "NG";
- and the rule that matters most: a better guess is still a guess. ipinfo
  saying "Ilorin" for a Lagos phone is ipinfo being right about the carrier
  gateway, so the city still never lands on somebody's profile by itself.
"""
import uuid
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from . import geo, ipinfo
from .models import IPLocation, Users

PUBLIC_IP = '102.89.34.7'


def a_user(name='traveller'):
    tag = uuid.uuid4().hex[:5]
    user = Users.objects.create(
        username='%s_%s' % (name, tag),
        email='%s_%s@vent.test' % (name, tag),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        is_active=True,
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def a_response(status=200, payload=None):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = payload or {}
    return response


class IpinfoLookupTests(TestCase):
    @override_settings(IPINFO_TOKEN='')
    def test_with_no_token_there_is_no_network_call(self):
        with mock.patch('requests.get') as get:
            self.assertEqual(ipinfo.lookup(PUBLIC_IP), (None, None))
        get.assert_not_called()
        self.assertFalse(ipinfo.is_configured())

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_successful_lookup_returns_the_country_name_not_the_code(self):
        with mock.patch('requests.get',
                        return_value=a_response(payload={'country': 'NG', 'city': 'Lagos'})):
            country, city = ipinfo.lookup(PUBLIC_IP)
        self.assertEqual(country, 'Nigeria')
        self.assertEqual(city, 'Lagos')

    @override_settings(IPINFO_TOKEN='tok')
    def test_an_unknown_country_code_is_not_passed_through_as_a_country(self):
        """"ZZ" would look like a country and match no restriction anywhere."""
        with mock.patch('requests.get',
                        return_value=a_response(payload={'country': 'ZZ', 'city': 'Nowhere'})):
            country, _city = ipinfo.lookup(PUBLIC_IP)
        self.assertIsNone(country)

    @override_settings(IPINFO_TOKEN='tok')
    def test_an_address_is_asked_about_once(self):
        payload = {'country': 'NG', 'city': 'Lagos'}
        with mock.patch('requests.get', return_value=a_response(payload=payload)) as get:
            ipinfo.lookup(PUBLIC_IP)
            ipinfo.lookup(PUBLIC_IP)
            ipinfo.lookup(PUBLIC_IP)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(IPLocation.objects.get(ip=PUBLIC_IP).source, 'ipinfo')

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_stale_answer_is_asked_again(self):
        IPLocation.objects.create(
            ip=PUBLIC_IP, country='Ghana', city='Accra', source='ipinfo',
            updated_at=timezone.now() - timezone.timedelta(days=ipinfo.CACHE_DAYS + 1))
        with mock.patch('requests.get',
                        return_value=a_response(payload={'country': 'NG', 'city': 'Lagos'})) as get:
            country, city = ipinfo.lookup(PUBLIC_IP)
        self.assertEqual(get.call_count, 1)
        self.assertEqual((country, city), ('Nigeria', 'Lagos'))

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_remembered_nothing_is_not_asked_again(self):
        """Re-asking about an address ipinfo does not know, on every sign-in,
        spends quota to learn the same nothing."""
        with mock.patch('requests.get',
                        return_value=a_response(payload={'bogon': True})) as get:
            self.assertEqual(ipinfo.lookup(PUBLIC_IP), (None, None))
            self.assertEqual(ipinfo.lookup(PUBLIC_IP), (None, None))
        self.assertEqual(get.call_count, 1)

    @override_settings(IPINFO_TOKEN='tok')
    def test_every_way_it_can_fail_is_a_quiet_none(self):
        for name, side in (
            ('refused', mock.Mock(side_effect=OSError('connection refused'))),
            ('timeout', mock.Mock(side_effect=Exception('timed out'))),
            ('rate limited', mock.Mock(return_value=a_response(status=429))),
            ('server error', mock.Mock(return_value=a_response(status=500))),
        ):
            IPLocation.objects.all().delete()
            with self.subTest(name):
                with mock.patch('requests.get', side):
                    self.assertEqual(ipinfo.lookup(PUBLIC_IP), (None, None))

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_malformed_body_is_a_quiet_none(self):
        broken = a_response()
        broken.json.side_effect = ValueError('not json')
        with mock.patch('requests.get', return_value=broken):
            self.assertEqual(ipinfo.lookup(PUBLIC_IP), (None, None))


class LocateFallbackTests(TestCase):
    """`locate` is the seam every caller uses. It must not change shape."""

    @override_settings(IPINFO_TOKEN='tok')
    def test_ipinfo_is_preferred_when_it_answers(self):
        with mock.patch.object(ipinfo, 'lookup', return_value=('Nigeria', 'Lagos')), \
             mock.patch.object(geo, '_get_reader') as reader:
            self.assertEqual(geo.locate(PUBLIC_IP), ('Nigeria', 'Lagos'))
        reader.assert_not_called()

    @override_settings(IPINFO_TOKEN='tok')
    def test_the_local_database_still_answers_when_ipinfo_cannot(self):
        local = mock.Mock()
        local.city.return_value = mock.Mock(
            country=mock.Mock(name='Ghana'), city=mock.Mock(), subdivisions=None)
        local.city.return_value.country.name = 'Ghana'
        local.city.return_value.city.name = 'Accra'
        with mock.patch.object(ipinfo, 'lookup', return_value=(None, None)), \
             mock.patch.object(geo, '_get_reader', return_value=local):
            self.assertEqual(geo.locate(PUBLIC_IP), ('Ghana', 'Accra'))

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_raising_ipinfo_cannot_break_the_lookup(self):
        with mock.patch.object(ipinfo, 'lookup', side_effect=RuntimeError('boom')), \
             mock.patch.object(geo, '_get_reader', return_value=None):
            self.assertEqual(geo.locate(PUBLIC_IP), (None, None))

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_private_address_is_never_sent_anywhere(self):
        with mock.patch.object(ipinfo, 'lookup') as lookup:
            self.assertEqual(geo.locate('192.168.1.5'), (None, None))
        lookup.assert_not_called()

    def test_a_district_in_brackets_is_still_tidied(self):
        """DB-IP names districts in brackets and ipinfo does not, but the
        tidier runs on both so neither can put "Lagos (Victoria Island Annex)"
        on a profile."""
        with mock.patch.object(ipinfo, 'lookup',
                               return_value=('Nigeria', 'Lagos (Victoria Island Annex)')):
            with override_settings(IPINFO_TOKEN='tok'):
                self.assertEqual(geo.locate(PUBLIC_IP), ('Nigeria', 'Lagos'))


class SuggestionTests(TestCase):
    """A better guess is offered. It is still never written."""

    def setUp(self):
        self.user, self.auth = a_user()

    @override_settings(IPINFO_TOKEN='tok')
    def test_the_suggestion_is_returned_and_nothing_is_written(self):
        with mock.patch.object(geo, 'locate', return_value=('Nigeria', 'Lagos')), \
             mock.patch.object(geo, 'client_ip', return_value=PUBLIC_IP):
            res = self.client.get('/settings/location-suggestion/', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['city'], 'Lagos')
        self.assertEqual(data['country'], 'Nigeria')

        self.user.refresh_from_db()
        self.assertFalse((self.user.state or '').strip(),
                         'a suggestion must never write itself onto the profile')

    def test_it_needs_an_account(self):
        self.assertNotEqual(
            self.client.get('/settings/location-suggestion/').status_code, 200)

    @override_settings(IPINFO_TOKEN='tok')
    def test_a_daily_refresh_still_refuses_to_write_the_city(self):
        """The whole point. ipinfo saying "Ilorin" for a Lagos phone is ipinfo
        being right about the carrier gateway."""
        request = mock.Mock()
        request.META = {'REMOTE_ADDR': PUBLIC_IP}
        with mock.patch.object(geo, 'client_ip', return_value=PUBLIC_IP), \
             mock.patch.object(geo, '_is_public', return_value=True), \
             mock.patch.object(geo, 'locate', return_value=('Nigeria', 'Ilorin')):
            geo.refresh_daily_location(self.user, request)

        self.user.refresh_from_db()
        self.assertEqual(self.user.country, 'Nigeria')
        self.assertFalse((self.user.state or '').strip())
        self.assertTrue(self.user.country_is_guess)

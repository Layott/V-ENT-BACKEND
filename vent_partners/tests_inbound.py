"""Signing in to V-ENT with an African Free Fire Community account.

Nobody has AFC's credentials yet, so the tests do two things: prove the guard
holds while it is unconfigured, and prove the whole flow works once the four
environment variables exist, with AFC's HTTP calls stubbed.

The matching rules are the part worth being careful about. Somebody who already
has a V-ENT account must get their account back rather than a second one, and a
handle that would be refused at signup must not slip in through the side door.
"""
import json
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_partners.models import ExternalIdentity
from vent_partners.views_sso import link_or_create_user

AFC_ENV = {
    'AFC_CLIENT_ID': 'afc-client',
    'AFC_CLIENT_SECRET': 'afc-secret',
    'AFC_AUTHORIZE_URL': 'https://africanfreefirecommunity.com/oauth/authorize',
    'AFC_TOKEN_URL': 'https://africanfreefirecommunity.com/oauth/token',
    'AFC_USERINFO_URL': 'https://africanfreefirecommunity.com/api/me',
    # The switch, on, because these tests are about the flow rather than about
    # whether it is currently offered. See SwitchedOffTests for the other half.
    'AFC_SSO_ENABLED': '1',
}

CREDENTIALS_ONLY = dict(AFC_ENV, AFC_SSO_ENABLED='0')


class InboundGuardTests(TestCase):
    def test_unconfigured_providers_are_reported_honestly(self):
        with mock.patch.dict('os.environ', {'AFC_SSO_ENABLED': '1',
                                            'AFC_CLIENT_ID': '',
                                            'AFC_CLIENT_SECRET': ''}):
            res = self.client.get('/partners/inbound/providers/')
            self.assertEqual(res.status_code, 200)
            afc = res.json()['data']['providers']['afc']
            self.assertEqual(afc['label'], 'African Free Fire Community')
            self.assertEqual(afc['short'], 'AFC')
            self.assertFalse(afc['configured'])

    def test_starting_an_unconfigured_provider_is_a_503(self):
        with mock.patch.dict('os.environ', {'AFC_SSO_ENABLED': '1',
                                            'AFC_CLIENT_ID': '',
                                            'AFC_CLIENT_SECRET': ''}):
            res = self.client.get('/partners/inbound/afc/start/')
        self.assertEqual(res.status_code, 503)
        self.assertFalse(res.json()['configured'])

    def test_an_unknown_provider_is_a_404(self):
        res = self.client.get('/partners/inbound/nowhere/start/')
        self.assertEqual(res.status_code, 404)


class InboundConfiguredTests(TestCase):
    def test_the_start_url_carries_client_id_scope_and_signed_state(self):
        with mock.patch.dict('os.environ', AFC_ENV):
            res = self.client.get('/partners/inbound/afc/start/')
            self.assertEqual(res.status_code, 200)
            url = res.json()['data']['url']
            self.assertIn('africanfreefirecommunity.com/oauth/authorize', url)
            self.assertIn('client_id=afc-client', url)
            self.assertIn('state=', url)
            self.assertIn('redirect_uri=', url)

    def test_a_tampered_state_is_refused(self):
        with mock.patch.dict('os.environ', AFC_ENV):
            res = self.client.get('/partners/inbound/afc/callback/?code=x&state=not-signed')
            self.assertEqual(res.status_code, 302)
            self.assertIn('error=sso-state', res['Location'])

    def test_the_full_callback_signs_somebody_in(self):
        # Start the flow properly rather than minting a state by hand. The
        # callback now requires an attempt that this server actually began,
        # because that is where the PKCE verifier is kept: a callback for a
        # sign-in nobody started has no verifier to send, and AFC would refuse
        # the exchange anyway.
        with mock.patch.dict('os.environ', AFC_ENV):
            start = self.client.get('/partners/inbound/afc/start/')
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start.json()['data']['url']).query)['state'][0]

        token_response = mock.Mock(status_code=200)
        token_response.json.return_value = {'access_token': 'afc-access'}
        me_response = mock.Mock(status_code=200)
        me_response.json.return_value = {
            'id': 'afc-9001', 'username': 'naija_ace', 'email': 'ace@afc.test', 'name': 'Naija Ace',
        }

        with mock.patch.dict('os.environ', AFC_ENV), \
             mock.patch('vent_partners.views_sso.http.post', return_value=token_response), \
             mock.patch('vent_partners.views_sso.http.get', return_value=me_response):
            res = self.client.get(f'/partners/inbound/afc/callback/?code=abc&state={state}')

        self.assertEqual(res.status_code, 302)
        self.assertIn('/auth/external?token=', res['Location'])

        user = Users.objects.get(username='naija_ace')
        self.assertEqual(user.signup_type, 'afc')
        self.assertTrue(user.login_session_token)
        self.assertTrue(
            ExternalIdentity.objects.filter(provider='afc', external_id='afc-9001').exists()
        )


class IdentityMatchingTests(TestCase):
    def test_a_returning_account_is_matched_by_its_external_id(self):
        first = link_or_create_user('afc', {'id': '1', 'username': 'repeat', 'email': 'r@afc.test'})
        second = link_or_create_user('afc', {'id': '1', 'username': 'renamed', 'email': 'r@afc.test'})
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ExternalIdentity.objects.filter(provider='afc').count(), 1)

    def test_an_existing_vent_account_is_matched_by_email_rather_than_duplicated(self):
        existing = Users.objects.create(username='already_here', email='same@vent.test')
        matched = link_or_create_user('afc', {'id': '77', 'username': 'other', 'email': 'same@vent.test'})
        self.assertEqual(matched.pk, existing.pk)
        self.assertEqual(Users.objects.filter(email='same@vent.test').count(), 1)

    def test_a_styled_unicode_handle_does_not_slip_in(self):
        user = link_or_create_user(
            'afc', {'id': '5', 'username': 'ｌａｙｏｔｔ', 'email': 'styled@afc.test'},
        )
        self.assertRegex(user.username, r'^[a-z0-9_]+$')

    def test_a_taken_handle_gets_a_free_one(self):
        Users.objects.create(username='popular', email='first@vent.test')
        user = link_or_create_user('afc', {'id': '6', 'username': 'popular', 'email': 'second@afc.test'})
        self.assertNotEqual(user.username, 'popular')
        self.assertEqual(Users.objects.filter(username='popular').count(), 1)

    def test_a_profile_with_no_id_is_refused(self):
        self.assertIsNone(link_or_create_user('afc', {'username': 'nobody'}))

    def test_an_account_without_an_email_still_works(self):
        user = link_or_create_user('afc', {'id': '9', 'username': 'noemail'})
        self.assertTrue(user.email.endswith('@afc.external'))

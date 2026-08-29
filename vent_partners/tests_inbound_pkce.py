"""Signing in with AFC has to carry PKCE, or it does not work at all.

AFC's partner integration guide, version 1.2:

    AFC sets PKCE_REQUIRED: True. This is not advisory and it is not only for
    public clients. Even though you are a confidential client with a
    client_secret, you must send code_challenge and code_challenge_method on
    the authorization request and code_verifier on the token request. If you
    omit them, the authorization request is rejected or the token exchange
    fails. This is the single most common cause of a first integration attempt
    failing.

V-ENT sent neither. The flow was written, tested against a stub, merged and
would have failed on the first real player, at the point where the CEO had
already told AFC the integration was ready.

These tests are the shape of the request, not the shape of our stub. They
assert what actually goes over the wire.
"""
import base64
import hashlib
import json
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings

from .models import InboundLogin


AFC_ENV = {
    'AFC_CLIENT_ID': 'vent-client',
    'AFC_CLIENT_SECRET': 'vent-secret',
}


class _Res:
    """Just enough of a requests response for the callback path."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class InboundPkceTests(TestCase):
    def _start(self):
        with mock.patch.dict('os.environ', AFC_ENV, clear=False):
            return self.client.get('/partners/inbound/afc/start/')

    def _authorize_params(self):
        res = self._start()
        self.assertEqual(res.status_code, 200, res.content[:300])
        url = res.json()['data']['url']
        return parse_qs(urlparse(url).query), url

    # ------------------------------------------------------------- authorize
    def test_the_authorize_request_carries_a_challenge(self):
        params, _ = self._authorize_params()
        self.assertIn('code_challenge', params)
        self.assertEqual(params['code_challenge_method'], ['S256'])

    def test_the_challenge_is_the_s256_of_the_stored_verifier(self):
        """Not any base64 string: the actual digest, or AFC refuses the
        exchange after the player has already consented."""
        params, _ = self._authorize_params()
        attempt = InboundLogin.objects.get(state=params['state'][0])
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(attempt.code_verifier.encode('ascii')).digest()
        ).decode('ascii').rstrip('=')
        self.assertEqual(params['code_challenge'][0], expected)

    def test_the_verifier_never_appears_in_the_url(self):
        """The whole point. The address goes out through the player's browser,
        and `state` is signed rather than encrypted, so anything in it is
        readable by whoever can read the address bar."""
        params, url = self._authorize_params()
        attempt = InboundLogin.objects.get(state=params['state'][0])
        self.assertNotIn(attempt.code_verifier, url)

    def test_the_verifier_is_long_enough_to_be_legal(self):
        """RFC 7636 requires 43 to 128 characters."""
        self._start()
        verifier = InboundLogin.objects.get().code_verifier
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)

    def test_two_sign_ins_do_not_share_a_verifier(self):
        self._start()
        self._start()
        verifiers = set(InboundLogin.objects.values_list('code_verifier', flat=True))
        self.assertEqual(len(verifiers), 2)

    def test_the_scope_asks_for_the_afc_fields_we_actually_use(self):
        params, _ = self._authorize_params()
        scope = params['scope'][0].split()
        for wanted in ('openid', 'profile', 'afc.freefire', 'afc.team', 'afc.standing'):
            self.assertIn(wanted, scope)

    def test_the_endpoints_default_to_the_afc_api_host(self):
        """The SSO surface is on the API host with an /sso/ prefix, not on the
        website origin. Getting this wrong fails at the first redirect."""
        params, url = self._authorize_params()
        self.assertTrue(url.startswith('https://api.africanfreefirecommunity.com/sso/authorize/'), url)

    # -------------------------------------------------------------- callback
    def _callback(self, state, code='the-code'):
        captured = {}

        def fake_post(url, data=None, timeout=None, **kw):
            captured['token'] = data
            return _Res({'access_token': 'at', 'refresh_token': 'rt'})

        def fake_get(url, headers=None, timeout=None, **kw):
            return _Res({'sub': 'a' * 64, 'preferred_username': 'afcplayer',
                         'email': 'afcplayer@afc.test', 'email_verified': True})

        with mock.patch.dict('os.environ', AFC_ENV, clear=False), \
                mock.patch('vent_partners.views_sso.http.post', side_effect=fake_post), \
                mock.patch('vent_partners.views_sso.http.get', side_effect=fake_get):
            res = self.client.get('/partners/inbound/afc/callback/',
                                  {'code': code, 'state': state})
        return res, captured

    def test_the_token_exchange_sends_the_matching_verifier(self):
        params, _ = self._authorize_params()
        state = params['state'][0]
        verifier = InboundLogin.objects.get(state=state).code_verifier

        _, captured = self._callback(state)
        self.assertIn('token', captured, 'the token endpoint was never called')
        self.assertEqual(captured['token'].get('code_verifier'), verifier)

    def test_the_attempt_is_single_use(self):
        """A replayed callback must not be able to exchange the code again."""
        params, _ = self._authorize_params()
        state = params['state'][0]
        self._callback(state)
        self.assertFalse(InboundLogin.objects.filter(state=state).exists())

        res, captured = self._callback(state)
        self.assertNotIn('token', captured)
        self.assertIn('sso-state', res.url)

    def test_a_callback_with_a_state_we_never_issued_is_refused(self):
        from django.core import signing
        forged = signing.dumps({'p': 'afc', 'n': 'forged'}, salt='vent.inbound-sso')
        res, captured = self._callback(forged)
        self.assertNotIn('token', captured)
        self.assertIn('sso-state', res.url)

    def test_abandoned_attempts_are_swept(self):
        """An abandoned sign-in leaves a row holding a secret. Without the
        sweep the table grows forever."""
        from datetime import timedelta
        from django.utils import timezone
        self._start()
        old = InboundLogin.objects.get()
        InboundLogin.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=2))

        self._start()   # sweeps on the way past
        self.assertFalse(InboundLogin.objects.filter(pk=old.pk).exists())
        self.assertEqual(InboundLogin.objects.count(), 1)

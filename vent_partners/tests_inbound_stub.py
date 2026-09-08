"""The inbound AFC sign-in, driven against a real HTTP server rather than a mock.

## Why this exists alongside tests_inbound

`tests_inbound` patches `views_sso.http.post` and `views_sso.http.get`, so it
proves the branching: what happens when the token call succeeds, when it fails,
when the profile carries no email. That is worth having and it stays.

What it cannot prove is that we send a request AFC would accept, because the
thing that would receive it never exists. A mock returns the same object whether
we post the right form fields, the wrong ones, or none at all. Every one of
these would pass a mocked test and fail against AFC:

- posting JSON where the endpoint wants form encoding
- omitting `code_verifier`, so PKCE fails at their end
- sending the client secret in a header when the server wants it in the body
- building the callback `redirect_uri` differently from the one sent to
  authorize, which OAuth requires to match exactly
- reading `access_token` out of the wrong place in the response

So this file stands up a small HTTP server that behaves the way AFC's published
contract says it does, points the environment at it, and lets the real
`requests` library talk to it. The stub is deliberately STRICT: it refuses
anything the real server would refuse, and every refusal is a test failure with
the reason attached. A permissive stub is only a slower mock.

The one thing no local test can prove is that AFC's live server agrees with its
own documentation. That was measured separately by hand on 8 September 2026
against api.africanfreefirecommunity.com, which answered 302 on authorize, 405
on a GET to token, 401 on userinfo, and `invalid_grant` rather than
`invalid_client` when handed the production credentials with a bogus code. That
last one is what proves our client id and secret are registered with them.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import TestCase

from vent_auth.models import Users

from .models import ExternalIdentity, InboundLogin

# What the stub expects to be told it is. A test that shares these with the
# environment below cannot drift from it.
CLIENT_ID = 'stub-client-id'
CLIENT_SECRET = 'stub-client-secret'
GOOD_CODE = 'the-authorization-code'
ACCESS_TOKEN = 'stub-access-token'


class AFCStub(BaseHTTPRequestHandler):
    """AFC's two server-to-server endpoints, as strictly as the real ones.

    `profile` and `refuse_token` are set on the class by each test, because
    BaseHTTPRequestHandler is instantiated per request and there is nowhere
    else to put per-test state.
    """

    profile = {}
    refuse_token = False
    # Every request the stub received, so a test can assert on what we SENT
    # rather than only on what came back. This is the half a mock cannot do.
    seen = []

    def log_message(self, *args):
        # The default handler writes every request to stderr, which buries the
        # test output. Silence is right here: failures are asserted, not read.
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != '/sso/token/':
            return self._json(404, {'error': 'not_found'})

        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length).decode()

        # Form encoding, not JSON. AFC's discovery document lists
        # client_secret_post, and a server that wants a form will not parse a
        # JSON body. Asserting the content type here is what catches the day
        # somebody "tidies" the call into json=.
        content_type = self.headers.get('Content-Type', '')
        if not content_type.startswith('application/x-www-form-urlencoded'):
            return self._json(400, {'error': 'invalid_request',
                                    'error_description': f'wanted a form, got {content_type!r}'})

        form = {k: v[0] for k, v in parse_qs(raw).items()}
        AFCStub.seen.append(('token', form))

        # The client credentials, in the body, as client_secret_post requires.
        if form.get('client_id') != CLIENT_ID or form.get('client_secret') != CLIENT_SECRET:
            return self._json(401, {'error': 'invalid_client'})

        if form.get('grant_type') != 'authorization_code':
            return self._json(400, {'error': 'unsupported_grant_type'})

        # PKCE. The real server checks the verifier against the challenge it was
        # given at authorize time. The stub cannot recompute that without the
        # challenge, but it CAN insist the field is present and non-empty, which
        # is the failure that actually happens: sending nothing at all.
        if not form.get('code_verifier'):
            return self._json(400, {'error': 'invalid_grant',
                                    'error_description': 'no code_verifier'})

        # redirect_uri must be sent and must be the callback, because OAuth
        # requires it to match the one given to authorize.
        if not (form.get('redirect_uri') or '').endswith('/partners/inbound/afc/callback/'):
            return self._json(400, {'error': 'invalid_grant',
                                    'error_description': f"redirect_uri was {form.get('redirect_uri')!r}"})

        if AFCStub.refuse_token or form.get('code') != GOOD_CODE:
            return self._json(400, {'error': 'invalid_grant'})

        return self._json(200, {
            'access_token': ACCESS_TOKEN,
            'token_type': 'Bearer',
            'expires_in': 3600,
            'scope': 'openid profile email',
        })

    def do_GET(self):
        if urlparse(self.path).path != '/sso/userinfo/':
            return self._json(404, {'error': 'not_found'})

        auth = self.headers.get('Authorization', '')
        AFCStub.seen.append(('userinfo', auth))
        if auth != f'Bearer {ACCESS_TOKEN}':
            # RFC 6750, and what the live server actually answered when probed.
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Bearer error="invalid_token"')
            self.end_headers()
            return

        return self._json(200, AFCStub.profile)


class InboundAgainstAStubServerTests(TestCase):
    """Every one of these drives real sockets. Nothing here is patched except
    the environment that says where AFC lives."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Port 0: the OS picks a free one, so two test runs at once cannot
        # collide and nothing has to be reserved.
        cls.server = HTTPServer(('127.0.0.1', 0), AFCStub)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        super().tearDownClass()

    def setUp(self):
        base = f'http://127.0.0.1:{self.port}'
        self.env = {
            'AFC_CLIENT_ID': CLIENT_ID,
            'AFC_CLIENT_SECRET': CLIENT_SECRET,
            'AFC_AUTHORIZE_URL': f'{base}/sso/authorize/',
            'AFC_TOKEN_URL': f'{base}/sso/token/',
            'AFC_USERINFO_URL': f'{base}/sso/userinfo/',
            'AFC_SSO_ENABLED': '1',
            # The callback the view builds its redirect_uri from. Without this
            # it would use the real api.v-ent.co and the stub would refuse it,
            # which is exactly the check being exercised.
            'BACKEND_PUBLIC_URL': 'https://api.v-ent.co',
        }
        AFCStub.profile = {
            'id': 'afc-4242',
            'username': 'stub_player',
            'email': 'stub.player@afc.test',
            'name': 'Stub Player',
        }
        AFCStub.refuse_token = False
        AFCStub.seen = []

    def _start(self):
        """Begin a sign-in properly and return its state, so the callback has a
        real PKCE verifier stored against it."""
        with mock.patch.dict('os.environ', self.env):
            res = self.client.get('/partners/inbound/afc/start/')
        self.assertEqual(res.status_code, 200)
        url = res.json()['data']['url']
        return parse_qs(urlparse(url).query)['state'][0]

    def _callback(self, state, code=GOOD_CODE):
        with mock.patch.dict('os.environ', self.env):
            return self.client.get(f'/partners/inbound/afc/callback/?code={code}&state={state}')

    # ------------------------------------------------------------------ flow

    def test_a_first_sign_in_creates_an_account_over_real_http(self):
        res = self._callback(self._start())

        self.assertEqual(res.status_code, 302, res.content[:300])
        self.assertIn('/auth/external?token=', res['Location'])

        user = Users.objects.get(email='stub.player@afc.test')
        self.assertEqual(user.signup_type, 'afc')
        self.assertTrue(user.login_session_token)
        self.assertTrue(
            ExternalIdentity.objects.filter(provider='afc', external_id='afc-4242').exists()
        )

    def test_the_token_request_is_shaped_the_way_afc_requires(self):
        """The assertion a mocked test cannot make: what we actually sent."""
        self._callback(self._start())

        token_calls = [form for kind, form in AFCStub.seen if kind == 'token']
        self.assertEqual(len(token_calls), 1)
        form = token_calls[0]

        self.assertEqual(form['grant_type'], 'authorization_code')
        self.assertEqual(form['client_id'], CLIENT_ID)
        self.assertEqual(form['client_secret'], CLIENT_SECRET)
        self.assertEqual(form['code'], GOOD_CODE)
        self.assertTrue(form['code_verifier'], 'PKCE verifier was not sent')
        self.assertEqual(form['redirect_uri'],
                         'https://api.v-ent.co/partners/inbound/afc/callback/')

    def test_the_access_token_is_presented_as_a_bearer(self):
        self._callback(self._start())
        userinfo_calls = [auth for kind, auth in AFCStub.seen if kind == 'userinfo']
        self.assertEqual(userinfo_calls, [f'Bearer {ACCESS_TOKEN}'])

    def test_a_second_sign_in_returns_the_same_account(self):
        self._callback(self._start())
        first = Users.objects.get(email='stub.player@afc.test')

        # A rename at AFC must not fork the account: the external id is what
        # identifies somebody, not their handle.
        AFCStub.profile = dict(AFCStub.profile, username='stub_player_renamed')
        self._callback(self._start())

        self.assertEqual(Users.objects.filter(email='stub.player@afc.test').count(), 1)
        self.assertEqual(Users.objects.get(pk=first.pk).pk, first.pk)
        self.assertEqual(ExternalIdentity.objects.filter(provider='afc').count(), 1)

    def test_an_existing_vent_account_is_linked_rather_than_duplicated(self):
        existing = Users.objects.create(username='already_a_member',
                                        email='stub.player@afc.test')
        self._callback(self._start())

        self.assertEqual(Users.objects.filter(email='stub.player@afc.test').count(), 1)
        self.assertTrue(
            ExternalIdentity.objects.filter(provider='afc', user=existing).exists()
        )

    # -------------------------------------------------------------- refusals

    def test_a_refused_exchange_leaves_no_half_made_account(self):
        AFCStub.refuse_token = True
        before = Users.objects.count()

        res = self._callback(self._start())

        self.assertEqual(res.status_code, 302)
        self.assertIn('error=sso-failed', res['Location'])
        self.assertEqual(Users.objects.count(), before)
        self.assertEqual(ExternalIdentity.objects.count(), 0)

    def test_a_profile_with_no_email_makes_nothing(self):
        """AFC really does send no email address on some accounts. Inventing one
        forked the CEO into a second account on 30 August 2026."""
        AFCStub.profile = {'id': 'afc-noemail', 'username': 'anonymous_ace'}
        before = Users.objects.count()

        res = self._callback(self._start())

        self.assertIn('error=sso-no-email', res['Location'])
        self.assertEqual(Users.objects.count(), before)
        self.assertEqual(ExternalIdentity.objects.count(), 0)

    def test_an_authorization_code_cannot_be_spent_twice(self):
        state = self._start()
        first = self._callback(state)
        self.assertIn('/auth/external?token=', first['Location'])

        # The attempt row is deleted when it is claimed, so the replay has no
        # verifier and must not reach AFC at all.
        AFCStub.seen = []
        second = self._callback(state)

        self.assertIn('error=sso-state', second['Location'])
        self.assertEqual(AFCStub.seen, [], 'a replayed callback still called AFC')
        self.assertEqual(Users.objects.filter(email='stub.player@afc.test').count(), 1)

    def test_a_tampered_state_never_reaches_afc(self):
        state = self._start()
        res = self._callback(state[:-4] + 'xxxx')

        self.assertIn('error=sso-state', res['Location'])
        self.assertEqual(AFCStub.seen, [])
        self.assertEqual(InboundLogin.objects.filter(state=state).count(), 1,
                         'a bad state consumed the real attempt')

    def test_wrong_credentials_are_reported_as_a_failure_not_a_sign_in(self):
        before = Users.objects.count()
        env = dict(self.env, AFC_CLIENT_SECRET='not-the-secret')

        with mock.patch.dict('os.environ', env):
            res = self.client.get('/partners/inbound/afc/start/')
            state = parse_qs(urlparse(res.json()['data']['url']).query)['state'][0]
            out = self.client.get(f'/partners/inbound/afc/callback/?code={GOOD_CODE}&state={state}')

        self.assertIn('error=sso-failed', out['Location'])
        self.assertEqual(Users.objects.count(), before)

    # ------------------------------------------------------------- the flag

    def test_with_the_flag_off_nothing_is_offered_and_nothing_starts(self):
        off = dict(self.env, AFC_SSO_ENABLED='0')
        with mock.patch.dict('os.environ', off):
            listed = self.client.get('/partners/inbound/providers/')
            started = self.client.get('/partners/inbound/afc/start/')

        providers = listed.json()['data']['providers']
        self.assertFalse(
            providers.get('afc', {}).get('configured'),
            'AFC is offered to the login page with the flag off',
        )
        self.assertEqual(started.status_code, 503)

    def test_with_the_flag_on_it_is_offered_and_the_url_points_at_afc(self):
        with mock.patch.dict('os.environ', self.env):
            listed = self.client.get('/partners/inbound/providers/')
            started = self.client.get('/partners/inbound/afc/start/')

        self.assertTrue(listed.json()['data']['providers']['afc']['configured'])
        self.assertEqual(started.status_code, 200)

        url = started.json()['data']['url']
        self.assertTrue(url.startswith(self.env['AFC_AUTHORIZE_URL']))
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['client_id'], [CLIENT_ID])
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['redirect_uri'],
                         ['https://api.v-ent.co/partners/inbound/afc/callback/'])

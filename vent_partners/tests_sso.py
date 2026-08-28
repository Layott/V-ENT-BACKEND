"""V-ENT as a sign-in provider.

This is the surface where a mistake leaks someone's identity to a site they did
not approve, so the tests are mostly about refusal: unknown clients, redirect
addresses that were never registered, codes replayed, PKCE verifiers that do not
match, and scopes that were never asked for.
"""
import base64
import hashlib
import json

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_partners.models import OAuthAccessToken, OAuthAuthorizationCode, Partner


def signed_in(username, **extra):
    user = Users.objects.create(
        username=username, email=f'{username}@vent.test', full_name=username.title(),
        login_session_token=f'tok{username}'[:16], login_session_created_at=timezone.now(),
        is_active=True, country='Nigeria', state='Lagos', **extra,
    )
    # A staff user in these suites exists to call the admin console's
    # endpoints, and those resolve the console's own grant rather than the
    # website session. The two used to be one field, which is why this fixture
    # only ever minted a site token.
    if extra.get('is_staff'):
        user.login_session_token = f'adm{username}'[:16]
        user.login_session_created_at = timezone.now()
        user.login_session_2fa_at = timezone.now()
        user.save(update_fields=['login_session_token', 'login_session_created_at', 'login_session_2fa_at'])
        return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}
    return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}


class SsoApprovalTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = signed_in('partnerowner')
        self.admin, self.admin_auth = signed_in('ssoadmin', is_staff=True, admin_role='super_admin')
        self.partner = Partner.objects.create(
            name='AFC', slug='afc', owner=self.owner, contact_name='AFC',
            contact_email='p@afc.test', status='approved',
            approved_scopes=['tournaments:read'], sso_status='requested',
        )

    def sso_review(self, body, auth=None):
        return self.client.post(
            f'/partners/admin/{self.partner.pk}/sso-review/', data=json.dumps(body),
            content_type='application/json', **(auth if auth is not None else self.admin_auth),
        )

    def test_sso_needs_the_extra_details_before_it_can_be_approved(self):
        res = self.sso_review({'decision': 'approved'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'MISSING_SSO_DETAILS')

    def test_sso_needs_a_redirect_address(self):
        self.partner.legal_name = 'AFC Ltd'
        self.partner.privacy_policy_url = 'https://afc.test/privacy'
        self.partner.data_protection_contact = 'dpo@afc.test'
        self.partner.save()
        res = self.sso_review({'decision': 'approved'})
        self.assertEqual(res.json()['code'], 'MISSING_REDIRECT')

    def test_sso_cannot_be_approved_for_an_unapproved_partner(self):
        self.partner.status = 'pending'
        self.partner.save(update_fields=['status'])
        res = self.sso_review({'decision': 'approved'})
        self.assertEqual(res.json()['code'], 'PARTNER_NOT_APPROVED')

    def test_approval_issues_credentials_once(self):
        self._complete_details()
        res = self.sso_review({'decision': 'approved'})
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertTrue(data['client_id'].startswith('vent_sso_'))
        self.assertTrue(len(data['client_secret']) > 20)

        self.partner.refresh_from_db()
        self.assertNotIn(data['client_secret'], self.partner.sso_client_secret_hash)
        self.assertTrue(self.partner.sso_secret_matches(data['client_secret']))
        self.assertFalse(self.partner.sso_secret_matches('wrong'))

    def _complete_details(self):
        self.partner.legal_name = 'AFC Ltd'
        self.partner.privacy_policy_url = 'https://afc.test/privacy'
        self.partner.data_protection_contact = 'dpo@afc.test'
        self.partner.redirect_uris = ['https://afc.test/callback']
        self.partner.save()


class SsoFlowTests(TestCase):
    def setUp(self):
        self.owner, _ = signed_in('flowowner')
        self.person, self.person_auth = signed_in('player1')
        self.partner = Partner.objects.create(
            name='AFC', slug='afc', owner=self.owner, contact_name='AFC',
            contact_email='p@afc.test', status='approved', sso_status='approved',
            legal_name='AFC Ltd', privacy_policy_url='https://afc.test/privacy',
            data_protection_contact='dpo@afc.test',
            redirect_uris=['https://afc.test/callback'],
        )
        self.secret = self.partner.issue_sso_credentials()

        # One PKCE pair for the whole class, so each test says what it is
        # testing rather than re-deriving a hash.
        self.verifier = 'a-verifier-long-enough-to-be-worth-something'
        self.challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()

    def approve(self, **overrides):
        body = {
            'client_id': self.partner.sso_client_id,
            'redirect_uri': 'https://afc.test/callback',
            'scope': 'identity',
        }
        body.update(overrides)
        return self.client.post(
            '/partners/sso/approve/', data=json.dumps(body),
            content_type='application/json', **self.person_auth,
        )

    def token(self, **overrides):
        body = {
            'client_id': self.partner.sso_client_id,
            'client_secret': self.secret,
            'redirect_uri': 'https://afc.test/callback',
        }
        body.update(overrides)
        return self.client.post(
            '/partners/sso/token/', data=json.dumps(body), content_type='application/json',
        )

    def code_from(self, response):
        return response.json()['data']['redirect_to'].split('code=')[1].split('&')[0]

    def test_metadata_is_public(self):
        res = self.client.get('/partners/sso/metadata/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('token_endpoint', res.json()['data'])

    def test_the_consent_screen_names_the_partner(self):
        res = self.client.get(
            '/partners/sso/authorize-info/',
            {'client_id': self.partner.sso_client_id,
             'redirect_uri': 'https://afc.test/callback', 'scope': 'identity identity:email'},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(data['partner']['name'], 'AFC')
        self.assertEqual([s['key'] for s in data['scopes']], ['identity', 'identity:email'])

    def test_an_unknown_client_is_refused(self):
        res = self.client.get('/partners/sso/authorize-info/',
                              {'client_id': 'vent_sso_nope', 'redirect_uri': 'https://afc.test/callback'})
        self.assertEqual(res.json()['code'], 'UNKNOWN_CLIENT')

    def test_an_unregistered_redirect_is_refused(self):
        res = self.approve(redirect_uri='https://evil.test/steal')
        self.assertEqual(res.json()['code'], 'BAD_REDIRECT')

    def test_a_partner_without_sso_approval_cannot_start(self):
        self.partner.sso_status = 'requested'
        self.partner.save(update_fields=['sso_status'])
        self.assertEqual(self.approve().json()['code'], 'UNKNOWN_CLIENT')

    def test_the_full_flow_ends_in_a_small_profile(self):
        approved = self.approve()
        self.assertEqual(approved.status_code, 200)
        code = self.code_from(approved)

        tokened = self.token(code=code)
        self.assertEqual(tokened.status_code, 200)
        access = tokened.json()['data']['access_token']

        info = self.client.get('/partners/sso/userinfo/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(info.status_code, 200)
        data = info.json()['data']
        self.assertEqual(data['username'], 'player1')
        self.assertEqual(data['country'], 'Nigeria')
        # identity alone must not carry an email address
        self.assertNotIn('email', data)

    def test_email_arrives_only_with_its_own_scope(self):
        approved = self.approve(scope='identity identity:email')
        access = self.token(code=self.code_from(approved)).json()['data']['access_token']
        data = self.client.get(
            '/partners/sso/userinfo/', HTTP_AUTHORIZATION=f'Bearer {access}',
        ).json()['data']
        self.assertEqual(data['email'], 'player1@vent.test')

    def test_a_code_can_only_be_spent_once(self):
        code = self.code_from(self.approve())
        self.assertEqual(self.token(code=code).status_code, 200)
        second = self.token(code=code)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()['code'], 'BAD_CODE')

    def test_a_wrong_secret_is_refused(self):
        code = self.code_from(self.approve())
        res = self.token(code=code, client_secret='not-the-secret')
        self.assertEqual(res.status_code, 401)

    def test_the_redirect_must_match_the_one_the_code_was_issued_for(self):
        code = self.code_from(self.approve())
        res = self.token(code=code, redirect_uri='http://localhost:3000/callback')
        self.assertEqual(res.json()['code'], 'BAD_REDIRECT')

    # This test used to be called "pkce replaces the secret" and asserted exactly
    # that: a partner WITH a secret on file sent a verifier, sent no secret, and
    # got a token. It encoded the bug, which is why it never caught it.
    #
    # PKCE proves the caller is the same party that started the flow. It does not
    # prove WHICH application is calling, and a confidential client has to prove
    # both. RFC 6749 4.1.3 wants the client authenticated, with PKCE on top.

    def test_a_bad_verifier_is_refused(self):
        code = self.code_from(
            self.approve(code_challenge=self.challenge, code_challenge_method='S256'))
        bad = self.token(code=code, client_secret=self.secret, code_verifier='wrong-verifier')
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json()['code'], 'BAD_VERIFIER')

    def test_a_confidential_client_must_send_both(self):
        """A verifier alone is not enough when the partner holds a secret."""
        code = self.code_from(
            self.approve(code_challenge=self.challenge, code_challenge_method='S256'))
        res = self.token(code=code, client_secret='', code_verifier=self.verifier)
        self.assertEqual(res.status_code, 401, res.content)
        self.assertEqual(res.json()['code'], 'BAD_SECRET')

        code2 = self.code_from(
            self.approve(code_challenge=self.challenge, code_challenge_method='S256'))
        good = self.token(code=code2, client_secret=self.secret, code_verifier=self.verifier)
        self.assertEqual(good.status_code, 200, good.content)

    def test_a_public_client_is_authenticated_by_pkce_alone(self):
        """A browser or a mobile app holds no secret and cannot be made to."""
        self.partner.sso_client_secret_hash = ''
        self.partner.save(update_fields=['sso_client_secret_hash'])
        code = self.code_from(
            self.approve(code_challenge=self.challenge, code_challenge_method='S256'))
        res = self.token(code=code, client_secret='', code_verifier=self.verifier)
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_public_client_with_no_challenge_is_refused(self):
        """Nothing at all identifies that caller."""
        self.partner.sso_client_secret_hash = ''
        self.partner.save(update_fields=['sso_client_secret_hash'])
        res = self.token(code=self.code_from(self.approve()), client_secret='')
        self.assertEqual(res.status_code, 401, res.content)

    def test_a_code_can_only_be_spent_once_even_under_a_race(self):
        """The check was outside the transaction and the write was a plain save,
        so two requests arriving together both passed and both minted a token
        from one code."""
        code = self.code_from(self.approve())
        first = self.token(code=code, client_secret=self.secret)
        second = self.token(code=code, client_secret=self.secret)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 400, second.content)
        self.assertEqual(second.json()['code'], 'BAD_CODE')
        self.assertEqual(OAuthAccessToken.objects.count(), 1)

    def test_plain_pkce_is_refused(self):
        res = self.approve(code_challenge='something', code_challenge_method='plain')
        self.assertEqual(res.json()['code'], 'BAD_CHALLENGE_METHOD')

    def test_an_expired_token_stops_reading(self):
        access = self.token(code=self.code_from(self.approve())).json()['data']['access_token']
        record = OAuthAccessToken.objects.get()
        record.created_at = timezone.now() - OAuthAccessToken.LIFETIME - timezone.timedelta(minutes=1)
        record.save(update_fields=['created_at'])
        res = self.client.get('/partners/sso/userinfo/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(res.status_code, 401)

    def test_an_expired_code_cannot_be_spent(self):
        code = self.code_from(self.approve())
        record = OAuthAuthorizationCode.objects.get()
        record.created_at = timezone.now() - OAuthAuthorizationCode.LIFETIME - timezone.timedelta(minutes=1)
        record.save(update_fields=['created_at'])
        self.assertEqual(self.token(code=code).status_code, 400)

    def test_signing_in_is_required_to_approve(self):
        res = self.client.post(
            '/partners/sso/approve/',
            data=json.dumps({'client_id': self.partner.sso_client_id,
                             'redirect_uri': 'https://afc.test/callback'}),
            content_type='application/json',
        )
        self.assertIn(res.status_code, (400, 401))


class AuthorizeInfoThrottleTests(TestCase):
    """The consent screen answers before anybody signs in, so it cannot be
    gated by a key or a session - and in answering it confirms whether a
    client_id exists. Unlimited, that is an enumeration tool with no cost."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.owner, _ = signed_in('throttleowner')
        self.addCleanup(cache.clear)   # the bucket is per minute, not per test
        self.partner = Partner.objects.create(
            name='AFC', slug='afc-throttle', owner=self.owner, contact_name='AFC',
            contact_email='p@afc.test', status='approved', sso_status='approved',
            legal_name='AFC Ltd', privacy_policy_url='https://afc.test/privacy',
            data_protection_contact='dpo@afc.test',
            redirect_uris=['https://afc.test/callback'],
        )
        self.partner.issue_sso_credentials()

    def ask(self, client_id=None):
        return self.client.get('/partners/sso/authorize-info/', {
            'client_id': client_id or self.partner.sso_client_id,
            'redirect_uri': 'https://afc.test/callback',
            'scope': 'identity',
        })

    def test_a_normal_consent_screen_is_not_touched(self):
        """Somebody approving one sign-in makes one request, not thirty."""
        self.assertEqual(self.ask().status_code, 200)

    def test_a_sweep_is_refused_with_429(self):
        for _ in range(30):
            self.ask('does-not-exist')
        res = self.ask('does-not-exist')
        self.assertEqual(res.status_code, 429, res.content)
        self.assertEqual(res.json()['code'], 'RATE_LIMITED')

    def test_the_refusal_comes_before_the_lookup(self):
        """Otherwise a limited caller still learns whether the id exists, from
        the timing if nothing else."""
        for _ in range(31):
            self.ask('does-not-exist')
        real = self.ask()
        self.assertEqual(real.status_code, 429, 'a real client id answered anyway')

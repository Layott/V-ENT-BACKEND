"""/auth/social-auth/ signs in whoever Google says, never whoever the body says.

Owner rule R58, 17 September 2026. The endpoint used to issue a session on an
email and a provider id typed into the request. Now it verifies the Google
id_token NextAuth holds, against this server's client id, and takes the
identity from the token.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Users, UserWallet

VERIFY = 'vent_auth.views_social.google_id_token.verify_oauth2_token'
CLAIMS = {
    'iss': 'https://accounts.google.com', 'sub': '104857609233123',
    'email': 'ada@example.com', 'email_verified': True,
    'name': 'Ada Obi', 'picture': '',
}


@override_settings(GOOGLE_CLIENT_ID='client-id.apps.googleusercontent.com')
class SocialAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def post(self, **body):
        return self.client.post('/auth/social-auth/', body, format='json')

    def test_the_body_alone_signs_nobody_in(self):
        """The fault this file exists for: email plus provider id used to be
        a session."""
        Users.objects.create(username='ada', email='ada@example.com',
                             signup_type='google', provider_id='104857609233123',
                             is_active=True)
        res = self.post(provider='google', provider_id='104857609233123',
                        email='ada@example.com')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'SOCIAL_TOKEN_REQUIRED')
        self.assertNotIn('session_token', str(res.data))

    def test_a_token_google_rejects_is_refused(self):
        with patch(VERIFY, side_effect=ValueError('Wrong audience')):
            res = self.post(provider='google', id_token='not-real')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data['code'], 'SOCIAL_TOKEN_INVALID')

    def test_google_unreachable_is_not_a_no(self):
        with patch(VERIFY, side_effect=OSError('dns')):
            res = self.post(provider='google', id_token='t')
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.data['code'], 'SOCIAL_VERIFY_UNAVAILABLE')

    def test_an_unverified_email_is_refused(self):
        with patch(VERIFY, return_value=dict(CLAIMS, email_verified=False)):
            res = self.post(provider='google', id_token='t')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data['code'], 'SOCIAL_EMAIL_UNVERIFIED')

    def test_a_good_token_makes_the_account_from_the_token_not_the_body(self):
        with patch(VERIFY, return_value=CLAIMS) as verify:
            res = self.post(provider='google', id_token='t',
                            provider_id='somebody-else', email='mallory@example.com',
                            full_name='Ada Obi')
        self.assertEqual(res.status_code, 201, res.data)
        verify.assert_called_once()
        self.assertEqual(verify.call_args[0][0], 't')
        self.assertEqual(verify.call_args[0][2], 'client-id.apps.googleusercontent.com')
        user = Users.objects.get(email='ada@example.com')
        self.assertEqual(user.provider_id, '104857609233123')
        self.assertEqual(user.signup_type, 'google')
        self.assertTrue(user.is_active)
        self.assertFalse(Users.objects.filter(email='mallory@example.com').exists())
        self.assertEqual(res.data['data']['session_token'], user.login_session_token)

    def test_a_good_token_signs_an_existing_google_account_in(self):
        user = Users.objects.create(username='ada', email='ada@example.com',
                                    signup_type='google', provider_id='104857609233123',
                                    is_active=True)
        UserWallet.objects.create(user_wallet_id='ada_w', user=user)
        with patch(VERIFY, return_value=CLAIMS):
            res = self.post(provider='google', id_token='t')
        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        self.assertEqual(res.data['data']['session_token'], user.login_session_token)
        self.assertEqual(res.data['data']['username'], 'ada')

    def test_a_password_account_with_the_same_verified_email_is_linked(self):
        user = Users.objects.create(username='ada', email='ada@example.com',
                                    signup_type='normal', is_active=False)
        with patch(VERIFY, return_value=CLAIMS):
            res = self.post(provider='google', id_token='t')
        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        self.assertEqual(user.provider_id, '104857609233123')
        self.assertTrue(user.is_active)
        self.assertEqual(Users.objects.filter(email='ada@example.com').count(), 1)

    def test_only_google(self):
        with patch(VERIFY, return_value=CLAIMS):
            res = self.post(provider='facebook', id_token='t')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PROVIDER_NOT_SUPPORTED')


@override_settings(GOOGLE_CLIENT_ID='')
class NoClientIdTests(TestCase):
    def test_no_client_id_refuses_rather_than_trusts(self):
        """A sign-in door with no key on it is a door anybody can walk
        through, so with nothing to verify against it stays shut."""
        with patch(VERIFY, return_value=CLAIMS) as verify:
            res = APIClient().post('/auth/social-auth/',
                                   {'provider': 'google', 'id_token': 't'},
                                   format='json')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data['code'], 'SOCIAL_AUTH_NOT_CONFIGURED')
        verify.assert_not_called()


class PasswordLoginOnAGoogleAccountTests(TestCase):
    """Found by the IDOR cases signing in as a fixture with no password: the
    auth backend called check_password on a null hash and the login endpoint
    was a 500 for the email of every Google-made account."""

    def test_a_password_against_an_account_with_none_is_refused_not_a_crash(self):
        Users.objects.create(username='gina', email='gina@example.com',
                             signup_type='google', provider_id='9', is_active=True,
                             password=None)
        res = APIClient().post('/auth/login/', {
            'username_or_email': 'gina@example.com', 'password': 'anything',
        }, format='json')
        self.assertNotEqual(res.status_code, 500)
        self.assertIn(res.status_code, (400, 401))
        self.assertNotIn('session_token', str(res.data))

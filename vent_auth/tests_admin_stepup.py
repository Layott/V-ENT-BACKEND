"""Reaching the admin console from a session that is already signed in.

An admin who signed in on the site was sent to /admin, bounced to /admin/login,
and asked for the username and password they had typed a moment earlier. The
session already carried that proof, so the second prompt only added friction.

What must NOT change is the second factor. These tests exist to hold that line:
the step-up door issues a pending token and never a session token, and it
refuses anybody whose session is not a staff account with a role.
"""
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from . import totp as totp_lib
from .models import AdminTOTP, Users


def make_user(**overrides):
    values = dict(
        username='an_admin',
        email='an_admin@example.com',
        is_staff=True,
        admin_role='super_admin',
        login_session_token='a-live-session-token',
    )
    values.update(overrides)
    return Users.objects.create(**values)


class AdminStepUpTests(TestCase):
    url = None

    def setUp(self):
        self.url = reverse('admin_step_up')

    def post(self, token=None):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        return self.client.post(self.url, content_type='application/json', **headers)

    def test_a_live_admin_session_gets_a_pending_token_and_no_session_token(self):
        make_user()

        response = self.post('a-live-session-token')

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertTrue(data['requires_2fa'])
        self.assertTrue(data['pending_token'])
        # The whole point: this door does not let anybody in on its own.
        self.assertNotIn('session_token', data)
        self.assertNotIn('token', data)

    def test_first_time_admin_is_given_the_secret_and_the_provisioning_uri(self):
        make_user()

        data = self.post('a-live-session-token').json()['data']

        # Without the URI there is no QR to scan, which is the whole enrolment.
        self.assertTrue(data['enrollment_required'])
        self.assertTrue(data['secret'])
        self.assertIn('otpauth://', data['provisioning_uri'])

    def test_an_enrolled_admin_is_not_handed_the_secret_again(self):
        user = make_user()
        AdminTOTP.objects.create(user=user, secret='ABCDEFGHIJKLMNOP', confirmed=True)

        data = self.post('a-live-session-token').json()['data']

        self.assertNotIn('secret', data)
        self.assertNotIn('enrollment_required', data)

    def test_no_session_is_refused(self):
        make_user()

        self.assertEqual(self.post().status_code, 400)
        self.assertEqual(self.post('not-a-real-token').status_code, 401)

    def test_a_signed_in_ordinary_player_is_refused(self):
        make_user(is_staff=False, admin_role=None)

        response = self.post('a-live-session-token')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'NOT_AN_ADMIN')

    def test_staff_without_a_role_is_refused_with_the_reason(self):
        make_user(admin_role=None)

        response = self.post('a-live-session-token')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'ACCOUNT_NO_ADMIN_ROLE')


class AdminEntryKeepsTheSiteSessionTests(TestCase):
    """Opening the console must not sign you out of the website.

    `admin_2fa_verify` used to mint a fresh token into `login_session_token` -
    the single field the website session reads - so the moment the TOTP code was
    accepted, every authed call from the site answered 401 and the admin had to
    sign in again. The step-up door alone did not fix that; it only removed the
    password prompt.

    One session per user stays deliberate. When the pending token came from a
    live session, that session is the proof, so the console reuses the token the
    person already holds rather than rotating it.
    """

    def setUp(self):
        self.user = make_user()
        AdminTOTP.objects.create(user=self.user, secret=totp_lib.generate_secret(),
                                 confirmed=True)

    def _code(self):
        secret = AdminTOTP.objects.get(user=self.user).secret
        return totp_lib._code_for_step(secret, totp_lib.current_step())

    def _verify(self, pending):
        return self.client.post(
            reverse('admin_2fa_verify'),
            {'pending_token': pending, 'code': self._code()},
            content_type='application/json')

    def test_step_up_entry_leaves_the_site_session_alone(self):
        step_up = self.client.post(
            reverse('admin_step_up'), content_type='application/json',
            HTTP_AUTHORIZATION='Bearer a-live-session-token')
        pending = step_up.json()['data']['pending_token']

        response = self._verify(pending)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        # The token the website is still holding must keep working.
        self.assertEqual(self.user.login_session_token, 'a-live-session-token')
        self.assertEqual(response.json()['data']['session_token'], 'a-live-session-token')

    def test_password_entry_still_mints_a_fresh_session(self):
        self.user.set_password('a-real-password')
        self.user.save()

        login = self.client.post(
            reverse('admin_login'),
            {'email': self.user.username, 'password': 'a-real-password'},
            content_type='application/json')
        pending = login.json()['data']['pending_token']

        response = self._verify(pending)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        # No session was being preserved here, so a new one is correct.
        self.assertNotEqual(self.user.login_session_token, 'a-live-session-token')
        self.assertTrue(self.user.login_session_token)


class FounderBadgeReachesItsOwnerTests(TestCase):
    """A founder must see their own badge.

    `get_user_informations` feeds the owner's view of their profile and the
    public profile view feeds everybody else's. Only the second carried
    `founder_badge`, so the mark appeared to every visitor and never to the
    person who earned it - which is exactly how it was reported.
    """

    def test_the_owner_payload_carries_the_founder_flag(self):
        user = make_user(username='a_founder', email='a_founder@example.com',
                         is_staff=False, admin_role=None,
                         login_session_token='founder-session')
        user.is_founder = True
        user.show_founder_badge = True
        user.login_session_created_at = timezone.now()
        user.save(update_fields=['is_founder', 'show_founder_badge',
                                 'login_session_created_at'])

        response = self.client.get(
            reverse('get_user_informations'),
            HTTP_AUTHORIZATION='Bearer founder-session')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['data']['founder_badge'])

    def test_a_founder_who_switched_it_off_does_not_get_it(self):
        user = make_user(username='shy_founder', email='shy@example.com',
                         is_staff=False, admin_role=None,
                         login_session_token='shy-session')
        user.is_founder = True
        user.show_founder_badge = False
        user.login_session_created_at = timezone.now()
        user.save(update_fields=['is_founder', 'show_founder_badge',
                                 'login_session_created_at'])

        response = self.client.get(
            reverse('get_user_informations'),
            HTTP_AUTHORIZATION='Bearer shy-session')

        self.assertFalse(response.json()['data']['founder_badge'])

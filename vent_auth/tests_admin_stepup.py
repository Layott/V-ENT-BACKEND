"""Reaching the admin console from a session that is already signed in.

An admin who signed in on the site was sent to /admin, bounced to /admin/login,
and asked for the username and password they had typed a moment earlier. The
session already carried that proof, so the second prompt only added friction.

What must NOT change is the second factor. These tests exist to hold that line:
the step-up door issues a pending token and never a session token, and it
refuses anybody whose session is not a staff account with a role.
"""
from django.test import TestCase
from django.urls import reverse

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

"""A member can actually turn two-factor on, and it actually protects them.

CEO, 7 September 2026: "please fix the 2fa."

The Security panel's switch wrote `two_factor_enabled: true` into a settings
JSON blob. Nothing generated a secret, the QR in the modal was decorative, no
code was ever checked and no `UserTOTP` row was created. Reading the flag from
real enrolment - which is the correct thing to read - then made the switch flip
straight back to Disabled, so the half that worked looked like the broken one.

These tests are written end to end on purpose. A test that only asserts the
endpoint answers 200 would have passed against the old fake toggle too. What
matters is the CONSEQUENCE: after confirming, signing in with a password alone
stops being enough.
"""
import uuid

from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from . import totp as totp_lib
from .models import UserTOTP, Users


def a_user(name='tf', password='Str0ngPass!23', admin=False):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.set_password(password)
    if admin:
        u.is_staff = True
        u.admin_role = 'super'
    u.login_session_created_at = tz.now()
    u.save()
    return u


def code_for(secret):
    return totp_lib._code_for_step(secret, totp_lib.current_step())


class EnrolmentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = a_user()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.user.login_session_token}

    def start(self):
        return self.client.post('/auth/2fa/start/', {}, format='json', **self.auth)

    def confirm(self, code):
        return self.client.post('/auth/2fa/confirm/', {'code': code},
                                format='json', **self.auth)

    # ------------------------------------------------------------- starting

    def test_starting_creates_an_unconfirmed_factor_with_a_secret(self):
        res = self.start()
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertTrue(data['secret'])
        self.assertIn('otpauth://', data['provisioning_uri'])
        self.assertIs(data['confirmed'], False)
        factor = UserTOTP.objects.get(user=self.user)
        self.assertFalse(factor.confirmed)

    def test_an_unconfirmed_factor_does_not_yet_guard_anything(self):
        """Abandoning setup halfway must not lock somebody out.

        This is why start and confirm are separate calls at all.
        """
        from . import login_2fa
        self.start()
        self.assertFalse(login_2fa.challenge_required(self.user))

    def test_starting_twice_returns_the_same_secret(self):
        """Somebody who closed the modal and reopened it is not told their
        authenticator is suddenly wrong."""
        first = self.start().json()['data']['secret']
        second = self.start().json()['data']['secret']
        self.assertEqual(first, second)

    def test_a_stranger_cannot_start_enrolment(self):
        res = self.client.post('/auth/2fa/start/', {}, format='json')
        self.assertEqual(res.status_code, 401)

    # ------------------------------------------------------------ confirming

    def test_a_real_code_switches_it_on(self):
        secret = self.start().json()['data']['secret']
        res = self.confirm(code_for(secret))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIs(res.json()['data']['confirmed'], True)
        self.assertTrue(UserTOTP.objects.get(user=self.user).confirmed)

    def test_a_wrong_code_does_not(self):
        self.start()
        res = self.confirm('000000')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(UserTOTP.objects.get(user=self.user).confirmed)

    def test_confirming_without_starting_is_refused_by_name(self):
        res = self.confirm('123456')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_NOT_STARTED')

    def test_an_empty_code_is_refused_by_name(self):
        self.start()
        res = self.confirm('')
        self.assertEqual(res.json()['code'], 'CODE_REQUIRED')

    def test_starting_again_once_confirmed_is_refused(self):
        """Pressing the button out of curiosity must not invalidate the
        authenticator somebody is relying on."""
        secret = self.start().json()['data']['secret']
        self.confirm(code_for(secret))
        res = self.start()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_ALREADY_ON')
        self.assertEqual(UserTOTP.objects.get(user=self.user).secret, secret)

    # -------------------------------------------------- what it actually does

    def test_after_confirming_a_password_alone_no_longer_signs_you_in(self):
        """The consequence, which is the only thing that proves any of it.

        The old fake toggle would have passed every test above that only
        checked a 200.
        """
        secret = self.start().json()['data']['secret']
        self.confirm(code_for(secret))

        res = self.client.post('/auth/login/',
                               {'username_or_email': self.user.email,
                                'password': 'Str0ngPass!23'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        body = res.json()
        payload = body.get('data', body)
        self.assertTrue(payload.get('requires_2fa'),
                        'signing in still handed out a session without a code')
        self.assertNotIn('session_token', payload,
                         'a session was issued before any code was checked')

    def test_before_confirming_a_password_alone_is_still_enough(self):
        self.start()
        res = self.client.post('/auth/login/',
                               {'username_or_email': self.user.email,
                                'password': 'Str0ngPass!23'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        body = res.json()
        payload = body.get('data', body)
        self.assertFalse(payload.get('requires_2fa'))

    # --------------------------------------------------------------- status

    def test_status_reports_real_enrolment_not_a_stored_flag(self):
        self.assertIs(self.client.get('/auth/2fa/status/', **self.auth)
                      .json()['data']['enabled'], False)
        secret = self.start().json()['data']['secret']
        self.assertIs(self.client.get('/auth/2fa/status/', **self.auth)
                      .json()['data']['enabled'], False)
        self.confirm(code_for(secret))
        self.assertIs(self.client.get('/auth/2fa/status/', **self.auth)
                      .json()['data']['enabled'], True)


class DisableTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = a_user('off')
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.user.login_session_token}
        self.secret = self.client.post(
            '/auth/2fa/start/', {}, format='json', **self.auth
        ).json()['data']['secret']
        self.client.post('/auth/2fa/confirm/', {'code': code_for(self.secret)},
                         format='json', **self.auth)

    def test_a_current_code_turns_it_off(self):
        # A fresh step, because confirming burned the current one.
        step = totp_lib.current_step() + 1
        res = self.client.post(
            '/auth/2fa/disable/',
            {'code': totp_lib._code_for_step(self.secret, step)},
            format='json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(UserTOTP.objects.filter(user=self.user).exists())

    def test_no_code_will_not_turn_it_off(self):
        """A stolen session must not be enough to remove the protection that
        exists precisely because sessions get stolen."""
        res = self.client.post('/auth/2fa/disable/', {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CODE_REQUIRED')
        self.assertTrue(UserTOTP.objects.filter(user=self.user).exists())

    def test_a_wrong_code_will_not(self):
        res = self.client.post('/auth/2fa/disable/', {'code': '000000'},
                               format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(UserTOTP.objects.filter(user=self.user).exists())

    def test_a_stranger_cannot(self):
        res = self.client.post('/auth/2fa/disable/', {'code': '123456'},
                               format='json')
        self.assertEqual(res.status_code, 401)
        self.assertTrue(UserTOTP.objects.filter(user=self.user).exists())


class AdminCannotDisableTests(TestCase):
    """A console account keeps its second factor.

    Not a policy flourish: `challenge_required` forces a code for an admin
    whatever the row says, so deleting the factor would mean their next sign-in
    silently enrols a NEW one and shows them the secret. Refused by name rather
    than half-working.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = a_user('adm', admin=True)
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.user.login_session_token}

    def test_refused_by_name(self):
        res = self.client.post('/auth/2fa/disable/', {'code': '123456'},
                               format='json', **self.auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_REQUIRED_FOR_ADMIN')

    def test_status_says_it_is_required_so_the_screen_can_say_so(self):
        self.assertIs(self.client.get('/auth/2fa/status/', **self.auth)
                      .json()['data']['required'], True)

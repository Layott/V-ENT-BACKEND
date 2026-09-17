"""A two-factor code locks after ten wrong tries.

Owner rule R59, 17 September 2026. `last_used_step` stopped a code being
replayed; nothing stopped a million of them being tried. `login_2fa.spend_code`
is the one function every code goes through (sign-in, the settings page, a
withdrawal), and it now counts.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from vent_auth import login_2fa, totp as totp_lib
from vent_auth.models import UserTOTP, Users


def signed_in(username):
    user = Users.objects.create(
        username=username, email=f'{username}@vent.test', full_name=username.title(),
        login_session_token=f'tk{username}'[:16], login_session_created_at=timezone.now(),
        is_active=True, country='Nigeria', state='Lagos',
    )
    return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}


def right_code(factor, offset=0):
    return totp_lib._code_for_step(factor.secret, totp_lib.current_step() + offset)


class SpendCodeLockTests(TestCase):
    def setUp(self):
        self.user, self.auth = signed_in('lockme')
        self.factor = UserTOTP.objects.create(
            user=self.user, secret=totp_lib.generate_secret(), confirmed=True)

    def test_wrong_codes_count_and_the_tenth_locks(self):
        for n in range(1, 10):
            ok, problem = login_2fa.spend_code(self.user, '000000')
            self.assertFalse(ok)
            self.assertEqual(problem, 'BAD_CODE')
            self.factor.refresh_from_db()
            self.assertEqual(self.factor.code_failures, n)
        ok, problem = login_2fa.spend_code(self.user, '000000')
        self.assertFalse(ok)
        self.assertEqual(problem, 'TWO_FACTOR_LOCKED')
        self.factor.refresh_from_db()
        self.assertIsNotNone(self.factor.code_locked_until)

    def test_a_locked_factor_refuses_the_right_code_too(self):
        for _ in range(10):
            login_2fa.spend_code(self.user, '000000')
        self.factor.refresh_from_db()
        ok, problem = login_2fa.spend_code(self.user, right_code(self.factor))
        self.assertFalse(ok)
        self.assertEqual(problem, 'TWO_FACTOR_LOCKED')
        self.assertGreaterEqual(login_2fa.code_lock_minutes_left(self.factor), 1)

    def test_the_clock_passing_unlocks_it(self):
        for _ in range(10):
            login_2fa.spend_code(self.user, '000000')
        later = timezone.now() + timedelta(minutes=login_2fa.CODE_LOCK_MINUTES + 1)
        with patch('vent_auth.login_2fa.timezone.now', return_value=later):
            self.factor.refresh_from_db()
            ok, problem = login_2fa.spend_code(self.user, right_code(self.factor))
        self.assertTrue(ok, problem)
        self.factor.refresh_from_db()
        self.assertIsNone(self.factor.code_locked_until)
        self.assertEqual(self.factor.code_failures, 0)

    def test_a_right_code_clears_the_count(self):
        for _ in range(4):
            login_2fa.spend_code(self.user, '000000')
        self.factor.refresh_from_db()
        ok, _ = login_2fa.spend_code(self.user, right_code(self.factor))
        self.assertTrue(ok)
        self.factor.refresh_from_db()
        self.assertEqual(self.factor.code_failures, 0)

    def test_the_settings_door_says_locked(self):
        for _ in range(10):
            login_2fa.spend_code(self.user, '000000')
        self.factor.refresh_from_db()
        res = self.client.post('/auth/2fa/disable/',
                               data=json.dumps({'code': right_code(self.factor)}),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_LOCKED')
        self.assertTrue(UserTOTP.objects.filter(user=self.user).exists())

    def test_the_sign_in_door_says_locked(self):
        for _ in range(10):
            login_2fa.spend_code(self.user, '000000')
        pending = login_2fa.pending_payload(self.user)
        res = self.client.post('/auth/login/2fa/verify/',
                               data=json.dumps({'pending_token': pending['pending_token'],
                                                'code': '000000'}),
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_LOCKED')

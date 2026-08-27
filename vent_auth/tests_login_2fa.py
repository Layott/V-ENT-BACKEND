"""The second factor at the front door, and what the console does with it.

The console used to have a sign-in of its own: an admin signed in to the site,
then signed in again with a password and a code. The CEO's instruction on
2026-08-27 was to take that second door out - and to make the second factor
compulsory at the ordinary sign-in instead, for every admin, whether or not they
ever turned it on.

The thing worth testing is that this did not quietly become "no second factor at
all". So the tests below are mostly about refusal:

  * an admin who signs in with a password alone gets a session, and that session
    opens nothing in the console
  * an admin who has never set up an authenticator cannot get past the sign-in
  * a member who turned two-factor on is asked for it, which the login never did
    before this change
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from . import totp as totp_lib
from .models import UserTOTP, Users


def a_user(admin_role=None, staff=False, password='pw-that-is-long'):
    user = Users.objects.create(
        username='u_%s' % uuid.uuid4().hex[:8],
        email='u_%s@vent.test' % uuid.uuid4().hex[:8],
        is_staff=staff,
        admin_role=admin_role,
        is_active=True,
    )
    user.set_password(password)
    user.save()
    return user


def with_authenticator(user, confirmed=True):
    return UserTOTP.objects.create(
        user=user, secret=totp_lib.generate_secret(), confirmed=confirmed)


def code_for(factor):
    return totp_lib._code_for_step(factor.secret, totp_lib.current_step())


class FrontDoorTests(TestCase):
    def sign_in(self, user, password='pw-that-is-long'):
        return self.client.post('/auth/login/', data={
            'username_or_email': user.username, 'password': password,
        }, content_type='application/json')

    def test_a_member_with_no_second_factor_signs_straight_in(self):
        res = self.sign_in(a_user())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json().get('session_token'))

    def test_a_member_who_turned_it_on_is_asked_for_it(self):
        """The Security page promised this and the login ignored it."""
        user = a_user()
        with_authenticator(user)
        res = self.sign_in(user)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body['data']['requires_2fa'])
        self.assertNotIn('session_token', body)

    def test_an_admin_is_always_asked_even_having_never_turned_it_on(self):
        user = a_user(admin_role='super_admin', staff=True)
        body = self.sign_in(user).json()
        self.assertTrue(body['data']['requires_2fa'])
        self.assertTrue(body['data']['enrollment_required'])
        self.assertTrue(body['data']['secret'])
        self.assertEqual(body['data']['enrollment_reason'], 'admin')

    def test_a_new_admin_cannot_get_a_session_without_finishing_enrolment(self):
        """Compulsory means there is no way past it, not a screen to dismiss."""
        user = a_user(admin_role='mod_admin', staff=True)
        pending = self.sign_in(user).json()['data']['pending_token']
        res = self.client.post('/auth/login/2fa/verify/', data={
            'pending_token': pending, 'code': '000000',
        }, content_type='application/json')
        self.assertEqual(res.status_code, 401, res.content)
        user.refresh_from_db()
        self.assertIsNone(user.login_session_2fa_at)

    def test_the_code_completes_the_sign_in_and_confirms_the_enrolment(self):
        user = a_user(admin_role='super_admin', staff=True)
        pending = self.sign_in(user).json()['data']['pending_token']
        factor = UserTOTP.objects.get(user=user)
        self.assertFalse(factor.confirmed)

        res = self.client.post('/auth/login/2fa/verify/', data={
            'pending_token': pending, 'code': code_for(factor),
        }, content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['session_token'])

        factor.refresh_from_db()
        self.assertTrue(factor.confirmed)
        user.refresh_from_db()
        self.assertIsNotNone(user.login_session_2fa_at)

    def test_a_code_cannot_be_spent_twice(self):
        user = a_user()
        factor = with_authenticator(user)
        code = code_for(factor)
        pending = self.sign_in(user).json()['data']['pending_token']
        first = self.client.post('/auth/login/2fa/verify/', data={
            'pending_token': pending, 'code': code,
        }, content_type='application/json')
        self.assertEqual(first.status_code, 200, first.content)

        pending2 = self.sign_in(user).json()['data']['pending_token']
        second = self.client.post('/auth/login/2fa/verify/', data={
            'pending_token': pending2, 'code': code,
        }, content_type='application/json')
        self.assertEqual(second.status_code, 401, second.content)

    def test_a_forged_pending_token_is_refused(self):
        res = self.client.post('/auth/login/2fa/verify/', data={
            'pending_token': 'not-a-real-token', 'code': '123456',
        }, content_type='application/json')
        self.assertEqual(res.status_code, 401, res.content)


class ConsoleReadsTheSessionTests(TestCase):
    """What `resolve_admin` will and will not accept now that it reads the site
    session. This is the half that could have silently become an open door."""

    def setUp(self):
        self.user = a_user(admin_role='super_admin', staff=True)
        self.factor = with_authenticator(self.user)

    def signed_in_with_code(self):
        res = self.client.post('/auth/login/', data={
            'username_or_email': self.user.username, 'password': 'pw-that-is-long',
        }, content_type='application/json')
        pending = res.json()['data']['pending_token']
        done = self.client.post('/auth/login/2fa/verify/', data={
            'pending_token': pending, 'code': code_for(self.factor),
        }, content_type='application/json')
        return done.json()['session_token']

    def test_a_session_that_passed_the_code_opens_the_console(self):
        token = self.signed_in_with_code()
        res = self.client.get('/auth/admin/me/', HTTP_AUTHORIZATION='Bearer %s' % token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['admin_role'], 'super_admin')

    def test_a_password_only_session_opens_nothing(self):
        """The whole point of the change surviving. An admin who somehow holds a
        session that never met the challenge is not an admin to this code."""
        self.user.login_session_token = 'password-only-token'
        self.user.login_session_created_at = timezone.now()
        self.user.login_session_2fa_at = None
        self.user.save()

        res = self.client.get('/auth/admin/me/',
                              HTTP_AUTHORIZATION='Bearer password-only-token')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_REQUIRED')

    def test_signing_in_again_without_a_code_drops_the_admin_session(self):
        """A member's own factor can be switched off. If that happened, the next
        password-only sign-in must not inherit yesterday's admin session."""
        self.signed_in_with_code()
        UserTOTP.objects.filter(user=self.user).delete()
        self.user.is_staff = False
        self.user.admin_role = None
        self.user.save(update_fields=['is_staff', 'admin_role'])

        res = self.client.post('/auth/login/', data={
            'username_or_email': self.user.username, 'password': 'pw-that-is-long',
        }, content_type='application/json')
        token = res.json()['session_token']

        self.user.refresh_from_db()
        self.assertIsNone(self.user.login_session_2fa_at)
        me = self.client.get('/auth/admin/me/', HTTP_AUTHORIZATION='Bearer %s' % token)
        self.assertIn(me.status_code, (401, 403), me.content)

    def test_a_staff_account_with_no_role_is_told_why(self):
        no_role = a_user(staff=True)
        no_role.login_session_token = 'role-less'
        no_role.login_session_created_at = timezone.now()
        no_role.login_session_2fa_at = timezone.now()
        no_role.save()
        res = self.client.get('/auth/admin/me/', HTTP_AUTHORIZATION='Bearer role-less')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'ACCOUNT_NO_ADMIN_ROLE')

    def test_the_retired_console_doors_are_gone(self):
        for path in ('/auth/admin/login/', '/auth/admin/step-up/',
                     '/auth/admin/2fa/verify/'):
            self.assertEqual(self.client.post(path).status_code, 404, path)

"""Settings that do something.

Each of these was a control that stored a value nothing read, or a button that
produced nothing at all. The tests are the difference between a preference and a
promise.
"""
import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from vent_auth import totp as totp_lib
from vent_auth.models import UserSetting, UserTOTP, Users


def signed_in(username, **extra):
    user = Users.objects.create(
        username=username, email=f'{username}@vent.test', full_name=username.title(),
        login_session_token=f'tk{username}'[:16], login_session_created_at=timezone.now(),
        is_active=True, country='Nigeria', state='Lagos', **extra,
    )
    return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}


class TwoFactorTests(TestCase):
    def setUp(self):
        self.user, self.auth = signed_in('twofactor')

    def post(self, path, body=None):
        return self.client.post(path, data=json.dumps(body or {}),
                                content_type='application/json', **self.auth)

    def test_it_starts_off(self):
        res = self.client.get('/setting/2fa/status/', **self.auth)
        self.assertFalse(res.json()['data']['enabled'])

    def test_beginning_hands_back_something_an_app_can_scan(self):
        res = self.post('/setting/2fa/begin/')
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertTrue(data['secret'])
        self.assertIn('otpauth://totp/', data['otpauth_url'])

    def test_a_wrong_code_does_not_switch_it_on(self):
        self.post('/setting/2fa/begin/')
        res = self.post('/setting/2fa/confirm/', {'code': '000000'})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(UserTOTP.objects.get(user=self.user).confirmed)

    def test_a_real_code_switches_it_on(self):
        self.post('/setting/2fa/begin/')
        secret = UserTOTP.objects.get(user=self.user).secret
        code = totp_lib._code_for_step(secret, totp_lib.current_step())
        res = self.post('/setting/2fa/confirm/', {'code': code})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(UserTOTP.objects.get(user=self.user).confirmed)

        status_res = self.client.get('/setting/2fa/status/', **self.auth)
        self.assertTrue(status_res.json()['data']['enabled'])

    def test_the_same_code_cannot_be_replayed(self):
        self.post('/setting/2fa/begin/')
        secret = UserTOTP.objects.get(user=self.user).secret
        code = totp_lib._code_for_step(secret, totp_lib.current_step())
        self.post('/setting/2fa/confirm/', {'code': code})
        again = self.post('/setting/2fa/disable/', {'code': code})
        self.assertEqual(again.status_code, 400)

    def test_turning_it_off_needs_a_current_code(self):
        self.post('/setting/2fa/begin/')
        secret = UserTOTP.objects.get(user=self.user).secret
        self.post('/setting/2fa/confirm/',
                  {'code': totp_lib._code_for_step(secret, totp_lib.current_step())})

        without = self.post('/setting/2fa/disable/', {})
        self.assertEqual(without.status_code, 400)
        self.assertTrue(UserTOTP.objects.filter(user=self.user).exists())

        # a code from the next step, so it is not the one already spent
        with_code = self.post('/setting/2fa/disable/', {
            'code': totp_lib._code_for_step(secret, totp_lib.current_step() + 1),
        })
        self.assertEqual(with_code.status_code, 200)
        self.assertFalse(UserTOTP.objects.filter(user=self.user).exists())


class PrivacyTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = signed_in('privateperson')
        self.stranger, self.stranger_auth = signed_in('stranger')

    def set_privacy(self, **values):
        UserSetting.objects.update_or_create(
            user=self.owner, defaults={'data': {'privacy': values}},
        )

    def test_a_public_profile_is_public(self):
        self.set_privacy(profile_visibility='public')
        res = self.client.get(f'/user/{self.owner.user_id}/profile/')
        self.assertEqual(res.status_code, 200)

    def test_a_private_profile_is_refused_to_everybody_else(self):
        self.set_privacy(profile_visibility='private')
        res = self.client.get(f'/user/{self.owner.user_id}/profile/', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'PRIVATE_PROFILE')

    def test_the_owner_still_sees_their_own_private_profile(self):
        self.set_privacy(profile_visibility='private')
        res = self.client.get(f'/user/{self.owner.user_id}/profile/', **self.owner_auth)
        self.assertEqual(res.status_code, 200)

    def test_followers_only_is_closed_to_a_stranger(self):
        self.set_privacy(profile_visibility='followers')
        res = self.client.get(f'/user/{self.owner.user_id}/profile/', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_hiding_location_hides_it(self):
        self.set_privacy(profile_visibility='public', show_location=False)
        data = self.client.get(f'/user/{self.owner.user_id}/profile/').json()['data']
        self.assertIsNone(data['country'])
        self.assertIsNone(data['state'])

    def test_an_email_is_not_served_unless_it_is_switched_on(self):
        self.set_privacy(profile_visibility='public', show_email=False)
        data = self.client.get(f'/user/{self.owner.user_id}/profile/').json()['data']
        self.assertIsNone(data.get('email'))

        self.set_privacy(profile_visibility='public', show_email=True)
        data2 = self.client.get(f'/user/{self.owner.user_id}/profile/').json()['data']
        self.assertEqual(data2.get('email'), self.owner.email)


class DangerZoneTests(TestCase):
    def setUp(self):
        self.user, self.auth = signed_in('dangerzone')

    def post(self, path, body=None):
        return self.client.post(path, data=json.dumps(body or {}),
                                content_type='application/json', **self.auth)

    def test_the_export_is_a_file_that_arrives_now(self):
        res = self.client.get('/setting/export/', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/json')
        self.assertIn('attachment; filename=', res['Content-Disposition'])
        payload = json.loads(res.content)
        self.assertEqual(payload['account']['username'], 'dangerzone')
        self.assertIn('wallet', payload)
        self.assertIn('tournaments', payload)

    def test_deactivating_hides_the_account_and_ends_the_session(self):
        res = self.post('/setting/deactivate/')
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_deactivated)
        self.assertIsNone(self.user.login_session_token)

    def test_a_deactivated_profile_is_not_served(self):
        self.post('/setting/deactivate/')
        res = self.client.get(f'/user/{self.user.user_id}/profile/')
        self.assertEqual(res.status_code, 404)

    def test_deleting_needs_confirmation_and_is_a_soft_delete(self):
        without = self.post('/setting/delete/')
        self.assertEqual(without.status_code, 400)

        res = self.post('/setting/delete/', {'confirm': True})
        self.assertEqual(res.status_code, 200)
        self.assertIn('grace_days', res.json()['data'])

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.deletion_requested_at)
        self.assertTrue(Users.objects.filter(pk=self.user.pk).exists())

    def test_signing_back_in_cancels_a_scheduled_deletion(self):
        self.user.set_password('CorrectHorse9!')
        self.user.save()
        self.post('/setting/delete/', {'confirm': True})

        res = self.client.post(
            '/auth/login/',
            data=json.dumps({'username_or_email': 'dangerzone', 'password': 'CorrectHorse9!'}),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.deletion_requested_at)
        self.assertFalse(self.user.is_deactivated)


class SessionInactivityTests(TestCase):
    """A session in use must not expire; an idle one still must.

    Reported by the CEO 2026-08-26: people were being signed out mid-task with
    "Your session expired". The stamp was written once at login and never
    touched, so the window counted down from sign-in whether or not the account
    was being used.
    """

    def setUp(self):
        from vent_auth.models import Users

        self.user = Users.objects.create(username='idle_user', email='idle@example.com')
        self.user.login_session_token = 'idletoken1234567'[:16]
        self.user.login_session_created_at = timezone.now()
        self.user.save()
        self.headers = {'HTTP_AUTHORIZATION': f'Bearer {self.user.login_session_token}'}

    def _stamp(self):
        self.user.refresh_from_db()
        return self.user.login_session_created_at

    def test_using_the_account_moves_the_stamp_forward(self):
        from datetime import timedelta

        was = timezone.now() - timedelta(minutes=30)
        self.user.login_session_created_at = was
        self.user.save(update_fields=['login_session_created_at'])

        self.client.get('/setting/', **self.headers)

        self.assertGreater(self._stamp(), was, 'activity should restart the countdown')

    def test_a_session_older_than_the_window_but_active_keeps_working(self):
        """The actual reported symptom: signed in a long time, working, thrown out."""
        from datetime import timedelta
        from vent_auth.views_helpers import session_timeout_minutes

        window = session_timeout_minutes()
        # Signed in well over a window ago, but used ten minutes ago.
        self.user.login_session_created_at = timezone.now() - timedelta(minutes=10)
        self.user.save(update_fields=['login_session_created_at'])

        res = self.client.get('/setting/', **self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertGreater(window, 10)

    def test_an_idle_session_still_expires(self):
        from datetime import timedelta
        from vent_auth.views_helpers import session_timeout_minutes

        self.user.login_session_created_at = (
            timezone.now() - timedelta(minutes=session_timeout_minutes() + 60)
        )
        self.user.save(update_fields=['login_session_created_at'])

        res = self.client.get('/setting/', **self.headers)
        self.assertEqual(res.status_code, 401)

    def test_an_expired_session_is_not_revived_by_the_touch(self):
        """The middleware runs before the view refuses the request. If it moved
        the stamp anyway it would hand back an account the timeout had closed."""
        from datetime import timedelta
        from vent_auth.views_helpers import session_timeout_minutes

        stale = timezone.now() - timedelta(minutes=session_timeout_minutes() + 60)
        self.user.login_session_created_at = stale
        self.user.save(update_fields=['login_session_created_at'])

        self.client.get('/setting/', **self.headers)
        self.assertEqual(self._stamp(), stale, 'an expired session must stay expired')

    def test_a_fresh_stamp_is_not_rewritten_on_every_request(self):
        """A write per request would be several per page rendered."""
        from datetime import timedelta

        recent = timezone.now() - timedelta(minutes=1)
        self.user.login_session_created_at = recent
        self.user.save(update_fields=['login_session_created_at'])

        self.client.get('/setting/', **self.headers)
        self.assertEqual(self._stamp(), recent, 'a recent stamp should be left alone')

    def test_an_unknown_token_touches_nothing(self):
        res = self.client.get(
            '/setting/', HTTP_AUTHORIZATION='Bearer notarealtoken00',
        )
        self.assertEqual(res.status_code, 401)

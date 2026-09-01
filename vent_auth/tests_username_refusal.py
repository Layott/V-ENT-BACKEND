"""Why a username cannot be used, said precisely rather than "taken".

CEO, 1 September 2026: "if there is a username that was saved during the
waitlist time and it is inputted, dont just show this username has been taken,
tell them that the taken username is one of the unique ones taken during the
waitlist."

The reason it matters: "that username is taken" invites somebody to keep trying
variations of a name they will never get, and it reads as though a stranger beat
them to it by a minute. 102 people reserved a handle before the platform opened,
and those names were gone before anybody could type them. It also fails the
returning waitlist member, who should be told "that is your own name, claim it"
rather than sent off to invent a new one.

Four outcomes, and they are genuinely different situations. These tests pin all
four, at every screen that refuses a name, because the whole point is that the
sentence is the same wherever you meet it.
"""
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from .models import Users, UserWallet, WaitlistReservation
from .views_helpers import username_refusal


def a_user(name, email=None):
    user = Users.objects.create(
        username=name, email=email or ('%s@vent.test' % name),
        login_session_token=('l-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.password = make_password('DemoPass!2026')
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('uw%s' % name)[:10], user=user, wallet_balance=0,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class UsernameRefusalTests(TestCase):
    def setUp(self):
        WaitlistReservation.objects.create(
            email='ada@example.test', username='shadowfax', position=1,
            hold_expires_at=timezone.now() + timedelta(days=30))

    # ------------------------------------------------------- the four cases

    def test_a_held_reservation_says_it_is_a_waitlist_name(self):
        code, message, data = username_refusal('shadowfax',
                                               email='stranger@example.test')
        self.assertEqual(code, 'USERNAME_RESERVED')
        self.assertIn('waitlist', message.lower())
        self.assertEqual(data['username'], 'shadowfax')

    def test_the_reserver_is_never_refused_their_own_name(self):
        """Matched by address, because that is all the waitlist captured."""
        self.assertIsNone(username_refusal('shadowfax', email='ada@example.test'))
        self.assertIsNone(username_refusal('SHADOWFAX', email='ADA@example.test'))

    def test_a_claimed_reservation_says_the_member_claimed_it(self):
        """Different from "reserved": it is somebody's account now, for good."""
        owner, _ = a_user('shadowfax', email='ada@example.test')
        WaitlistReservation.objects.filter(username='shadowfax').update(
            claimed_at=timezone.now(), claimed_user=owner)

        code, message, _ = username_refusal('shadowfax',
                                            email='stranger@example.test')
        self.assertEqual(code, 'USERNAME_TAKEN_WAITLIST')
        self.assertIn('waitlist', message.lower())

    def test_an_ordinary_account_gets_the_plain_message(self):
        a_user('temi')
        code, _message, _data = username_refusal('temi',
                                                 email='stranger@example.test')
        self.assertEqual(code, 'USERNAME_ALREADY_TAKEN')

    def test_a_lapsed_hold_puts_the_name_back_in_the_pool(self):
        """The deadline is the whole reason the hold has one."""
        WaitlistReservation.objects.filter(username='shadowfax').update(
            hold_expires_at=timezone.now() - timedelta(days=1))
        self.assertIsNone(username_refusal('shadowfax',
                                           email='stranger@example.test'))

    def test_a_free_name_is_free(self):
        self.assertIsNone(username_refusal('nobody_has_this',
                                           email='x@example.test'))

    def test_changing_to_your_own_current_name_is_not_a_refusal(self):
        user, _ = a_user('temi')
        self.assertIsNone(username_refusal('temi', email=user.email,
                                           exclude_user=user))

    # ------------------------------------------- the same reason everywhere

    def test_signup_gives_the_waitlist_reason(self):
        res = self.client.post('/auth/signup/', data={
            'email': 'stranger@example.test', 'username': 'shadowfax',
            'password': 'DemoPass!2026', 'fullname': 'A Stranger',
        }, content_type='application/json')
        self.assertEqual(res.status_code, 409, res.json())
        body = res.json()
        self.assertEqual(body['code'], 'USERNAME_RESERVED')
        self.assertEqual(body['field'], 'username')
        self.assertIn('waitlist', body['message'].lower())
        self.assertFalse(Users.objects.filter(username='shadowfax').exists())

    def test_the_reserver_can_still_sign_up_with_their_own_name(self):
        res = self.client.post('/auth/signup/', data={
            'email': 'ada@example.test', 'username': 'shadowfax',
            'password': 'DemoPass!2026', 'fullname': 'Ada',
        }, content_type='application/json')
        self.assertIn(res.status_code, (200, 201), res.json())

    def test_the_availability_probe_gives_the_reason_too(self):
        """The first place somebody hears it, while they are still typing."""
        res = self.client.post('/auth/admin/check-username-availability/',
                               data={'username': 'shadowfax'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.json())
        body = res.json()
        self.assertFalse(body['available'])
        self.assertEqual(body['reason'], 'USERNAME_RESERVED')
        self.assertIn('waitlist', body['message'].lower())

    def test_the_probe_still_reports_a_free_name_as_free(self):
        res = self.client.post('/auth/admin/check-username-availability/',
                               data={'username': 'nobody_has_this'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(res.json()['available'])

    def test_changing_username_in_settings_gives_the_reason(self):
        _user, auth = a_user('temi')
        res = self.client.post('/setting/username/',
                               data={'username': 'shadowfax'},
                               content_type='application/json', **auth)
        self.assertEqual(res.status_code, 409, res.json())
        self.assertEqual(res.json()['code'], 'USERNAME_RESERVED')
        self.assertIn('waitlist', res.json()['message'].lower())

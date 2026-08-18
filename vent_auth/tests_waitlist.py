"""Claiming a pre-launch waitlist reservation.

Covers the paths that decide whether 102 real people get their accounts: the
happy claim, the single-use token, the reserved-username hold, and the case that
actually exists in production - somebody on the waitlist whose ordinary signup
is stuck inactive because the verification link was broken.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Users, UserWallet, WaitlistReservation, Transaction


def make_reservation(**overrides):
    values = dict(
        email='reserver@example.com',
        username='reserver',
        display_name='Res Erver',
        position=7,
        country='Nigeria',
        claim_token='tok-happy-path',
        hold_expires_at=timezone.now() + timedelta(days=90),
    )
    values.update(overrides)
    return WaitlistReservation.objects.create(**values)


class ClaimPreviewTests(TestCase):
    def test_preview_returns_what_the_page_needs(self):
        make_reservation()
        response = self.client.get(reverse('waitlist_claim_preview', args=['tok-happy-path']))

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['username'], 'reserver')
        self.assertEqual(data['email'], 'reserver@example.com')
        self.assertEqual(data['position'], 7)
        self.assertTrue(data['username_reserved'])

    def test_unknown_token_is_404_and_says_so_plainly(self):
        response = self.client.get(reverse('waitlist_claim_preview', args=['nope']))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'CLAIM_TOKEN_INVALID')


class ClaimTests(TestCase):
    url = '/auth/waitlist/claim/'

    def test_claim_creates_an_active_signed_in_founding_account(self):
        reservation = make_reservation()

        response = self.client.post(self.url, {
            'token': 'tok-happy-path',
            'password': 'a-real-password',
        }, content_type='application/json')

        self.assertEqual(response.status_code, 201)
        data = response.json()['data']
        self.assertEqual(data['username'], 'reserver')
        self.assertEqual(data['founding_position'], 7)
        self.assertTrue(data['session_token'])

        user = Users.objects.get(username='reserver')
        # Active without a second verification step: the token arrived in their
        # inbox, which is the same proof the verification link provides.
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_founding_member)
        self.assertEqual(user.founding_position, 7)
        self.assertTrue(user.check_password('a-real-password'))
        # A user without a wallet gets 401s across the app, so the claim must
        # create one.
        self.assertTrue(UserWallet.objects.filter(user=user).exists())

        reservation.refresh_from_db()
        self.assertIsNotNone(reservation.claimed_at)
        self.assertEqual(reservation.claimed_user_id, user.pk)
        self.assertIsNone(reservation.claim_token)

    def test_token_works_only_once(self):
        make_reservation()
        first = self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'a-real-password'},
                                 content_type='application/json')
        self.assertEqual(first.status_code, 201)

        second = self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'another-password'},
                                  content_type='application/json')
        self.assertEqual(second.status_code, 404)
        self.assertEqual(Users.objects.filter(username='reserver').count(), 1)

    def test_short_password_is_refused(self):
        make_reservation()
        response = self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'short'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'PASSWORD_TOO_SHORT')
        self.assertFalse(Users.objects.filter(username='reserver').exists())

    def test_claim_rescues_an_account_stuck_inactive(self):
        """The winlola case: on the waitlist, then signed up while the
        verification link pointed at a dead host, so the account exists but has
        never been usable."""
        stuck = Users.objects.create(
            email='reserver@example.com', username='reserver',
            full_name='Res Erver', is_active=False)
        make_reservation()

        response = self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'a-real-password'},
                                    content_type='application/json')

        self.assertEqual(response.status_code, 201)
        stuck.refresh_from_db()
        self.assertTrue(stuck.is_active)
        self.assertTrue(stuck.is_founding_member)
        self.assertTrue(stuck.check_password('a-real-password'))
        # Rescued, not duplicated.
        self.assertEqual(Users.objects.filter(email='reserver@example.com').count(), 1)

    def test_username_taken_since_the_reservation_is_reported(self):
        make_reservation()
        Users.objects.create(email='someone@else.com', username='reserver', is_active=True)

        response = self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'a-real-password'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'USERNAME_TAKEN')

    def test_reservation_without_a_username_asks_for_one(self):
        make_reservation(username=None, email='noname@example.com', claim_token='tok-noname')

        missing = self.client.post(self.url, {'token': 'tok-noname', 'password': 'a-real-password'},
                                   content_type='application/json')
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()['code'], 'USERNAME_REQUIRED')

        chosen = self.client.post(self.url, {'token': 'tok-noname', 'password': 'a-real-password',
                                             'username': 'PickedLater'},
                                  content_type='application/json')
        self.assertEqual(chosen.status_code, 201)
        self.assertTrue(Users.objects.filter(username='pickedlater').exists())

    @override_settings(WAITLIST_CLAIM_BONUS_VC=0)
    def test_no_coins_are_credited_while_the_bonus_is_zero(self):
        make_reservation()
        self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'a-real-password'},
                         content_type='application/json')

        wallet = UserWallet.objects.get(user__username='reserver')
        self.assertEqual(wallet.wallet_balance, 0)
        self.assertFalse(Transaction.objects.filter(description='Founding member bonus').exists())

    @override_settings(WAITLIST_CLAIM_BONUS_VC=2)
    def test_bonus_is_credited_once_turned_on(self):
        make_reservation()
        self.client.post(self.url, {'token': 'tok-happy-path', 'password': 'a-real-password'},
                         content_type='application/json')

        wallet = UserWallet.objects.get(user__username='reserver')
        self.assertEqual(wallet.wallet_balance, 2)
        self.assertEqual(
            Transaction.objects.filter(description='Founding member bonus').count(), 1)


class ReservedUsernameHoldTests(TestCase):
    url = '/auth/signup/'

    def post_signup(self, **payload):
        body = {'email': 'stranger@example.com', 'username': 'reserver',
                'password': 'a-real-password'}
        body.update(payload)
        return self.client.post(self.url, body, content_type='application/json')

    def test_a_stranger_cannot_take_a_reserved_username(self):
        make_reservation()
        response = self.post_signup()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'USERNAME_RESERVED')
        self.assertFalse(Users.objects.filter(username='reserver').exists())

    def test_the_reserver_is_never_blocked_by_their_own_reservation(self):
        make_reservation()
        response = self.post_signup(email='reserver@example.com')
        self.assertNotEqual(response.status_code, 409)

    def test_hold_lapses_so_abandoned_names_come_back(self):
        make_reservation(hold_expires_at=timezone.now() - timedelta(days=1))
        response = self.post_signup()
        self.assertNotEqual(response.status_code, 409)

    def test_a_claimed_reservation_stops_holding_the_name(self):
        reservation = make_reservation()
        reservation.claimed_at = timezone.now()
        reservation.save()
        # Held by the real account now, not by the reservation.
        self.assertFalse(reservation.holds_username())

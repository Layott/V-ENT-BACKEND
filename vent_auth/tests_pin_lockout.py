"""A wallet PIN locks after five wrong tries.

Owner rules R58 and R59, 17 September 2026. Ten doors compared a PIN by hand
and none of them counted, so a four digit PIN could be walked from one address
in under an hour. `wallets.check_pin` is now the only place a PIN is compared,
and it locks the wallet for fifteen minutes after five wrong tries, whichever
door the tries came through.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import wallets
from .models import (Games, OrgMember, Organization, OrgWallet, TeamMembers,
                     Teams, TeamWallet, UserWallet, Users)


def a_user(name, coins=0, pin='1234'):
    user = Users.objects.create(username=name, email='%s@vent.test' % name,
                                is_active=True,
                                login_session_token=('p-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.create(
        user_wallet_id=name[:10], user=user, wallet_balance=coins,
        pin_hash=make_password(pin) if pin else None)
    return user, wallet, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class CheckPinTests(TestCase):
    def setUp(self):
        self.user, self.wallet, self.auth = a_user('pin_owner', coins=50)

    def wrong(self, times):
        for _ in range(times):
            with self.assertRaises(wallets.WalletError) as ctx:
                wallets.check_pin(self.wallet, '0000')
            yield ctx.exception

    def test_a_right_pin_passes_and_counts_nothing(self):
        wallets.check_pin(self.wallet, '1234')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pin_failures, 0)
        self.assertIsNone(self.wallet.pin_locked_until)

    def test_a_wrong_pin_says_how_many_tries_are_left(self):
        errors = list(self.wrong(1))
        self.assertEqual(errors[0].code, 'INVALID_PIN')
        self.assertEqual(errors[0].params, {'tries_left': 4})
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pin_failures, 1)

    def test_the_fifth_wrong_try_locks_the_wallet(self):
        errors = list(self.wrong(5))
        self.assertEqual([e.code for e in errors[:4]], ['INVALID_PIN'] * 4)
        self.assertEqual(errors[4].code, 'PIN_LOCKED')
        self.assertEqual(errors[4].params, {'minutes': wallets.PIN_LOCK_MINUTES})
        self.wallet.refresh_from_db()
        self.assertIsNotNone(self.wallet.pin_locked_until)
        self.assertEqual(self.wallet.pin_failures, 0)

    def test_a_locked_wallet_refuses_even_the_right_pin(self):
        list(self.wrong(5))
        with self.assertRaises(wallets.WalletError) as ctx:
            wallets.check_pin(self.wallet, '1234')
        self.assertEqual(ctx.exception.code, 'PIN_LOCKED')
        self.assertGreaterEqual(ctx.exception.params['minutes'], 1)
        self.assertLessEqual(ctx.exception.params['minutes'], wallets.PIN_LOCK_MINUTES)

    def test_the_clock_passing_unlocks_it_and_a_right_pin_clears_the_count(self):
        list(self.wrong(5))
        later = timezone.now() + timedelta(minutes=wallets.PIN_LOCK_MINUTES + 1)
        with patch('vent_auth.wallets.timezone.now', return_value=later):
            wallets.check_pin(self.wallet, '1234')
        self.wallet.refresh_from_db()
        self.assertIsNone(self.wallet.pin_locked_until)
        self.assertEqual(self.wallet.pin_failures, 0)

    def test_a_right_pin_between_wrong_ones_resets_the_count(self):
        list(self.wrong(3))
        wallets.check_pin(self.wallet, '1234')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pin_failures, 0)
        # Four more wrong ones do not lock it: the count started again.
        errors = list(self.wrong(4))
        self.assertTrue(all(e.code == 'INVALID_PIN' for e in errors))

    def test_no_pin_at_all_is_its_own_refusal(self):
        _, bare, _ = a_user('pin_none', pin=None)
        with self.assertRaises(wallets.WalletError) as ctx:
            wallets.check_pin(bare, '1234')
        self.assertEqual(ctx.exception.code, 'PIN_REQUIRED')

    def test_the_body_carries_the_numbers_beside_the_code(self):
        errors = list(self.wrong(1))
        body = errors[0].body()
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['code'], 'INVALID_PIN')
        self.assertEqual(body['tries_left'], 4)
        self.assertIn('4 tries left', body['message'])


class SharedWalletsLockTooTests(TestCase):
    """The count lives on every kind of wallet, not only a person's."""

    def setUp(self):
        self.owner, _, _ = a_user('pin_team_owner')
        game = Games.objects.create(game_title='EA FC P')
        team = Teams.objects.create(
            team_name='Pin United', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1)
        self.team_wallet = TeamWallet.objects.create(
            team_wallet_id='tw_pin', team=team, wallet_balance=10,
            pin_hash=make_password('4321'))
        org = Organization.objects.create(
            org_name='Pin Org', org_creator=self.owner, org_owner=self.owner)
        self.org_wallet = OrgWallet.objects.create(
            org_wallet_id='ow_pin', org=org, wallet_balance=10,
            pin_hash=make_password('4321'))

    def test_a_team_wallet_locks(self):
        for _ in range(5):
            with self.assertRaises(wallets.WalletError):
                wallets.check_pin(self.team_wallet, '0000')
        with self.assertRaises(wallets.WalletError) as ctx:
            wallets.check_pin(self.team_wallet, '4321')
        self.assertEqual(ctx.exception.code, 'PIN_LOCKED')

    def test_an_org_wallet_locks(self):
        for _ in range(5):
            with self.assertRaises(wallets.WalletError):
                wallets.check_pin(self.org_wallet, '0000')
        with self.assertRaises(wallets.WalletError) as ctx:
            wallets.check_pin(self.org_wallet, '4321')
        self.assertEqual(ctx.exception.code, 'PIN_LOCKED')


class EveryDoorCountsTests(TestCase):
    """The doors that used to compare a PIN by hand now share the count: wrong
    tries at the verify endpoint lock the send endpoint, and the other way."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet, self.auth = a_user('pin_door', coins=50)
        a_user('pin_friend')

    def test_wrong_tries_at_verify_lock_the_send(self):
        for _ in range(5):
            res = self.client.post('/auth/wallet/pin/verify/', {'pin': '0000'},
                                   format='json', **self.auth)
            self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PIN_LOCKED')
        self.assertEqual(res.data['minutes'], wallets.PIN_LOCK_MINUTES)

        res = self.client.post('/auth/wallet/send/', {
            'recipient_username': 'pin_friend', 'amount': 1, 'pin': '1234',
        }, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PIN_LOCKED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 50)

    def test_the_verify_endpoint_says_how_many_tries_are_left(self):
        res = self.client.post('/auth/wallet/pin/verify/', {'pin': '0000'},
                               format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INVALID_PIN')
        self.assertEqual(res.data['tries_left'], 4)

    def test_guessing_the_current_pin_on_the_change_screen_counts_too(self):
        for _ in range(5):
            res = self.client.post('/auth/wallet/pin/set/', {
                'current_pin': '0000', 'new_pin': '9999',
            }, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PIN_LOCKED')
        self.wallet.refresh_from_db()
        # The PIN did not change.
        self.assertTrue(check_password('1234', self.wallet.pin_hash))

"""A withdrawal fee that is real on both sides.

Until 13 September the withdraw screen told people "Withdrawal fee (2% + N50)"
and drew a net payout from that, while the server sent the whole amount. Now
the rate is a dashboard number (0 + 0 by default, which is what the server
always did), the server stamps what it used on the request, and the screen
asks for the number rather than computing it.
"""
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AdminSetting, UserWallet, Users, WithdrawalRequest

PIN = '4417'


def make_user(name, coins=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.title(), login_session_token=('u-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.create(
        user_wallet_id=('w%s' % user.user_id)[:10], user=user,
        wallet_balance=coins, kyc_verified=True, pin_hash=make_password(PIN))
    return user, wallet


class WithdrawalFeeTests(TestCase):

    def setUp(self):
        self.user, self.wallet = make_user('wfee', coins=100)
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def ask(self, amount):
        return self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': amount, 'pin': PIN, 'bank_name': 'GTBank',
            'account_number': '0123456789', 'account_name': 'W Fee',
        }, format='json')

    def test_by_default_nothing_comes_off(self):
        res = self.ask(10)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['data']['fee_ngn'], 0.0)
        self.assertEqual(res.data['data']['payout_ngn'], 10000.0)

    def test_the_rate_on_the_dashboard_is_stamped_on_the_request(self):
        AdminSetting.put('platform_fees', withdrawal_fee_pct=2, withdrawal_fee_flat_ngn=50)
        res = self.ask(10)
        self.assertEqual(res.status_code, 201, res.data)
        row = WithdrawalRequest.objects.get()
        self.assertEqual(row.amount, 10, 'the coins leave in full')
        self.assertEqual(row.fee_pct, Decimal('2'))
        self.assertEqual(row.fee_flat_ngn, Decimal('50'))
        self.assertEqual(row.fee_ngn, Decimal('250.00'))
        self.assertEqual(row.payout_ngn, Decimal('9750.00'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 90)

    def test_a_change_after_the_request_does_not_rewrite_it(self):
        AdminSetting.put('platform_fees', withdrawal_fee_pct=2, withdrawal_fee_flat_ngn=50)
        self.ask(10)
        AdminSetting.put('platform_fees', withdrawal_fee_pct=10, withdrawal_fee_flat_ngn=0)
        row = WithdrawalRequest.objects.get()
        self.assertEqual(row.fee_ngn, Decimal('250.00'))
        self.assertEqual(row.payout_ngn, Decimal('9750.00'))

    def test_the_fee_never_exceeds_the_payout(self):
        AdminSetting.put('platform_fees', withdrawal_fee_pct=0, withdrawal_fee_flat_ngn=99999)
        res = self.ask(5)
        self.assertEqual(res.status_code, 201, res.data)
        row = WithdrawalRequest.objects.get()
        self.assertEqual(row.fee_ngn, Decimal('5000'))
        self.assertEqual(row.payout_ngn, Decimal('0'))

    def test_the_quote_says_the_number_before_the_pin(self):
        AdminSetting.put('platform_fees', withdrawal_fee_pct=2, withdrawal_fee_flat_ngn=50)
        res = self.client.get('/auth/wallet/withdraw/quote/?amount=10')
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data['data']
        self.assertEqual(data['amount_vc'], 10)
        self.assertEqual(data['gross_ngn'], 10000.0)
        self.assertEqual(data['fee_pct'], 2.0)
        self.assertEqual(data['fee_flat_ngn'], 50.0)
        self.assertEqual(data['fee_ngn'], 250.0)
        self.assertEqual(data['payout_ngn'], 9750.0)
        self.assertEqual(data['limits'], {'minimum': 5, 'daily_max': 500})
        self.assertFalse(WithdrawalRequest.objects.exists(), 'a quote writes nothing')

    def test_the_quote_needs_a_signed_in_wallet(self):
        res = APIClient().get('/auth/wallet/withdraw/quote/?amount=10')
        self.assertGreaterEqual(res.status_code, 400)
        self.assertEqual(res.data['status'], 'error')

    def test_the_history_carries_the_fee(self):
        AdminSetting.put('platform_fees', withdrawal_fee_pct=2, withdrawal_fee_flat_ngn=50)
        self.ask(10)
        res = self.client.get('/auth/wallet/withdraw/status/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data'][0]['fee_ngn'], 250.0)
        self.assertEqual(res.data['data'][0]['payout_ngn'], 9750.0)


class TheQueueSaysWhatToSendTests(TestCase):
    """An admin sends payout_ngn, and a row from before the fee is worth its
    coins in full, which is what was always sent."""

    def setUp(self):
        self.user, self.wallet = make_user('wqueue', coins=100)
        self.admin, _ = make_user('wqadmin')
        self.admin.is_staff = True
        self.admin.admin_role = 'super_admin'
        self.admin.login_session_2fa_at = timezone.now()
        self.admin.save()
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.admin.login_session_token)

    def test_a_stamped_row_and_an_unstamped_row(self):
        stamped = WithdrawalRequest.objects.create(
            wallet=self.wallet, amount=10, bank_name='GTBank',
            account_number='0123456789', account_name='S',
            fee_pct=2, fee_flat_ngn=50, fee_ngn=250, payout_ngn=9750)
        old = WithdrawalRequest.objects.create(
            wallet=self.wallet, amount=7, bank_name='GTBank',
            account_number='0123456789', account_name='O')
        res = self.client.get('/auth/admin/payouts/pending/')
        self.assertEqual(res.status_code, 200, res.data)
        rows = {r['id']: r for r in res.data['data']}
        self.assertEqual(rows[stamped.id]['fee_ngn'], 250.0)
        self.assertEqual(rows[stamped.id]['payout_ngn'], 9750.0)
        self.assertEqual(rows[old.id]['fee_ngn'], 0.0)
        self.assertEqual(rows[old.id]['payout_ngn'], 7000.0)

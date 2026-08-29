"""The four things the wallet does, walked end to end.

CEO, 29 August 2026: "check the top, up withdraw and the rest of the flows
properly and make sure it works."

There was no test over any of them. `tests_payout_no_kyc.py` covered one refusal
and nothing covered the money actually moving, which is the part where being
wrong is expensive and silent: a balance that does not change, a PIN that is not
asked for, a send that debits one wallet and credits nobody.

Each test is written from the request the frontend actually makes, taken from
`src/app/wallets/*/page.js`, rather than from what the view happens to accept.
A payload nothing sends is a passing test about nothing.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Transaction, Users, UserWallet, WithdrawalRequest

PIN = '4417'


def _user(username, balance=0, pin=PIN, kyc=False):
    user = Users.objects.create(
        username=username, email='%s@vent.test' % username,
        full_name=username.title(),
        login_session_token=('tk-%s' % username)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.create(
        user_wallet_id='w%09d' % user.user_id, user=user,
        wallet_balance=balance, kyc_verified=kyc)
    if pin:
        from django.contrib.auth.hashers import make_password
        wallet.pin_hash = make_password(pin)
        wallet.save(update_fields=['pin_hash'])
    return user, wallet


class BalanceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = _user('bal', balance=25)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def test_the_balance_the_page_reads_is_the_balance_in_the_database(self):
        res = self.client.get('/auth/wallet/balance/')
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data['data']
        # The wallet page reads these three names. If any is renamed the page
        # shows zero and nothing errors, which is the worst possible failure
        # for a screen about money.
        self.assertIn('balance', str(data.keys()))
        balance = data.get('balance_vc', data.get('balance'))
        self.assertEqual(int(balance), 25)

    def test_signed_out_cannot_read_a_balance(self):
        self.client.credentials()
        res = self.client.get('/auth/wallet/balance/')
        # 400 rather than 401 is the shape this API has always had. What matters
        # here is that no balance comes back.
        self.assertNotEqual(res.status_code, 200, res.data)
        self.assertNotIn('balance_vc', str(res.data))


class SendTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sender, self.sender_wallet = _user('sender', balance=100)
        self.receiver, self.receiver_wallet = _user('receiver', balance=5)
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.sender.login_session_token)

    def _send(self, **over):
        payload = {'recipient_username': 'receiver', 'amount': 30, 'pin': PIN, 'note': ''}
        payload.update(over)
        return self.client.post('/auth/wallet/send/', payload, format='json')

    def test_sending_debits_one_wallet_and_credits_the_other(self):
        res = self._send()
        self.assertEqual(res.status_code, 200, res.data)
        self.sender_wallet.refresh_from_db()
        self.receiver_wallet.refresh_from_db()
        self.assertEqual(self.sender_wallet.wallet_balance, 70)
        self.assertEqual(self.receiver_wallet.wallet_balance, 35)

    def test_both_sides_get_a_transaction_row(self):
        self._send()
        self.assertTrue(
            Transaction.objects.filter(wallet=self.sender_wallet, type='send').exists(),
            'the sender has no record of sending it')
        self.assertTrue(
            Transaction.objects.filter(wallet=self.receiver_wallet, type='receive').exists(),
            'the receiver has no record of being paid')

    def test_the_wrong_pin_moves_nothing(self):
        res = self._send(pin='0000')
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.sender_wallet.refresh_from_db()
        self.receiver_wallet.refresh_from_db()
        self.assertEqual(self.sender_wallet.wallet_balance, 100)
        self.assertEqual(self.receiver_wallet.wallet_balance, 5)

    def test_more_than_the_balance_moves_nothing(self):
        res = self._send(amount=500)
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.sender_wallet.refresh_from_db()
        self.assertEqual(self.sender_wallet.wallet_balance, 100)

    def test_a_negative_amount_cannot_pull_money_the_other_way(self):
        res = self._send(amount=-50)
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.sender_wallet.refresh_from_db()
        self.receiver_wallet.refresh_from_db()
        self.assertEqual(self.sender_wallet.wallet_balance, 100)
        self.assertEqual(self.receiver_wallet.wallet_balance, 5)

    def test_sending_to_somebody_who_does_not_exist_is_refused(self):
        res = self._send(recipient_username='nobody_at_all')
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.sender_wallet.refresh_from_db()
        self.assertEqual(self.sender_wallet.wallet_balance, 100)


class WithdrawTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = _user('payee', balance=80, kyc=True)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def _withdraw(self, **over):
        payload = {
            'amount': 40, 'pin': PIN,
            'bank_name': 'GTBank',
            'account_number': '0123456789',
            'account_name': 'Payee Payee',
        }
        payload.update(over)
        return self.client.post('/auth/wallet/withdraw/initiate/', payload, format='json')

    def test_a_request_is_recorded_for_an_admin_to_approve(self):
        res = self._withdraw()
        self.assertEqual(res.status_code, 201, res.data)
        request = WithdrawalRequest.objects.get(wallet=self.wallet)
        self.assertEqual(request.status, 'pending')
        self.assertEqual(request.amount, 40)

    def test_the_wrong_pin_records_nothing(self):
        res = self._withdraw(pin='9999')
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_more_than_the_balance_is_refused(self):
        res = self._withdraw(amount=500)
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_without_a_verified_identity_it_is_refused(self):
        self.wallet.kyc_verified = False
        self.wallet.save(update_fields=['kyc_verified'])
        res = self._withdraw()
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_the_history_endpoint_shows_it(self):
        self._withdraw()
        res = self.client.get('/auth/wallet/withdraw/status/')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('40', str(res.data))


class TopUpTests(TestCase):
    """Paystack is not called here. What is checked is that the endpoint exists,
    refuses nonsense, and does not credit anybody before a payment clears."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = _user('topper', balance=0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def test_initiating_does_not_credit_anything_by_itself(self):
        before = self.wallet.wallet_balance
        self.client.post('/auth/wallet/topup/initiate/', {'amount': 5000}, format='json')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, before,
                         'coins were credited before the payment cleared')

    def test_verifying_an_unknown_reference_credits_nothing(self):
        res = self.client.post(
            '/auth/wallet/topup/verify/', {'reference': 'not-a-real-reference'}, format='json')
        self.assertGreaterEqual(res.status_code, 400, res.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 0)

    def test_a_zero_or_negative_top_up_is_refused(self):
        for amount in (0, -100):
            res = self.client.post(
                '/auth/wallet/topup/initiate/', {'amount': amount}, format='json')
            self.assertGreaterEqual(res.status_code, 400, (amount, res.data))


class PinTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = _user('pinner', balance=10, pin=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def test_a_pin_can_be_set_and_then_verified(self):
        res = self.client.post('/auth/wallet/pin/set/', {'new_pin': '1234'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.pin_hash)
        self.assertNotIn('1234', self.wallet.pin_hash, 'the PIN is stored in the clear')

        ok = self.client.post('/auth/wallet/pin/verify/', {'pin': '1234'}, format='json')
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_the_wrong_pin_does_not_verify(self):
        self.client.post('/auth/wallet/pin/set/', {'new_pin': '1234'}, format='json')
        res = self.client.post('/auth/wallet/pin/verify/', {'pin': '4321'}, format='json')
        self.assertGreaterEqual(res.status_code, 400, res.data)

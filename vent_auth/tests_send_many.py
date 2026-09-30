"""Sending to several people at once (inbox 386): one PIN, all or nothing."""
from django.test import TestCase
from rest_framework.test import APIClient

from vent_auth.models import Transaction, UserWallet
from vent_auth.tests_wallet_flows import PIN, _user

URL = '/auth/wallet/send-many/'


class SendManyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.me, self.wallet = _user('smsender', balance=10)
        self.ada, self.ada_w = _user('smada', balance=0)
        self.bo, self.bo_w = _user('smbo', balance=0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.me.login_session_token)

    def send(self, recipients, pin=PIN, **extra):
        return self.client.post(URL, {'recipients': recipients, 'pin': pin, **extra}, format='json')

    def balances(self):
        return [UserWallet.objects.get(pk=w.pk).wallet_balance for w in (self.wallet, self.ada_w, self.bo_w)]

    def test_two_people_paid_in_one_send(self):
        r = self.send([{'to_kind': 'user', 'to': 'smada', 'amount': 3},
                       {'to_kind': 'user', 'to': 'smbo', 'amount': 2}], note='for the match')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['data']['total'], 5)
        self.assertEqual(r.json()['data']['new_balance'], 5)
        self.assertEqual(self.balances(), [5, 3, 2])
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, type='send').count(), 2)

    def test_one_unknown_name_moves_nothing(self):
        r = self.send([{'to_kind': 'user', 'to': 'smada', 'amount': 3},
                       {'to_kind': 'user', 'to': 'nobody-here', 'amount': 2}])
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['index'], 1)
        self.assertEqual(self.balances(), [10, 0, 0])

    def test_total_over_balance_moves_nothing(self):
        r = self.send([{'to_kind': 'user', 'to': 'smada', 'amount': 6},
                       {'to_kind': 'user', 'to': 'smbo', 'amount': 5}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['code'], 'INSUFFICIENT_BALANCE')
        self.assertEqual(self.balances(), [10, 0, 0])

    def test_wrong_pin_moves_nothing(self):
        r = self.send([{'to_kind': 'user', 'to': 'smada', 'amount': 1}], pin='0000')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.balances(), [10, 0, 0])

    def test_fractions_self_and_duplicates_refused(self):
        for rows, code in [
            ([{'to': 'smada', 'amount': '0.201'}], 'AMOUNT_TOO_PRECISE'),
            ([{'to': 'smada', 'amount': 'two'}], 'AMOUNT_MUST_NUMBER'),
            ([{'to': 'smada', 'amount': True}], 'AMOUNT_REQUIRED'),
            ([{'to': 'smada', 'amount': 0}], 'AMOUNT_MUST_POSITIVE'),
            ([{'to': 'smsender', 'amount': 1}], 'CANNOT_SEND_YOURSELF'),
            ([{'to': 'smada', 'amount': 1}, {'to': 'smada', 'amount': 1}], 'DUPLICATE_RECIPIENT'),
            ([], 'NO_RECIPIENTS'),
            ([{'to': 'smada', 'amount': 1}] * 21, 'TOO_MANY_RECIPIENTS'),
        ]:
            with self.subTest(code=code):
                r = self.send(rows)
                self.assertEqual(r.json()['code'], code, r.content)
        self.assertEqual(self.balances(), [10, 0, 0])

    def test_signed_out_refused(self):
        self.client.credentials()
        self.assertIn(self.send([{'to': 'smada', 'amount': 1}]).status_code, (401, 403))
        self.assertEqual(self.balances(), [10, 0, 0])

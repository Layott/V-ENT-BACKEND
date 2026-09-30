"""Amounts under one coin (inbox 387).

CEO, 30 September 2026: "Yes people should be able to send amounts under
N1000". Balances and wallet transactions hold hundredths of a coin; these
tests hold both halves: what may now move, and what is still refused.
"""
import json
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from vent_auth import coins, wallets
from vent_auth.models import Notification, Transaction, UserWallet
from vent_auth.tests_wallet_flows import PIN, _user


class ParseTests(SimpleTestCase):
    def test_reads_what_people_type(self):
        for given, expected in [('0.2', '0.20'), ('12.35', '12.35'), ('5', '5.00'), (5, '5.00'),
                                (0.2, '0.20'), (Decimal('0.01'), '0.01'), (' 1,5 ', '1.50')]:
            with self.subTest(given=given):
                self.assertEqual(coins.parse(given), Decimal(expected))

    def test_refuses_with_a_code(self):
        for given, code in [('0.001', 'AMOUNT_TOO_PRECISE'), ('0', 'AMOUNT_MUST_POSITIVE'),
                            ('-1', 'AMOUNT_MUST_POSITIVE'), ('abc', 'AMOUNT_MUST_NUMBER'),
                            ('nan', 'AMOUNT_MUST_NUMBER'), ('inf', 'AMOUNT_MUST_NUMBER'),
                            (None, 'AMOUNT_REQUIRED'), (True, 'AMOUNT_REQUIRED')]:
            with self.subTest(given=given):
                with self.assertRaises(coins.AmountError) as caught:
                    coins.parse(given)
                self.assertEqual(caught.exception.code, code)

    def test_json_and_labels(self):
        self.assertEqual(coins.as_json(Decimal('20.00')), 20)
        self.assertIsInstance(coins.as_json(Decimal('20.00')), int)
        self.assertEqual(coins.as_json(Decimal('0.80')), 0.8)
        self.assertEqual(json.dumps({'a': coins.as_json(Decimal('12.35'))}), '{"a": 12.35}')
        self.assertEqual(coins.label(Decimal('0.80')), '0.8')
        self.assertEqual(coins.label(Decimal('12.00')), '12')
        self.assertEqual(coins.label(Decimal('12.35')), '12.35')


class SendUnderOneTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.me, self.wallet = _user('cusender', balance=1)
        self.ada, self.ada_w = _user('cuada', balance=0)
        self.bo, self.bo_w = _user('cubo', balance=0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.me.login_session_token)

    def balance(self, w):
        return UserWallet.objects.get(pk=w.pk).wallet_balance

    def test_send_two_tenths(self):
        r = self.client.post('/auth/wallet/send/', {'to_kind': 'user', 'to': 'cuada', 'amount': '0.2',
                                                    'pin': PIN}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['data']['new_balance'], 0.8)
        self.assertEqual(self.balance(self.wallet), Decimal('0.80'))
        self.assertEqual(self.balance(self.ada_w), Decimal('0.20'))
        credit = Transaction.objects.get(wallet=self.ada_w, type='receive')
        self.assertEqual(credit.amount, Decimal('0.20'))
        # The notification used to be written in a try that swallowed every
        # error, so a Decimal json.dumps could not encode would have lost it
        # without a word. It must arrive, with the amount as a number.
        note = Notification.objects.get(user=self.ada)
        self.assertIn('0.2 VC', note.title + note.body)
        self.assertEqual(note.metadata['amount'], 0.2)

    def test_balance_reads_as_a_number(self):
        self.client.post('/auth/wallet/send/', {'to_kind': 'user', 'to': 'cuada', 'amount': '0.25',
                                                'pin': PIN}, format='json')
        data = self.client.get('/auth/wallet/balance/').json()['data']
        self.assertEqual(data['balance'], 0.75)
        self.assertEqual(data['balance_ngn'], 750)

    def test_the_whole_balance_to_the_hundredth(self):
        # 0.1 + 0.2 must be 0.3 exactly, or a balance of 0.3 reads as too small.
        UserWallet.objects.filter(pk=self.wallet.pk).update(wallet_balance=Decimal('0.30'))
        r = self.client.post('/auth/wallet/send-many/', {'pin': PIN, 'recipients': [
            {'to_kind': 'user', 'to': 'cuada', 'amount': '0.1'},
            {'to_kind': 'user', 'to': 'cubo', 'amount': '0.2'}]}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['data']['total'], 0.3)
        self.assertEqual(self.balance(self.wallet), Decimal('0.00'))
        self.assertEqual(self.balance(self.bo_w), Decimal('0.20'))

    def test_a_hundredth_more_than_the_balance_is_refused(self):
        r = self.client.post('/auth/wallet/send/', {'to_kind': 'user', 'to': 'cuada', 'amount': '1.01',
                                                    'pin': PIN}, format='json')
        self.assertEqual(r.json()['code'], 'INSUFFICIENT_BALANCE')
        self.assertIn('1 VC available', r.json()['message'])
        self.assertEqual(self.balance(self.wallet), Decimal('1.00'))

    def test_third_decimal_place_refused_before_the_pin(self):
        r = self.client.post('/auth/wallet/send/', {'to_kind': 'user', 'to': 'cuada', 'amount': '0.005',
                                                    'pin': '0000'}, format='json')
        self.assertEqual(r.json()['code'], 'AMOUNT_TOO_PRECISE')
        self.assertEqual(self.balance(self.wallet), Decimal('1.00'))

    def test_transfer_between_any_wallets_takes_hundredths(self):
        debit, credit = wallets.transfer(self.wallet, self.bo_w, '0.35')
        self.assertEqual((debit.amount, credit.amount), (Decimal('-0.35'), Decimal('0.35')))
        with self.assertRaises(wallets.WalletError) as caught:
            wallets.transfer(self.wallet, self.bo_w, '0.351')
        self.assertEqual(caught.exception.code, 'AMOUNT_TOO_PRECISE')
        with self.assertRaises(wallets.WalletError) as caught:
            wallets.transfer(self.wallet, self.bo_w, '0.66')
        self.assertEqual(caught.exception.code, 'INSUFFICIENT_BALANCE')
        self.assertIn('0.65 VC available', str(caught.exception))
        self.assertEqual(caught.exception.params['available'], 0.65)

    def test_statement_sums_to_the_balance(self):
        for amount in ('0.1', '0.2', '0.05'):
            wallets.transfer(self.wallet, self.ada_w, amount)
        rows = [row['amount'] for row in wallets.statement(self.ada_w)]
        self.assertEqual(sum(Decimal(str(a)) for a in rows), self.balance(self.ada_w))
        self.assertEqual(self.balance(self.ada_w), Decimal('0.35'))


class NoRawBalanceTests(SimpleTestCase):
    """A balance printed or JSON-encoded without coins.py.

    Found twice on the day balances became decimal: `'%d VC' % balance` would
    print 0.8 as "0", and a balance handed to json.dump stopped the seed
    removal export dead. Both are text the eye passes over, so the source is
    read for them.
    """
    import re
    PRINTED = re.compile(r"%d[^'\"]*VC[^'\"]*['\"]\s*%\s*\(?[^)\n]*wallet_balance|\{[a-z_.]*wallet_balance\}")
    DUMPED = re.compile(r"(metadata|_vc)['\"]?\s*[:=]\s*[a-z_.]*wallet_balance\b")

    def offenders(self, pattern):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        hits = []
        for path in root.rglob('*.py'):
            parts = set(path.parts)
            if parts & {'migrations', 'tools', '.venv', 'venv', 'node_modules'} or path.name.startswith('tests'):
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8', errors='ignore').splitlines(), 1):
                if pattern.search(line):
                    hits.append('%s:%d %s' % (path.relative_to(root), number, line.strip()))
        return hits

    def test_no_balance_printed_raw(self):
        self.assertEqual(self.offenders(self.PRINTED), [])

    def test_no_balance_dumped_raw(self):
        self.assertEqual(self.offenders(self.DUMPED), [])

    def test_the_patterns_catch_the_real_faults(self):
        for bad in ["'There is not enough: %d VC available.' % wallet.wallet_balance",
                    "f'your balance is {wallet.wallet_balance} VC.'",
                    "'wallet_balance_vc': wallet.wallet_balance if wallet else None,"]:
            with self.subTest(bad=bad):
                self.assertTrue(self.PRINTED.search(bad) or self.DUMPED.search(bad))
        for good in ["'%s VC available.' % coins.label(wallet.wallet_balance)",
                     "'wallet_balance_vc': coins.as_json(wallet.wallet_balance) if wallet else None,"]:
            with self.subTest(good=good):
                self.assertFalse(self.PRINTED.search(good) or self.DUMPED.search(good))

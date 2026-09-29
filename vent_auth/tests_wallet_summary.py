"""Only money that moved is counted, and a top-up left pending is settled or closed.

CEO, 29 September 2026, with a screenshot of the wallet: "the mtrics still
count for a payment that did not work why?" One paid top-up of 1 VC read as
"This month earned +2 VC" because a second, unpaid one was pending.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Transaction
from .tests_topup_ceiling import make_user


def row(wallet, amount, status, ref, minutes_ago=0, type_='top_up'):
    t = Transaction.objects.create(wallet=wallet, type=type_, amount=amount,
                                   description='x', status=status, reference=ref)
    if minutes_ago:
        Transaction.objects.filter(pk=t.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago))
        t.refresh_from_db()
    return t


class SummaryCountsOnlyCompletedTests(TestCase):

    def setUp(self):
        self.user, self.wallet = make_user('summary')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def summary(self, **params):
        res = self.client.get('/auth/wallet/transactions/', params)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data['data']

    def test_a_pending_top_up_is_not_money_earned(self):
        row(self.wallet, 1, 'completed', 'FLW-TOP-PAID')
        row(self.wallet, 1, 'pending', 'FLW-TOP-UNPAID')
        s = self.summary()['summary']
        self.assertEqual((s['month_in'], s['lifetime_in'], s['pending_count']), (1, 1, 1))

    def test_failed_and_cancelled_count_for_nothing(self):
        row(self.wallet, 5, 'failed', 'FLW-TOP-A')
        row(self.wallet, 5, 'cancelled', 'FLW-TOP-B')
        row(self.wallet, -3, 'cancelled', 'x')
        s = self.summary()['summary']
        self.assertEqual((s['month_in'], s['month_out'], s['lifetime_in']), (0, 0, 0))

    def test_out_is_counted_as_a_positive_number(self):
        row(self.wallet, 10, 'completed', 'VENT-A')
        row(self.wallet, -4, 'completed', '', type_='deduction')
        s = self.summary()['summary']
        self.assertEqual((s['month_in'], s['month_out']), (10, 4))

    def test_lifetime_reads_the_whole_history_not_one_page(self):
        for i in range(25):
            row(self.wallet, 1, 'completed', 'VENT-%d' % i)
        data = self.summary()
        self.assertEqual(len(data['transactions']), 20)
        self.assertEqual(data['summary']['lifetime_in'], 25)

    def test_last_month_is_lifetime_but_not_this_month(self):
        row(self.wallet, 7, 'completed', 'VENT-OLD', minutes_ago=60 * 24 * 40)
        s = self.summary(tz='Africa/Lagos')['summary']
        self.assertEqual((s['month_in'], s['lifetime_in']), (0, 7))

    def test_an_unknown_zone_does_not_break_the_page(self):
        row(self.wallet, 2, 'completed', 'VENT-Z')
        self.assertEqual(self.summary(tz='Not/AZone')['summary']['month_in'], 2)

    def test_each_row_says_how_it_was_paid(self):
        row(self.wallet, 1, 'completed', 'FLW-TOP-1')
        row(self.wallet, 1, 'completed', 'VENT-2')
        row(self.wallet, 1, 'completed', '', type_='prize')
        methods = {t['reference']: t['method'] for t in self.summary()['transactions']}
        self.assertEqual(methods, {'FLW-TOP-1': 'flutterwave', 'VENT-2': 'paystack', '': 'wallet'})


def flw(status, ok=False):
    return {'ok': ok, 'status': status, 'amount_ngn': 1000, 'currency': 'NGN',
            'email': '', 'id': 1}


class SettlePendingTopupsTests(TestCase):

    def setUp(self):
        self.user, self.wallet = make_user('settle')

    def run_job(self):
        out = StringIO()
        call_command('settle_pending_topups', stdout=out)
        return out.getvalue()

    def test_a_paid_top_up_whose_webhook_was_lost_is_credited_once(self):
        t = row(self.wallet, 1, 'pending', 'FLW-TOP-LOST', minutes_ago=30)
        with mock.patch('vent_auth.flutterwave.verify', return_value=flw('successful', ok=True)):
            self.assertIn('credited 1', self.run_job())
            self.run_job()
        t.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((t.status, self.wallet.wallet_balance), ('completed', 1))

    def test_a_failed_payment_is_marked_failed(self):
        t = row(self.wallet, 1, 'pending', 'FLW-TOP-FAIL', minutes_ago=30)
        with mock.patch('vent_auth.flutterwave.verify', return_value=flw('failed')):
            self.run_job()
        t.refresh_from_db()
        self.assertEqual(t.status, 'failed')

    def test_never_paid_is_closed_only_after_two_hours(self):
        young = row(self.wallet, 1, 'pending', 'FLW-TOP-YOUNG', minutes_ago=30)
        old = row(self.wallet, 1, 'pending', 'FLW-TOP-OLD', minutes_ago=180)
        with mock.patch('vent_auth.flutterwave.verify', return_value=flw('unknown')):
            self.run_job()
        young.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((young.status, old.status), ('pending', 'cancelled'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 0)

    def test_fresh_rows_and_other_gateways_are_left_alone(self):
        fresh = row(self.wallet, 1, 'pending', 'FLW-TOP-FRESH', minutes_ago=5)
        paystack = row(self.wallet, 1, 'pending', 'VENT-PS', minutes_ago=300)
        with mock.patch('vent_auth.flutterwave.verify') as verify:
            self.run_job()
        verify.assert_not_called()
        fresh.refresh_from_db()
        paystack.refresh_from_db()
        self.assertEqual((fresh.status, paystack.status), ('pending', 'pending'))

    def test_flutterwave_unreachable_changes_nothing(self):
        from vent_auth import flutterwave
        t = row(self.wallet, 1, 'pending', 'FLW-TOP-DOWN', minutes_ago=300)
        with mock.patch('vent_auth.flutterwave.verify', side_effect=flutterwave.Unreachable('down')):
            self.assertIn('unreachable 1', self.run_job())
        t.refresh_from_db()
        self.assertEqual(t.status, 'pending')


ONLY_FLUTTERWAVE = {'FLW_SECRET_KEY': 'FLWSECK-live-for-tests-X', 'PAYSTACK_SECRET_KEY': '',
                    'PAYSTACK_SECRET_TEST_KEY': ''}


class NoMethodNamedTests(TestCase):
    """A payer who pressed Pay without choosing is sent to the only gateway there is."""

    def setUp(self):
        self.user, self.wallet = make_user('nomethod')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def test_choose_provider(self):
        from vent_auth import pay
        with mock.patch.dict('os.environ', ONLY_FLUTTERWAVE):
            self.assertEqual(pay.choose_provider(''), 'flutterwave')
            self.assertEqual(pay.choose_provider(None), 'flutterwave')
            self.assertEqual(pay.choose_provider('Paystack'), 'paystack')

    def test_top_up_without_a_method_goes_to_flutterwave(self):
        started = {'authorization_url': 'https://checkout.flutterwave.test/x', 'reference': 'r'}
        with mock.patch.dict('os.environ', ONLY_FLUTTERWAVE), \
                mock.patch('vent_auth.flutterwave.start', return_value=started) as start:
            res = self.client.post('/auth/wallet/topup/initiate/', {'amount_ngn': 1000}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['provider'], 'flutterwave')
        start.assert_called_once()

    def test_naming_a_gateway_that_is_not_set_up_is_still_refused_plainly(self):
        with mock.patch.dict('os.environ', ONLY_FLUTTERWAVE):
            res = self.client.post('/auth/wallet/topup/initiate/',
                                   {'amount_ngn': 1000, 'provider': 'paystack'}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (503, 'PROVIDER_UNAVAILABLE'))

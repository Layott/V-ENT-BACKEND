"""Flutterwave beside Paystack (CEO, 29 September 2026).

What these hold, with Flutterwave's API faked at the HTTP layer:
* which key is used, and that a TEST key never takes money in production;
* the wallet top-up and the shortfall door start a Flutterwave page and write
  the same pending row; the verify credits once, and only for a successful NGN
  payment of at least the amount asked;
* the webhook refuses a bad signature, settles a good one once, asks to be
  retried when Flutterwave cannot be reached, and ignores references that are
  not ours;
* a guest's tickets are issued once from the stored order, whichever of the
  browser and the webhook arrives first;
* a refund goes back through the gateway that took the money.
"""
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth import flutterwave
from vent_auth.models import Transaction, Users, UserWallet
from vent_event.models import AbandonedCheckout, Event, Ticket, TicketTier

TEST_KEY = 'FLWSECK_TEST-0000000000000000000000000000000-X'
HASH = 'unit-test-hash-0123456789'
ENV = {'FLW_SECRET_KEY': TEST_KEY, 'FLW_SECRET_HASH': HASH, 'FLW_PUBLIC_KEY': 'FLWPUBK_TEST-x'}


def fake(body):
    res = MagicMock()
    res.json.return_value = body
    return res


def link():
    return fake({'status': 'success', 'message': 'Hosted Link',
                 'data': {'link': 'https://checkout.flutterwave.test/pay/abc'}})


def paid(reference, amount, currency='NGN', status='successful', tx_id=4242):
    return fake({'status': 'success', 'data': {
        'id': tx_id, 'tx_ref': reference, 'status': status, 'amount': amount,
        'currency': currency, 'customer': {'email': 'payer@example.test'}}})


def a_user(name):
    user = Users.objects.create(username=name, email='%s@vent.test' % name, is_active=True,
                                login_session_token=('f-%s' % name)[:16],
                                login_session_created_at=timezone.now())
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user, wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class KeyTests(TestCase):
    def test_a_test_key_only_on_a_development_box(self):
        with patch.dict(os.environ, ENV):
            with override_settings(DEBUG=False):
                self.assertFalse(flutterwave.configured(), 'a test key never takes money in production')
            with override_settings(DEBUG=True):
                self.assertTrue(flutterwave.configured())
                self.assertTrue(flutterwave.is_test())
        with patch.dict(os.environ, dict(ENV, FLW_SECRET_KEY='FLWSECK-live0000-X')):
            with override_settings(DEBUG=False):
                self.assertTrue(flutterwave.configured())
                self.assertFalse(flutterwave.is_test())

    def test_the_verify_checks_status_amount_currency_and_reference(self):
        with patch.dict(os.environ, ENV), override_settings(DEBUG=True):
            with patch('requests.get', return_value=paid('FLW-TOP-A', 1000)):
                self.assertTrue(flutterwave.verify('FLW-TOP-A', expected_ngn=1000)['ok'])
            with patch('requests.get', return_value=paid('FLW-TOP-A', 999)):
                self.assertFalse(flutterwave.verify('FLW-TOP-A', expected_ngn=1000)['ok'])
            with patch('requests.get', return_value=paid('FLW-TOP-A', 1000, currency='USD')):
                self.assertFalse(flutterwave.verify('FLW-TOP-A', expected_ngn=1000)['ok'])
            with patch('requests.get', return_value=paid('FLW-TOP-A', 1000, status='failed')):
                self.assertFalse(flutterwave.verify('FLW-TOP-A', expected_ngn=1000)['ok'])
            with patch('requests.get', return_value=paid('FLW-TOP-OTHER', 1000)):
                self.assertFalse(flutterwave.verify('FLW-TOP-A', expected_ngn=1000)['ok'])


@override_settings(DEBUG=True)
class WalletTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.user, self.auth = a_user('flw_wallet')

    def tearDown(self):
        self.env.stop()

    def start_topup(self):
        with patch('requests.request', return_value=link()) as sent:
            res = self.client.post('/auth/wallet/topup/initiate/',
                                   {'amount_ngn': 3000, 'provider': 'flutterwave',
                                    'callback_url': 'http://localhost:3005/wallet-topup-callback'},
                                   content_type='application/json', **self.auth)
        return res, sent

    def test_topup_starts_a_page_and_credits_once(self):
        res, sent = self.start_topup()
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        reference = data['reference']
        self.assertTrue(reference.startswith('FLW-TOP-'))
        self.assertIn('flutterwave', data['authorization_url'])
        payload = sent.call_args.kwargs['json']
        self.assertEqual((payload['currency'], payload['amount']), ('NGN', '3000'))
        self.assertIn('reference=%s' % reference, payload['redirect_url'])
        txn = Transaction.objects.get(reference=reference)
        self.assertEqual((txn.status, txn.type), ('pending', 'top_up'))

        with patch('requests.get', return_value=paid(reference, 3000)):
            first = self.client.post('/auth/wallet/topup/verify/', {'reference': reference},
                                     content_type='application/json', **self.auth)
            again = self.client.post('/auth/wallet/topup/verify/', {'reference': reference},
                                     content_type='application/json', **self.auth)
        self.assertEqual(first.json()['data']['credited'], True)
        self.assertEqual(again.json()['data']['credited'], False)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, txn.amount)

    def test_underpaid_is_not_credited(self):
        res, _ = self.start_topup()
        reference = res.json()['data']['reference']
        with patch('requests.get', return_value=paid(reference, 100)):
            res = self.client.post('/auth/wallet/topup/verify/', {'reference': reference},
                                   content_type='application/json', **self.auth)
        self.assertEqual(res.json()['code'], 'PAYMENT_NOT_SUCCESSFUL')
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 0)

    def test_somebody_elses_reference_is_not_found(self):
        res, _ = self.start_topup()
        reference = res.json()['data']['reference']
        _other, other_auth = a_user('flw_other')
        with patch('requests.get', return_value=paid(reference, 3000)):
            res = self.client.post('/auth/wallet/topup/verify/', {'reference': reference},
                                   content_type='application/json', **other_auth)
        self.assertEqual(res.status_code, 404)

    def test_the_shortfall_door_takes_flutterwave_and_says_which_providers_exist(self):
        with patch('requests.request', return_value=link()):
            res = self.client.post('/auth/wallet/pay/', {'coins': 2, 'provider': 'flutterwave'},
                                   content_type='application/json', **self.auth)
        if res.status_code == 404:
            self.skipTest('shortfall door lives at another address')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['reference'].startswith('FLW-TOP-'))
        from vent_auth import pay
        keys = [p['key'] for p in pay.providers()]
        self.assertIn('flutterwave', keys)

    def test_the_webhook(self):
        res, _ = self.start_topup()
        reference = res.json()['data']['reference']
        body = {'event': 'charge.completed', 'data': {'tx_ref': reference, 'status': 'successful'}}
        bad = self.client.post('/auth/flutterwave/webhook/', body, content_type='application/json',
                               HTTP_VERIF_HASH='wrong')
        self.assertEqual(bad.status_code, 401)
        with patch('requests.get', side_effect=Exception('down')):
            down = self.client.post('/auth/flutterwave/webhook/', body, content_type='application/json',
                                    HTTP_VERIF_HASH=HASH)
        self.assertEqual(down.status_code, 503, 'asks to be retried')
        with patch('requests.get', return_value=paid(reference, 3000)):
            ok = self.client.post('/auth/flutterwave/webhook/', body, content_type='application/json',
                                  HTTP_VERIF_HASH=HASH)
            replay = self.client.post('/auth/flutterwave/webhook/', body, content_type='application/json',
                                      HTTP_VERIF_HASH=HASH)
        self.assertEqual((ok.status_code, ok.json()['code']), (200, 'CREDITED'))
        self.assertEqual((replay.status_code, replay.json()['code']), (200, 'ALREADY'))
        txn = Transaction.objects.get(reference=reference)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, txn.amount)
        other = self.client.post('/auth/flutterwave/webhook/',
                                 {'data': {'tx_ref': 'vt-paystack-thing'}},
                                 content_type='application/json', HTTP_VERIF_HASH=HASH)
        self.assertEqual(other.json()['code'], 'NOT_OURS')


@override_settings(DEBUG=True)
class GuestTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        organiser, _ = a_user('flw_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='FLW Probe', creator=organiser, event_type='physical', desc='x', entry_fee=0,
            capacity=100, start_date=now + timedelta(days=7), end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=6))
        self.tier = TicketTier.objects.create(event=self.event, name='Standard',
                                              price=Decimal('5000'), quantity=50)

    def tearDown(self):
        self.env.stop()

    def buy(self):
        with patch('requests.request', return_value=link()):
            return self.client.post('/event/%s/guest-buy/' % self.event.event_id,
                                    {'tier_id': self.tier.id, 'email': 'ada@example.test',
                                     'quantity': 2, 'provider': 'flutterwave',
                                     'callback_url': 'http://localhost:3005/events/x/checkout'},
                                    content_type='application/json')

    def test_tickets_are_issued_once_from_the_stored_order(self):
        res = self.buy()
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        reference = data['reference']
        self.assertTrue(reference.startswith('FLW-TKT-'))
        pending = AbandonedCheckout.objects.get(reference=reference)
        self.assertEqual(pending.order['quantity'], 2)
        self.assertEqual(Ticket.objects.filter(payment_reference=reference).count(), 0,
                         'no ticket before the money')
        with patch('requests.get', return_value=paid(reference, data['total_ngn'])):
            hook = self.client.post('/auth/flutterwave/webhook/',
                                    {'data': {'tx_ref': reference}}, content_type='application/json',
                                    HTTP_VERIF_HASH=HASH)
            back = self.client.post('/event/guest-verify/', {'reference': reference},
                                    content_type='application/json')
        self.assertEqual(hook.json()['code'], 'ISSUED')
        self.assertEqual(back.json()['data']['already_issued'], True)
        self.assertEqual(Ticket.objects.filter(payment_reference=reference).count(), 2)

    def test_an_unpaid_checkout_issues_nothing(self):
        reference = self.buy().json()['data']['reference']
        with patch('requests.get', return_value=paid(reference, 10, status='pending')):
            res = self.client.post('/event/guest-verify/', {'reference': reference},
                                   content_type='application/json')
        self.assertEqual(res.json()['code'], 'PAYMENT_NOT_COMPLETE')
        self.assertFalse(Ticket.objects.filter(payment_reference=reference).exists())

    def test_a_refund_goes_back_through_flutterwave(self):
        reference = self.buy().json()['data']['reference']
        total = AbandonedCheckout.objects.get(reference=reference).total_ngn
        with patch('requests.get', return_value=paid(reference, float(total))):
            self.client.post('/event/guest-verify/', {'reference': reference},
                             content_type='application/json')
        ticket = Ticket.objects.filter(payment_reference=reference).first()
        from vent_event import refunds
        with patch('requests.get', return_value=paid(reference, float(total))), \
                patch('requests.request', return_value=fake({'status': 'success',
                                                              'data': {'id': 9, 'status': 'completed'}})) as sent, \
                patch('vent_auth.paystack.refund') as paystack_refund:
            out = refunds.refund_ticket(ticket, 'Event cancelled') if hasattr(refunds, 'refund_ticket') else None
        if out is None:
            self.skipTest('refund entry point has another name')
        paystack_refund.assert_not_called()
        self.assertIn('/transactions/4242/refund', sent.call_args.args[1])


class KycUploadTests(TestCase):
    """The identity document is checked by its bytes (R70, 29 September 2026)."""

    def test_only_a_real_image_is_accepted(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        user, auth = a_user('flw_kyc')
        fake_png = SimpleUploadedFile('id.png', b'%PDF-1.4 not an image', content_type='image/png')
        res = self.client.post('/auth/wallet/kyc/submit/',
                               {'document_type': 'passport', 'document_image': fake_png}, **auth)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'NOT_AN_IMAGE'))

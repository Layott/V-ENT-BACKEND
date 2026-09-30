"""Paying in your own currency through Flutterwave (inbox 361).

Flutterwave faked at the HTTP layer, as in tests_flutterwave. What these hold:
* only currencies proven on the account are quoted, NGN always;
* a quote is rounded UP (whole units where there is no smaller coin), signed,
  expires, and is refused for another currency or another price;
* a foreign checkout sends that currency, that amount and only that
  currency's methods, and remembers the charge; naira is unchanged;
* verify asks for the same currency and at least the same amount, and what it
  reports (and what is credited) is the naira price;
* a refund goes back in that currency, in proportion;
* the top-up door, end to end, credits the naira price's coins once;
* the quotes endpoint defaults to the payer's own currency.
"""
import os
from decimal import Decimal
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.test import TestCase, override_settings

from vent_auth import flutterwave
from vent_auth import flutterwave_currency as fx
from vent_auth.models import AdminSetting, ForeignCharge, Transaction, UserWallet
from vent_auth.tests_flutterwave import ENV, a_user, fake, link, paid

# NGN per one unit: what Flutterwave's rates call would give.
RATES = {'GHS': Decimal('111.665842'), 'XOF': Decimal('2.274605'), 'KES': Decimal('10.40269')}


def fake_rate(currency):
    if currency == 'NGN':
        return Decimal('1')
    return RATES[currency]


# A test key counts only on a development box, as in tests_flutterwave.
@override_settings(DEBUG=True)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        AdminSetting.put(fx.SETTING_SECTION, currencies=['GHS', 'XOF'])
        self.rate = patch.object(fx, 'rate', side_effect=fake_rate)
        self.rate.start()

    def tearDown(self):
        self.rate.stop()
        self.env.stop()


class QuoteTests(Base):
    def test_only_proven_currencies_are_offered(self):
        self.assertEqual(fx.enabled(), ['NGN', 'GHS', 'XOF'])
        with self.assertRaises(fx.CurrencyError):
            fx.quote(1000, 'KES')

    def test_the_amount_is_rounded_up_never_short(self):
        q = fx.quote(Decimal('5000'), 'GHS')
        self.assertEqual(q['amount'], '44.78')           # 44.776... rounded up
        self.assertGreaterEqual(Decimal(q['amount']) * RATES['GHS'], Decimal('5000'))
        self.assertEqual(fx.quote(Decimal('5000'), 'XOF')['amount'], '2199')  # whole francs
        self.assertIn('ghanamobilemoney', q['methods'])

    def test_a_quote_is_charged_exactly_and_only_for_what_it_was_for(self):
        q = fx.quote(Decimal('5000'), 'GHS')
        self.assertEqual(fx.redeem(q['quote'], 'GHS', Decimal('5000'))[0], Decimal('44.78'))
        for currency, price in (('XOF', '5000'), ('GHS', '6000')):
            with self.assertRaises(fx.CurrencyError) as caught:
                fx.redeem(q['quote'], currency, Decimal(price))
            self.assertEqual(caught.exception.code, 'QUOTE_MISMATCH')
        with self.assertRaises(fx.CurrencyError) as caught:
            fx.redeem(q['quote'] + 'x', 'GHS', Decimal('5000'))
        self.assertEqual(caught.exception.code, 'QUOTE_INVALID')
        with patch.object(signing, 'loads', side_effect=signing.SignatureExpired('old')):
            with self.assertRaises(fx.CurrencyError) as caught:
                fx.redeem(q['quote'], 'GHS', Decimal('5000'))
        self.assertEqual(caught.exception.code, 'QUOTE_EXPIRED')

    def test_the_default_is_the_payers_own_currency(self):
        self.assertEqual(fx.for_country('Ghana'), 'GHS')
        self.assertEqual(fx.for_country('Senegal'), 'XOF')
        self.assertEqual(fx.for_country('Kenya'), 'NGN')      # not enabled
        self.assertEqual(fx.for_country(''), 'NGN')


class CheckoutTests(Base):
    def test_a_foreign_checkout_sends_that_currency_and_remembers_it(self):
        q = fx.quote(Decimal('5000'), 'GHS')
        with patch('requests.request', return_value=link()) as sent:
            got = flutterwave.start(reference='FLW-TOP-GH1', amount_ngn=5000, email='a@b.test',
                                    currency='GHS', quote_token=q['quote'])
        payload = sent.call_args.kwargs['json']
        self.assertEqual((payload['currency'], payload['amount']), ('GHS', '44.78'))
        self.assertEqual(payload['payment_options'], 'card, ghanamobilemoney')
        self.assertEqual(got['currency'], 'GHS')
        charge = ForeignCharge.objects.get(reference='FLW-TOP-GH1')
        self.assertEqual((charge.currency, charge.amount, charge.amount_ngn),
                         ('GHS', Decimal('44.78'), Decimal('5000')))

    def test_naira_is_unchanged(self):
        with patch('requests.request', return_value=link()) as sent:
            flutterwave.start(reference='FLW-TOP-NG1', amount_ngn=3000, email='a@b.test')
        payload = sent.call_args.kwargs['json']
        self.assertEqual((payload['currency'], payload['amount']), ('NGN', '3000'))
        self.assertNotIn('payment_options', payload)
        self.assertFalse(ForeignCharge.objects.exists())

    def test_verify_asks_for_the_same_currency_and_amount_and_reports_naira(self):
        ForeignCharge.objects.create(reference='FLW-TOP-GH2', currency='GHS', amount=Decimal('44.78'),
                                     rate=RATES['GHS'], amount_ngn=Decimal('5000'))
        with patch('requests.get', return_value=paid('FLW-TOP-GH2', 44.78, currency='GHS')):
            good = flutterwave.verify('FLW-TOP-GH2', expected_ngn=5000)
        self.assertTrue(good['ok'])
        self.assertEqual(good['amount_ngn'], 5000.0)
        for amount, currency in ((44.00, 'GHS'), (44.78, 'NGN'), (5000, 'NGN')):
            with patch('requests.get', return_value=paid('FLW-TOP-GH2', amount, currency=currency)):
                self.assertFalse(flutterwave.verify('FLW-TOP-GH2', expected_ngn=5000)['ok'],
                                 (amount, currency))

    def test_a_refund_goes_back_in_that_currency_in_proportion(self):
        ForeignCharge.objects.create(reference='FLW-TKT-GH3', currency='GHS', amount=Decimal('44.78'),
                                     rate=RATES['GHS'], amount_ngn=Decimal('5000'))
        with patch('requests.get', return_value=paid('FLW-TKT-GH3', 44.78, currency='GHS')), \
                patch('requests.request', return_value=fake({'status': 'success', 'data': {}})) as sent:
            flutterwave.refund('FLW-TKT-GH3', 2500)
        self.assertEqual(sent.call_args.kwargs['json'], {'amount': '22.39'})

    def test_a_currency_not_proven_is_refused_before_flutterwave_is_asked(self):
        with patch('requests.request', return_value=link()) as sent:
            with self.assertRaises(fx.CurrencyError):
                flutterwave.start(reference='FLW-TOP-KE1', amount_ngn=5000, email='a@b.test',
                                  currency='KES')
        sent.assert_not_called()


class TopUpDoorTests(Base):
    def setUp(self):
        super().setUp()
        self.user, self.auth = a_user('flw_cedi')

    def test_a_cedi_top_up_credits_the_naira_price_once(self):
        q = fx.quote(Decimal('5000'), 'GHS')
        with patch('requests.request', return_value=link()) as sent:
            res = self.client.post('/auth/wallet/topup/initiate/', {
                'amount_ngn': 5000, 'provider': 'flutterwave', 'currency': 'GHS', 'quote': q['quote'],
                'callback_url': 'http://localhost:3005/wallet-topup-callback',
            }, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual((data['currency'], data['amount']), ('GHS', '44.78'))
        self.assertEqual(sent.call_args.kwargs['json']['currency'], 'GHS')
        reference = data['reference']
        txn = Transaction.objects.get(reference=reference)
        self.assertEqual(txn.amount, 5)                       # 5 VC for 5,000 NGN

        with patch('requests.get', return_value=paid(reference, 44.78, currency='GHS')):
            first = self.client.post('/auth/wallet/topup/verify/', {'reference': reference},
                                     content_type='application/json', **self.auth)
            again = self.client.post('/auth/wallet/topup/verify/', {'reference': reference},
                                     content_type='application/json', **self.auth)
        self.assertTrue(first.json()['data']['credited'])
        self.assertFalse(again.json()['data']['credited'])
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 5)

    def test_a_stale_price_is_refused_with_a_code(self):
        q = fx.quote(Decimal('5000'), 'GHS')
        with patch('requests.request', return_value=link()):
            res = self.client.post('/auth/wallet/topup/initiate/', {
                'amount_ngn': 6000, 'provider': 'flutterwave', 'currency': 'GHS', 'quote': q['quote'],
            }, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'QUOTE_MISMATCH')


class QuotesEndpointTests(Base):
    def test_every_enabled_currency_is_quoted_and_the_default_is_theirs(self):
        user, auth = a_user('flw_accra')
        user.country = 'Ghana'
        user.save(update_fields=['country'])
        res = self.client.get('/auth/pay/currencies/?amount_ngn=5000', **auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual([c['code'] for c in data['currencies']], ['NGN', 'GHS', 'XOF'])
        self.assertEqual(data['default'], 'GHS')
        ghs = next(c for c in data['currencies'] if c['code'] == 'GHS')
        self.assertEqual(ghs['amount'], '44.78')

    def test_a_nonsense_amount_is_refused(self):
        for amount in ('', 'abc', '-5', 'NaN', '999999999999'):
            res = self.client.get('/auth/pay/currencies/?amount_ngn=%s' % amount)
            self.assertEqual(res.status_code, 400, amount)
            self.assertEqual(res.json()['code'], 'INVALID_INPUT')

"""Paying in your own currency through Flutterwave (inbox 361).

CEO, 29 September 2026: "yeah using flutterwave, we have options of payment
available for different countries and options which you can check."

## The rules (tasks/specs/local-currency-2026-09-29.md)

* **Prices stay in naira.** VENT COINS, tickets and entry fees are set in NGN.
  A local currency is how somebody PAYS, never what a thing costs.
* **The amount is fixed before anybody pays.** `quote()` converts the naira
  price with Flutterwave's own rate (`GET /v3/transfers/rates`) and hands back
  a signed quote. The screen shows that amount and that rate; checkout charges
  exactly the quote it is handed, so what was shown is what is charged even if
  the rate moves in between. The quote expires in 15 minutes.
* **Each checkout remembers what it charged.** A ForeignCharge row holds the
  reference, currency, amount, rate and the naira value. Verify asks for that
  currency and at least that amount; a refund goes back in that currency.
* **What is credited is the naira value that was asked for.** A rate that
  moves after payment cannot change what somebody receives.
* **Only currencies proven on the account are offered.** `manage.py
  flw_currencies` asks Flutterwave for a checkout link in each (a link, not a
  charge) and records the ones it accepts. Until it has run, only NGN.

Methods per currency from the v3 payment-methods documentation, read on
29 September and checked again on 30 September 2026 (francophone mobile
money is `mobilemoneyxof` / `mobilemoneyxaf`).
"""
import logging
import math
from decimal import Decimal, ROUND_UP

from django.core import signing
from django.core.cache import cache

log = logging.getLogger(__name__)

#: code -> label, the countries (by the English name the IP lookup returns)
#: that pay in it, and the Flutterwave `payment_options` it supports.
CURRENCIES = {
    'NGN': {'label': 'Nigerian naira', 'countries': ['Nigeria'],
            'methods': 'card,banktransfer,ussd,account,internetbanking,nqr,enaira,opay'},
    'GHS': {'label': 'Ghanaian cedi', 'countries': ['Ghana'],
            'methods': 'card,ghanamobilemoney'},
    'KES': {'label': 'Kenyan shilling', 'countries': ['Kenya'], 'methods': 'card,mpesa'},
    'UGX': {'label': 'Ugandan shilling', 'countries': ['Uganda'],
            'methods': 'card,mobilemoneyuganda'},
    'RWF': {'label': 'Rwandan franc', 'countries': ['Rwanda'],
            'methods': 'card,mobilemoneyrwanda'},
    'TZS': {'label': 'Tanzanian shilling', 'countries': ['Tanzania'],
            'methods': 'card,mobilemoneytanzania'},
    'MWK': {'label': 'Malawian kwacha', 'countries': ['Malawi'],
            'methods': 'card,mobilemoneymalawi'},
    'ZAR': {'label': 'South African rand', 'countries': ['South Africa'],
            'methods': 'card,account,1voucher'},
    'XOF': {'label': 'West African CFA franc',
            'countries': ['Benin', 'Burkina Faso', "Côte d'Ivoire", 'Ivory Coast',
                          'Guinea-Bissau', 'Mali', 'Niger', 'Senegal', 'Togo'],
            'methods': 'card,mobilemoneyxof'},
    'XAF': {'label': 'Central African CFA franc',
            'countries': ['Cameroon', 'Central African Republic', 'Chad', 'Congo',
                          'Republic of the Congo', 'Equatorial Guinea', 'Gabon'],
            'methods': 'card,mobilemoneyxaf'},
    'EGP': {'label': 'Egyptian pound', 'countries': ['Egypt'], 'methods': 'card,fawrypay'},
    'USD': {'label': 'US dollar', 'countries': ['United States'], 'methods': 'card,account'},
    'GBP': {'label': 'British pound', 'countries': ['United Kingdom'], 'methods': 'card,account'},
    'EUR': {'label': 'Euro',
            'countries': ['Germany', 'France', 'Spain', 'Italy', 'Netherlands', 'Belgium',
                          'Portugal', 'Ireland', 'Austria', 'Finland', 'Greece',
                          'Luxembourg', 'Slovakia', 'Slovenia', 'Estonia', 'Latvia',
                          'Lithuania', 'Malta', 'Cyprus', 'Croatia'],
            'methods': 'card,account'},
}

#: Charged in whole units: there is no smaller coin people pay with.
WHOLE_UNITS = {'XOF', 'XAF', 'UGX', 'RWF', 'TZS', 'MWK'}

#: The least Flutterwave will take in a currency, found on the live account
#: on 30 September 2026: 0.99 USD, GBP or EUR is refused ("One or more required
#: parameters missing") and 1.00 accepted. A price that converts to less is not
#: offered in that currency at all, rather than offered and refused.
MINIMUM = {'USD': Decimal('1'), 'GBP': Decimal('1'), 'EUR': Decimal('1')}

QUOTE_SALT = 'vent.flw-quote'
QUOTE_SECONDS = 15 * 60
RATE_SECONDS = 10 * 60
SETTING_SECTION = 'flutterwave'


class CurrencyError(Exception):
    """A currency that cannot be charged, or a quote that cannot be used."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def enabled():
    """The currencies proven on the live account, NGN always first."""
    from .models import AdminSetting
    stored = (AdminSetting.load().data or {}).get(SETTING_SECTION, {}).get('currencies') or []
    out = ['NGN'] + [c for c in stored if c in CURRENCIES and c != 'NGN']
    return out


def for_country(country_name):
    """The currency somebody in this country pays in, if it is enabled."""
    if not country_name:
        return 'NGN'
    name = str(country_name).strip().lower()
    for code in enabled():
        if any(name == c.lower() for c in CURRENCIES[code]['countries']):
            return code
    return 'NGN'


def rate(currency):
    """NGN per one unit of `currency`, Flutterwave's own figure, cached."""
    if currency == 'NGN':
        return Decimal('1')
    key = 'flw-rate:%s' % currency
    cached = cache.get(key)
    if cached:
        return Decimal(str(cached))
    from . import flutterwave
    data = flutterwave._call('GET', '/transfers/rates?amount=1000&destination_currency=NGN'
                                    '&source_currency=%s' % currency)
    source = Decimal(str((data.get('source') or {}).get('amount') or 0))
    if source <= 0:
        raise CurrencyError('RATE_UNAVAILABLE', 'No rate for %s just now.' % currency)
    per_unit = (Decimal('1000') / source).quantize(Decimal('0.000001'))
    cache.set(key, str(per_unit), RATE_SECONDS)
    return per_unit


def amount_in(currency, amount_ngn, per_unit):
    """What to charge in `currency` for `amount_ngn`, rounded UP so the naira
    received is never short of the price."""
    raw = Decimal(str(amount_ngn)) / per_unit
    step = Decimal('1') if currency in WHOLE_UNITS else Decimal('0.01')
    return raw.quantize(step, rounding=ROUND_UP)


def quote(amount_ngn, currency):
    """A signed quote the screen shows and checkout charges."""
    currency = str(currency or 'NGN').upper()
    if currency not in enabled():
        raise CurrencyError('CURRENCY_UNAVAILABLE', 'That currency cannot be used here.')
    per_unit = rate(currency)
    amount = amount_in(currency, amount_ngn, per_unit)
    if amount < MINIMUM.get(currency, Decimal('0')):
        raise CurrencyError('CURRENCY_BELOW_MINIMUM', 'That amount is too small to pay in %s.' % currency)
    body = {'c': currency, 'a': str(amount), 'r': str(per_unit), 'n': str(Decimal(str(amount_ngn)))}
    return {
        'code': currency,
        'label': CURRENCIES[currency]['label'],
        'methods': CURRENCIES[currency]['methods'].split(','),
        'amount': str(amount),
        'rate': str(per_unit),
        'amount_ngn': str(Decimal(str(amount_ngn))),
        'quote': signing.dumps(body, salt=QUOTE_SALT, compress=True),
    }


def redeem(token, currency, amount_ngn):
    """The quote a checkout was handed, checked: ours, unexpired, for this
    currency and this naira price. Returns (amount, rate) as Decimals."""
    try:
        body = signing.loads(token, salt=QUOTE_SALT, max_age=QUOTE_SECONDS)
    except signing.SignatureExpired:
        raise CurrencyError('QUOTE_EXPIRED', 'That price has expired. Check the amount again.') from None
    except signing.BadSignature:
        raise CurrencyError('QUOTE_INVALID', 'That price could not be read.') from None
    if body.get('c') != currency or Decimal(body.get('n')) != Decimal(str(amount_ngn)):
        raise CurrencyError('QUOTE_MISMATCH', 'That price is for something else. Check the amount again.')
    return Decimal(body['a']), Decimal(body['r'])


def choice(request):
    """What the payer chose at a door: `currency` and the `quote` they saw.

    Read through vent_auth.inputs: a currency is one of the codes in
    CURRENCIES and nothing else, and a quote is bounded text whose signature
    `redeem` checks before anything is charged. A bad value is refused as
    INVALID_INPUT naming the field."""
    from . import inputs
    data = getattr(request, 'data', {}) or {}
    raw = str(data.get('currency') or 'NGN').strip().upper()
    currency = inputs.read_choice({'currency': raw}, 'currency', tuple(CURRENCIES), default='NGN')
    token = inputs.read_text(data, 'quote', max_length=2000, default='')
    return {'currency': currency, 'quote_token': token}


def terms(amount_ngn, currency='NGN', quote_token=''):
    """(currency, amount, rate, payment_options) for a checkout of `amount_ngn`.

    NGN is charged as it always was. Anything else must be enabled, and uses
    the quote the payer saw when there is one, else a fresh quote."""
    currency = str(currency or 'NGN').upper()
    if currency == 'NGN':
        return 'NGN', Decimal(str(amount_ngn)), Decimal('1'), None
    if currency not in enabled():
        raise CurrencyError('CURRENCY_UNAVAILABLE', 'That currency cannot be used here.')
    if quote_token:
        amount, per_unit = redeem(quote_token, currency, amount_ngn)
    else:
        per_unit = rate(currency)
        amount = amount_in(currency, amount_ngn, per_unit)
    if amount < MINIMUM.get(currency, Decimal('0')):
        raise CurrencyError('CURRENCY_BELOW_MINIMUM', 'That amount is too small to pay in %s.' % currency)
    # Flutterwave reads this as a comma + space list (v3 docs, checked 30 Sept).
    return currency, amount, per_unit, ', '.join(CURRENCIES[currency]['methods'].split(','))


def record(reference, currency, amount, per_unit, amount_ngn):
    """The row verify and refund read for a charge not in naira."""
    from .models import ForeignCharge
    ForeignCharge.objects.create(reference=reference, currency=currency, amount=amount,
                                 rate=per_unit, amount_ngn=Decimal(str(amount_ngn)))


def refund_amount(charge, amount_ngn):
    """Part of a foreign charge, in its currency, in proportion to the naira."""
    share = Decimal(str(amount_ngn)) / charge.amount_ngn if charge.amount_ngn else Decimal('0')
    raw = charge.amount * min(share, Decimal('1'))
    step = Decimal('1') if charge.currency in WHOLE_UNITS else Decimal('0.01')
    return raw.quantize(step)

"""A naira price has to be a whole number of VENT COINS.

Found by the walk on 12 September 2026: a Day 2 pass priced at 1,500 naira
charged ONE coin, because every naira-to-coin conversion on the platform
floors (`int(ngn // NGN_PER_COIN)`), and one coin is 1,000 naira. A 1,600
group rate charged one coin. Anything under 1,000 naira would have been free
from a wallet, while a guest paying the same tier through Paystack paid the
naira in full. Twenty-one call sites shared the floor.

The floor is not the fault. The coin is the unit of account on this platform
(the memberships module refused part-periods for the same reason), and a
price that is not whole coins is a price a wallet cannot pay. The fault was
accepting such a price from an organiser or a vendor and then quietly
charging less than they asked for. So the price is refused where it is set,
with the unit written in the refusal, and the conversion stays exact because
it only ever sees whole coins.

The code and the structured fields are the ones vent_billing already used for
its plan prices (`PRICE_NOT_WHOLE_COINS`, `nearest_lower`, `nearest_upper`),
so the screen has one translation for the one fault wherever it is raised.

Whether the coin should be smaller (NGN_PER_COIN is one environment variable)
so a 500 naira drink can exist is the CEO's decision and is written in the
12 September handover. Nothing here decides it.
"""
from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.response import Response

CODE = 'PRICE_NOT_WHOLE_COINS'


def ngn_per_coin():
    from vent_auth.views_wallet import NGN_PER_COIN
    return int(NGN_PER_COIN)


def whole_coins(amount_ngn):
    """`(ok, message)`. Zero is whole. Anything that is not a multiple of the
    coin is refused with the unit in the sentence."""
    if amount_ngn in (None, ''):
        return True, ''
    try:
        amount = Decimal(str(amount_ngn))
    except (InvalidOperation, TypeError, ValueError):
        return False, 'The price has to be a number.'
    if amount < 0:
        return False, 'A price cannot be negative.'
    unit = ngn_per_coin()
    if amount % unit != 0:
        return False, ('Prices are in VENT COINS, and one coin is %s naira. %s is not '
                       'a price a wallet can pay; use %s or %s.'
                       % (f'{unit:,}', f'{amount:,.0f}',
                          f'{(amount // unit) * unit:,.0f}',
                          f'{(amount // unit + 1) * unit:,.0f}'))
    return True, ''


def nearest(amount_ngn):
    """The two whole prices either side, as the fields the screen's
    translation fills in."""
    unit = ngn_per_coin()
    try:
        amount = Decimal(str(amount_ngn))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal(0)
    lower = int((amount // unit) * unit) if amount >= 0 else 0
    return {'nearest_lower': lower, 'nearest_upper': lower + unit, 'ngn_per_coin': unit}


def refuse_if_not_whole(amount_ngn, field=None, prefix=''):
    """The refusal as a Response, or None when the price is fine. Every door a
    price comes through calls this, so they all answer with the same code and
    the same fields."""
    ok, why = whole_coins(amount_ngn)
    if ok:
        return None
    body = {'status': 'error', 'data': nearest(amount_ngn),
            'message': (prefix + ': ' + why) if prefix else why, 'code': CODE}
    if field:
        body['field'] = field
    return Response(body, status=status.HTTP_400_BAD_REQUEST)

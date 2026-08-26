"""Turning what an organiser typed into VENT COINS.

A prize pool is entered in whatever currency the organiser thinks in - naira,
dollars, or coins directly - and the platform pays in VENT COINS. That
conversion happens here, on the server, because money worked out in a browser is
money somebody can edit before it is sent.

One coin is 1,000 NGN, the rate every screen on the platform states. The dollar
rate is a setting rather than a live feed on purpose: a prize pool that moves
between the moment it is announced and the moment it is paid is a dispute
waiting to happen, so the rate is fixed, visible, and changed deliberately.
"""
import os
from decimal import Decimal, ROUND_HALF_UP

CURRENCIES = {
    'VC': {'label': 'VENT COINS', 'symbol': 'VC'},
    'NGN': {'label': 'Nigerian Naira', 'symbol': '₦'},
    'USD': {'label': 'US Dollar', 'symbol': '$'},
}


def ngn_per_coin():
    from vent_auth.views_wallet import NGN_PER_COIN
    return Decimal(NGN_PER_COIN)


def ngn_per_usd():
    try:
        return Decimal(os.environ.get('NGN_TO_USD_RATE', '1500'))
    except Exception:
        return Decimal('1500')


def rates():
    """What the create screen needs to show a live conversion."""
    return {
        'ngn_per_coin': int(ngn_per_coin()),
        'ngn_per_usd': int(ngn_per_usd()),
        'currencies': CURRENCIES,
        'note': 'VENT COINS are the platform currency. 1 VC = '
                f'{int(ngn_per_coin())} NGN.',
    }


def to_coins(amount, currency):
    """Convert an amount in a supported currency into whole VENT COINS.

    Returns Decimal('0') for anything unparseable rather than raising: a prize
    field left blank is a position with no prize, not an error.
    """
    try:
        value = Decimal(str(amount or '0'))
    except Exception:
        return Decimal('0')
    if value <= 0:
        return Decimal('0')

    code = (currency or 'VC').upper()
    if code == 'VC':
        coins = value
    elif code == 'NGN':
        coins = value / ngn_per_coin()
    elif code == 'USD':
        coins = (value * ngn_per_usd()) / ngn_per_coin()
    else:
        return Decimal('0')

    # Coins are whole. Rounding half up rather than down, so an organiser who
    # types a total never ends up with a pool that pays out slightly less than
    # what they announced.
    return coins.quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def from_coins(coins, currency):
    """The other direction, for showing what a pool is worth in local money."""
    try:
        value = Decimal(str(coins or '0'))
    except Exception:
        return Decimal('0')

    code = (currency or 'VC').upper()
    if code == 'VC':
        return value
    if code == 'NGN':
        return (value * ngn_per_coin()).quantize(Decimal('0.01'))
    if code == 'USD':
        return ((value * ngn_per_coin()) / ngn_per_usd()).quantize(Decimal('0.01'))
    return Decimal('0')

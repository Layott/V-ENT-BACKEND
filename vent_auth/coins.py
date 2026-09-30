"""VENT COINS amounts: the one place that reads, writes and prints them.

CEO, 30 September 2026: "Yes people should be able to send amounts under
N1000". A coin is 1000 naira, and balances were whole numbers, so nothing
under 1 VC could move. Balances and wallet transactions are now
DECIMAL(14, 2): a coin splits into hundredths, 0.01 VC, which is 10 naira.

Prices, entry fees, top ups and withdrawals still ask for whole coins. A whole
number is a valid decimal, so they needed no change; only moving coins from
one wallet to another takes a fraction.

Three things go wrong with a Decimal if nobody is careful, and each has a
function here:

- parsing. `int("0.2")` refuses and `float("0.1")` is not 0.1. `parse` reads
  text or a number into an exact Decimal, and refuses a third decimal place
  rather than rounding somebody's money for them.
- JSON. `json.dumps` cannot encode a Decimal, and a JSONField (notification
  metadata, audit rows) is json.dumps. `as_json` gives a number: an int when
  whole, so every existing screen and test reading `20` still reads `20`.
- printing. `'%d VC' % Decimal('0.8')` prints "0 VC". `label` prints "0.8".
"""

from decimal import Decimal, InvalidOperation, ROUND_DOWN

#: Hundredths of a coin: the smallest amount a wallet can hold.
PLACES = Decimal('0.01')
SMALLEST = PLACES


class AmountError(ValueError):
    """Not an amount of coins. `code` is the API refusal code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse(value):
    """An amount somebody typed or sent, as an exact Decimal of hundredths.

    Accepts an int, a Decimal, or text such as "0.2", "12.35", "5". A float
    is read through its shortest text form, so 0.2 means 0.2. Refuses a bool,
    anything that is not a number, more than two decimal places
    (AMOUNT_TOO_PRECISE), and nothing or less (AMOUNT_MUST_POSITIVE).
    """
    if value is None or isinstance(value, bool):
        raise AmountError('AMOUNT_REQUIRED')
    if isinstance(value, float):
        value = repr(value)
    try:
        amount = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise AmountError('AMOUNT_MUST_NUMBER')
    if not amount.is_finite():
        raise AmountError('AMOUNT_MUST_NUMBER')
    if amount != amount.quantize(PLACES, rounding=ROUND_DOWN):
        raise AmountError('AMOUNT_TOO_PRECISE')
    if amount <= 0:
        raise AmountError('AMOUNT_MUST_POSITIVE')
    return amount.quantize(PLACES)


def exact(value):
    """Any stored or computed amount as a Decimal of hundredths (no checks)."""
    if value is None:
        return Decimal('0.00')
    if isinstance(value, float):
        value = repr(value)
    return Decimal(str(value)).quantize(PLACES)


def as_json(value):
    """A number JSON can carry: an int when whole, else a float of 2 places."""
    if value is None:
        return 0
    amount = exact(value)
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def label(value):
    """"12", "0.8", "12.35": no trailing zeros, never rounded away."""
    amount = exact(value)
    if amount == amount.to_integral_value():
        return str(int(amount))
    return format(amount.normalize(), 'f')

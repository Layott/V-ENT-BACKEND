"""Which Paystack key we are using, decided in one place.

CEO, 7 September 2026: "can we test with a paystack test secret key?"

They could all along. `PAYSTACK_SECRET_TEST_KEY` has been sitting in `.env`
holding a real `sk_test_` key, and every reader looks for `PAYSTACK_SECRET_KEY`,
so the guest card checkout has been answering PAYMENT_UNAVAILABLE - "card
payment is not set up for this platform yet" - for a key that was there.

## The rule, and the direction it fails in

The live key wins whenever it is set. The test key is used ONLY when there is
no live key AND `DEBUG` is on.

That asymmetry is the whole point. Falling back to a test key in production
would take orders that look paid and are not: a `sk_test_` charge succeeds, the
ticket issues, and no money exists. That is worse than refusing the sale,
because refusing is visible in the same minute and a fake sale is discovered
when somebody tries to reconcile it. So the fallback is barred wherever DEBUG
is off, whatever else is set.

`is_test()` exists so a caller can SAY so. A checkout running on test keys must
never look identical to one taking real money.
"""
import os

from django.conf import settings

LIVE = 'PAYSTACK_SECRET_KEY'
TEST = 'PAYSTACK_SECRET_TEST_KEY'


def secret():
    """The key to send, or '' when there is none we may use."""
    live = (os.environ.get(LIVE) or '').strip()
    if live:
        return live
    # Only ever off a development box. See the note above for why this cannot
    # be relaxed "just for a staging check".
    if getattr(settings, 'DEBUG', False):
        return (os.environ.get(TEST) or '').strip()
    return ''


def is_test():
    """True when the key in use is a Paystack test key.

    Read off the key itself rather than off which variable it came from: a live
    key pasted into the test variable is still a live key, and a test key
    pasted into the live one still cannot take money. The key knows what it is.
    """
    return secret().startswith('sk_test_')


def configured():
    """Whether a card payment can be attempted at all."""
    return bool(secret())


def headers():
    """The Authorization header both callers need, built once."""
    return {
        'Authorization': 'Bearer %s' % secret(),
        'Content-Type': 'application/json',
    }

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


BASE = 'https://api.paystack.co'


class Unreachable(Exception):
    """Paystack did not answer. Nothing was charged; it is safe to retry."""


class Refused(Exception):
    """Paystack answered and said no, in its own words (`str(exc)`).

    Its 400 carries the useful half: an address it will not accept, an
    amount under its floor. Three callers each posted to /transaction/
    initialize themselves, and two of them called raise_for_status and
    threw that sentence away, so a guest whose address Paystack refused was
    told the gateway could not be reached (18 September 2026).
    """

    def __init__(self, message):
        super().__init__(message or 'The payment could not be started.')

    @property
    def about_the_email(self):
        return 'email' in str(self).lower()


def initialize(payload, timeout=10):
    """POST /transaction/initialize. Returns Paystack's `data` (with the
    authorization_url) or raises Unreachable / Refused. One function, so
    the gateway's reason reaches the person at every door."""
    import logging

    import requests as http_requests

    log = logging.getLogger(__name__)
    try:
        res = http_requests.post('%s/transaction/initialize' % BASE, json=payload,
                                 headers=headers(), timeout=timeout)
        body = res.json()
    except Exception as exc:                                  # noqa: BLE001
        log.exception('paystack initialize failed')
        raise Unreachable(str(exc))
    if not body.get('status'):
        log.warning('paystack refused an initialize: %s', body.get('message'))
        raise Refused(body.get('message'))
    return body.get('data') or {}


def refund(reference, amount_ngn, timeout=10):
    """POST /refund: send part or all of a card payment back to the card.

    `reference` is the payment's own reference (the one a guest ticket
    carries in `payment_reference`); `amount_ngn` is what to send back, in
    naira, and Paystack takes it in kobo. A payment can be refunded in
    parts, one per ticket, up to what was charged. Paystack answers with a
    refund record whose status starts as pending; the money reaches the
    card in its own time. Raises Unreachable / Refused like initialize.
    """
    import logging

    import requests as http_requests

    log = logging.getLogger(__name__)
    payload = {'transaction': reference,
               'amount': int(round(float(amount_ngn) * 100))}
    try:
        res = http_requests.post('%s/refund' % BASE, json=payload,
                                 headers=headers(), timeout=timeout)
        body = res.json()
    except Exception as exc:                                  # noqa: BLE001
        log.exception('paystack refund failed for %s', reference)
        raise Unreachable(str(exc))
    if not body.get('status'):
        log.warning('paystack refused a refund of %s: %s', reference, body.get('message'))
        raise Refused(body.get('message'))
    return body.get('data') or {}

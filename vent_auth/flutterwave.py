"""Flutterwave: which key, starting a payment, confirming it, refunding it.

CEO, 29 September 2026: "we now have flutterwave available to use to collect
payments, lets take all options available", then "it should be live right
now". Flutterwave sits beside Paystack, never instead of it: every door that
took Paystack takes either, and the person chooses.

"All options" is Flutterwave's hosted checkout with no `payment_options`
filter, so the page offers every method switched on in the Flutterwave
dashboard: card, bank transfer, USSD, account, NQR, mobile money and the rest.
Which appear is the dashboard's decision, not this file's.

## The rules this file keeps

* **Which key.** The live key wins whenever it is set. A TEST key is used only
  on a DEBUG box, never in production, for the reason `paystack.py` gives: a
  test charge "succeeds", the ticket issues, and no money exists. The key says
  what it is (`FLWSECK_TEST`), so a test key pasted into the live variable is
  still refused in production.
* **Which provider owns a payment.** Every Flutterwave reference starts `FLW-`.
  Verify and refund read the prefix, so a payment is always confirmed and
  refunded where it was taken, with no column to keep in step.
* **Nothing is trusted from the browser or the webhook body.** Both only say
  "look at this reference"; the answer comes from asking Flutterwave, and it
  must say successful, in NGN, for at least the amount we asked for.
* **The webhook proves itself** with the secret hash set in the dashboard,
  compared in constant time (owner rule R72).

Checked on 29 September 2026 against the v3 documentation (Standard, webhooks,
verification, refunds) and against the test API itself: `/v3/payments`
answered a hosted link, `/v3/transactions/verify_by_reference?tx_ref=`
answered "No transaction was found" for a made-up reference. The keys V-ENT
holds (FLWPUBK_ / FLWSECK_) are v3 keys; the newer v4 API uses OAuth client
credentials and is a different integration.
"""
import hmac
import logging
import os
import uuid

from django.conf import settings

log = logging.getLogger(__name__)

BASE = 'https://api.flutterwave.com/v3'
PREFIX = 'FLW-'


class Unreachable(Exception):
    """Flutterwave did not answer. Nothing was charged; it is safe to retry."""


class Refused(Exception):
    """Flutterwave answered and said no, in its own words (`str(exc)`)."""

    def __init__(self, message):
        super().__init__(message or 'The payment could not be started.')


def secret():
    """The secret key to send, or '' when there is none we may use."""
    key = (os.environ.get('FLW_SECRET_KEY') or '').strip()
    if not key:
        return ''
    if key.startswith('FLWSECK_TEST') and not getattr(settings, 'DEBUG', False):
        return ''
    return key


def is_test():
    return secret().startswith('FLWSECK_TEST')


def configured():
    return bool(secret())


def secret_hash():
    return (os.environ.get('FLW_SECRET_HASH') or '').strip()


def owns(reference):
    return str(reference or '').startswith(PREFIX)


def new_reference(kind='PAY'):
    """`FLW-<kind>-<random>`: the prefix names the provider, the kind the door."""
    return '%s%s-%s' % (PREFIX, kind, uuid.uuid4().hex[:16].upper())


def _headers():
    return {'Authorization': 'Bearer %s' % secret(), 'Content-Type': 'application/json'}


def _call(method, path, payload=None, timeout=15):
    import requests as http_requests
    try:
        res = http_requests.request(method, '%s%s' % (BASE, path), json=payload,
                                    headers=_headers(), timeout=timeout)
        body = res.json()
    except Exception as exc:                                  # noqa: BLE001
        log.exception('flutterwave %s %s failed', method, path)
        raise Unreachable(str(exc))
    if body.get('status') != 'success':
        log.warning('flutterwave refused %s %s: %s', method, path, body.get('message'))
        raise Refused(body.get('message'))
    return body.get('data') or {}


def _with_reference(url, reference):
    """The page the payer comes back to, carrying `reference` as Paystack's
    return does, so every return page reads one parameter whichever provider
    took the money. Flutterwave adds its own `tx_ref` and `status` beside it."""
    if not url:
        return url
    joiner = '&' if '?' in url else '?'
    return '%s%sreference=%s' % (url, joiner, reference)


def start(*, reference, amount_ngn, email, name='', callback_url='', title='V-ENT',
          description='', meta=None):
    """A hosted checkout link. Every method enabled on the account is offered."""
    payload = {
        'tx_ref': reference,
        'amount': str(amount_ngn),
        'currency': 'NGN',
        'redirect_url': _with_reference(callback_url, reference),
        'customer': {'email': email, 'name': name or email},
        'customizations': {'title': title, 'description': description[:100]},
        'meta': {k: str(v) for k, v in (meta or {}).items()},
    }
    data = _call('POST', '/payments', payload)
    link = data.get('link')
    if not link:
        raise Refused('Flutterwave did not return a payment page.')
    return {'authorization_url': link, 'reference': reference}


def verify(reference, expected_ngn=None):
    """What Flutterwave says about this reference.

    Returns `{'ok': bool, 'status', 'amount_ngn', 'currency', 'email', 'id'}`.
    `ok` is true only for a successful NGN payment of at least `expected_ngn`:
    a payment for less than was asked is not a payment for the thing.
    """
    import requests as http_requests
    try:
        res = http_requests.get('%s/transactions/verify_by_reference' % BASE,
                                params={'tx_ref': reference}, headers=_headers(), timeout=15)
        body = res.json()
    except Exception as exc:                                  # noqa: BLE001
        log.exception('flutterwave verify failed for %s', reference)
        raise Unreachable(str(exc))
    data = body.get('data') or {}
    if body.get('status') != 'success' or not data:
        return {'ok': False, 'status': 'unknown', 'amount_ngn': 0, 'currency': '',
                'email': '', 'id': None}
    amount = float(data.get('amount') or 0)
    currency = str(data.get('currency') or '')
    ok = (data.get('status') == 'successful' and data.get('tx_ref') == reference
          and currency == 'NGN'
          and (expected_ngn is None or amount + 0.005 >= float(expected_ngn)))
    return {'ok': ok, 'status': data.get('status') or '', 'amount_ngn': amount,
            'currency': currency, 'email': (data.get('customer') or {}).get('email') or '',
            'id': data.get('id')}


def refund(reference, amount_ngn):
    """Send part or all of a payment back to where it came from."""
    found = verify(reference)
    if not found.get('id'):
        raise Refused('Flutterwave has no payment with that reference.')
    return _call('POST', '/transactions/%s/refund' % found['id'],
                 {'amount': str(amount_ngn)})


def signature_ok(request):
    """The webhook carries the dashboard's secret hash in `verif-hash`."""
    expected = secret_hash()
    given = request.headers.get('verif-hash') or ''
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())

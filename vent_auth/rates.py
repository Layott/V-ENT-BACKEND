"""Keeping the exchange rates honest.

The rates were seeded by hand and go stale the day they are written: the seeded
cedi rate was 0.0098 when the real one was 0.00827, which is a 15 per cent error
on every price a Ghanaian reader was shown.

So they are refreshed from a published feed. This is the one outbound
dependency the platform has, which is worth stating plainly given everything
else runs on the box:

  https://open.er-api.com/v6/latest/NGN   - no key, no account, daily updates

Nothing about V-ENT is sent to it. It is a GET for a table of public numbers,
and the request carries no user data, no identifiers and no referrer.

**It is allowed to fail.** A refresh that cannot reach the feed leaves the
existing rates exactly as they were and says so. Rates are display-only - money
moves in naira - so a stale rate makes a guide slightly wrong, while a blank one
would make prices unreadable. Never trade the second for the first.
"""
import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)

FEED_URL = 'https://open.er-api.com/v6/latest/NGN'
TIMEOUT_SECONDS = 20

# A rate outside this range is not a currency move, it is a broken response or a
# base-currency change at the far end. Refuse it rather than write nonsense over
# figures somebody is reading prices from.
MIN_RATE = Decimal('0.0000001')
MAX_RATE = Decimal('100000')


def fetch_rates(url=FEED_URL, timeout=TIMEOUT_SECONDS):
    """The feed's NGN table, or (None, reason).

    Returns (rates_dict, None) on success, where the dict maps currency code to
    how many of it one naira buys.
    """
    request = urllib.request.Request(url, headers={'User-Agent': 'V-ENT/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None, 'The rates service answered %s.' % response.status
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.URLError as exc:
        return None, 'Could not reach the rates service: %s' % exc.reason
    except (ValueError, TimeoutError) as exc:
        return None, 'The rates service sent something unreadable: %s' % exc

    if payload.get('result') != 'success':
        return None, 'The rates service reported: %s' % payload.get('result')

    if payload.get('base_code') != 'NGN':
        # Everything here is stored against the naira. A feed answering in some
        # other base would be silently wrong rather than obviously wrong.
        return None, 'The rates service answered in %s, not NGN.' % payload.get('base_code')

    rates = payload.get('rates') or {}
    if not rates:
        return None, 'The rates service sent no rates.'

    return rates, None


def refresh_rates(url=FEED_URL, timeout=TIMEOUT_SECONDS):
    """Update every active currency from the feed.

    Returns (updated_count, skipped, error). `error` is None on success; when it
    is set nothing was written at all.
    """
    from .models import Currency

    rates, error = fetch_rates(url, timeout)
    if error:
        logger.warning('Rate refresh failed: %s', error)
        return 0, [], error

    updated = 0
    skipped = []
    now = timezone.now()

    for currency in Currency.objects.all():
        if currency.code == 'NGN':
            # The base is 1 by definition; a feed saying otherwise is wrong.
            continue

        raw = rates.get(currency.code)
        if raw is None:
            skipped.append('%s (not in the feed)' % currency.code)
            continue

        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            skipped.append('%s (unreadable rate)' % currency.code)
            continue

        if not (MIN_RATE <= value <= MAX_RATE):
            skipped.append('%s (rate out of range: %s)' % (currency.code, value))
            continue

        currency.rate_from_ngn = value
        currency.rate_updated = now
        currency.save(update_fields=['rate_from_ngn', 'rate_updated'])
        updated += 1

    return updated, skipped, None

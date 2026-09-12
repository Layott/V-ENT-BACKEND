"""Checking a listing before it is stored, and what a free account may have.

Two jobs, and they are here together because they are the same question asked
from two sides: what may this listing say, and may this account have it at all.

`clean()` reads `catalogue.FIELDS`, which is the same table a screen reads to
draw the form. A form that asks for something the server will not store, or a
server that requires something the form never asked for, is the drift this
avoids by construction.
"""
from django.utils import timezone

from vent_auth import premium

from . import catalogue
from .models import Listing


class ListingError(ValueError):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def _int(value, field, *, minimum=0, maximum=10 ** 9):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ListingError('That has to be a number.', field)
    if not minimum <= number <= maximum:
        raise ListingError('A number between %s and %s.' % (minimum, maximum),
                           field)
    return number


def clean(raw, *, seller):
    """The stored form of a proposed listing, or a refusal that names a field."""
    if not isinstance(raw, dict):
        raise ListingError('A listing is a set of named settings.')

    kind = str(raw.get('kind') or '').strip()
    if kind not in catalogue.KINDS:
        raise ListingError('Say whether this is a service, a swap or a sale.',
                           'kind')

    category = str(raw.get('category') or '').strip()
    if category not in catalogue.CATEGORIES:
        raise ListingError('Choose a category.', 'category')

    title = str(raw.get('title') or '').strip()
    if len(title) < 3:
        raise ListingError('Give the listing a title.', 'title')

    out = {
        'kind': kind,
        'category': category,
        'title': title[:140],
        'description': str(raw.get('description') or '')[:5000],
        'tags': [str(t).strip()[:40] for t in (raw.get('tags') or [])
                 if str(t).strip()][:12],
    }

    uses = set(catalogue.fields_for(kind))
    required = set(catalogue.required_for(kind))

    # Only the fields this KIND uses. A sale that arrives carrying a duration
    # is a form that has drifted, and storing it quietly is how the drift
    # survives.
    if 'price' in uses:
        out['price'] = _int(raw.get('price', 0), 'price', maximum=10 ** 8)
    if 'price_kind' in uses:
        price_kind = str(raw.get('price_kind') or '').strip()
        if price_kind and price_kind not in catalogue.PRICING:
            raise ListingError('An hourly rate, a package or one fixed price.',
                               'price_kind')
        out['price_kind'] = price_kind
    if 'quantity' in uses:
        out['quantity'] = _int(raw.get('quantity', 1), 'quantity', minimum=1,
                               maximum=100000)
    if 'duration_minutes' in uses and raw.get('duration_minutes') not in ('', None):
        out['duration_minutes'] = _int(raw.get('duration_minutes'),
                                       'duration_minutes', minimum=1,
                                       maximum=60 * 24 * 30)
    if 'experience' in uses:
        out['experience'] = str(raw.get('experience') or '')[:2000]
    if 'delivery' in uses:
        delivery = str(raw.get('delivery') or '').strip()
        if delivery and delivery not in catalogue.DELIVERY:
            raise ListingError('Online, in person, or either.', 'delivery')
        out['delivery'] = delivery
        # An in-person service with no address is a service nobody can attend.
        if delivery in ('in_person', 'hybrid') and not str(raw.get('location') or '').strip():
            raise ListingError('Say where an in-person service happens.',
                               'location')
    if 'location' in uses:
        out['location'] = str(raw.get('location') or '')[:255]
    if 'offered' in uses:
        out['offered'] = str(raw.get('offered') or '').strip()[:200]
    if 'wanted' in uses:
        out['wanted'] = str(raw.get('wanted') or '').strip()[:200]
    if 'trade_value' in uses and raw.get('trade_value') not in ('', None):
        out['trade_value'] = _int(raw.get('trade_value'), 'trade_value',
                                  maximum=10 ** 8)
    if 'condition' in uses:
        condition = str(raw.get('condition') or '').strip()
        if condition and condition not in catalogue.CONDITIONS:
            raise ListingError('New or used.', 'condition')
        out['condition'] = condition
    if 'payment_methods' in uses:
        out['payment_methods'] = str(raw.get('payment_methods') or '')[:200]

    for field in required:
        value = out.get(field)
        if value in (None, '', 0) and not (field == 'price' and out.get('price') == 0):
            raise ListingError('This one is needed before it can go live.',
                               field)
    # `price` is required on a sale and a service, and zero is not a price.
    if 'price' in required and not out.get('price'):
        raise ListingError('Set a price.', 'price')

    # ---- the premium fields ------------------------------------------------
    # Refused by NAME rather than dropped. A seller who believes they set a
    # discount code and did not is a seller whose customers do not get the
    # discount, and nobody finds out until somebody complains.
    wanted_premium = [key for key in ('discount_code', 'portfolio', 'promo_material')
                      if raw.get(key)]
    if wanted_premium and not premium.has_premium(seller):
        raise ListingError('That is a premium feature.', wanted_premium[0])

    if raw.get('discount_code'):
        out['discount_code'] = str(raw['discount_code']).strip()[:40]
        out['discount_percent'] = _int(raw.get('discount_percent', 0),
                                       'discount_percent', maximum=90)

    # ---- bidding -----------------------------------------------------------
    if raw.get('bidding'):
        out['bidding'] = True
        days = _int(raw.get('bid_days', catalogue.BID_DAYS_FREE), 'bid_days',
                    minimum=1, maximum=catalogue.BID_DAYS_PREMIUM)
        allowed = (catalogue.BID_DAYS_PREMIUM if premium.has_premium(seller)
                   else catalogue.BID_DAYS_FREE)
        if days > allowed:
            raise ListingError(
                'Bidding runs for up to %s days on this account.' % allowed,
                'bid_days')
        out['bid_days'] = days

    return out


def active_count(user):
    """How many listings this account has live right now."""
    return Listing.objects.filter(seller=user, status='active').count()


def may_publish(user):
    """(allowed, limit). The spec: free accounts get ONE active listing.

    Returns the limit as well as the answer, because a screen that says "you
    have reached your limit" without saying what the limit is tells somebody
    nothing they can act on.
    """
    if premium.has_premium(user):
        return True, None
    limit = catalogue.FREE_ACTIVE_LISTINGS
    return active_count(user) < limit, limit


def expire_due(now=None):
    """Close listings whose date has passed. Returns how many.

    Run from the same cron as the other due-work commands. A listing with an
    expiry that nothing enforces is a listing that says it ended and did not.
    """
    now = now or timezone.now()
    return Listing.objects.filter(
        status='active', expires_at__isnull=False, expires_at__lte=now
    ).update(status='expired')

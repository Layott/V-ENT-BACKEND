"""Rate limiting for endpoints that answer without a key or a session.

The partner API already limits per key (`vent_partners/auth.py`), because every
request there carries one. The endpoints that do not are the problem: the SSO
consent screen has to draw a partner's name before anybody has signed in, so it
answers `AllowAny` - and in doing so it confirms whether a `client_id` exists.
Left unlimited that is an enumeration tool with no cost attached.

Counting in the cache rather than the database, for the same reason the partner
limiter does: a rate limit that writes a row per request is its own denial of
service.

The deliberate choice here is that **a cache outage opens the door rather than
closing it**. A limiter that fails shut turns one broken Redis into a site-wide
outage, and the thing being protected is enumeration of public client ids, not
anybody's money.
"""
import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


def client_ip(request):
    """The caller's address, trusting the first hop of X-Forwarded-For.

    nginx sets it in front of this, and that is the only proxy in the path. The
    first entry is the client; anything after it is the chain, and anything a
    client puts there itself would be appended, not prepended.
    """
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        first = forwarded.split(',')[0].strip()
        if first:
            return first[:45]          # an IPv6 address is at most 45 characters
    return (request.META.get('REMOTE_ADDR') or 'unknown')[:45]


def too_many(request, name, per_minute, *, extra=''):
    """True when this caller has already had its minute's worth of `name`.

    `extra` narrows the bucket further - pass a client_id and one noisy partner
    cannot spend everybody else's allowance.
    """
    minute = timezone.now().strftime('%Y%m%d%H%M')
    key = 'throttle:%s:%s:%s:%s' % (name, client_ip(request), extra, minute)
    try:
        cache.get_or_set(key, 0, timeout=120)
        used = cache.incr(key)
    except ValueError:
        # The entry expired between get_or_set and incr. Rare, and one request
        # slipping through a boundary is not worth a lock.
        cache.set(key, 1, timeout=120)
        used = 1
    except Exception:
        logger.warning('rate limiting unavailable for %s', name, exc_info=True)
        return False
    return used > max(1, per_minute)

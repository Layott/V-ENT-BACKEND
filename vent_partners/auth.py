"""Authenticating a partner, and refusing one politely.

A partner sends `Authorization: Bearer vent_pk_<key id>.<secret>`. The key id is
looked up, the secret is compared against a hash, and then the scope the endpoint
needs is checked against both the key and the partner - because a partner can be
suspended after a key was issued, and the key must stop working the moment that
happens rather than at the next issue.
"""
import functools
import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import PartnerApiKey

logger = logging.getLogger(__name__)


def _fail(message, code, http_status=status.HTTP_401_UNAUTHORIZED):
    return Response(
        {'status': 'error', 'code': code, 'message': message, 'data': None},
        status=http_status,
    )


def resolve_key(request):
    """The key behind this request, or a Response explaining why there is none."""
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return None, _fail(
            'Send your key as: Authorization: Bearer vent_pk_<key id>.<secret>',
            'MISSING_KEY',
        )

    raw = header.split(' ', 1)[1].strip()
    if not raw.startswith(PartnerApiKey.PREFIX) or '.' not in raw:
        return None, _fail('That is not a V-ENT API key.', 'MALFORMED_KEY')

    body = raw[len(PartnerApiKey.PREFIX):]
    key_id, _, secret = body.partition('.')

    key = (
        PartnerApiKey.objects
        .select_related('partner')
        .filter(key_id=key_id, revoked_at__isnull=True)
        .first()
    )
    # The same answer whether the id is unknown or the secret is wrong, so the
    # endpoint cannot be used to work out which key ids exist.
    if key is None or not key.matches(secret):
        return None, _fail('That key is not valid.', 'INVALID_KEY')

    if not key.partner.is_active:
        return None, _fail(
            f'This partner account is {key.partner.status}.',
            'PARTNER_INACTIVE',
            status.HTTP_403_FORBIDDEN,
        )

    return key, None


def _rate_limited(key):
    """True when this key has already had its minute's worth.

    Counting in the cache rather than the database: a rate limit that writes a
    row per request is its own denial of service.
    """
    bucket = timezone.now().strftime('%Y%m%d%H%M')
    cache_key = f'partner-rate:{key.key_id}:{bucket}'
    try:
        used = cache.get_or_set(cache_key, 0, timeout=120)
        used = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=120)
        used = 1
    except Exception:
        # A cache that is unavailable must not close the API.
        logger.warning('partner rate limiting unavailable', exc_info=True)
        return False
    return used > max(1, key.rate_limit_per_minute)


def _touch(key):
    """Record use, at most once a minute, so reads do not become writes."""
    if key.last_used_at and timezone.now() - key.last_used_at < timedelta(minutes=1):
        return
    PartnerApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())


def requires_scope(scope):
    """Guard a partner endpoint with exactly one scope."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            key, error = resolve_key(request)
            if error is not None:
                return error

            if not key.allows(scope):
                return _fail(
                    f'This key does not have the {scope} scope.',
                    'SCOPE_REQUIRED',
                    status.HTTP_403_FORBIDDEN,
                )

            if _rate_limited(key):
                return _fail(
                    f'Rate limit of {key.rate_limit_per_minute} requests a minute reached.',
                    'RATE_LIMITED',
                    status.HTTP_429_TOO_MANY_REQUESTS,
                )

            _touch(key)
            request.partner_key = key
            request.partner = key.partner
            return view(request, *args, **kwargs)
        return wrapper
    return decorator

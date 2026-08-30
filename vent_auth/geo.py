"""Resolve a request's country and region from its IP address.

Runs entirely on this machine. The lookup reads a MaxMind-format database file
from disk (DB-IP's free City Lite build, refreshed monthly by a cron job), so a
signup never waits on a third-party geolocation API and no user IP leaves the
server. That matters twice over: it keeps signup fast on a high-latency link,
and it keeps a personal data point in-house.

Everything degrades quietly. If the database file is missing or the address is
private, the caller gets (None, None) and signup carries on - a missing country
must never be the reason somebody cannot create an account.
"""
import ipaddress
import logging
import os
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_reader = None
_reader_lock = threading.Lock()
_reader_failed = False


def _db_path():
    return getattr(settings, 'GEOIP_DB_PATH', '') or os.environ.get('GEOIP_DB_PATH', '')


def _get_reader():
    """Open the database once and keep it. geoip2 readers are thread-safe."""
    global _reader, _reader_failed
    if _reader is not None or _reader_failed:
        return _reader

    with _reader_lock:
        if _reader is not None or _reader_failed:
            return _reader
        path = _db_path()
        if not path or not os.path.exists(path):
            _reader_failed = True
            logger.info('geoip: no database at %r, location lookup disabled', path)
            return None
        try:
            import geoip2.database
            _reader = geoip2.database.Reader(path)
        except Exception as exc:
            _reader_failed = True
            logger.warning('geoip: could not open %r (%s)', path, exc)
            return None
    return _reader


def client_ip(request):
    """The caller's real address.

    nginx sits in front in production and appends the client to
    X-Forwarded-For, so REMOTE_ADDR is always the proxy. Only the first entry in
    that header is meaningful; the rest can be forged by the client. In
    development there is no proxy, so REMOTE_ADDR is used directly.
    """
    if not settings.DEBUG:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            first = forwarded.split(',')[0].strip()
            if first:
                return first
    return request.META.get('REMOTE_ADDR') or ''


def _is_public(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_reserved
                or addr.is_link_local or addr.is_unspecified)


def locate(ip):
    """(country_name, region_name) for an address, or (None, None).

    Never raises. A geolocation failure is not worth failing a signup over.
    """
    if not ip or not _is_public(ip):
        return None, None

    reader = _get_reader()
    if reader is None:
        return None, None

    try:
        response = reader.city(ip)
    except Exception:
        # AddressNotFoundError and friends - unknown address, nothing to do.
        return None, None

    country = getattr(response.country, 'name', None)

    # City first, because "Lagos, Nigeria" is what a profile shows and what
    # anyone reading it expects. The subdivision (state or province) is the
    # fallback for an address the database only places that coarsely.
    region = getattr(getattr(response, 'city', None), 'name', None)
    if not region:
        subdivisions = getattr(response, 'subdivisions', None)
        if subdivisions:
            try:
                region = subdivisions.most_specific.name
            except Exception:
                region = None

    return country, _tidy_place(region)


def _tidy_place(name):
    """Trim what a city name carries for the database's benefit, not a reader's.

    DB-IP names districts in brackets - a real lookup came back
    "Lagos (Victoria Island Annex)" - and a profile should read "Lagos".
    """
    if not name:
        return name
    import re
    cleaned = re.sub(r'\s*\([^)]*\)', '', name).strip()
    return cleaned or name


def locate_request(request):
    """Convenience wrapper: resolve straight from a DRF/Django request."""
    return locate(client_ip(request))


def refresh_daily_location(user, request):
    """Update a user's country and city from their IP, once per day.

    Called on the way through login, both the password path and Google's. The
    lookup is a local file read so it costs nothing worth measuring, but the
    write is worth rationing: once per local day per account is enough to keep a
    profile honest without touching the database on every sign-in.

    Returns True when something changed. Never raises - a location that cannot
    be resolved is not a reason to fail a login, and a private address (anyone
    testing on localhost) is skipped entirely.
    """
    from django.utils import timezone

    try:
        ip = client_ip(request)
        if not ip or not _is_public(ip):
            return False

        already_today = (
            user.location_updated_at
            and timezone.localtime(user.location_updated_at).date() == timezone.localdate()
        )
        if already_today:
            return False

        country, city = locate(ip)
        if not country:
            return False

        # A guess never overwrites an answer.
        #
        # This wrote the IP's country and city over whatever the account
        # already held, every day. Two things went wrong with that. A player
        # who told us where they are had it quietly replaced by wherever their
        # carrier's gateway happens to sit - Nigerian mobile data resolved a
        # Lagos sign-in to Ilorin - and `country` is not decoration: a
        # challenge open to one country is gated on this field, so a wrong
        # guess locks somebody out of challenges in their own country.
        #
        # So it fills a blank and nothing else. Somebody who has never said
        # where they are still gets a sensible default on their first sign-in;
        # somebody who has said gets to keep it.
        fields = ['last_login_ip', 'location_updated_at']
        if not (user.country or '').strip():
            user.country = country
            fields.append('country')
        if city and not (user.state or '').strip():
            user.state = city
            fields.append('state')

        user.last_login_ip = ip
        user.location_updated_at = timezone.now()
        user.save(update_fields=fields)
        return True
    except Exception:
        logger.warning('geoip: daily location refresh failed', exc_info=True)
        return False


def record_login(user, request, *, method='password'):
    """Write a sign-in to the account's history, and say whether it looks new.

    Returns True when this address has not been seen on the account before,
    which is what a "new sign-in" alert should be based on. Never raises: a
    history write must not be able to fail a login.
    """
    from .models import LoginEvent

    try:
        ip = client_ip(request) or None
        country, city = locate(ip) if ip else (None, None)
        agent = (request.META.get('HTTP_USER_AGENT') or '')[:400]

        seen_before = LoginEvent.objects.filter(user=user, ip=ip).exists() if ip else True

        LoginEvent.objects.create(
            user=user, ip=ip, city=city or '', country=country or '',
            user_agent=agent, method=method,
        )

        # Keep the table short rather than letting a year of sign-ins pile up.
        stale = list(
            LoginEvent.objects.filter(user=user)
            .order_by('-created_at')
            .values_list('id', flat=True)[LoginEvent.KEEP_PER_USER:]
        )
        if stale:
            LoginEvent.objects.filter(id__in=stale).delete()

        return not seen_before
    except Exception:
        logger.warning('login history write failed', exc_info=True)
        return False

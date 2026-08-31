"""ipinfo.io, as the sharper of the two location providers.

CEO, 31 August 2026, after being told the platform reads a local DB-IP file:
"wire up ipinfo".

It is genuinely better than the free DB-IP City Lite build, particularly on
mobile ranges, which is where V-ENT's traffic lives. It does not make a city
knowable: a carrier gateway is a real place and it is not where the subscriber
is, so ipinfo saying "Ilorin" for a Lagos phone is ipinfo being right about the
gateway. The rule the rest of the code keeps - a guessed city is offered, never
asserted - is unchanged by having a better guess. What ipinfo does buy is a
country that is right more often, and a city worth *offering*.

Three things this module is careful about, because a third-party call on a
sign-in path is exactly where a platform picks up a stall it never recovers
from:

1. **It can always be switched off.** No token, no network call, and `locate`
   falls through to the local file. Nothing here is on the critical path for an
   account to exist.
2. **It never blocks for long.** A short timeout, and every failure - refused,
   slow, rate-limited, malformed - is a quiet (None, None).
3. **It is asked once per address.** Answers are cached in the database with a
   long TTL, because an address's city does not move. That keeps a login off
   the network almost always, and keeps the 50,000-a-month free tier a
   non-issue: it is 50,000 *distinct addresses*, not 50,000 sign-ins.
"""
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# An address does not change city often, and a stale answer is no worse than
# the local database's answer, which is a monthly snapshot anyway.
CACHE_DAYS = 30

# Long enough for a healthy round trip from Lagos, short enough that a bad day
# at ipinfo costs a sign-in a moment rather than a timeout.
DEFAULT_TIMEOUT = 2.0

DEFAULT_ENDPOINT = 'https://ipinfo.io/%s/json'


def _endpoint():
    """Overridable, so a self-hosted mirror or a proxy can stand in - and so
    this can be pointed somewhere local to prove the wiring without spending a
    lookup."""
    return (getattr(settings, 'IPINFO_ENDPOINT', '') or '').strip() or DEFAULT_ENDPOINT


def is_configured():
    return bool(_token())


def _token():
    return (getattr(settings, 'IPINFO_TOKEN', '') or '').strip()


def _timeout():
    try:
        return float(getattr(settings, 'IPINFO_TIMEOUT', DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def _cached(ip):
    """A recent answer for this address, or None.

    A row that says "we asked and got nothing" counts: re-asking about an
    address ipinfo does not know, on every sign-in, spends quota to learn the
    same nothing.
    """
    from .models import IPLocation

    row = IPLocation.objects.filter(ip=ip).first()
    if row is None:
        return None
    age = timezone.now() - row.updated_at
    if age.days >= CACHE_DAYS:
        return None
    return row


def _remember(ip, country, city, source):
    from .models import IPLocation

    IPLocation.objects.update_or_create(
        ip=ip,
        defaults={
            'country': country or '',
            'city': city or '',
            'source': source,
            'updated_at': timezone.now(),
        },
    )


def lookup(ip):
    """(country_name, city_name) for an address, or (None, None).

    Never raises. A geolocation failure is not worth failing a sign-in over,
    and this one is a network call, so it has more ways to fail than the local
    file does.
    """
    if not ip or not is_configured():
        return None, None

    try:
        row = _cached(ip)
        if row is not None:
            return (row.country or None), (row.city or None)
    except Exception:
        # A cache read that fails must not stop the lookup, and must not stop
        # the caller either.
        logger.warning('ipinfo: cache read failed for %r', ip, exc_info=True)

    try:
        import requests

        response = requests.get(
            _endpoint() % ip,
            params={'token': _token()},
            timeout=_timeout(),
            headers={'Accept': 'application/json'},
        )
        if response.status_code != 200:
            # 429 is the one worth naming: it means the month's quota is gone
            # and every later call this month will also fail, so it is a thing
            # somebody should see in the log rather than a blip.
            if response.status_code == 429:
                logger.warning('ipinfo: rate limited, falling back to the local database')
            return None, None
        payload = response.json()
    except Exception:
        logger.info('ipinfo: lookup failed for %r, falling back', ip, exc_info=True)
        return None, None

    # ipinfo returns a two-letter country code. The rest of the platform stores
    # and compares country NAMES - a tournament open to "Nigeria" is checked
    # against this field - so a code written here would silently fail to match
    # every restriction in the product.
    country = _country_name(payload.get('country'))
    city = (payload.get('city') or '').strip() or None
    if payload.get('bogon'):
        country, city = None, None

    try:
        _remember(ip, country, city, 'ipinfo')
    except Exception:
        logger.warning('ipinfo: cache write failed for %r', ip, exc_info=True)

    return country, city


def _country_name(code):
    """A two-letter code as the name the rest of the platform stores.

    `constants/countries` on the frontend and the tournament restriction check
    both work in names. Anything unrecognised comes back as None rather than as
    a two-letter string that would look like a country and match nothing.
    """
    if not code:
        return None
    code = str(code).strip().upper()
    if len(code) != 2:
        return None
    try:
        import pycountry

        found = pycountry.countries.get(alpha_2=code)
        if found is not None:
            return getattr(found, 'common_name', None) or found.name
    except Exception:
        pass
    return COUNTRY_NAMES.get(code)


# Africa first, then the rest of the traffic V-ENT actually sees. `pycountry`
# is used when it is installed; this is the fallback so the feature does not
# depend on a package being present.
COUNTRY_NAMES = {
    'NG': 'Nigeria', 'GH': 'Ghana', 'KE': 'Kenya', 'ZA': 'South Africa',
    'CI': 'Ivory Coast', 'SN': 'Senegal', 'CM': 'Cameroon', 'UG': 'Uganda',
    'TZ': 'Tanzania', 'RW': 'Rwanda', 'ET': 'Ethiopia', 'EG': 'Egypt',
    'MA': 'Morocco', 'DZ': 'Algeria', 'TN': 'Tunisia', 'ZM': 'Zambia',
    'ZW': 'Zimbabwe', 'BW': 'Botswana', 'NA': 'Namibia', 'MZ': 'Mozambique',
    'AO': 'Angola', 'BJ': 'Benin', 'BF': 'Burkina Faso', 'ML': 'Mali',
    'NE': 'Niger', 'TG': 'Togo', 'GM': 'Gambia', 'SL': 'Sierra Leone',
    'LR': 'Liberia', 'GN': 'Guinea', 'CD': 'DR Congo', 'CG': 'Congo',
    'GA': 'Gabon', 'MW': 'Malawi', 'MU': 'Mauritius', 'SD': 'Sudan',
    'GB': 'United Kingdom', 'US': 'United States', 'CA': 'Canada',
    'FR': 'France', 'DE': 'Germany', 'ES': 'Spain', 'PT': 'Portugal',
    'IT': 'Italy', 'NL': 'Netherlands', 'BE': 'Belgium', 'IE': 'Ireland',
    'BR': 'Brazil', 'AR': 'Argentina', 'MX': 'Mexico', 'IN': 'India',
    'PK': 'Pakistan', 'PH': 'Philippines', 'ID': 'Indonesia', 'AE': 'United Arab Emirates',
    'SA': 'Saudi Arabia', 'TR': 'Turkey', 'AU': 'Australia', 'NZ': 'New Zealand',
    'CN': 'China', 'JP': 'Japan', 'KR': 'South Korea', 'RU': 'Russia',
}

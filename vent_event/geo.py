"""Where an event is, and roughly where the people coming to it are from.

CEO, 29 August 2026: "the getting there should actually show a map and then the
location on the map, plus the open in maps button, should be there too. And
there should be ways for people to also see like markers of other people coming
to the event, they dont need to see specific people, just like markers that
theres people around them going to that event and people can decide if they want
that their going to that event be made public".

Two things, and the second one is the one to be careful with. A map of who is
coming to a public event, drawn from real locations, is a map of where those
people live. Three decisions keep it from being that:

1. **The exact point never arrives and is never stored.** The coordinate is
   rounded to a cell before it is written. The rounding happens on the server,
   because the client cannot be trusted to have done it, and the raw value is
   discarded in the same expression that rounds it.

2. **A cell is only ever shown once enough people share it.** One marker in a
   village is one person's home. `MIN_PER_CELL` is what stops a marker from
   being an address.

3. **Nothing is shared unless somebody said so.** There is no row until an
   attendee asks for one, and removing it is a single request. No names, no
   usernames and no ids are ever in the response.

The cell is 0.05 degrees, which is about 5.5km north to south and rather less
than that east to west in Lagos. Big enough that a cell is a district rather
than a street; small enough that "people near me are going" still means
something in a city.
"""

from decimal import Decimal, InvalidOperation

#: Degrees per cell. A district, not a street.
CELL = Decimal('0.05')

#: How many people must share a cell before it is drawn at all. Below this a
#: marker would be one household.
MIN_PER_CELL = 3


class BadCoordinate(ValueError):
    """A latitude or longitude that is not one."""


def to_decimal(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BadCoordinate('%s is not a number' % name)
    if not number.is_finite():
        raise BadCoordinate('%s is not a number' % name)
    return number


def check_point(latitude, longitude):
    """Validate a coordinate pair and return it as Decimals."""
    lat = to_decimal(latitude, 'latitude')
    lng = to_decimal(longitude, 'longitude')
    if not (Decimal('-90') <= lat <= Decimal('90')):
        raise BadCoordinate('latitude is out of range')
    if not (Decimal('-180') <= lng <= Decimal('180')):
        raise BadCoordinate('longitude is out of range')
    return lat, lng


def to_cell(latitude, longitude):
    """Round a point to the centre of its cell.

    The returned pair is the only thing that is ever stored. The value passed in
    is not kept anywhere: not on the model, not in a log line, not in the
    response. That is the whole privacy argument, so it lives in one function
    and everything that writes an origin goes through it.
    """
    lat, lng = check_point(latitude, longitude)
    # Floor to the cell, then take its centre, so a cell has one canonical
    # coordinate however it was reached.
    lat_cell = (lat / CELL).to_integral_value(rounding='ROUND_FLOOR') * CELL + CELL / 2
    lng_cell = (lng / CELL).to_integral_value(rounding='ROUND_FLOOR') * CELL + CELL / 2
    return lat_cell.quantize(Decimal('0.000001')), lng_cell.quantize(Decimal('0.000001'))


# --------------------------------------------------------------------------
# Reading a coordinate out of a map link.
#
# An organiser pastes a Google or Apple Maps URL because that is what they have.
# Most of those URLs already carry the coordinate, so asking them to type it
# again is asking them to make a mistake. Where it cannot be read, the field is
# simply left alone and they can enter it by hand.
# --------------------------------------------------------------------------

import re  # noqa: E402  (kept next to the thing that uses it)

_PATTERNS = [
    re.compile(r'@(-?\d+\.\d+),(-?\d+\.\d+)'),            # google maps /@lat,lng,17z
    re.compile(r'[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)'),     # ?q=lat,lng
    re.compile(r'[?&]ll=(-?\d+\.\d+),\s*(-?\d+\.\d+)'),    # apple maps ?ll=lat,lng
    re.compile(r'[?&]daddr=(-?\d+\.\d+),\s*(-?\d+\.\d+)'),
    re.compile(r'[?&]mlat=(-?\d+\.\d+)&mlon=(-?\d+\.\d+)'),  # openstreetmap
    re.compile(r'/(-?\d+\.\d+),(-?\d+\.\d+)'),             # bare /lat,lng
]


def point_from_map_link(url):
    """The coordinate inside a pasted map link, or None."""
    if not url:
        return None
    for pattern in _PATTERNS:
        found = pattern.search(str(url))
        if not found:
            continue
        try:
            return check_point(found.group(1), found.group(2))
        except BadCoordinate:
            continue
    return None


# ---------------------------------------------------------------------------
# Turning a typed address into a point
# ---------------------------------------------------------------------------
#
# CEO, 8 September 2026: "what I want is that when an event organizer puts an
# address it should be located on the map and shown. that flow of it showing on
# map to all users should be fixed."
#
# Until now the map appeared only when the organiser pasted a Google Maps LINK,
# because `point_from_map_link` reads the coordinate out of the URL. An
# organiser who simply typed "Landmark Centre, Victoria Island, Lagos" - which
# is what most people do - got "There is no map of this venue."
#
# ## Nominatim, and why not Google
#
# OpenStreetMap's Nominatim needs no key and no billing account, which matters
# for a platform whose whole infrastructure budget is a credit balance. Google's
# geocoder is better on Nigerian addresses and costs money per request; if that
# trade stops being right, `_GEOCODERS` is the one place to change.
#
# Its usage policy asks for a real User-Agent and no more than one request a
# second. The cache below is what keeps us inside that: a venue is looked up
# once, ever, and every later event at the same address is free.
#
# ## Why this never blocks a save
#
# An event must save whether or not a third party is up. Every failure here -
# a timeout, a 500, an address nobody can find - leaves the coordinate empty,
# which is exactly today's behaviour, and the page falls back to the address
# text it already shows. Creating an event must not depend on OpenStreetMap
# being awake.

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

#: Nominatim asks for something that identifies the caller.
_UA = 'V-ENT/1.0 (+https://v-ent.co; events@v-ent.co)'

#: What the latitude and longitude columns store.
_PLACES = Decimal('0.000001')

#: Short, because this runs inside a save. A geocode that takes longer than
#: this is one the organiser should not be waiting for.
_TIMEOUT = 4


#: Words that appear in the name of every second venue and so prove nothing.
#: "Convention Centre" matching "Convention Centre" is not corroboration.
_GENERIC = {
    'centre', 'center', 'hall', 'hotel', 'park', 'arena', 'stadium',
    'convention', 'conference', 'complex', 'plaza', 'mall', 'grounds',
    'road', 'street', 'avenue', 'close', 'way', 'island', 'city', 'the',
    'and', 'of', 'at', 'suites', 'resort', 'club', 'house', 'building',
}


def corroborates(query, display_name):
    """Whether an answer actually matches what was asked.

    Nominatim matches fuzzily, and on 8 September "Eko Convention Centre"
    came back as "Calabar International Convention Centre, Lagos-Calabar
    Coast" - 5.04, 8.31, which is Cross River and about 600km from the event.
    It looked like a hit and would have put a wrong pin on a real event page.
    A wrong pin is worse than no pin, because nobody checks a map that looks
    confident.

    So the answer has to carry the DISTINCTIVE words that were asked for.
    "Convention" and "Centre" prove nothing; "Eko" is the whole question.
    """
    asked = {w for w in _words(query) if w not in _GENERIC and len(w) > 2}
    if not asked:
        # Nothing distinctive was asked - "The Hotel" - so there is nothing to
        # corroborate and nothing to trust either.
        return False
    got = set(_words(display_name))
    return bool(asked & got)


def _words(text):
    import re
    return [w for w in re.split(r'[^a-z0-9]+', str(text or '').lower()) if w]


#: ISO codes for the countries this platform actually runs events in. The
#: search is bounded to these unless the address names somewhere else.
#:
#: This is not a nicety. Without it, "Eko Convention Centre, Landmark Centre"
#: - an address with no city in it - walked down the ladder to a bare
#: "Landmark" and matched a feature in NUNAVUT, at 70.9, -111.2. It even
#: corroborated, because the word "Landmark" was genuinely in the answer.
#: Corroboration alone cannot bound geography; a country can.
_HOME_COUNTRIES = 'ng,gh,ke,za,ci,sn,tz,ug,rw,cm,eg,ma'


def country_hint(address):
    """An ISO code list to bound the search to, or '' for the whole world.

    If the address names a country we know, search THAT country. Otherwise
    search the ones V-ENT operates in. An organiser running an event in Berlin
    types Germany, and that is what lifts the bound.
    """
    from vent_auth import regions
    text = str(address or '').lower()
    for country in regions.ALL_COUNTRIES:
        if country.lower() in text:
            # Nominatim wants a code, and we hold names. Rather than carry a
            # second table, an explicit country in the text is enough to stop
            # bounding: the name itself will steer the match.
            return ''
    return _HOME_COUNTRIES


def _nominatim(address):
    """(lat, lng) from OpenStreetMap, or None."""
    query = {
        'q': address,
        'format': 'json',
        'limit': 1,
        'accept-language': 'en',
    }
    hint = country_hint(address)
    if hint:
        query['countrycodes'] = hint
    url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={'User-Agent': _UA})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        rows = json.loads(response.read().decode('utf-8'))
    if not rows:
        return None
    row = rows[0]
    if not corroborates(address, row.get('display_name', '')):
        logger.info('geocode rejected %r -> %r (does not corroborate)',
                    address[:60], str(row.get('display_name'))[:60])
        return None
    try:
        return Decimal(str(row['lat'])), Decimal(str(row['lon']))
    except (KeyError, InvalidOperation):
        return None


_GEOCODERS = (_nominatim,)


def attempts_for(venue, location):
    """The forms to try, most specific first.

    A human types "Landmark Centre, Victoria Island, Lagos". Nominatim answers
    nothing to that and answers correctly to "Landmark Centre Lagos", so one
    attempt was never going to be enough - and the first version of this
    returned None for every real Lagos venue while the service had the answer
    all along.

    Each step gives up one piece of precision. The list is deduplicated and
    keeps its order, so a venue with no location does not ask the same
    question twice.
    """
    venue = ' '.join(str(venue or '').split())
    location = ' '.join(str(location or '').split())
    parts = [p.strip() for p in location.split(',') if p.strip()]
    city = parts[-1] if parts else ''

    tries = []
    if venue and location:
        tries.append('%s, %s' % (venue, location))
    if venue and city:
        # The one that actually works. Commas are what Nominatim dislikes, and
        # a venue plus its city is the form it answers.
        tries.append('%s %s' % (venue, city))
    if venue:
        tries.append(venue)
    if location:
        tries.append(location)
    if len(parts) > 1:
        # Drop the street, keep the district and city.
        tries.append(', '.join(parts[1:]))
    if city:
        tries.append(city)

    seen, out = set(), []
    for t in tries:
        key = t.lower()
        if len(t) >= 4 and key not in seen:
            seen.add(key)
            out.append(t)
    return out


def geocode(venue, location=None):
    """A point for what an organiser typed, or None. Cached for ever.

    Takes the venue and the address separately so the ladder can build the
    forms that actually resolve. Passing a single string still works: it is
    treated as the location.

    Cached because the same venue is used by many events and because the
    service asks us not to hammer it. A MISS is cached too, against the
    original text: an address nobody can find will not become findable on the
    next save, and re-asking every time an organiser edits their event is how
    a free service's rate limit is reached and then withdrawn.
    """
    if location is None:
        venue, location = '', venue

    original = ', '.join(p for p in (
        ' '.join(str(venue or '').split()),
        ' '.join(str(location or '').split()),
    ) if p)
    if len(original) < 6:
        return None

    from django.conf import settings
    if not getattr(settings, 'GEOCODING_ENABLED', True):
        # Off under the test runner, and off wherever somebody turns it off.
        #
        # `Event.save()` calls this, so with it on EVERY test that creates an
        # event with an address reaches OpenStreetMap. That makes the suite
        # slow, dependent on somebody else's uptime, and - worse - not
        # deterministic: `tests_map` passed on one run and failed on the next
        # with the same code, because the first got no answer and the second
        # got a real one. A test that means "no pin" has to be able to say so.
        #
        # It also hammers a free service that asks us not to, which is the
        # thing the cache above exists to avoid.
        return None

    from .models import GeocodedAddress

    row = GeocodedAddress.objects.filter(address__iexact=original).first()
    if row is not None:
        if row.latitude is None or row.longitude is None:
            return None
        return row.latitude, row.longitude

    point = None
    matched = ''
    for attempt in attempts_for(venue, location):
        for geocoder in _GEOCODERS:
            try:
                point = geocoder(attempt)
            except Exception as exc:        # noqa: BLE001 - see the note above
                # Never let a third party stop an event being saved.
                logger.info('geocode failed for %r: %s', attempt[:80], exc)
                point = None
            if point:
                matched = attempt
                break
        if point:
            break

    if point:
        # Rounded to what the column stores, so the first call and every
        # cached call afterwards return the SAME thing. Without this the first
        # answer carried the geocoder's full precision and the second came
        # back at six places, and two callers asking the same question got
        # different numbers.
        point = (point[0].quantize(_PLACES), point[1].quantize(_PLACES))

    GeocodedAddress.objects.update_or_create(
        address=original,
        defaults={'latitude': point[0] if point else None,
                  'longitude': point[1] if point else None,
                  'matched': matched},
    )
    return point

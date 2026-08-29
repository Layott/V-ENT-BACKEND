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

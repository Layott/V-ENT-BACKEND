"""Which region a country is in, in one place.

CEO, 7 September 2026: "the wrong locations is showing for users, i am seeing
united states everywhere for region, even under the filter for all countries, i
am seeing lagos, abuja and then other countries not there."

Three separate faults, and this file is the answer to all three:

1. **The region column was the user's STATE.** `region = u.state or u.country`,
   so somebody in Lagos had the region "Lagos", and somebody with no state had
   the region "Nigeria". A state is not a region and a country is not a region.

2. **The region filter did nothing.** It was read off the query string, checked
   against 'global', and then never used in a single query. Picking West Africa
   returned the whole world.

3. **The country filter was a hardcoded list of seven with Lagos and Abuja in
   it.** Two Nigerian cities offered as countries, and every country outside
   the five listed unreachable.

Africa-first, deliberately: the five African regions are complete because that
is who the platform is for and an organiser filtering to West Africa expects
Benin and Togo to be there. The rest of the world is by continent, which is the
honest resolution for a platform that does not operate there yet.
"""

WEST_AFRICA = 'West Africa'
EAST_AFRICA = 'East Africa'
NORTH_AFRICA = 'North Africa'
CENTRAL_AFRICA = 'Central Africa'
SOUTHERN_AFRICA = 'Southern Africa'
EUROPE = 'Europe'
NORTH_AMERICA = 'North America'
SOUTH_AMERICA = 'South America'
ASIA = 'Asia'
OCEANIA = 'Oceania'
MIDDLE_EAST = 'Middle East'

# The order regions are offered in. Africa first because that is the market.
ORDER = [
    WEST_AFRICA, EAST_AFRICA, NORTH_AFRICA, CENTRAL_AFRICA, SOUTHERN_AFRICA,
    EUROPE, NORTH_AMERICA, SOUTH_AMERICA, ASIA, MIDDLE_EAST, OCEANIA,
]

BY_REGION = {
    WEST_AFRICA: [
        'Nigeria', 'Ghana', 'Senegal', 'Ivory Coast', "Cote d'Ivoire", 'Mali',
        'Burkina Faso', 'Benin', 'Togo', 'Guinea', 'Sierra Leone', 'Liberia',
        'Niger', 'Gambia', 'Guinea-Bissau', 'Cape Verde', 'Mauritania',
    ],
    EAST_AFRICA: [
        'Kenya', 'Uganda', 'Tanzania', 'Ethiopia', 'Rwanda', 'Burundi',
        'Somalia', 'South Sudan', 'Eritrea', 'Djibouti', 'Seychelles',
        'Mauritius', 'Madagascar', 'Comoros',
    ],
    NORTH_AFRICA: [
        'Egypt', 'Morocco', 'Algeria', 'Tunisia', 'Libya', 'Sudan',
    ],
    CENTRAL_AFRICA: [
        'Cameroon', 'Chad', 'Central African Republic', 'Congo',
        'Democratic Republic of the Congo', 'DR Congo', 'Gabon',
        'Equatorial Guinea', 'Sao Tome and Principe',
    ],
    SOUTHERN_AFRICA: [
        'South Africa', 'Zimbabwe', 'Zambia', 'Botswana', 'Namibia',
        'Mozambique', 'Malawi', 'Angola', 'Lesotho', 'Eswatini', 'Swaziland',
    ],
    EUROPE: [
        'United Kingdom', 'Ireland', 'France', 'Germany', 'Spain', 'Portugal',
        'Italy', 'Netherlands', 'Belgium', 'Sweden', 'Norway', 'Denmark',
        'Finland', 'Poland', 'Ukraine', 'Romania', 'Greece', 'Austria',
        'Switzerland', 'Czechia', 'Hungary', 'Russia', 'Turkey', 'Serbia',
        'Croatia', 'Bulgaria', 'Slovakia', 'Lithuania', 'Latvia', 'Estonia',
    ],
    NORTH_AMERICA: [
        'United States', 'United States of America', 'USA', 'Canada', 'Mexico',
        'Jamaica', 'Trinidad and Tobago', 'Cuba', 'Haiti',
        'Dominican Republic', 'Costa Rica', 'Panama',
    ],
    SOUTH_AMERICA: [
        'Brazil', 'Argentina', 'Colombia', 'Chile', 'Peru', 'Venezuela',
        'Ecuador', 'Bolivia', 'Uruguay', 'Paraguay', 'Guyana',
    ],
    ASIA: [
        'China', 'Japan', 'South Korea', 'India', 'Pakistan', 'Bangladesh',
        'Indonesia', 'Philippines', 'Vietnam', 'Thailand', 'Malaysia',
        'Singapore', 'Nepal', 'Sri Lanka', 'Myanmar', 'Cambodia', 'Kazakhstan',
    ],
    MIDDLE_EAST: [
        'United Arab Emirates', 'Saudi Arabia', 'Qatar', 'Kuwait', 'Bahrain',
        'Oman', 'Israel', 'Jordan', 'Lebanon', 'Iraq', 'Iran', 'Yemen', 'Syria',
    ],
    OCEANIA: [
        'Australia', 'New Zealand', 'Fiji', 'Papua New Guinea',
    ],
}

# country (lowercased) -> region. Built once at import.
_LOOKUP = {}
for _region, _countries in BY_REGION.items():
    for _country in _countries:
        _LOOKUP[_country.strip().lower()] = _region

# Every country we know, in one sorted list. This is what the country filter is
# built from - never a hand-typed list on a screen, which is how Lagos and
# Abuja ended up being offered as countries.
ALL_COUNTRIES = sorted({c for cs in BY_REGION.values() for c in cs})


def region_for(country):
    """The region a country is in, or '' when we do not know it.

    '' rather than a guess. Putting somebody in the wrong region is worse than
    leaving it blank: a filter that quietly includes the wrong people is not
    discoverable, and a blank is.
    """
    if not country:
        return ''
    return _LOOKUP.get(str(country).strip().lower(), '')


def countries_in(region):
    """Every country in a region, for filtering. Empty list when unknown."""
    if not region:
        return []
    return list(BY_REGION.get(region, []))


def is_country(name):
    """Whether this is a country we know, used to keep cities out of the list."""
    return bool(name) and str(name).strip().lower() in _LOOKUP

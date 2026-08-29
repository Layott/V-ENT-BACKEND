"""What counts as a good game, per game.

PRD section 3: "Player/Team Performance Metrics: KDA (Kills, Deaths, Assists),
damage dealt, healing done, objective captures, etc., **specific to the game**"
and "MVP and Performance-Based Indicators: Data on who was selected as MVP and
based on which metrics."

The last clause is the whole design. An MVP that appears with no arithmetic
behind it is an opinion, and the argument that follows it is unresolvable. So a
tournament stores WHICH metrics it counted and WHAT each was worth, the score
is the sum, and the answer to "why him" is a row of numbers.

Three things this gets right that a fixed stat table does not:

**Deaths count against you.** A metric carries `higher_is_better`, and a
negative weight is the normal way to express a death or an own goal. Ranking on
a raw total would crown whoever died most in a game that pays for damage.

**The defaults come from the game.** EA FC does not have kills and Call of Duty
does not have assists in the football sense. An organiser opening the screen
should see their own sport, not a union of every sport.

**The organiser owns the weights.** These are defaults, not rules. The document
asks for "tie breakers for MVPs and teams" among the organiser's settings, so
the order of the metrics is theirs and so is what each is worth.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    # What one of these is worth in the MVP score. Negative for the things that
    # should cost somebody the award.
    default_weight: float = 1.0
    higher_is_better: bool = True
    # A whole number of things, or a measured quantity. Decides how the input is
    # drawn and how the total is displayed.
    decimals: int = 0
    notes: str = ''


METRICS = {
    # --- shooters ----------------------------------------------------------
    'kills': Metric('kills', 'Kills', default_weight=1.0),
    'deaths': Metric('deaths', 'Deaths', default_weight=-0.5,
                     higher_is_better=False,
                     notes='Negative on purpose. Ranking on a raw total would '
                           'crown whoever died most in a game that pays for '
                           'damage.'),
    'assists': Metric('assists', 'Assists', default_weight=0.5),
    'damage': Metric('damage', 'Damage dealt', default_weight=0.001,
                     notes='Weighted small because it is counted in thousands '
                           'and would otherwise drown every other metric.'),
    'headshots': Metric('headshots', 'Headshots', default_weight=0.25),
    'revives': Metric('revives', 'Revives', default_weight=0.5),
    'objectives': Metric('objectives', 'Objectives captured', default_weight=2.0),
    'plants': Metric('plants', 'Bombs planted', default_weight=1.0),
    'defuses': Metric('defuses', 'Bombs defused', default_weight=1.5),
    'healing': Metric('healing', 'Healing done', default_weight=0.001),
    'placement': Metric('placement', 'Placement points', default_weight=1.0),

    # --- football ----------------------------------------------------------
    'goals': Metric('goals', 'Goals', default_weight=3.0),
    'assists_football': Metric('assists_football', 'Assists', default_weight=2.0),
    'saves': Metric('saves', 'Saves', default_weight=1.0),
    'clean_sheet': Metric('clean_sheet', 'Clean sheet', default_weight=2.0),
    'own_goals': Metric('own_goals', 'Own goals', default_weight=-3.0,
                        higher_is_better=False),
    'possession': Metric('possession', 'Possession %', default_weight=0.05,
                         decimals=1),
    'shots_on_target': Metric('shots_on_target', 'Shots on target',
                              default_weight=0.5),

    # --- fighting ----------------------------------------------------------
    'rounds_won': Metric('rounds_won', 'Rounds won', default_weight=2.0),
    'perfects': Metric('perfects', 'Perfect rounds', default_weight=3.0),
    'combos': Metric('combos', 'Longest combo', default_weight=0.2),

    # --- anything ----------------------------------------------------------
    'match_wins': Metric('match_wins', 'Matches won', default_weight=3.0),
    'fair_play': Metric('fair_play', 'Fair play', default_weight=1.0,
                        notes='A judged mark rather than a counted one. Some '
                              'organisers weigh conduct and the platform '
                              'should let them say so.'),
    'penalties': Metric('penalties', 'Penalty points', default_weight=-2.0,
                        higher_is_better=False),
}


# What each game starts with. An organiser opening the screen should see their
# own sport, not the union of every sport on the platform.
#
# Matched case-insensitively against the game title, on a substring, because
# the seeded titles are "EA FC 25" and "Call of Duty: Warzone" rather than tidy
# keys, and a new year in the name must not silently empty the list.
#
# ORDER IS SPECIFICITY. The first needle found in the title wins, so a mode
# goes ABOVE the franchise it belongs to: "Call of Duty: Warzone" contains both
# 'warzone' and 'call of duty', and it is a battle royale rather than a
# multiplayer shooter. Adding a new mode means putting it above its franchise,
# not at the end.
BY_GAME = (
    # Modes first, above the franchises they belong to.
    ('warzone', ('kills', 'deaths', 'damage', 'revives', 'placement')),
    # Then everything else.
    ('ea fc', ('goals', 'assists_football', 'shots_on_target', 'possession',
               'own_goals')),
    ('fifa', ('goals', 'assists_football', 'shots_on_target', 'possession',
              'own_goals')),
    ('efootball', ('goals', 'assists_football', 'shots_on_target', 'own_goals')),
    ('pes', ('goals', 'assists_football', 'shots_on_target', 'own_goals')),
    ('call of duty', ('kills', 'deaths', 'assists', 'damage', 'objectives')),
    ('counter', ('kills', 'deaths', 'assists', 'headshots', 'plants', 'defuses')),
    ('valorant', ('kills', 'deaths', 'assists', 'headshots', 'plants', 'defuses')),
    ('apex', ('kills', 'deaths', 'damage', 'revives', 'placement')),
    ('pubg', ('kills', 'deaths', 'damage', 'placement')),
    ('free fire', ('kills', 'deaths', 'damage', 'placement')),
    ('fortnite', ('kills', 'deaths', 'placement')),
    ('mortal kombat', ('rounds_won', 'perfects', 'combos')),
    ('tekken', ('rounds_won', 'perfects', 'combos')),
    ('street fighter', ('rounds_won', 'perfects', 'combos')),
    ('rocket league', ('goals', 'assists_football', 'saves', 'shots_on_target')),
)

# When the game is not one we know. Deliberately short: three things anybody can
# count, rather than a guess at a sport nobody named.
FALLBACK = ('match_wins', 'fair_play', 'penalties')


def get(key):
    """A metric by key, or None. Tolerant of spacing and case."""
    slug = str(key or '').strip().lower().replace('-', '_').replace(' ', '_')
    return METRICS.get(slug)


def defaults_for_game(game_title):
    """The metric keys a tournament on this game should start with.

    Never empty: an organiser with an unrecognised game still gets something to
    edit, and an empty screen reads as a broken feature rather than as a game
    the platform has not met.
    """
    title = str(game_title or '').strip().lower()
    # First match wins, and BY_GAME is ordered most specific first. Not longest
    # match: "call of duty" is longer than "warzone" and less specific, so
    # length would give a battle royale the multiplayer set and drop placement,
    # which is most of how a battle royale is scored.
    for needle, keys in BY_GAME:
        if needle in title:
            return list(keys)
    return list(FALLBACK)


def catalogue():
    """Every metric, for the organiser's picker."""
    return [
        {
            'key': m.key,
            'label': m.label,
            'default_weight': m.default_weight,
            'higher_is_better': m.higher_is_better,
            'decimals': m.decimals,
            'notes': m.notes,
        }
        for m in METRICS.values()
    ]

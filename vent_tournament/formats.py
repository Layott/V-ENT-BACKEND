"""What a tournament format actually is.

A format was a string on the model and a scattering of `if bracket_type ==` in
the views, which is how the participant-count rule came to be "must be even" for
every format while the message said "for single elimination tournaments". Round
robin with five teams is a normal tournament and the form refused it.

So a format is a **definition** here, not a branch. Each one states:

  * how many participants it needs, and whether the count has to be even
  * how the field is seeded
  * how somebody advances, and how the thing ends
  * how a result becomes points - the scoring method
  * which tie-breakers apply, **in order**, because "who finished above whom"
    is decided by the first one that separates them and organisers argue about
    exactly this

Adding a format is a new entry in this file plus, if it scores in a way nothing
else does, a scoring method. It is not a new `if` in six views.

The formats and the numbers in them come from how these events are actually run:

  * Counter-Strike majors run Swiss with Buchholz seeding for both stages, then
    a single-elimination top cut
  * PUBG Mobile and Free Fire run points across several matches, placement plus
    kills, and the placement table differs between the two
  * EA FC leagues here run an aggregate tie across per-player fixtures, which is
    the format V-ENT already had and which must keep working exactly as it does

Sources are recorded against each scoring method in `scoring.py`.
"""
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Tie-breakers, named. The order they appear in a format's list IS the order
# they are applied, and standings report which one separated two participants.
# --------------------------------------------------------------------------

TIEBREAKERS = {
    'head_to_head': 'The result between the tied participants',
    'buchholz': 'Strength of the opponents faced',
    'wins': 'Number of wins',
    'goal_difference': 'Goals scored minus goals conceded',
    'goals_for': 'Goals scored',
    'aggregate_goals': 'Total goals across the tie',
    'rounds_difference': 'Rounds won minus rounds lost',
    'maps_won': 'Maps won',
    'total_kills': 'Total kills',
    'best_placement': 'Best single-match placement',
    'placement_count': 'Number of first places',
    'most_recent': 'The most recent result between them',
    'coin_toss': 'Decided by the organiser',
}


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    summary: str

    # What a valid field looks like. `even_only` is a single-elimination
    # concern: an odd count leaves somebody without an opponent in round one.
    min_participants: int = 2
    max_participants: int = 0          # 0 means no ceiling
    even_only: bool = False
    power_of_two_preferred: bool = False

    seeding: str = 'as_registered'     # as_registered | random | ranked | groups
    advancement: str = 'knockout'      # knockout | table | points | swiss
    scoring: str = 'match_win'

    # In order. The first one that separates two participants decides.
    tiebreakers: tuple = ()

    # Whether this format is normally one stage of something larger, and what
    # it usually feeds into. Real events compose: Swiss into a top cut, groups
    # into a playoff.
    can_feed_into: tuple = ()
    plays_all_at_once: bool = False    # battle royale: everybody in one match

    notes: str = ''

    def count_problem(self, n):
        """Why this number of participants will not work, or None."""
        if n is None:
            return None
        if n < self.min_participants:
            return 'at_least'
        if self.max_participants and n > self.max_participants:
            return 'at_most'
        if self.even_only and n % 2 != 0:
            return 'even'
        return None


FORMATS = {
    'single_elimination': Format(
        key='single_elimination',
        label='Single elimination',
        summary='One loss and you are out. The fastest way to find a winner.',
        min_participants=2,
        even_only=True,
        power_of_two_preferred=True,
        advancement='knockout',
        scoring='match_win',
        tiebreakers=('head_to_head',),
        notes=(
            'A field that is not a power of two needs byes in the first round, '
            'which the strongest seeds should receive.'
        ),
    ),
    'double_elimination': Format(
        key='double_elimination',
        label='Double elimination',
        summary='Two losses to be out. One bad game does not end a run.',
        min_participants=4,
        even_only=True,
        power_of_two_preferred=True,
        advancement='knockout',
        scoring='match_win',
        tiebreakers=('head_to_head',),
        notes=(
            'Twice the matches of single elimination for the same field, so it '
            'needs roughly twice the time. The grand final is where organisers '
            'differ: a bracket reset gives the lower-bracket side the two wins '
            'the upper-bracket side has already earned.'
        ),
    ),
    'round_robin': Format(
        key='round_robin',
        label='Round robin',
        summary='Everyone plays everyone. The table decides it.',
        min_participants=3,
        max_participants=20,
        advancement='table',
        scoring='points_3_1_0',
        tiebreakers=('head_to_head', 'goal_difference', 'goals_for', 'wins'),
        can_feed_into=('single_elimination', 'double_elimination'),
        notes=(
            'Matches grow with the square of the field: eight teams is 28 '
            'matches, sixteen is 120. Past about twelve it wants splitting into '
            'groups.'
        ),
    ),
    'swiss': Format(
        key='swiss',
        label='Swiss',
        summary=(
            'Paired against somebody on the same record each round. Nobody is '
            'knocked out early and everybody plays the same number of games.'
        ),
        min_participants=4,
        advancement='swiss',
        scoring='match_win',
        # Buchholz is the strength of the opponents you were given, and it is
        # what Counter-Strike majors seed the next round by.
        tiebreakers=('buchholz', 'head_to_head', 'rounds_difference'),
        can_feed_into=('single_elimination', 'double_elimination'),
        notes=(
            'Rounds are usually enough to separate the field: 5 rounds for 16, '
            '6 for 32. Teams reaching three wins advance and three losses are '
            'out, which is the shape a Counter-Strike major runs.'
        ),
    ),
    'gsl': Format(
        key='gsl',
        label='GSL groups',
        summary=(
            'Groups of four, double elimination inside each. Two advance, and '
            'everybody plays at least twice.'
        ),
        min_participants=8,
        even_only=True,
        seeding='groups',
        advancement='knockout',
        scoring='match_win',
        tiebreakers=('head_to_head', 'rounds_difference'),
        can_feed_into=('single_elimination',),
        notes=(
            'Five matches per group of four: two openers, a winners match, a '
            'losers match, and a decider. It feeds a knockout stage.'
        ),
    ),
    'battle_royale': Format(
        key='battle_royale',
        label='Battle royale points',
        summary=(
            'Several matches, points for where you finish and for each kill. '
            'The table after the last match decides it.'
        ),
        min_participants=2,
        advancement='points',
        scoring='battle_royale',
        plays_all_at_once=True,
        tiebreakers=('total_kills', 'best_placement', 'placement_count', 'most_recent'),
        notes=(
            'The placement table is the argument: PUBG Mobile pays 10 for a win '
            'down to 1 for eighth, Free Fire pays 12 down to 1 for tenth. Both '
            'pay 1 a kill. Set it to match the game being played.'
        ),
    ),
    'aggregate_2v2': Format(
        key='aggregate_2v2',
        label='Aggregate tie',
        summary=(
            'Each player faces their opposite number, and the tie is decided on '
            'total goals across those fixtures.'
        ),
        min_participants=2,
        # NOT even_only. That flag is a knockout concern - an odd count leaves
        # somebody without an opponent in round one - and this is a table: a
        # round robin where every side plays every other. Five nations is ten
        # fixtures and nobody sits out.
        #
        # It was set here and it refused exactly the event this format was
        # written for. The Rivalry Series is five nations, and the wizard would
        # not create it.
        advancement='table',
        scoring='aggregate_goals',
        # Total goals, never a count of individual wins. Winning one fixture 5-0
        # and losing the other 1-0 wins the tie 5-1.
        tiebreakers=('aggregate_goals', 'head_to_head', 'goals_for'),
        notes=(
            'The EA FC league format V-ENT already runs. A tie is TOTAL GOALS '
            'across the per-player fixtures, never a win count.'
        ),
    ),
    'ladder': Format(
        key='ladder',
        label='Ladder',
        summary='Play when you like over a period. The table is the standing.',
        min_participants=2,
        advancement='table',
        scoring='points_3_1_0',
        tiebreakers=('wins', 'head_to_head', 'goal_difference'),
        notes='Good for a season that runs for weeks rather than an afternoon.',
    ),
}


# Spellings that reach here from screens and from old rows, mapped to the key
# they mean.
#
# `swiss_system` is the one that mattered: the create wizard has saved
# "swiss-system" since it was written, this resolved it to nothing, and a Swiss
# tournament therefore had no format at all - no note, no defaults, no entry in
# the catalogue. Nothing raised, which is why it survived.
ALIASES = {
    'swiss_system': 'swiss',
    'swiss_rounds': 'swiss',
    'league': 'round_robin',
    'roundrobin': 'round_robin',
    'rr': 'round_robin',
    'single': 'single_elimination',
    'double': 'double_elimination',
    'se': 'single_elimination',
    'de': 'double_elimination',
    'br': 'battle_royale',
    'gsl_groups': 'gsl',
    'aggregate': 'aggregate_2v2',
    'aggregate_2_v_2': 'aggregate_2v2',
}


def get(key):
    """A format by key, tolerant of the ways the value has been written.

    The model has held 'Single Elimination', 'single-elimination' and
    'single_elimination' at various points, which is what broke the format
    filters before this existed.
    """
    slug = str(key or '').strip().lower().replace('-', '_').replace(' ', '_')
    return FORMATS.get(ALIASES.get(slug, slug))


def catalogue():
    """Every format, for the wizard and for the admin console."""
    return [
        {
            'key': f.key,
            'label': f.label,
            'summary': f.summary,
            'min_participants': f.min_participants,
            'max_participants': f.max_participants or None,
            'even_only': f.even_only,
            'power_of_two_preferred': f.power_of_two_preferred,
            'scoring': f.scoring,
            'tiebreakers': [
                {'key': t, 'label': TIEBREAKERS[t]} for t in f.tiebreakers
            ],
            'can_feed_into': list(f.can_feed_into),
            'notes': f.notes,
        }
        for f in FORMATS.values()
    ]


# ---------------------------------------------------------------------------
# Which tiebreakers make sense here
# ---------------------------------------------------------------------------

# Tiebreakers that mean something whatever is being played. They are about the
# competition rather than the sport: who beat whom, who has won more, and the
# organiser deciding when nothing else separates two people.
UNIVERSAL_TIEBREAKERS = ('head_to_head', 'wins', 'most_recent', 'coin_toss')

# What a game counts. A football game counts goals and has no idea what a kill
# is; a battle royale counts placement and kills and has no goals. Offering all
# thirteen to everybody is how an EA FC organiser was asked to choose between
# "Goals scored" and "Total kills" in the same list.
#
# Keyed by a substring of the game's title, matched case-insensitively and
# LONGEST FIRST, so "ea fc" is tested before "fc" would be. Same ordering rule
# as the MVP metric catalogue, for the same reason: a short key that is a
# prefix of a longer one wins by accident otherwise.
GAME_TIEBREAKERS = {
    # Football
    'ea fc': ('goal_difference', 'goals_for', 'aggregate_goals'),
    'ea sports fc': ('goal_difference', 'goals_for', 'aggregate_goals'),
    'fifa': ('goal_difference', 'goals_for', 'aggregate_goals'),
    'efootball': ('goal_difference', 'goals_for', 'aggregate_goals'),
    'fc mobile': ('goal_difference', 'goals_for', 'aggregate_goals'),
    'pes': ('goal_difference', 'goals_for', 'aggregate_goals'),
    # Battle royale: placement and kills, no goals
    'free fire': ('total_kills', 'best_placement', 'placement_count'),
    'pubg': ('total_kills', 'best_placement', 'placement_count'),
    'fortnite': ('total_kills', 'best_placement', 'placement_count'),
    'warzone': ('total_kills', 'best_placement', 'placement_count'),
    # Round-based shooters: rounds and maps
    'valorant': ('rounds_difference', 'maps_won'),
    'counter-strike': ('rounds_difference', 'maps_won'),
    'cs2': ('rounds_difference', 'maps_won'),
    'rainbow six': ('rounds_difference', 'maps_won'),
    'call of duty': ('rounds_difference', 'maps_won', 'total_kills'),
    # Fighting and one-on-one: rounds
    'tekken': ('rounds_difference',),
    'street fighter': ('rounds_difference',),
    'mortal kombat': ('rounds_difference',),
}


def tiebreakers_for(format_key=None, game_title=None):
    """The tiebreakers worth offering for this tournament, in order.

    CEO: "EAFC WAS THE GAME SELECTED AND YET I STILL SEE TIE BREAKER FOR
    BATTLE ROYALE GAMES".

    The rules screen was sending the whole catalogue of thirteen to every
    tournament, so an EA FC organiser was offered Total Kills, Maps Won and
    Best Single-Match Placement beside Goals Scored. Each format already
    declared the ones it uses and nothing read them.

    The order is deliberate: what the FORMAT prefers first, because that is
    the closest thing to a house rule, then what the GAME counts, then the
    universal ones. Duplicates are dropped keeping the earliest position, so a
    tiebreaker the format leads with stays at the top.

    Falls back to the whole catalogue only when neither the format nor the
    game is recognised. An unknown game with a short list would be worse than
    a long one: the organiser could not choose what they needed at all.
    """
    ordered = []

    fmt = FORMATS.get(format_key) if format_key else None
    if fmt is not None:
        ordered.extend(fmt.tiebreakers)

    if game_title:
        needle = str(game_title).strip().lower()
        # Longest key first, so 'ea fc' is not beaten by a shorter substring.
        for key in sorted(GAME_TIEBREAKERS, key=len, reverse=True):
            if key in needle:
                ordered.extend(GAME_TIEBREAKERS[key])
                break

    ordered.extend(UNIVERSAL_TIEBREAKERS)

    seen = set()
    out = []
    for key in ordered:
        if key in TIEBREAKERS and key not in seen:
            seen.add(key)
            out.append(key)

    return out or list(TIEBREAKERS)

"""What a format commits an organiser to, worked out rather than described.

The spec line is "Tournament structure, explained automatically once the
bracket is chosen". A sentence per format already existed in `formats.py` and
the rules editor printed it, so the missing half was never the words: it was
the ARITHMETIC. "Round robin" tells somebody nothing. "12 teams, 66 fixtures,
11 rounds" tells them their afternoon is not long enough, which is the decision
they are actually making on that screen.

Three things this is careful about.

**It is computed by the same rules the generator uses.** The number shown in
the wizard and the number of matches that appear when the bracket is built come
from one place, and `tests_structure.py` builds real brackets and asserts they
agree. A promise of 15 matches followed by 16 matches is worse than no promise.

**It reports what will REALLY be drawn.** `services/bracket.generate` has three
shapes: a table, double elimination, and single elimination for everything
else. So choosing Swiss today produces a single elimination bracket, and the
wizard says so rather than describing a Swiss draw that will not be built.
`drawn_as` is that answer, from the same branch the generator takes.

**It returns codes and numbers, never sentences.** The organiser may be reading
in French. Words built in Python cannot be translated, so the caller gets
`advancement='table'` and `rounds=11`, and the screen owns the wording. Same
reason refusals carry a `code`.
"""
import math

from . import formats as fmt


def _next_power_of_two(n):
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


def drawn_as(key):
    """The bracket the generator will actually build for this format.

    Mirrors the branch in `services.bracket.generate`. Kept as its own function
    so the two cannot disagree quietly: if that dispatch grows a fourth shape,
    this is the one other place to change, and the agreement test fails until
    it is.
    """
    definition = fmt.get(key)
    if definition is None:
        return 'single_elimination'
    if definition.advancement == 'table':
        return 'round_robin'
    if definition.key == 'double_elimination':
        return 'double_elimination'
    return 'single_elimination'


def _knockout_shape(n, seats):
    slots = _next_power_of_two(n)
    byes = slots - n
    return {
        'kind': 'knockout',
        'slots': slots,
        'byes': byes,
        'rounds': int(math.log2(slots)),
        # What the generator creates, including the bye rows it marks finished
        # at once. The organiser sees these in the bracket.
        'matches': slots - 1,
        # What anybody actually plays. Every participant except the winner is
        # knocked out exactly once, so it is n - 1 whatever the bye count.
        'games': max(n - 1, 0),
        # Seats do NOT multiply a knockout. A tie made of one match per seat is
        # a TABLE format's idea; a knockout match is one match however many
        # players each side fields, and multiplying here would have told an
        # organiser of a quad tournament they had four times the fixtures.
        'games_on_the_floor': max(n - 1, 0),
    }


def _double_elimination_shape(n, seats):
    slots = _next_power_of_two(n)
    winners_rounds = int(math.log2(slots))
    return {
        'kind': 'knockout',
        'slots': slots,
        'byes': slots - n,
        # The generator numbers rounds by the winners bracket plus the grand
        # final; the losers bracket runs alongside it rather than after it.
        'rounds': winners_rounds + 1,
        'winners_rounds': winners_rounds,
        'losers_rounds': max(2 * (winners_rounds - 1), 0),
        'matches': 2 * slots - 2,
        # Everybody except the champion has to lose twice, and one match
        # produces one loss. The grand final is decisive here rather than a
        # reset, so the winners-bracket side can be knocked out on one loss,
        # which is the single match of slack in the number.
        'games': max(2 * n - 2, 0),
        'games_on_the_floor': max(2 * n - 2, 0),
    }


def _table_shape(n, seats):
    fixtures = n * (n - 1) // 2
    return {
        'kind': 'table',
        'slots': n,
        'byes': 0,
        # The circle method adds a ghost for an odd field, so an odd count
        # takes one more round and somebody sits out each of them.
        'rounds': (n - 1) if n % 2 == 0 else n,
        'matches': fixtures,
        'games': fixtures,
        # A fixture of two seats is two matches to run. The schedule is built
        # from this number, not from the fixture count.
        'games_on_the_floor': fixtures * seats,
        'sits_out_each_round': 1 if n % 2 else 0,
    }


def _points_shape(n, seats):
    """Battle royale: everybody is in the same match, several times over."""
    return {
        'kind': 'points',
        'slots': n,
        'byes': 0,
        # How many matches is the organiser's choice and lives in the ruleset,
        # so claiming a number here would be inventing one.
        'rounds': None,
        'matches': None,
        'games': None,
        'games_on_the_floor': None,
        'everyone_at_once': True,
    }


def _shape_for(definition, drawn, n, seats):
    if definition.plays_all_at_once:
        return _points_shape(n, seats)
    if drawn == 'round_robin':
        return _table_shape(n, seats)
    if drawn == 'double_elimination':
        return _double_elimination_shape(n, seats)
    return _knockout_shape(n, seats)


def describe(raw, participants=None, seats=1):
    """The structure of `raw`, for `participants` entrants, or None.

    `raw` is however the format was written: a catalogue key, a wizard's
    hyphenated spelling, or an old row's label. Anything the catalogue does not
    know returns None rather than a guess, because a guess here becomes a
    number on a screen.
    """
    definition = fmt.get(raw)
    if definition is None:
        return None

    seats = max(1, int(seats or 1))
    drawn = drawn_as(definition.key)

    out = {
        'format': definition.key,
        'label': definition.label,
        'summary': definition.summary,
        'notes': definition.notes,
        'advancement': definition.advancement,
        'scoring': definition.scoring,
        'seeding': definition.seeding,
        'plays_all_at_once': definition.plays_all_at_once,
        'tiebreakers': list(definition.tiebreakers),
        'can_feed_into': list(definition.can_feed_into),
        'seats_per_side': seats,
        # The count rule, so a screen validating an entrant count reads the
        # same numbers the server refuses on. The wizard kept its own copy of
        # this and it was wrong for four of the eight formats.
        'count': {
            'min': definition.min_participants,
            'max': definition.max_participants,   # 0 means no ceiling
            'even_only': definition.even_only,
            'power_of_two_preferred': definition.power_of_two_preferred,
        },
        'drawn_as': drawn,
        # True when the bracket built is not the shape the format names. Swiss
        # and GSL are drawn as knockouts today, and somebody choosing them
        # should be told that on the screen where they choose.
        'drawn_as_differs': drawn != definition.key,
        'shape': None,
    }

    try:
        n = int(participants)
    except (TypeError, ValueError):
        return out
    if n < 2:
        return out

    shape = _shape_for(definition, drawn, n, seats)
    shape['participants'] = n
    # Why this count will be refused, in the catalogue's own words, so the
    # screen does not need a second opinion about what is valid.
    shape['problem'] = definition.count_problem(n)
    out['shape'] = shape
    return out

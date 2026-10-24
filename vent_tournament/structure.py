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

**It reports what will REALLY be drawn.** Until 27 September 2026 the
generator had three shapes and drew Swiss and GSL as single elimination; this
module said so honestly. It now draws each format as itself (`bracket.draw_into`)
and battle royale not at all, so `drawn_as` names the format, or None for a
points table. It still mirrors the generator's dispatch, and the agreement test
still fails the day the two part.

**It returns codes and numbers, never sentences.** The organiser may be reading
in French. Words built in Python cannot be translated, so the caller gets
`advancement='table'` and `rounds=11`, and the screen owns the wording. Same
reason refusals carry a `code`.
"""
import math

from . import formats as fmt
from . import stage_settings


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
    if definition.plays_all_at_once:
        return None
    if definition.advancement == 'table':
        return 'round_robin'
    if definition.key in ('double_elimination', 'swiss', 'gsl', 'bracket_groups', 'custom_bracket'):
        return definition.key
    return 'single_elimination'


def _threshold_shape(shape, n, threshold, double=False):
    """A knockout that stops when `threshold` are left (P3 W1): the rounds
    that would decide between them are not drawn. Mirrors the cut in
    `services.bracket`, and the agreement test holds the two together."""
    slots = shape['slots']
    shape['qualify'] = threshold
    if threshold * 2 > slots:
        shape['threshold_problem'] = 'threshold_too_big'
        return shape
    cut = int(math.log2(threshold))
    if double:
        upper = shape['winners_rounds'] - cut + 1
        shape.update({
            # The last lower round follows the last upper round.
            'rounds': upper + 1, 'winners_rounds': upper, 'losers_rounds': 2 * upper - 2,
            # Upper rounds keep slots - threshold/2 matches, lower rounds
            # slots - threshold; there is no grand final.
            'matches': 2 * slots - 3 * threshold // 2,
            # Everybody knocked out lost twice; the lower qualifiers once.
            'games': max(2 * (n - threshold) + threshold // 2, 0),
            'grand_final_reset': False,
        })
        shape['games_on_the_floor'] = shape['games']
        shape.pop('games_at_most', None)
    else:
        shape.update({'rounds': shape['rounds'] - cut, 'matches': slots - threshold,
                      'games': max(n - threshold, 0)})
        shape['games_on_the_floor'] = shape['games']
    return shape


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


def _double_elimination_shape(n, seats, reset=False):
    slots = _next_power_of_two(n)
    winners_rounds = int(math.log2(slots))
    shape = {
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
        'grand_final_reset': reset,
    }
    if reset:
        # The reset is a row that exists from the start and is played only
        # when the losers' champion wins the first grand final.
        shape['rounds'] += 1
        shape['matches'] += 1
        shape['games_at_most'] = shape['games'] + 1
    return shape


def _swiss_shape(n, seats, rounds=0):
    rounds = rounds or max(1, math.ceil(math.log2(max(2, n))))
    per_round = n // 2 + (n % 2)          # the bye is a row too
    return {
        'kind': 'swiss',
        'slots': n,
        'byes': rounds if n % 2 else 0,
        'rounds': rounds,
        'matches': per_round * rounds,
        'games': (n // 2) * rounds,
        'games_on_the_floor': (n // 2) * rounds,
        'sits_out_each_round': n % 2,
    }


def _gsl_shape(n, seats):
    groups = n // 4
    return {
        'kind': 'gsl',
        'slots': n,
        'byes': 0,
        'groups': groups,
        'rounds': 3,
        'matches': 5 * groups,
        'games': 5 * groups,
        'games_on_the_floor': 5 * groups,
        'advance_per_group': 2,
    }


def _bracket_groups_shape(n, seats, groups, settings):
    """Each group a knockout of its own (P3 W1), dealt by snake, with the
    stage's threshold. Mirrors `services.bracket._generate_bracket_groups`."""
    if not groups or groups < 2:
        groups = max(1, math.ceil(n / int(settings.get('group_size') or 4)))
    double = settings.get('bracket', 'double') == 'double'
    threshold = stage_settings.threshold_of(settings)
    sizes = [0] * groups
    for i in range(n):
        pot, pos = divmod(i, groups)
        sizes[pos if pot % 2 == 0 else groups - 1 - pos] += 1
    parts = []
    for size in sizes:
        part = (_double_elimination_shape(size, seats, reset=settings.get('grand_final') == 'reset'
                                          and not threshold)
                if double else _knockout_shape(size, seats))
        parts.append(_threshold_shape(part, size, threshold, double=double) if threshold else part)
    shape = {
        'kind': 'bracket_groups',
        'slots': n,
        'byes': sum(p['byes'] for p in parts),
        'groups': groups,
        'group_sizes': sizes,
        'bracket': 'double' if double else 'single',
        'rounds': max(p['rounds'] for p in parts),
        'matches': sum(p['matches'] for p in parts),
        'games': sum(p['games'] for p in parts),
        'games_on_the_floor': sum(p['games_on_the_floor'] for p in parts),
        'qualify': groups * max(threshold, 1),
    }
    problems = [p.get('threshold_problem') for p in parts if p.get('threshold_problem')]
    if problems:
        shape['threshold_problem'] = problems[0]
    if any(size < (3 if double else 2) for size in sizes):
        shape['threshold_problem'] = 'group_too_small'
    return shape


def _custom_shape(n, settings):
    """A custom bracket's numbers, from the schema it will be drawn from (P3
    W2): the organiser's, or its preset's for this many entrants."""
    from . import bracket_schema
    try:
        schema = bracket_schema.schema_for(settings, n)
        seeds = bracket_schema.check(schema)
    except (bracket_schema.SchemaError, ValueError):
        return {'kind': 'custom', 'slots': None, 'byes': None, 'rounds': None, 'matches': None,
                'games': None, 'games_on_the_floor': None, 'threshold_problem': 'schema_invalid'}
    matches = schema['matches']
    shape = {
        'kind': 'custom',
        'slots': seeds,
        'byes': max(seeds - n, 0),
        'rounds': max(m['round'] for m in matches),
        'matches': len(matches),
        'games': max(len(matches) - max(seeds - n, 0), 0),
        'branches': sorted({m.get('branch') or '' for m in matches} - {''}),
    }
    shape['games_on_the_floor'] = shape['games']
    if n > seeds:
        shape['threshold_problem'] = 'schema_too_small'
    return shape


def _groups_shape(n, seats, groups):
    """A round robin split into groups, dealt the way `split_into_groups` deals."""
    sizes = [0] * groups
    for i in range(n):
        pot, pos = divmod(i, groups)
        sizes[pos if pot % 2 == 0 else groups - 1 - pos] += 1
    parts = [_table_shape(size, seats) for size in sizes]
    return {
        'kind': 'table',
        'slots': n,
        'byes': 0,
        'groups': groups,
        'group_sizes': sizes,
        'rounds': max(p['rounds'] for p in parts),
        'matches': sum(p['matches'] for p in parts),
        'games': sum(p['games'] for p in parts),
        'games_on_the_floor': sum(p['games_on_the_floor'] for p in parts),
    }


def _league_phase_shape(n, seats, settings):
    """A league phase (P3 W3a): everybody plays pots x per_pot others once,
    so the field plays n x that / 2 matches; one more round than each plays,
    because the rounds are filled greedily."""
    each = int(settings.get('pots') or 4) * int(settings.get('per_pot') or 2)
    fixtures = n * each // 2
    return {
        'kind': 'table',
        'slots': n,
        'byes': 0,
        'rounds': each + 1,
        'matches': fixtures,
        'games': fixtures,
        'games_on_the_floor': fixtures * seats,
        'sits_out_each_round': 0,
        'legs': 1,
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


def _shape_for(definition, drawn, n, seats, settings=None, groups=0):
    settings = settings or {}
    if definition.plays_all_at_once:
        return _points_shape(n, seats)
    if definition.key == 'league_phase':
        return _league_phase_shape(n, seats, settings)
    if definition.key == 'challenge_ladder':
        # Nothing is drawn up front: each challenge is one match.
        return {'kind': 'ladder', 'slots': n, 'byes': 0, 'rounds': 0, 'matches': 0,
                'games': 0, 'games_on_the_floor': 0, 'sits_out_each_round': 0, 'legs': 1}
    if drawn == 'round_robin':
        # A League is home and away unless told once (inbox 440).
        legs = int(settings.get('legs') or (2 if definition.key == 'league' else 1))
        shape = (_groups_shape(n, seats, groups) if groups and groups > 1
                 else _table_shape(n, seats))
        if legs > 1:
            for k in ('rounds', 'matches', 'games', 'games_on_the_floor'):
                shape[k] *= legs
        # So a screen can say "twice, home and away" rather than "once" beside
        # a doubled count (walk of inbox 440: "each meet once: 39800").
        shape['legs'] = legs
        return shape
    threshold = stage_settings.threshold_of(settings) if drawn in (
        'single_elimination', 'double_elimination') else 0
    if drawn == 'double_elimination':
        shape = _double_elimination_shape(
            n, seats, reset=settings.get('grand_final') == 'reset' and not threshold)
        return _threshold_shape(shape, n, threshold, double=True) if threshold else shape
    if drawn == 'swiss':
        return _swiss_shape(n, seats, int(settings.get('rounds') or 0))
    if drawn == 'gsl':
        return _gsl_shape(n, seats)
    if drawn == 'bracket_groups':
        return _bracket_groups_shape(n, seats, groups, settings)
    if drawn == 'custom_bracket':
        return _custom_shape(n, settings)
    shape = _knockout_shape(n, seats)
    if threshold:
        return _threshold_shape(shape, n, threshold)
    if settings.get('third_place') and shape['rounds'] >= 2:
        shape['matches'] += 1
        shape['games'] += 1
        shape['games_on_the_floor'] += 1
    return shape


def describe(raw, participants=None, seats=1, settings=None, groups=0):
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
        # True when the bracket built is not the shape the format names: a
        # ladder or aggregate league scheduled as a round robin, a points
        # table that is not drawn at all.
        'drawn_as_differs': drawn != definition.key,
        'shape': None,
    }

    try:
        n = int(participants)
    except (TypeError, ValueError):
        return out
    if n < 2:
        return out

    shape = _shape_for(definition, drawn, n, seats, settings, groups)
    shape['participants'] = n
    # Why this count will be refused, in the catalogue's own words, so the
    # screen does not need a second opinion about what is valid.
    shape['problem'] = definition.count_problem(n) or shape.pop('threshold_problem', None)
    shape.pop('threshold_problem', None)
    out['shape'] = shape
    return out

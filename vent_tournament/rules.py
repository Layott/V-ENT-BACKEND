"""The organiser's own rules, which is what they actually asked for.

`formats.py` describes how these tournaments are usually run. That is a starting
point and nothing more: an organiser running a Free Fire event in Lagos may want
15 points for a win rather than 12, two points a kill rather than one, kills
counted before placement, and their own order of tie-breakers. None of that is
unusual and all of it was impossible.

So a tournament carries a **ruleset**, which is a copy of a preset that the
organiser then edits. Three consequences worth stating:

  * changing a preset later does not silently change a tournament that is
    already running, because the tournament holds its own copy
  * every number a result is scored by can be pointed at, which is what an
    organiser needs when somebody asks why a team finished where it did
  * nothing about a format is hardcoded in a view any more, so "add a scoring
    system" is data

Validation is deliberately strict about shape and deliberately loose about
values. Fifteen points for a win is somebody's league. Zero points for a win is
somebody's mistake, but it is theirs to make, and refusing it would be this
module deciding it knows their sport better than they do. What it refuses is
nonsense: a tie-breaker that does not exist, a placement table keyed by
something that is not a position, a value that is not a number.
"""
from . import formats as fmt
from . import scoring


DEFAULT_POINTS = {'win': 3, 'draw': 1, 'loss': 0}

# What a ruleset looks like when it is built from a format, before the organiser
# touches anything.
PRESETS = {
    'single_elimination': {
        'points': {'win': 1, 'draw': 0, 'loss': 0},
        'tiebreakers': ['head_to_head'],
        'best_of': 1,
        'third_place_match': False,
    },
    'double_elimination': {
        'points': {'win': 1, 'draw': 0, 'loss': 0},
        'tiebreakers': ['head_to_head'],
        'best_of': 3,
        'bracket_reset': True,
    },
    'round_robin': {
        'points': {'win': 3, 'draw': 1, 'loss': 0},
        'tiebreakers': ['head_to_head', 'goal_difference', 'goals_for', 'wins'],
        'double_round': False,
    },
    'swiss': {
        'points': {'win': 1, 'draw': 0, 'loss': 0},
        'tiebreakers': ['buchholz', 'head_to_head', 'rounds_difference'],
        'rounds': 5,
        'advance_at_wins': 3,
        'eliminate_at_losses': 3,
    },
    'gsl': {
        'points': {'win': 1, 'draw': 0, 'loss': 0},
        'tiebreakers': ['head_to_head', 'rounds_difference'],
        'group_size': 4,
        'advance_per_group': 2,
    },
    'battle_royale': {
        'points': {'win': 0, 'draw': 0, 'loss': 0},
        'placement_points': dict(scoring.PUBG_PLACEMENT),
        'points_per_kill': 1,
        'tiebreakers': ['total_kills', 'best_placement', 'placement_count', 'most_recent'],
        'matches': 6,
    },
    'aggregate_2v2': {
        'points': {'win': 3, 'draw': 1, 'loss': 0},
        'tiebreakers': ['aggregate_goals', 'head_to_head', 'goals_for'],
        'fixtures_per_tie': 2,
    },
    'ladder': {
        'points': {'win': 3, 'draw': 1, 'loss': 0},
        'tiebreakers': ['wins', 'head_to_head', 'goal_difference'],
    },
}

# Placement tables an organiser can start from rather than typing out.
PLACEMENT_PRESETS = {
    'pubg_mobile': dict(scoring.PUBG_PLACEMENT),
    'free_fire': dict(scoring.FREE_FIRE_PLACEMENT),
    'flat_top_three': {1: 5, 2: 3, 3: 1},
    'winner_only': {1: 1},
}


def preset_for(format_key):
    """A fresh, editable ruleset for a format. Never the preset object itself."""
    definition = fmt.get(format_key)
    key = definition.key if definition else 'single_elimination'
    base = PRESETS.get(key, PRESETS['single_elimination'])

    ruleset = {
        'format': key,
        'preset': key,
        'points': dict(base.get('points', DEFAULT_POINTS)),
        'tiebreakers': list(base.get('tiebreakers', ['wins'])),
        'scoring': definition.scoring if definition else 'match_win',
    }
    for extra in ('placement_points', 'points_per_kill', 'best_of', 'rounds',
                  'matches', 'group_size', 'advance_per_group', 'double_round',
                  'third_place_match', 'bracket_reset', 'advance_at_wins',
                  'eliminate_at_losses', 'fixtures_per_tie'):
        if extra in base:
            value = base[extra]
            ruleset[extra] = dict(value) if isinstance(value, dict) else value
    return ruleset


class RulesetError(ValueError):
    """A ruleset that cannot be stored, with a sentence saying why."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def _positive_int(value, field, *, allow_zero=True):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise RulesetError('%s has to be a whole number.' % field, field)
    if n < 0 or (n == 0 and not allow_zero):
        raise RulesetError('%s cannot be negative.' % field, field)
    return n


def clean(raw):
    """Check a submitted ruleset and return the stored form.

    Strict about shape, loose about values. Fifteen points for a win is
    somebody's league; a tie-breaker that does not exist is a bug waiting to
    surface in a final standings table, so that is refused.
    """
    if not isinstance(raw, dict):
        raise RulesetError('A ruleset is a set of named settings.')

    format_key = str(raw.get('format') or '').strip().lower()
    definition = fmt.get(format_key)
    if definition is None:
        raise RulesetError('There is no format called %r.' % format_key, 'format')

    out = {'format': definition.key, 'preset': raw.get('preset') or definition.key}

    # Points. Any whole number, including ones nobody else uses.
    points = raw.get('points') or {}
    if not isinstance(points, dict):
        raise RulesetError('Points must name a value for win, draw and loss.', 'points')
    out['points'] = {
        outcome: _positive_int(points.get(outcome, DEFAULT_POINTS[outcome]),
                               'Points for a %s' % outcome)
        for outcome in ('win', 'draw', 'loss')
    }

    # Tie-breakers, in the order given. The order IS the setting.
    order = raw.get('tiebreakers')
    if order is None:
        order = list(definition.tiebreakers)
    if not isinstance(order, list):
        raise RulesetError('Tie-breakers are a list, in the order they apply.',
                           'tiebreakers')
    unknown = [t for t in order if t not in fmt.TIEBREAKERS]
    if unknown:
        raise RulesetError(
            'No tie-breaker called %s.' % ', '.join(str(u) for u in unknown),
            'tiebreakers')
    if len(set(order)) != len(order):
        raise RulesetError('A tie-breaker can only appear once.', 'tiebreakers')
    out['tiebreakers'] = list(order)

    method = raw.get('scoring') or definition.scoring
    if method not in scoring.METHODS:
        raise RulesetError('There is no scoring method called %r.' % method, 'scoring')
    out['scoring'] = method

    # The placement table, fully editable: any positions, any values.
    if 'placement_points' in raw or method == 'battle_royale':
        table = raw.get('placement_points')
        if table is None:
            table = PLACEMENT_PRESETS['pubg_mobile']
        if not isinstance(table, dict):
            raise RulesetError(
                'The placement table maps a finishing position to points.',
                'placement_points')
        cleaned = {}
        for position, value in table.items():
            place = _positive_int(position, 'Position %r' % position, allow_zero=False)
            cleaned[place] = _positive_int(value, 'Points for position %s' % place)
        if not cleaned:
            raise RulesetError('The placement table cannot be empty.',
                               'placement_points')
        out['placement_points'] = cleaned
        out['points_per_kill'] = _positive_int(
            raw.get('points_per_kill', 1), 'Points per kill')

    for field, allow_zero in (
        ('best_of', False), ('rounds', False), ('matches', False),
        ('group_size', False), ('advance_per_group', False),
        ('advance_at_wins', False), ('eliminate_at_losses', False),
        ('fixtures_per_tie', False),
    ):
        if field in raw and raw[field] is not None:
            out[field] = _positive_int(raw[field], field.replace('_', ' '),
                                       allow_zero=allow_zero)

    for flag in ('double_round', 'third_place_match', 'bracket_reset'):
        if flag in raw:
            out[flag] = bool(raw[flag])

    return out


def build_table(ruleset, results):
    """Score results the way THIS tournament says to."""
    method = ruleset.get('scoring', 'match_win')

    if method == 'battle_royale':
        table = {}
        points_for = {int(k): int(v) for k, v in (ruleset.get('placement_points') or {}).items()}
        per_kill = int(ruleset.get('points_per_kill', 1))
        for r in results:
            row = scoring._row(table, r['participant'])
            placement = int(r.get('placement') or 0)
            kills = int(r.get('kills') or 0)
            row.played += 1
            row.kills += kills
            if placement:
                row.placements.append(placement)
            row.points += points_for.get(placement, 0) + kills * per_kill
            if placement == 1:
                row.wins += 1
        return table

    table = scoring.match_win(results)
    if method == 'aggregate_goals':
        for row in table.values():
            row.points = row.goals_for
        return table

    # Everything else pays the organiser's own numbers for a win, draw and loss.
    points = ruleset.get('points', DEFAULT_POINTS)
    for row in table.values():
        row.points = (
            row.wins * int(points.get('win', 3))
            + row.draws * int(points.get('draw', 1))
            + row.losses * int(points.get('loss', 0))
        )
    return table

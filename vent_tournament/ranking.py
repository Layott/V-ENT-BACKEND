"""The one way a table is ranked.

CEO, 16 October 2026, agreeing the wrong results go first: "these are fine."
Until then a stage's group table ranked by a fixed list and ignored the
tie-breakers the organiser chose, a stage Swiss scored 3-1-0 whatever the
organiser set, and seven places in the code each sorted a table their own way,
so the same rules could give a different order on two screens. Everything that
ranks a table calls `order` now, and `tools/check-one-ranking.py` fails a commit
that sorts a standings list anywhere else.

What `order` promises:

- Points first, then each tie-breaker in the organiser's order.
- Head to head is a mini-table between the sides still level at that point,
  and it is applied again inside any smaller group it leaves level, so three
  sides level on points are separated by their results against each other.
- Sides level on points and on every tie-breaker share a rank: two of them on
  third are both third. `position` is still unique (rank, then seed, then name)
  so a list has one order, but the rank is what a place and a prize go by.
- Every row says what put it below the row above (`decided_by`), because "why is
  that team above mine" deserves an answer better than "the system decided".
"""
from . import formats


class RulesError(ValueError):
    """A stage's scoring that cannot be saved. `code` is translated by the screen."""

    def __init__(self, code, field, message=None):
        super().__init__(message or code)
        self.code = code
        self.field = field


# Tie-breaker key: (the row field it reads, whether higher is better).
CATALOGUE = {
    'points': ('points', True),
    'goal_difference': ('goal_difference', True),
    'goals_for': ('goals_for', True),
    'goals_against': ('goals_against', False),
    'wins': ('wins', True),
    'fixtures_won': ('fixtures_won', True),
    'buchholz': ('buchholz', True),
    # Formats that name the same numbers their own way.
    'rounds_difference': ('goal_difference', True),
    'aggregate_goals': ('goals_for', True),
    'maps_won': ('wins', True),
    'seed': ('seed', False),
}

# What a table and a Swiss fall back to when nobody chose, which is what they
# did before 16 October 2026, so a tournament nobody configured ranks the same.
TABLE_DEFAULT = ['head_to_head', 'goal_difference', 'goals_for', 'wins', 'seed']
SWISS_DEFAULT = ['buchholz', 'goal_difference', 'goals_for', 'seed']

_UNSEEDED = 10 ** 6


def _field_value(row, key):
    spec = CATALOGUE.get(key)
    if spec is None:
        return None
    field, higher = spec
    raw = row.get(field)
    if raw is None and key in ('wins', 'maps_won'):
        raw = row.get('won')       # the league table counts wins as `won`
    if raw is None:
        raw = _UNSEEDED if key == 'seed' else 0
    return raw if higher else -raw


def order(rows, tiebreakers, *, h2h=None, value=None, lead=()):
    """Rank `rows` and return them in order, stamped in place.

    `tiebreakers` is applied after points, in order; unknown keys separate
    nobody. `h2h(ids)` answers head to head for a set of registration ids as
    {id: number}, higher better; without it head to head is skipped. `value(row,
    key)` answers keys the catalogue does not know (battle royale), higher
    better, or None to fall back to the catalogue. `lead` are keys applied
    before points (a battle royale champion on match point).

    Each row gets `rank` (shared when level on everything), `position`
    (unique), `tied` and `decided_by`.
    """
    rows = list(rows)

    def val(row, key):
        if value is not None:
            found = value(row, key)
            if found is not None:
                return found
        found = _field_value(row, key)
        return 0 if found is None else found

    def split(group, keys):
        """Ordered tiers of `group`: (rows level on every key, the key that put
        that tier below the one above it)."""
        if len(group) <= 1 or not keys:
            return [(group, None)]
        key, rest = keys[0], keys[1:]
        if key == 'head_to_head':
            if h2h is None:
                return split(group, rest)
            mini = h2h({r['registration_id'] for r in group})
            score = lambda r: mini.get(r['registration_id'], 0)   # noqa: E731
        else:
            score = lambda r: val(r, key)                         # noqa: E731
        buckets = {}
        for r in group:
            buckets.setdefault(score(r), []).append(r)
        if len(buckets) == 1:
            return split(group, rest)
        tiers = []
        for index, mark in enumerate(sorted(buckets, reverse=True)):
            bucket = buckets[mark]
            # Head to head again inside a smaller group it left level.
            again = key == 'head_to_head' and 1 < len(bucket) < len(group)
            for sub_index, (tier, separated) in enumerate(split(bucket, keys if again else rest)):
                tiers.append((tier, key if (sub_index == 0 and index > 0) else separated))
        return tiers

    keys = list(lead) + ['points'] + [k for k in (tiebreakers or []) if k != 'points']
    out = []
    for tier, separated in split(rows, keys):
        tier = sorted(tier, key=lambda r: (r.get('seed') if r.get('seed') is not None else _UNSEEDED,
                                           str(r.get('name') or r.get('player') or '').lower()))
        rank = len(out) + 1
        for index, r in enumerate(tier):
            r['rank'] = rank
            r['position'] = len(out) + 1
            r['tied'] = len(tier) > 1
            r['decided_by'] = separated if (index == 0 and out) else None
            out.append(r)
    return out


def stage_rules(stage):
    """A stage's scoring: its own rules, else the organiser's tournament rules,
    else the standard 3, 1, 0. `tiebreakers` is None when nobody chose, so the
    caller uses the format's default."""
    from .models import LeagueRules

    raw = stage.rules if isinstance(stage.rules, dict) else {}
    league = LeagueRules.objects.filter(tournament_id=stage.tournament_id).first()

    def pick(key, default):
        if raw.get(key) is not None:
            return int(raw[key])
        if league is not None:
            return int(getattr(league, key, default))
        return default

    win = pick('points_win', 3)
    chosen = raw.get('tiebreakers') or (list(league.tiebreakers) if league and league.tiebreakers else None)
    return {
        'points_win': win,
        'points_draw': pick('points_draw', 1),
        'points_loss': pick('points_loss', 0),
        'points_bye': int(raw['points_bye']) if raw.get('points_bye') is not None else win,
        'tiebreakers': chosen,
        'buchholz': raw.get('buchholz') if raw.get('buchholz') in ('wins', 'points') else 'wins',
    }


POINT_KEYS = ('points_win', 'points_draw', 'points_loss', 'points_bye')


def offered(format_key):
    """The tie-breakers a stage of this format may rank by, in the order shown.

    The format's own first, then the ones every table can use, then seed. Only
    keys `order` can compute: a choice that separates nobody would be a
    control that does nothing. The stage planner shows this list and
    `clean_rules` accepts exactly it.
    """
    computable = (set(CATALOGUE) - {'points'}) | {'head_to_head'}
    generic = ['head_to_head', 'goal_difference', 'goals_for', 'goals_against', 'wins']
    if format_key == 'swiss':
        generic.insert(0, 'buchholz')
    wanted = [t for t in formats.tiebreakers_for(format_key) if t in computable] + generic + ['seed']
    return [t for t in dict.fromkeys(wanted) if t in computable]


def clean_rules(format_key, raw):
    """A stage's scoring as the organiser sent it, checked. None means the standard."""
    if raw in (None, '') or raw == {}:
        return None
    if not isinstance(raw, dict):
        raise RulesError('RULES_INVALID', 'rules')
    out = {}
    for key in POINT_KEYS:
        if raw.get(key) in (None, ''):
            continue
        try:
            number = int(raw[key])
        except (TypeError, ValueError):
            raise RulesError('RULES_INVALID', key)
        if not 0 <= number <= 100:
            raise RulesError('RULES_INVALID', key)
        out[key] = number
    if raw.get('tiebreakers') not in (None, ''):
        if not isinstance(raw['tiebreakers'], list):
            raise RulesError('RULES_INVALID', 'tiebreakers')
        allowed = set(offered(format_key))
        out['tiebreakers'] = [str(k) for k in raw['tiebreakers'] if str(k) in allowed][:12]
    if raw.get('buchholz') not in (None, ''):
        if raw['buchholz'] not in ('wins', 'points'):
            raise RulesError('RULES_INVALID', 'buchholz')
        out['buchholz'] = raw['buchholz']
    return out or None

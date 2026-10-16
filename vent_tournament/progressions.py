"""The lines between stages: who goes where, and when (P1b).

CEO, 16 October 2026: "i aprove the design". Section 6 of the bracket
structure design. A stage used to know the next one by `order` and how many
`advances`, so every tournament was a straight chain. A `Progression` line says
"places a to b of this stage (each group, or one group) go to that stage, this
side", which also covers a split, a split league with its points, a merge of
pools and qualifiers entering the lower bracket. The engine follows the lines;
this module owns them.

Every tournament planned before keeps the chain its order and advances
describe: `convert` writes those lines once, with whatever the stage already
sent, and `ensure` does it for a tournament whose stages were made without the
planner.
"""


def _get(stage, name, default=None):
    if isinstance(stage, dict):
        return stage.get(name, default)
    return getattr(stage, name, default)


def chain(stages):
    """The lines the old planner implies, as dicts that index into `stages`."""
    lines = []
    for index in range(len(stages) - 1):
        advances = int(_get(stages[index], 'advances') or 0)
        if advances <= 0:
            continue
        lines.append({'source': index, 'target': index + 1, 'group': None,
                      'place_from': 1, 'place_to': advances, 'side': 'any',
                      'carry_points': False, 'fire': 'after_preview', 'order': index})
    return lines


def lines_of(tournament):
    from .models import Progression
    return Progression.objects.filter(source__tournament=tournament).select_related(
        'source', 'target')


def convert(tournament):
    """Write the chain lines of a tournament that has none; return how many."""
    from .models import Progression
    stages = list(tournament.stages.all().order_by('order'))
    if len(stages) < 2 or lines_of(tournament).exists():
        return 0
    made = 0
    for line in chain(stages):
        source, target = stages[line['source']], stages[line['target']]
        fired = source.status == 'complete'
        Progression.objects.create(
            source=source, target=target, group=None,
            place_from=line['place_from'], place_to=line['place_to'],
            side='any', carry_points=False, fire='after_preview', order=line['order'],
            sent=list(source.advanced or []) if fired else [],
            fired_at=source.completed_at if fired else None)
        made += 1
    return made


def ensure(tournament):
    """The lines exist: stages made without the planner get their chain."""
    return convert(tournament)


# ---------------------------------------------------------------------------
# Checking a plan's lines
# ---------------------------------------------------------------------------

SIDES = ('any', 'winners', 'losers')
FIRES = ('after_preview', 'on_close', 'manual')


class LineError(ValueError):
    """Why a plan's lines cannot be saved: a code the screen translates, the
    field, and which line (`index`)."""

    def __init__(self, code, field='progressions', index=None):
        super().__init__(code)
        self.code = code
        self.field = field
        self.index = index


def _whole(raw, name, index, default=None):
    value = raw.get(name, default)
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LineError('PROGRESSION_NOT_A_NUMBER', name, index)


def _groups_covered(line, stages):
    groups = int(_get(stages[line['source']], 'groups') or 0)
    return 1 if line['group'] is not None else max(groups, 1)


def inflow(lines, stages, target, participants=None):
    """How many reach stage `target`: what its lines send, plus anybody seeded
    straight into it. A stage no line feeds is a starting stage: its own
    invited entrants, else the registrations nobody was seeded past."""
    direct = len(_get(stages[target], 'direct_entrants') or [])
    feeding = [l for l in lines if l['target'] == target]
    if feeding:
        return sum((l['place_to'] - l['place_from'] + 1) * _groups_covered(l, stages)
                   for l in feeding) + direct
    if direct:
        return direct
    if participants is None:
        return None
    seeded = sum(len(_get(s, 'direct_entrants') or []) for s in stages)
    return max(participants - seeded, 0)


def _cycle(lines, count):
    out = {i: [l['target'] for l in lines if l['source'] == i] for i in range(count)}
    state = {}

    def visit(node):
        state[node] = 1
        for nxt in out[node]:
            if state.get(nxt) == 1 or (state.get(nxt) is None and visit(nxt)):
                return True
        state[node] = 2
        return False

    return any(state.get(i) is None and visit(i) for i in range(count))


def clean(lines, stages, participants=None):
    """Check a plan's lines against its stages and return the stored form.

    `stages` are the cleaned stages in plan order; a line names its source and
    target by their index. Raises `LineError`.
    """
    from . import formats

    if not isinstance(lines, list):
        raise LineError('PROGRESSION_NOT_A_LIST')
    count = len(stages)
    out = []
    for index, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise LineError('PROGRESSION_NOT_AN_OBJECT', index=index)
        source = _whole(raw, 'source', index)
        target = _whole(raw, 'target', index)
        if source is None or target is None or not (0 <= source < count and 0 <= target < count):
            raise LineError('PROGRESSION_STAGE_UNKNOWN', 'target', index)
        if source == target:
            raise LineError('PROGRESSION_SAME_STAGE', 'target', index)
        group = _whole(raw, 'group', index)
        place_from = _whole(raw, 'place_from', index, 1)
        place_to = _whole(raw, 'place_to', index, 0)
        if place_from < 1 or place_to < place_from:
            raise LineError('PROGRESSION_PLACES', 'place_to', index)
        side = str(raw.get('side') or 'any')
        if side not in SIDES:
            raise LineError('PROGRESSION_SIDE_UNKNOWN', 'side', index)
        fire = str(raw.get('fire') or 'after_preview')
        if fire not in FIRES:
            raise LineError('PROGRESSION_FIRE_UNKNOWN', 'fire', index)
        groups = int(_get(stages[source], 'groups') or 0)
        if group is not None and not (1 <= group <= max(groups, 1)):
            raise LineError('PROGRESSION_GROUP_UNKNOWN', 'group', index)
        src_fmt = formats.get(_get(stages[source], 'format'))
        tgt_fmt = formats.get(_get(stages[target], 'format'))
        if src_fmt is None or tgt_fmt is None or tgt_fmt.key not in (src_fmt.can_feed_into or ()):
            raise LineError('PROGRESSION_FORMAT_CANNOT_FOLLOW', 'target', index)
        if side != 'any' and tgt_fmt.key != 'double_elimination':
            raise LineError('PROGRESSION_LOSERS_SIDE_NEEDS_DOUBLE_ELIMINATION', 'side', index)
        out.append({'source': source, 'target': target, 'group': group,
                    'place_from': place_from, 'place_to': place_to, 'side': side,
                    'carry_points': bool(raw.get('carry_points')), 'fire': fire,
                    'order': _whole(raw, 'order', index, index),
                    'id': _whole(raw, 'id', index)})

    if _cycle(out, count):
        raise LineError('PROGRESSION_CYCLE')

    for i, a in enumerate(out):
        for b in out[i + 1:]:
            same_groups = a['group'] is None or b['group'] is None or a['group'] == b['group']
            if (a['source'] == b['source'] and same_groups
                    and a['place_from'] <= b['place_to'] and b['place_from'] <= a['place_to']):
                raise LineError('PROGRESSION_PLACE_TWICE', 'place_from', out.index(b))

    for index, line in enumerate(out):
        size = inflow(out, stages, line['source'], participants)
        groups = int(_get(stages[line['source']], 'groups') or 0)
        if size is not None and line['place_to'] > size // max(groups, 1):
            raise LineError('PROGRESSION_MORE_THAN_A_GROUP_HOLDS', 'place_to', index)

    for target in sorted({l['target'] for l in out}):
        fmt = formats.get(_get(stages[target], 'format'))
        reaching = inflow(out, stages, target, participants)
        where = next(i for i, l in enumerate(out) if l['target'] == target)
        if reaching < fmt.min_participants:
            raise LineError('PROGRESSION_TARGET_TOO_FEW', 'place_to', where)
        if fmt.max_participants and reaching > fmt.max_participants:
            raise LineError('PROGRESSION_TARGET_TOO_MANY', 'place_to', where)
    return out

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
    """The lines inside one tournament. A feed to or from another tournament
    (P2) is not one of its own lines: the plan editor would delete it, and the
    chain conversion would take it for a plan."""
    from .models import Progression
    return Progression.objects.filter(source__tournament=tournament,
                                      target__tournament=tournament).select_related(
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
    from . import formats, stage_settings

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
        if src_fmt is None or tgt_fmt is None or tgt_fmt.key not in stage_settings.feeds_into(
                src_fmt.key, _get(stages[source], 'settings')):
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


# ---------------------------------------------------------------------------
# Running the lines
# ---------------------------------------------------------------------------

def outgoing(stage):
    ensure(stage.tournament)
    return list(stage.outgoing.select_related('target').order_by('order', 'id'))


def incoming(stage):
    ensure(stage.tournament)
    return list(stage.incoming.select_related('source').order_by('order', 'id'))


def rows_for(line, rows=None):
    """The standings rows a line sends, best first.

    Places `place_from` to `place_to` of its group, or of every group taken
    rank by rank (1A, 1B, 2A, 2B), which seeded into a knockout is the cross
    every football competition uses. A Swiss with a win target sends those who
    reached it first; a battle royale line covering the stage's own advances
    sends what the battle royale engine qualifies, with each squad's head start.
    """
    from . import formats, stage_engine
    if line.is_circuit_line:
        # A circuit ranking (P5c): its places, best first, as feed rows.
        from . import feeds
        return feeds.circuit_rows(line)
    stage = line.source
    fmt = formats.get(stage.format)
    key = fmt.key if fmt else stage.format
    if key == 'battle_royale':
        from . import br_engine
        if line.place_from == 1 and line.place_to == (stage.advances or 0):
            return list(br_engine.advancing(stage))
        return list(br_engine.standings(stage))[line.place_from - 1:line.place_to]
    rows = list(rows if rows is not None else stage_engine.standings(stage))
    if key == 'swiss' and (stage.settings or {}).get('win_target'):
        qualified = [r for r in rows if r['status'] == 'qualified']
        rows = qualified + [r for r in rows if r not in qualified]
        return rows[line.place_from - 1:line.place_to]
    groups = stage.groups or 0
    if key in ('gsl', 'bracket_groups') or groups > 1:
        by_group = {}
        for r in rows:
            by_group.setdefault(r['group'] or 0, []).append(r)
        wanted = [line.group] if line.group is not None else sorted(by_group)
        out = []
        for place in range(line.place_from, line.place_to + 1):
            for g in wanted:
                if place - 1 < len(by_group.get(g, [])):
                    out.append(by_group[g][place - 1])
        return out
    ranked = [r for r in rows if r['rank']]
    return ranked[line.place_from - 1:line.place_to]


def sending(line):
    """The rows this line would send now, in the order the stage it feeds
    takes them: what the preview shows and what `fire` sends."""
    return _by_record(line, rows_for(line))


def _by_record(line, rows):
    """A stage seeded "by record across the whole stage" takes the rows
    reordered on record, across groups (copies, so each row keeps the rank it
    had inside its own group)."""
    from . import ranking
    if line.is_circuit_line or getattr(line.target, 'placement', None) != 'by_record' or not rows:
        return rows
    by_id = {r['registration_id']: r for r in rows}
    across = ranking.order([dict(r) for r in rows], ['goal_difference', 'goals_for', 'seed'])
    return [by_id[r['registration_id']] for r in across]


def _sent_row(row, carry_points):
    out = {'registration_id': row['registration_id'], 'name': row.get('name'),
           'handle': row.get('handle'), 'group': row.get('group'), 'rank': row.get('rank'),
           'points': row.get('points'),
           'carry': (row.get('points') or 0) if carry_points else (row.get('carry') or 0)}
    if row.get('source_registration_id'):
        out['source_registration_id'] = row['source_registration_id']
        out['reused'] = bool(row.get('reused'))
    if row.get('source_entrant'):
        # Sent from a circuit ranking (P5c): who it was there, `user:<id>` or
        # `team:<id>`, since a ranking has no registrations.
        out['source_entrant'] = row['source_entrant']
        out['reused'] = bool(row.get('reused'))
    return out


def fire(line, user, order=None):
    """Send this line's people: record who, and when. Returns the rows sent."""
    from django.utils import timezone
    from . import stage_engine
    rows = sending(line) if not order else rows_for(line)
    if order and line.is_circuit_line:
        # A ranking sends in its own order; there is no field to reorder.
        raise stage_engine.StageEngineError('NOT_IN_THIS_STAGE', 'order')
    if order:
        allowed = set(line.source.entrants or [])
        wanted = [int(i) for i in order]
        if any(i not in allowed for i in wanted):
            raise stage_engine.StageEngineError('NOT_IN_THIS_STAGE', 'order')
        if len(set(wanted)) != len(wanted):
            raise stage_engine.StageEngineError('DUPLICATE_IN_ORDER', 'order')
        carry = {r['registration_id']: r.get('carry') for r in rows}
        every = {r['registration_id']: r for r in stage_engine.standings(line.source)}
        rows = [dict(every[i], carry=carry.get(i) or every[i].get('carry') or 0)
                for i in wanted if i in every]
    from . import feeds
    if feeds.is_feed(line):
        # A feed (P2), or a circuit ranking (P5c): the qualifiers are
        # registered in the target, and the rows sent are those target
        # registrations.
        rows = feeds.qualify(line, rows)
    line.sent = [_sent_row(r, line.carry_points) for r in rows]
    line.fired_at = timezone.now()
    line.save(update_fields=['sent', 'fired_at'])
    from . import happenings
    happenings.sent(line)
    return line.sent


def ready(target):
    """Every line into `target` has fired, so it can be drawn."""
    feeding = incoming(target)
    # A feed with a declined place waiting for the organiser's pick (P2)
    # holds the draw: drawing now would leave that seat to nobody.
    return bool(feeding) and all(l.fired_at is not None and not l.waiting for l in feeding)


def sent_union(stage):
    """Everybody this stage's lines have sent, in line order: the stage's
    `advanced`, which older readers still use."""
    out, seen = [], set()
    for line in outgoing(stage):
        for row in (line.sent or []):
            if row['registration_id'] not in seen:
                seen.add(row['registration_id'])
                out.append(row)
    return out


def clear(stage):
    """Undo this stage's lines: an unadvanced or undrawn stage sends again.
    A feed that sent (P2) takes back the registrations it made."""
    from . import feeds
    for line in stage.outgoing.select_related('source', 'target').exclude(fired_at=None):
        if feeds.is_feed(line):
            feeds.unqualify(line)
    stage.outgoing.update(sent=[], fired_at=None)


def pull(line, user):
    """The organiser sends a line that waits to be pressed."""
    from . import stage_engine
    source = line.source
    if line.fired_at is not None:
        raise stage_engine.StageEngineError('ALREADY_ADVANCED')
    if line.is_circuit_line:
        # A ranking has no stage to finish: the organiser pulls it when the
        # season says so (P5c).
        fire(line, user)
        return stage_engine.fill_targets(user, [line])
    if not stage_engine.stage_finished(source):
        raise stage_engine.StageEngineError('STAGE_NOT_FINISHED', open=stage_engine.open_count(source))
    fire(line, user)
    return stage_engine.after_fire(source, user, [line])


# ---------------------------------------------------------------------------
# Final places
# ---------------------------------------------------------------------------

def placement_order(tournament):
    """The stages grouped for giving final places, best first.

    Deeper in the graph places first (the final before the playoff before the
    groups); among stages at the same depth, the one fed by better places
    first (the top half of a split league before the bottom half); stages with
    the same depth and the same best place are placed together, by rank, so
    the thirds of two parallel pools share one place.
    """
    ensure(tournament)
    stages = list(tournament.stages.all().order_by('order'))
    lines = list(lines_of(tournament))
    feeders = {s.id: [l.source_id for l in lines if l.target_id == s.id] for s in stages}
    depth = {}

    def depth_of(sid, seen=()):
        if sid in depth:
            return depth[sid]
        if sid in seen:
            return 0
        depth[sid] = 1 + max((depth_of(f, seen + (sid,)) for f in feeders[sid]), default=-1)
        return depth[sid]

    best = {s.id: min((l.place_from for l in lines if l.target_id == s.id), default=0)
            for s in stages}
    grouped = {}
    for s in stages:
        grouped.setdefault((-depth_of(s.id), best[s.id]), []).append(s)
    return [grouped[key] for key in sorted(grouped)]

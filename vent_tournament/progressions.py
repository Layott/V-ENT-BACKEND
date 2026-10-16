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

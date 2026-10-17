"""A tournament copied into a new draft (inbox 446).

CEO, 12 October 2026: "users should be able to duplicate their own
tournaments." An organiser running the same cup every month rebuilt it from an
empty wizard each time.

What a copy is: the tournament as it was SET UP, never what happened in it.

Copied: every column of the tournament (name with "(copy)", game, format,
dates, sizes, entry, prizes as typed, socials, options, artwork, the
organisation), the scoring table, the ruleset, the entry requirements, the prize
table, the stage plan and the lines between its stages, the lineup and squad
rules, and the sponsors.

Not copied: registrations, matches, results, disputes, payouts, ledger lines,
invite codes, staff (people agree to run a tournament, they are not enlisted
into a copy), production assets, a prize payout schedule (a run time belongs to
one tournament), the event link (a tournament sits in at most one event), and
any slug: the copy gets its own address from its own name, and the blueprint
it came from (a copy of an edition is not the next edition; P7).

The copy is a draft owned by whoever pressed Duplicate, so nothing about it is
public until they publish it.
"""
from django.db import transaction

from .models import Tournament

#: Columns that describe what happened to a tournament or who it is, never how
#: it was set up.
SKIP_COLUMNS = {
    'tournament_id', 'slug', 'interaction_count', 'is_draft', 'status',
    'completed_at', 'cancelled_at', 'cancelled_reason', 'check_in_closed_at',
    'deleted_at', 'deleted_by', 'deleted_reason', 'tournament_creator',
    'blueprint', 'blueprint_edition',
}

#: One-to-one settings rows, copied whole.
ONE_TO_ONE = ('league_rules', 'ruleset', 'squad_rules', 'lineup_rules')

#: Settings rows, one per entry, copied whole.
MANY = ('entry_requirements', 'prize_distributions')

#: Per model, the columns that record a state rather than a setting; they go
#: back to their defaults on the copy.
RESET = {
    'TournamentStage': {'status', 'advanced', 'completed_at', 'drawn_at',
                        'entrants', 'direct_entrants'},
    'Progression': {'sent', 'fired_at'},
    'LineupRules': {'locked_by_hand', 'reopened_by_hand'},
    'TournamentRuleset': {'updated_by'},
}

TITLE_LIMIT = Tournament._meta.get_field('tournament_title').max_length


def _copy_row(row, tournament):
    """A new row like `row`, pointing at `tournament`, unsaved."""
    model = type(row)
    reset = RESET.get(model.__name__, set())
    values = {}
    for field in model._meta.concrete_fields:
        if field.primary_key or field.name == 'tournament':
            continue
        if field.name in reset:
            continue
        values[field.attname] = getattr(row, field.attname)
    return model(tournament=tournament, **values)


def _copy_line(line, source, target):
    """A new Progression like `line`, between `source` and `target`, unsaved."""
    reset = RESET['Progression']
    values = {}
    for field in type(line)._meta.concrete_fields:
        if field.primary_key or field.name in ('source', 'target') or field.name in reset:
            continue
        values[field.attname] = getattr(line, field.attname)
    return type(line)(source=source, target=target, **values)


def copy_title(title):
    """`title` with " (copy)", within the column's length."""
    suffix = ' (copy)'
    base = (title or 'Tournament').strip()
    return base[:TITLE_LIMIT - len(suffix)] + suffix


@transaction.atomic
def duplicate(tournament, by):
    """A new draft set up like `tournament`, owned by `by`."""
    values = {}
    for field in Tournament._meta.concrete_fields:
        if field.name in SKIP_COLUMNS or field.primary_key:
            continue
        values[field.attname] = getattr(tournament, field.attname)
    values['tournament_title'] = copy_title(tournament.tournament_title)
    copy = Tournament(**values)
    copy.tournament_creator = by
    copy.is_draft = True
    copy.status = 'draft'
    copy.save()

    for name in ONE_TO_ONE:
        row = getattr(tournament, name, None) if _has(tournament, name) else None
        if row is not None:
            _copy_row(row, copy).save()

    for name in MANY:
        rows = [_copy_row(r, copy) for r in getattr(tournament, name).all()]
        if rows:
            type(rows[0]).objects.bulk_create(rows)

    # The lines between stages (P1b) follow their stages: each one is copied
    # between the copies of the two stages it joined.
    copied = {}
    for stage in tournament.stages.all().order_by('order', 'pk'):
        new_stage = _copy_row(stage, copy)
        new_stage.save()
        copied[stage.pk] = new_stage
    from .models import Progression
    for line in Progression.objects.filter(source__tournament=tournament).order_by('order', 'pk'):
        if line.source_id in copied and line.target_id in copied:
            _copy_line(line, copied[line.source_id], copied[line.target_id]).save()

    copy.sponsors.set(tournament.sponsors.all())
    return copy


def _has(tournament, name):
    """Whether a one-to-one row exists, without raising when it does not."""
    try:
        return getattr(tournament, name) is not None
    except Exception:                                            # noqa: BLE001
        return False

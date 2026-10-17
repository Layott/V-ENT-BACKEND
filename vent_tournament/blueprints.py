"""Blueprints: a tournament set up once, made again on a schedule (P7).

Design section 16, P7: "blueprints and recurring tournaments". After
Challengermode's blueprints (research C5): new tournaments are made from one
and "look, feel and play out exactly like the Blueprint", with the start time
set each time, recurring hourly to monthly.

Three things are decided here and nowhere else:

**When.** `start_of(bp, k)` is the k-th start: the first start's wall clock
time in the blueprint's own time zone, k days, weeks, fortnights or months on.
Each start is worked out from the first, never from the one before, so a
monthly blueprint anchored on the 31st falls on the 28th in February and goes
back to the 31st in March instead of drifting to the 28th for ever, and an
18:00 cup stays at 18:00 on the wall when the clocks change.

**What an edition is.** `make_edition` copies the blueprint's tournament as it
stands now with `duplicate()` (everything it was set up with, nothing that
happened in it), moves every date by the distance between that tournament's
start and the edition's, numbers it, and publishes it if the blueprint says to.
The owner is told either way.

**Which ones are due.** `run_due` is what the cron calls
(`manage.py make_due_editions`): an edition is made `lead_days` before its
start, so people can enter. A start already past is skipped rather than made
into a tournament that began with nobody in it, which is what three weeks of a
stopped cron would otherwise produce. A blueprint whose tournament is gone
pauses itself and says why. Two runs at once cannot make one edition twice:
the blueprint row is locked while an edition is made, and the edition number is
unique per blueprint underneath that.
"""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

STEP_DAYS = {'day': 1, 'week': 7, 'fortnight': 14}

#: The dates an edition moves with its start, per row duplicate() copies (the
#: tournament, each stage, the one-to-one rows by name). A date left out opens
#: or closes on the SOURCE's calendar: an EA FC edition two weeks on would have
#: its lineup window shut before it began. tests_blueprints checks every date
#: column of every copied row is moved here, reset by duplicate, or bookkeeping.
MOVED = {
    'tournament': ('start_date_and_time', 'end_date_and_time',
                   'registration_opens_at', 'registration_closes_at'),
    'stages': ('starts_at', 'ends_at'),
    'lineup_rules': ('opens_at', 'closes_at', 'changes_open_at', 'changes_close_at'),
}

#: The most editions one blueprint makes in one run. Only a lead longer than
#: several periods ever reaches more than one; this bounds a typo of 60 days on
#: a daily blueprint to a handful of tournaments per run, not sixty.
MAX_PER_RUN = 12


class BlueprintError(Exception):
    """An edition that cannot be made. `code` is translated by the screen."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _zone(bp):
    try:
        return ZoneInfo(bp.timezone or 'Africa/Lagos')
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('Africa/Lagos')


def start_of(bp, k):
    """The k-th scheduled start (k from 0), or None when nothing is scheduled."""
    if bp.every not in STEP_DAYS and bp.every != 'month':
        return None
    if bp.first_start is None:
        return None
    zone = _zone(bp)
    local = bp.first_start.astimezone(zone)
    if bp.every in STEP_DAYS:
        day = local.date() + timedelta(days=STEP_DAYS[bp.every] * k)
    else:
        index = local.month - 1 + k
        year, month = local.year + index // 12, index % 12 + 1
        day = date(year, month, min(local.day, calendar.monthrange(year, month)[1]))
    return datetime.combine(day, local.time()).replace(tzinfo=zone)


def next_after(bp, moment):
    """The first scheduled start strictly after `moment`, or None."""
    first = start_of(bp, 0)
    if first is None:
        return None
    if first > moment:
        return first
    if bp.every in STEP_DAYS:
        k = int((moment - first).total_seconds() // (STEP_DAYS[bp.every] * 86400)) - 1
    else:
        k = (moment.year - first.year) * 12 + moment.month - first.month - 1
    k = max(k, 0)
    while True:
        start = start_of(bp, k)
        if start > moment:
            return start
        k += 1


def finished(bp):
    return bp.ends_after is not None and bp.made >= bp.ends_after


def reschedule(bp, now=None):
    """Set `next_start` from the schedule as it stands, and save it.

    Called when a blueprint is made, edited or resumed. Nothing is scheduled
    for a blueprint run by hand, one that has made every edition it was asked
    for, or one with no first start.
    """
    now = now or timezone.now()
    bp.next_start = None if finished(bp) else next_after(bp, now)
    bp.save(update_fields=['next_start', 'updated_at'])
    return bp


def _source(bp):
    """The tournament an edition copies, or None when it is gone or not theirs."""
    source = bp.tournament
    if source is None or getattr(source, 'deleted_at', None):
        return None
    if source.tournament_creator_id != bp.owner_id:
        return None
    return source


def pause(bp, reason):
    """Stop a blueprint by itself, saying why with a code the screen translates."""
    from .models import TournamentBlueprint
    TournamentBlueprint.objects.filter(pk=bp.pk).update(
        active=False, paused_reason=reason, next_start=None, updated_at=timezone.now())
    bp.active, bp.paused_reason, bp.next_start = False, reason, None


def edition_title(bp, number):
    from .duplicate import TITLE_LIMIT
    suffix = ' #%d' % number
    return (bp.name or 'Tournament').strip()[:TITLE_LIMIT - len(suffix)] + suffix


def make_edition(bp, starts_at, *, scheduled=False):
    """The next edition of `bp`, starting at `starts_at`. Raises BlueprintError.

    `scheduled` is the cron's call (or Make one now with no date): it makes the
    edition at `next_start` and moves the schedule on. A one-off at a date the
    owner picked leaves the schedule as it was.
    """
    from .duplicate import duplicate
    from .models import TournamentBlueprint

    if starts_at is None:
        raise BlueprintError('NO_NEXT_EDITION')
    if starts_at <= timezone.now():
        raise BlueprintError('START_IN_PAST')
    source = _source(bp)
    if source is None:
        # Saved before the refusal is raised, so the pause outlives it.
        pause(bp, 'BLUEPRINT_SOURCE_GONE')
        raise BlueprintError('BLUEPRINT_SOURCE_GONE')

    with transaction.atomic():
        locked = TournamentBlueprint.objects.select_for_update().get(pk=bp.pk)
        if finished(locked):
            raise BlueprintError('BLUEPRINT_FINISHED')
        if scheduled and locked.next_start != starts_at:
            # Another run made this one while we waited for the lock.
            raise BlueprintError('EDITION_ALREADY_MADE')
        number = locked.next_number
        edition = duplicate(source, locked.owner, title=edition_title(locked, number))
        moved = starts_at - source.start_date_and_time
        fields = _move(edition, MOVED['tournament'], moved)
        edition.blueprint = locked
        edition.blueprint_edition = number
        fields += ['blueprint', 'blueprint_edition']
        if locked.publish:
            # The same pair the edit door writes when an organiser publishes.
            edition.is_draft = False
            edition.status = 'registration_open'
            fields += ['is_draft', 'status']
        edition.save(update_fields=fields)
        for stage in edition.stages.all():
            changed = _move(stage, MOVED['stages'], moved)
            if changed:
                stage.save(update_fields=changed)
        for name, columns in MOVED.items():
            if name in ('tournament', 'stages'):
                continue
            row = _one_to_one(edition, name)
            if row is not None:
                changed = _move(row, columns, moved)
                if changed:
                    row.save(update_fields=changed)

        locked.made += 1
        locked.next_number = number + 1
        if scheduled:
            locked.next_start = None if finished(locked) else next_after(locked, starts_at)
        elif finished(locked):
            locked.next_start = None
        locked.save(update_fields=['made', 'next_number', 'next_start', 'updated_at'])
        transaction.on_commit(lambda: _tell_owner(locked, edition))

    bp.refresh_from_db()
    return edition


def _move(row, columns, moved):
    """Move each date in `columns` that is set by `moved`; the names changed."""
    changed = []
    for name in columns:
        value = getattr(row, name)
        if value:
            setattr(row, name, value + moved)
            changed.append(name)
    return changed


def _one_to_one(tournament, name):
    """The one-to-one row `name` of `tournament`, or None when it has none."""
    from django.core.exceptions import ObjectDoesNotExist
    try:
        return getattr(tournament, name)
    except ObjectDoesNotExist:
        return None


def _tell_owner(bp, edition):
    from vent_auth.views_notifications import create_notification
    if edition.is_draft:
        title = '%s is ready for you to check' % edition.tournament_title
        body = ('Your blueprint %s made it as a draft. Open it, check the details and publish it '
                'so people can enter.' % bp.name)
    else:
        title = '%s is open for entries' % edition.tournament_title
        body = 'Your blueprint %s made it and published it.' % bp.name
    create_notification(bp.owner, 'tournament', title, body,
                        link='/tournaments/%s/manage' % edition.slug,
                        metadata={'tournament_id': edition.tournament_id,
                                  'blueprint': bp.token, 'edition': edition.blueprint_edition})


def due(now=None):
    """(blueprint, start) for every edition the cron would make now, in order."""
    from .models import TournamentBlueprint
    now = now or timezone.now()
    out = []
    for bp in (TournamentBlueprint.objects.filter(active=True, next_start__isnull=False)
               .exclude(every='none').select_related('tournament', 'owner')):
        start, made = bp.next_start, bp.made
        while start is not None and start <= now:
            start = next_after(bp, start)
        while (start is not None and start - timedelta(days=bp.lead_days) <= now
               and len([1 for b, _ in out if b.pk == bp.pk]) < MAX_PER_RUN
               and (bp.ends_after is None or made < bp.ends_after)):
            out.append((bp, start))
            made += 1
            start = next_after(bp, start)
    return out


def run_due(now=None):
    """Make every edition that is due. [(blueprint, edition or None, code or None)]."""
    from .models import TournamentBlueprint
    now = now or timezone.now()
    results = []
    for bp in (TournamentBlueprint.objects.filter(active=True, next_start__isnull=False)
               .exclude(every='none')):
        # Starts already past move the schedule on without making anything.
        start = bp.next_start
        while start is not None and start <= now:
            start = next_after(bp, start)
        if start != bp.next_start:
            bp.next_start = start
            bp.save(update_fields=['next_start', 'updated_at'])
        for _ in range(MAX_PER_RUN):
            if not bp.active or bp.next_start is None:
                break
            if bp.next_start - timedelta(days=bp.lead_days) > now:
                break
            try:
                edition = make_edition(bp, bp.next_start, scheduled=True)
            except BlueprintError as exc:
                results.append((bp, None, exc.code))
                break
            results.append((bp, edition, None))
    return results

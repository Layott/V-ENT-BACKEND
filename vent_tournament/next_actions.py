"""What needs doing on a tournament, and by whom (P4a, design section 12).

Two questions, answered from the models that already hold the facts:

* `for_viewer`: what this one person can do next, each with its deadline.
  "Per viewer next actions: one source for every button and countdown ("Check
  in by 14:00 WAT")." After Challengermode's `allowedActions`, which hands a
  client the actions a user may take, a translation key for the state and a
  timer for the first deadline (research, challengermode 2.5). Until this,
  the check-in strip, the bracket's "Your match" button and the qualifier's
  decline banner each worked out on their own whether somebody had something
  to do.
* `actions_list`: what the people running the tournament need to settle:
  "ties, disputes, stuck matches, qualifiers who declined, pulls waiting for a
  preview". Each item names the permission that settles it, so a helper who
  only records results sees the matches and not the draw.

Both answer with a translation key per row (`next.<action>`, `todo.<kind>`)
and instants rather than sentences: the screen words it in the reader's
language and prints the time in their zone, with the zone named.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

#: What a viewer can be asked to do, in the order rows with the same deadline
#: are listed.
ACTIONS = ('decline_place', 'check_in', 'check_in_match', 'confirm_score', 'report', 'play',
           'register')

#: A registration that still plays.
LIVE = ('pending', 'confirmed')

#: Matches a viewer may still have something to do in.
OPEN_MATCH = ('scheduled', 'in_progress', 'pending_opponent_confirm')

#: Statuses in which nobody registers, whatever the dates say. The join view
#: refuses these with STATE_CONFLICT.
CLOSED_STATUSES = ('live', 'completed', 'cancelled', 'registration_closed')


def _now(now):
    return now or timezone.now()


def registration_closed(tournament, now=None):
    """Why nobody can register now, or None.

    STATE_CONFLICT once the tournament is live, over, cancelled or closed by
    the organiser; REGISTRATION_CLOSED past the close date the organiser set.
    The close date was on every screen and enforced by nothing until P4a: the
    join view read the status alone, so "Registration closes Friday" took
    entries on Saturday. Opening is not here: an early access member may enter
    before it (`vent_billing.entitlements.registration_window_error`).
    """
    if tournament.is_draft or tournament.status in CLOSED_STATUSES:
        return 'STATE_CONFLICT'
    closes = tournament.registration_closes_at
    if closes is not None and _now(now) > closes:
        return 'REGISTRATION_CLOSED'
    return None


def _capacity_left(tournament):
    capacity = tournament.max_number_of_teams or tournament.player_size or 0
    if not capacity:
        return True
    return tournament.registrations.filter(status__in=LIVE).count() < capacity


def _entries_acted_by(user, tournament):
    """The places in this tournament `user` acts for (`acting_user`): their
    own, a club they own, a squad they captain. A member who does not act for
    the side is asked nothing: only the one who acts reports, confirms and
    checks in (`BracketMatch.participant_owned_by`)."""
    if user is None:
        return []
    rows = (tournament.registrations.filter(status__in=LIVE)
            .filter(Q(user_id=user.user_id) | Q(team__team_owner_id=user.user_id)
                    | Q(squad__members__user_id=user.user_id))
            .select_related('user', 'team', 'squad').distinct())
    out = []
    for reg in rows:
        actor = reg.acting_user
        if actor is not None and actor.user_id == user.user_id:
            out.append(reg)
    return out


def _row(action, deadline=None, opens_at=None, match=None, registration=None,
         needs_account=False):
    return {
        'action': action,
        'key': 'next.%s' % action,
        'deadline': deadline,
        'opens_at': opens_at,
        'match': match,
        'registration_id': registration.id if registration is not None else None,
        'needs_account': needs_account,
    }


def _register(user, tournament, now):
    from .access import plays_in, role_of
    if registration_closed(tournament, now) or not _capacity_left(tournament):
        return []
    if user is not None and (role_of(user, tournament) in ('organiser', 'admin')
                             or plays_in(user, tournament)):
        return []
    opens = tournament.registration_opens_at
    return [_row('register',
                 deadline=tournament.registration_closes_at or tournament.start_date_and_time,
                 opens_at=opens if opens and opens > now else None,
                 needs_account=user is None)]


def _check_in(entries, tournament, now):
    """Check-in before the draw. Once the bracket is drawn it is history:
    closing it is refused (BRACKET_EXISTS), and nobody is asked to check in to
    a draw that has already happened."""
    from . import options
    if tournament.status in ('live', 'completed', 'cancelled'):
        return []
    window = options.check_in_state(tournament, now)
    if window is None or window['closed'] or tournament.bracket_matches.exists():
        return []
    opens = window['opens_at']
    return [_row('check_in', deadline=window['closes_at'],
                 opens_at=opens if opens > now else None, registration=reg)
            for reg in entries if reg.checked_in_at is None]


def _decline(entries, now):
    from .feeds import PLAYED, _matches_of
    out = []
    for reg in entries:
        line = reg.qualified_via
        if line is None or reg.decline_until is None or now > reg.decline_until:
            continue
        if _matches_of(line, reg).filter(status__in=PLAYED).exists():
            continue
        out.append(_row('decline_place', deadline=reg.decline_until, registration=reg))
    return out


def _match_brief(match, opponent):
    from .match_shape import names
    return {'id': match.id, 'round_number': match.round_number, 'stage_id': match.stage_id,
            'opponent': names(opponent)[0]}


def _matches(user, entries, tournament, now):
    from .models import BracketMatch, MatchScore
    ids = {reg.id for reg in entries}
    if not ids:
        return []
    matches = (BracketMatch.objects
               .filter(tournament=tournament, status__in=OPEN_MATCH,
                       participant_1__isnull=False, participant_2__isnull=False)
               .filter(Q(participant_1_id__in=ids) | Q(participant_2_id__in=ids))
               .select_related('participant_1__user', 'participant_2__user')
               .order_by('round_number', 'match_number', 'id'))
    out = []
    for m in matches:
        slot = 1 if m.participant_1_id in ids else 2
        mine = m.participant_1 if slot == 1 else m.participant_2
        opponent = m.participant_2 if slot == 1 else m.participant_1
        brief = _match_brief(m, opponent)
        if m.status == 'pending_opponent_confirm':
            latest = (MatchScore.objects.filter(match=m, confirmed=False, superseded_by__isnull=True)
                      .order_by('-submitted_at').first())
            if latest is not None and latest.submitted_by_id != user.user_id:
                out.append(_row('confirm_score', match=brief, registration=mine))
            continue
        checked_in = getattr(m, 'checked_in_p%s_at' % slot) is not None
        if m.status == 'scheduled' and m.check_in_deadline and not checked_in:
            if m.check_in_deadline > now:
                out.append(_row('check_in_match', deadline=m.check_in_deadline, match=brief,
                                registration=mine))
            # Past the deadline it is the forfeit's or the organiser's to settle.
            continue
        if m.status == 'scheduled' and m.scheduled_at and m.scheduled_at > now:
            out.append(_row('play', deadline=m.scheduled_at, match=brief, registration=mine))
            continue
        if tournament.score_confirmation_mode == 'organizer_only':
            continue
        out.append(_row('report', match=brief, registration=mine))
    return out


def for_viewer(user, tournament, now=None):
    """Everything `user` (None when signed out) can do next here, soonest
    deadline first and rows with no deadline last. Each row: `action`, `key`,
    `deadline`, `opens_at`, `match`, `registration_id`, `needs_account`."""
    now = _now(now)
    if tournament.is_draft:
        return []
    entries = _entries_acted_by(user, tournament)
    rows = (_decline(entries, now) + _check_in(entries, tournament, now)
            + (_matches(user, entries, tournament, now) if user is not None else [])
            + _register(user, tournament, now))
    # A stable sort keeps matches in round order within the same deadline.
    rows.sort(key=lambda r: (r['deadline'] is None,
                             r['deadline'] if r['deadline'] is not None else now,
                             ACTIONS.index(r['action'])))
    return rows


# ---------------------------------------------------------------------------
# What the people running it need to settle
# ---------------------------------------------------------------------------

#: How long a match can sit before somebody running the tournament is told.
UNCONFIRMED_AFTER = timedelta(hours=1)
LATE_AFTER = timedelta(hours=2)

#: What the list can hold, in the order it is listed.
KINDS = ('dispute', 'no_show', 'unconfirmed', 'late', 'tie', 'declined', 'advance', 'pull',
         'close_check_in')

#: The permission (`access.PERMISSIONS`, or 'manage') that settles each kind. A
#: helper who only records results is shown the matches and never the draw.
KIND_PERMISSION = {
    'dispute': 'report', 'no_show': 'report', 'unconfirmed': 'report', 'late': 'report',
    'tie': 'place', 'advance': 'place', 'pull': 'place',
    'declined': 'manage', 'close_check_in': 'manage',
}

#: Disputes nobody has closed.
OPEN_DISPUTE = ('open', 'under_review')


def _stage_view(stage):
    return {'id': stage.id, 'label': stage.label} if stage is not None else None


def _match_view(match):
    from .match_shape import names
    if match is None:
        return None
    return {'id': match.id, 'round_number': match.round_number, 'stage_id': match.stage_id,
            'sides': [names(match.participant_1)[0], names(match.participant_2)[0]]}


def _item(kind, since=None, stage=None, match=None, detail=None):
    return {
        'kind': kind,
        'key': 'todo.%s' % kind,
        'permission': KIND_PERMISSION[kind],
        'since': since,
        'stage': _stage_view(stage),
        'match': _match_view(match),
        'detail': detail or {},
    }


def _stuck(tournament, now):
    """Disputes, and the matches nobody is moving: the results desk's list."""
    from .models import BracketMatch, MatchScore, TournamentDispute
    out = []
    disputes = (TournamentDispute.objects.filter(tournament=tournament, status__in=OPEN_DISPUTE)
                .select_related('raised_by', 'match__stage', 'match__participant_1__user',
                                'match__participant_2__user'))
    for d in disputes:
        out.append(_item('dispute', since=d.created_at, match=d.match,
                         stage=d.match.stage if d.match is not None else None,
                         detail={'raised_by': d.raised_by.username}))
    matches = (BracketMatch.objects
               .filter(tournament=tournament, status__in=OPEN_MATCH,
                       participant_1__isnull=False, participant_2__isnull=False)
               .select_related('stage', 'participant_1__user', 'participant_2__user')
               .order_by('round_number', 'match_number', 'id'))
    for m in matches:
        no_show = (m.status == 'scheduled' and m.check_in_deadline is not None
                   and m.check_in_deadline <= now
                   and m.checked_in_p1_at is None and m.checked_in_p2_at is None)
        if no_show:
            # settle_no_show leaves "neither checked in" for a person.
            out.append(_item('no_show', since=m.check_in_deadline, match=m, stage=m.stage))
        elif m.status == 'pending_opponent_confirm':
            latest = (MatchScore.objects.filter(match=m, confirmed=False, superseded_by__isnull=True)
                      .order_by('-submitted_at').first())
            if latest is not None and now - latest.submitted_at > UNCONFIRMED_AFTER:
                out.append(_item('unconfirmed', since=latest.submitted_at, match=m, stage=m.stage))
        elif (m.status in ('scheduled', 'in_progress') and m.scheduled_at is not None
              and now - m.scheduled_at > LATE_AFTER):
            out.append(_item('late', since=m.scheduled_at, match=m, stage=m.stage))
    return out


def _cuts(first, last, line):
    """Whether a run of level places (`first` to `last`) straddles an edge of
    the places a line sends: its last place, or the one before its first."""
    return first <= line.place_to < last or first < line.place_from <= last


def _ties(stages):
    """Level places that decide who a line sends. A tie wholly inside the
    places a line sends, or wholly outside them, changes nobody's fate."""
    from . import progressions, stage_engine
    out = []
    for stage in stages:
        lines = [l for l in progressions.outgoing(stage) if l.fired_at is None]
        if not lines:
            continue
        by_group = {}
        for row in stage_engine.standings(stage):
            if row.get('rank'):
                by_group.setdefault(row.get('group') or 0, []).append(row)
        for group, rows in sorted(by_group.items()):
            runs, run = [], []
            for row in rows:
                if run and row['rank'] == run[0]['rank'] and row.get('tied'):
                    run.append(row)
                    continue
                runs.append(run)
                run = [row] if row.get('tied') else []
            runs.append(run)
            for run in runs:
                if len(run) < 2:
                    continue
                first = run[0]['rank']
                last = first + len(run) - 1
                if any((line.group is None or line.group == group) and _cuts(first, last, line)
                       for line in lines):
                    out.append(_item('tie', stage=stage, detail={
                        'group': group or None, 'places': [first, last],
                        'names': [r['name'] for r in run]}))
    return out


def _finished_stages(tournament):
    """Stages whose last match is decided and which still have a line that
    has not sent anybody: this tournament's own, and those of a qualifier
    feeding it. The stage's own status is no test: the last stage of a
    tournament completes itself with its feeds still waiting to go."""
    from . import stage_engine
    from .models import Progression, TournamentStage
    feeding = list(Progression.objects.filter(target__tournament=tournament, source__isnull=False)
                   .values_list('source_id', flat=True))
    stages = (TournamentStage.objects.filter(Q(tournament=tournament) | Q(id__in=feeding),
                                             outgoing__fired_at__isnull=True).distinct())
    return {s.id: s for s in stages if stage_engine.stage_finished(s)}


def _placing(tournament):
    """Ties, and the lines waiting for a press: the draw's list."""
    from . import progressions
    from .models import Progression
    progressions.ensure(tournament)
    done = _finished_stages(tournament)
    out = _ties([s for s in done.values() if s.tournament_id == tournament.pk])
    lines = (Progression.objects
             .filter(Q(source__tournament=tournament) | Q(target__tournament=tournament),
                     source__in=list(done), fired_at__isnull=True)
             .select_related('source').order_by('order', 'id'))
    for line in lines:
        if line.fire == 'after_preview':
            out.append(_item('advance', stage=line.source, detail={'line_id': line.id}))
        elif line.fire == 'manual':
            out.append(_item('pull', stage=line.source, detail={'line_id': line.id}))
    return out


def _declined(tournament):
    """Places a qualifier left that the organiser fills."""
    from .models import Progression
    lines = (Progression.objects.filter(target__tournament=tournament, source__isnull=False)
             .select_related('target').order_by('order', 'id'))
    return [_item('declined', stage=line.target, detail={'count': len(line.waiting)})
            for line in lines if line.waiting]


def _close_check_in(tournament, now):
    """Check-in the clock has ended and nobody has closed, before any draw."""
    from . import options
    if tournament.status == 'cancelled' or tournament.bracket_matches.exists():
        return []
    window = options.check_in_state(tournament, now)
    if window is None or window['closed_by_organiser'] or now <= window['closes_at']:
        return []
    return [_item('close_check_in', since=window['closes_at'])]


def actions_list(tournament, user, now=None):
    """What `user` can settle on this tournament right now, by kind and oldest
    first. Each: `kind`, `key`, `permission`, `since`, `stage`, `match`,
    `detail`. Only the kinds their permissions reach: a viewer who runs
    nothing gets an empty list (the door refuses them before this is asked)."""
    from .access import may_manage, permissions_of
    now = _now(now)
    if tournament.is_draft:
        return []
    held = permissions_of(user, tournament)
    items = []
    if 'report' in held:
        items += _stuck(tournament, now)
    if 'place' in held:
        items += _placing(tournament)
    if may_manage(user, tournament):
        items += _declined(tournament) + _close_check_in(tournament, now)
    items.sort(key=lambda i: (KINDS.index(i['kind']), i['since'] is None,
                              i['since'] if i['since'] is not None else now))
    return items

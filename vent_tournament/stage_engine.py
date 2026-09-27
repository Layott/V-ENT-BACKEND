"""Running a tournament made of stages: draw one, play it, carry people on.

`stages.py` checks a plan. This runs it. A stage used to be a line in a plan
with nowhere to put its matches, and advancing trusted whatever standings the
browser sent, which meant the page that happened to be open decided who went
through. Now:

  * **Drawing** a stage takes its entrants (the registrations for the first
    stage; the people the previous stage sent on, placed by the stage's rule,
    for every later one; plus anybody seeded straight into it) and draws them
    with the same generators a single-format tournament uses.
  * **Standings** are worked out here, on the server, from the stage's own
    matches, for every format: a table for groups and leagues, a record and
    Buchholz for Swiss, finishing places for a knockout and for GSL groups.
  * **Advancing** reads those standings. The organiser still presses it, sees
    the list first, may reorder it, and an open dispute still stops it.

Linking follows toornament's placement and start.gg's progressions: so many
per group go through, group winners meet runners-up of another group (1A v
2B), and an invited side can start in a later stage.
"""
from datetime import timedelta
import random

from django.db import transaction
from django.utils import timezone

from . import formats, stage_settings


class StageEngineError(ValueError):
    def __init__(self, code, field=None, **extra):
        super().__init__(code)
        self.code = code
        self.field = field
        self.extra = extra


TERMINAL = ('completed', 'bye', 'walkover_p1', 'walkover_p2', 'cancelled')


# ---------------------------------------------------------------------------
# Who enters a stage
# ---------------------------------------------------------------------------

def _stages(tournament):
    return list(tournament.stages.all().order_by('order'))


def previous_stage(stage):
    return stage.tournament.stages.filter(order__lt=stage.order).order_by('-order').first()


def next_stage(stage):
    return stage.tournament.stages.filter(order__gt=stage.order).order_by('order').first()


def _later_direct(stage):
    """Registration ids seeded straight into a stage after this one."""
    ids = set()
    for later in stage.tournament.stages.filter(order__gt=stage.order):
        ids.update(int(i) for i in (later.direct_entrants or []))
    return ids


def entrants_for(stage, seed_strategy='registration', manual_order=None):
    """Who plays in this stage, best seed first."""
    from .models import TournamentRegistration
    from .services import bracket

    tournament = stage.tournament
    direct = [int(i) for i in (stage.direct_entrants or [])]
    prev = previous_stage(stage)

    if prev is None:
        skip = _later_direct(stage)
        regs = [r for r in bracket.confirmed_registrations(tournament) if r.id not in skip]
        ordered = bracket.seed_registrations(regs, seed_strategy, manual_order)
    else:
        if prev.status != 'complete':
            raise StageEngineError('PREVIOUS_STAGE_NOT_FINISHED')
        carried = [int(row['registration_id']) for row in (prev.advanced or [])
                   if isinstance(row, dict) and row.get('registration_id')]
        by_id = {r.id: r for r in TournamentRegistration.objects.filter(
            id__in=carried + direct).select_related('user', 'team')}
        ordered = [by_id[i] for i in carried if i in by_id]
        if stage.placement == 'random':
            random.shuffle(ordered)
        elif stage.placement == 'manual' and manual_order:
            wanted = [int(i) for i in manual_order]
            ordered = [by_id[i] for i in wanted if i in by_id and i in carried] + \
                      [r for r in ordered if r.id not in wanted]
        # 'cross' and 'by_record' are already the order `advancing` wrote.

    if direct:
        from .models import TournamentRegistration as TR
        by_id = {r.id: r for r in TR.objects.filter(id__in=direct)}
        # Seeded straight in means seeded at the top: that is what an invited
        # side or last year's champion is for.
        front = [by_id[i] for i in direct if i in by_id
                 and by_id[i].status == 'confirmed']
        ordered = front + [r for r in ordered if r.id not in set(direct)]
    return ordered


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def draw(stage, user, seed_strategy='registration', manual_order=None):
    """Draw one stage. Runs inside transaction.atomic()."""
    from .models import BracketGeneration
    from .services import bracket

    if stage.drawn_at is not None or stage.matches.exists():
        raise StageEngineError('STAGE_ALREADY_DRAWN')
    fmt = formats.get(stage.format)
    if fmt is None:
        raise StageEngineError('UNKNOWN_FORMAT', 'format')
    key = fmt.key
    if key not in bracket.DRAWABLE:
        raise StageEngineError('FORMAT_HAS_NO_BRACKET', 'format')

    ordered = entrants_for(stage, seed_strategy, manual_order)
    if len(ordered) < fmt.min_participants:
        raise StageEngineError('NOT_ENOUGH_ENTRANTS', need=fmt.min_participants,
                               have=len(ordered))

    settings = stage.settings or stage_settings.clean(key, {})
    try:
        summary = bracket.draw_into(stage.tournament, stage, key, ordered, settings,
                                    groups=stage.groups)
    except bracket.BracketError as exc:
        raise StageEngineError(exc.code.upper())

    stage.entrants = [r.id for r in ordered]
    stage.drawn_at = timezone.now()
    stage.status = 'running'
    stage.settings = settings
    stage.save(update_fields=['entrants', 'drawn_at', 'status', 'settings'])

    for match in stage.matches.filter(participant_1__isnull=False,
                                      participant_2__isnull=False,
                                      status='scheduled'):
        arm_check_in(match)

    tournament = stage.tournament
    if tournament.status != 'live':
        tournament.status = 'live'
        tournament.save(update_fields=['status'])

    BracketGeneration.objects.create(
        tournament=tournament, generated_by=user,
        seed_strategy=seed_strategy if seed_strategy in ('random', 'ranked', 'manual_order', 'registration') else 'random',
        seed_payload={'registration_ids': stage.entrants, 'stage_id': stage.id},
        match_count=summary['matches_created'], rounds_count=summary['rounds_count'],
        notes='stage=%s format=%s' % (stage.order, key),
    )
    summary.update({'stage_id': stage.id, 'format': key, 'entrants': len(ordered)})
    return summary


# ---------------------------------------------------------------------------
# Standings, per format
# ---------------------------------------------------------------------------

def _name(reg):
    from .match_shape import names
    return names(reg)[0] or ''


def _blank(reg, group=None):
    from .match_shape import names
    return {
        'registration_id': reg.id, 'name': _name(reg), 'handle': names(reg)[1],
        'group': group,
        'seed': reg.seed, 'played': 0, 'wins': 0, 'draws': 0, 'losses': 0,
        'goals_for': 0, 'goals_against': 0, 'goal_difference': 0, 'points': 0,
        'buchholz': 0, 'rank': None, 'status': 'playing', 'decided_by': None,
    }


def _points(stage):
    rules = stage.rules if isinstance(stage.rules, dict) else {}
    return (int(rules.get('points_win', 3)), int(rules.get('points_draw', 1)),
            int(rules.get('points_loss', 0)))


def _tally(rows, match, pts):
    a, b = rows.get(match.participant_1_id), rows.get(match.participant_2_id)
    if a is None or b is None:
        return
    win, draw_pts, loss = pts
    for row, gf, ga in ((a, match.score_p1, match.score_p2), (b, match.score_p2, match.score_p1)):
        row['played'] += 1
        row['goals_for'] += gf
        row['goals_against'] += ga
    if match.winner_id is None:
        a['draws'] += 1
        b['draws'] += 1
        a['points'] += draw_pts
        b['points'] += draw_pts
    else:
        w, l = (a, b) if match.winner_id == match.participant_1_id else (b, a)
        w['wins'] += 1
        l['losses'] += 1
        w['points'] += win
        l['points'] += loss


def _played(stage):
    return [m for m in stage.matches.select_related('participant_1', 'participant_2')
            if m.status in ('completed', 'walkover_p1', 'walkover_p2')
            and m.participant_1_id and m.participant_2_id]


def _head_to_head_points(tied_ids, matches, pts):
    mini = {i: 0 for i in tied_ids}
    for m in matches:
        if m.participant_1_id in mini and m.participant_2_id in mini:
            if m.winner_id is None:
                mini[m.participant_1_id] += pts[1]
                mini[m.participant_2_id] += pts[1]
            else:
                mini[m.winner_id] += pts[0]
    return mini


def _rank_table(rows, matches, pts):
    """Points, then the result between the tied sides, then goal difference,
    goals scored, wins, then seed. Each row says which one separated it."""
    for row in rows:
        row['goal_difference'] = row['goals_for'] - row['goals_against']
    by_points = {}
    for row in rows:
        by_points.setdefault(row['points'], []).append(row)
    h2h = {}
    for tied in by_points.values():
        if len(tied) > 1:
            h2h.update(_head_to_head_points([r['registration_id'] for r in tied], matches, pts))
    key = lambda r: (-r['points'], -h2h.get(r['registration_id'], 0),
                     -r['goal_difference'], -r['goals_for'], -r['wins'],
                     r['seed'] if r['seed'] is not None else 10 ** 6, r['name'].lower())
    ranked = sorted(rows, key=key)
    names = ['points', 'head_to_head', 'goal_difference', 'goals_for', 'wins', 'seed']
    for i, row in enumerate(ranked):
        row['rank'] = i + 1
        if i:
            prev = ranked[i - 1]
            pk, rk = key(prev), key(row)
            for idx, name in enumerate(names):
                if pk[idx] != rk[idx]:
                    row['decided_by'] = name
                    break
    return ranked


def _table_standings(stage):
    from .models import TournamentRegistration

    pts = _points(stage)
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    groups = {}
    for m in stage.matches.all():
        g = m.group_number or 0
        for pid in (m.participant_1_id, m.participant_2_id):
            if pid in regs:
                groups.setdefault(g, {}).setdefault(pid, _blank(regs[pid], m.group_number))
    played = _played(stage)
    out = []
    for g in sorted(groups):
        rows = groups[g]
        group_matches = [m for m in played if (m.group_number or 0) == g]
        for m in group_matches:
            _tally(rows, m, pts)
        out.extend(_rank_table(list(rows.values()), group_matches, pts))
    return out


def _swiss_standings(stage):
    from .models import TournamentRegistration

    settings = stage.settings or {}
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    rows = {i: _blank(r) for i, r in regs.items()}
    opponents = {i: [] for i in regs}
    for m in stage.matches.all():
        if m.status == 'bye' and m.winner_id in rows:
            rows[m.winner_id]['wins'] += 1
            rows[m.winner_id]['points'] += 3
            continue
        if m.status not in ('completed', 'walkover_p1', 'walkover_p2'):
            continue
        a, b = m.participant_1_id, m.participant_2_id
        if a in opponents and b in opponents:
            opponents[a].append(b)
            opponents[b].append(a)
        _tally(rows, m, (3, 1, 0))
    for i, row in rows.items():
        row['buchholz'] = sum(rows[o]['wins'] for o in opponents[i] if o in rows)
        row['goal_difference'] = row['goals_for'] - row['goals_against']
        target, limit = settings.get('win_target') or 0, settings.get('loss_limit') or 0
        if target and row['wins'] >= target:
            row['status'] = 'qualified'
        elif limit and row['losses'] >= limit:
            row['status'] = 'eliminated'
    key = lambda r: (-r['points'], -r['buchholz'], -r['goal_difference'],
                     -r['goals_for'], r['seed'] if r['seed'] is not None else 10 ** 6,
                     r['name'].lower())
    ranked = sorted(rows.values(), key=key)
    names = ['points', 'buchholz', 'goal_difference', 'goals_for', 'seed']
    for i, row in enumerate(ranked):
        row['rank'] = i + 1
        if i:
            pk, rk = key(ranked[i - 1]), key(row)
            for idx, name in enumerate(names):
                if pk[idx] != rk[idx]:
                    row['decided_by'] = name
                    break
    return ranked


def _loser(m):
    if m.winner_id is None:
        return None
    return m.participant_2_id if m.winner_id == m.participant_1_id else m.participant_1_id


def _knockout_standings(stage):
    """Finishing places: the final decides first and second, a third-place
    match third and fourth, everybody else by how late they went out."""
    from .models import TournamentRegistration

    matches = list(stage.matches.all())
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    place = {}
    final = next((m for m in matches if m.is_final), None)
    done = final is not None and final.status in TERMINAL and final.winner_id
    if done:
        place[final.winner_id] = 1
        runner = _loser(final)
        if runner is None and final.bracket_side == 'grand_final':
            gf1 = next((m for m in matches if m.bracket_side == 'grand_final'
                        and not m.is_final), None)
            runner = _loser(gf1) if gf1 else None
        if runner:
            place[runner] = 2
    third = next((m for m in matches if final and m is not final and not m.is_final
                  and m.round_number == final.round_number
                  and m.bracket_side == final.bracket_side and m.status == 'completed'), None)
    nxt = 3
    if third is not None:
        if third.winner_id:
            place.setdefault(third.winner_id, 3)
        if _loser(third):
            place.setdefault(_loser(third), 4)
        nxt = 5
    for m in sorted((m for m in matches if m.status == 'completed' and m is not third),
                    key=lambda m: (-m.round_number, m.bracket_side, m.match_number)):
        lost = _loser(m)
        # In double elimination a winners-bracket loss is not elimination.
        if lost and lost not in place and not (m.bracket_side == 'winners'
                                              and m.loser_to_match_id):
            place[lost] = nxt
            nxt += 1
    rows = []
    for rid, reg in regs.items():
        row = _blank(reg)
        row['rank'] = place.get(rid)
        row['status'] = 'placed' if rid in place else 'playing'
        rows.append(row)
    for m in matches:
        if m.status == 'completed' and m.participant_1_id in regs and m.participant_2_id in regs:
            _tally({r['registration_id']: r for r in rows}, m, (1, 0, 0))
    rows.sort(key=lambda r: (r['rank'] is None, r['rank'] or 0, r['seed'] or 10 ** 6))
    return rows


def _gsl_standings(stage):
    from .models import TournamentRegistration

    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    out = []
    for g in sorted({m.group_number for m in stage.matches.all() if m.group_number}):
        ms = {(m.round_number, m.match_number): m for m in stage.matches.filter(group_number=g)}
        members = {pid for m in ms.values() for pid in (m.participant_1_id, m.participant_2_id) if pid}
        place = {}
        wm, em, dec = ms.get((2, 1)), ms.get((2, 2)), ms.get((3, 1))
        if wm and wm.winner_id:
            place[wm.winner_id] = 1
        if dec and dec.winner_id:
            place[dec.winner_id] = 2
            if _loser(dec):
                place[_loser(dec)] = 3
        if em and em.status == 'completed' and _loser(em):
            place[_loser(em)] = 4
        rows = []
        for pid in members:
            row = _blank(regs[pid], g) if pid in regs else None
            if row is None:
                continue
            row['rank'] = place.get(pid)
            row['status'] = ('qualified' if place.get(pid) in (1, 2)
                             else 'eliminated' if place.get(pid) in (3, 4) else 'playing')
            rows.append(row)
        rows.sort(key=lambda r: (r['rank'] is None, r['rank'] or 0, r['seed'] or 10 ** 6))
        out.extend(rows)
    return out


def standings(stage):
    """Rows for this stage, whatever its format, ranked."""
    fmt = formats.get(stage.format)
    key = fmt.key if fmt else stage.format
    if stage.drawn_at is None:
        return []
    if key == 'swiss':
        return _swiss_standings(stage)
    if key == 'gsl':
        return _gsl_standings(stage)
    if key in ('single_elimination', 'double_elimination'):
        return _knockout_standings(stage)
    return _table_standings(stage)


def stage_finished(stage):
    """Every match in the stage is decided, and a Swiss has run its rounds."""
    if stage.drawn_at is None:
        return False
    if stage.matches.exclude(status__in=TERMINAL).exists():
        return False
    if stage.format == 'swiss':
        return swiss_finished(stage)
    return True


# ---------------------------------------------------------------------------
# Advancing
# ---------------------------------------------------------------------------

def advancing(stage):
    """Who goes through, in the order the next stage is seeded.

    Groups: rank-major across groups, groups in order (1A, 1B, 1C, 1D, 2A, 2B,
    ...). Seeded into a knockout that way, standard seeding puts every group
    winner against a runner-up from a different group, which is the cross.
    """
    rows = standings(stage)
    count = stage.advances or 0
    if count <= 0:
        return []
    fmt = formats.get(stage.format)
    key = fmt.key if fmt else stage.format

    if key == 'swiss' and (stage.settings or {}).get('win_target'):
        chosen = [r for r in rows if r['status'] == 'qualified'][:count]
        if len(chosen) < count:
            chosen += [r for r in rows if r not in chosen][:count - len(chosen)]
    elif key == 'gsl' or (stage.groups or 0) > 1:
        per = 2 if key == 'gsl' else count
        by_group = {}
        for r in rows:
            by_group.setdefault(r['group'] or 0, []).append(r)
        chosen = []
        for rank in range(per):
            for g in sorted(by_group):
                if rank < len(by_group[g]):
                    chosen.append(by_group[g][rank])
    else:
        chosen = [r for r in rows if r['rank']][:count]

    nxt = next_stage(stage)
    if nxt is not None and nxt.placement == 'by_record':
        chosen.sort(key=lambda r: (-r['points'], -r['goal_difference'], -r['goals_for'],
                                   r['seed'] or 10 ** 6))
    return chosen


def advance(stage, user, order=None, ignore_disputes=False, draw_next=True):
    """Close `stage`, record who goes through, and draw the next one."""
    tournament = stage.tournament
    if stage.status == 'complete':
        raise StageEngineError('ALREADY_ADVANCED')
    nxt = next_stage(stage)
    if nxt is None:
        raise StageEngineError('LAST_STAGE')
    if not stage_finished(stage):
        raise StageEngineError('STAGE_NOT_FINISHED',
                               open=stage.matches.exclude(status__in=TERMINAL).count())
    open_disputes = tournament.disputes.filter(
        status__in=('open', 'under_review'), match__stage=stage).count()
    if open_disputes and not ignore_disputes:
        raise StageEngineError('DISPUTES_OPEN', count=open_disputes)

    chosen = advancing(stage)
    if order:
        # The organiser reordering or swapping: allowed among this stage's own
        # entrants only, and the count stays what the plan said.
        allowed = set(stage.entrants or [])
        wanted = [int(i) for i in order]
        if any(i not in allowed for i in wanted):
            raise StageEngineError('NOT_IN_THIS_STAGE', 'order')
        if len(set(wanted)) != len(wanted):
            raise StageEngineError('DUPLICATE_IN_ORDER', 'order')
        rows = {r['registration_id']: r for r in standings(stage)}
        chosen = [rows[i] for i in wanted if i in rows]
    if not chosen:
        raise StageEngineError('NOBODY_ADVANCES')

    stage.advanced = [{'registration_id': r['registration_id'], 'name': r['name'],
                       'handle': r.get('handle'),
                       'group': r['group'], 'rank': r['rank'], 'points': r['points']}
                      for r in chosen]
    stage.status = 'complete'
    stage.completed_at = timezone.now()
    stage.save(update_fields=['advanced', 'status', 'completed_at'])

    summary = None
    if draw_next:
        summary = draw(nxt, user)
    return {'advanced': stage.advanced, 'next_stage_id': nxt.id, 'draw': summary}


# ---------------------------------------------------------------------------
# Swiss rounds
# ---------------------------------------------------------------------------

def _swiss_active(stage, rows):
    settings = stage.settings or {}
    target, limit = settings.get('win_target') or 0, settings.get('loss_limit') or 0
    return [r for r in rows if not ((target and r['wins'] >= target)
                                    or (limit and r['losses'] >= limit))]


def swiss_finished(stage):
    from .services import bracket

    if stage.matches.exclude(status__in=TERMINAL).exists():
        return False
    last_round = max((m.round_number for m in stage.matches.all()), default=0)
    total = bracket.swiss_rounds_for(len(stage.entrants or []), stage.settings)
    if last_round >= total:
        return True
    return len(_swiss_active(stage, _swiss_standings(stage))) < 2


def next_swiss_round(stage):
    """Pair and create the next round, when the current one is complete."""
    from .models import TournamentRegistration
    from .services import bracket

    if swiss_finished(stage):
        return None
    if stage.matches.exclude(status__in=TERMINAL).exists():
        return None
    rows = _swiss_standings(stage)
    active = _swiss_active(stage, rows)
    regs = {r.id: r for r in TournamentRegistration.objects.filter(
        id__in=[r['registration_id'] for r in active])}
    players = [regs[r['registration_id']] for r in active if r['registration_id'] in regs]
    record = {r['registration_id']: (r['wins'], r['losses']) for r in rows}
    played = set()
    had_bye = set()
    for m in stage.matches.all():
        if m.status == 'bye' and m.winner_id:
            had_bye.add(m.winner_id)
        elif m.participant_1_id and m.participant_2_id:
            played.add(frozenset((m.participant_1_id, m.participant_2_id)))
    pairs, bye = bracket.pair_swiss(players, record, played, had_bye)
    round_number = max((m.round_number for m in stage.matches.all()), default=0) + 1
    total = bracket.swiss_rounds_for(len(stage.entrants or []), stage.settings)
    from .services import advance as adv
    with adv.suspend_advance():
        created = bracket._swiss_round(stage.tournament, stage, stage.settings, round_number,
                                       pairs, bye, total)
    for m in stage.matches.filter(round_number=round_number, status='scheduled'):
        arm_check_in(m)
    return {'round_number': round_number, 'matches_created': created}


def on_match_resolved(match):
    stage = match.stage
    if stage is None:
        return
    if stage.format == 'swiss':
        next_swiss_round(stage)


# ---------------------------------------------------------------------------
# Match day: check-in and no-shows
# ---------------------------------------------------------------------------

def schedule(match):
    """Give a next-round match its start time: the break after the later of
    its two sides' last matches.

    CEO, 27 September 2026, on `match_interval_minutes` ("Minutes between
    rounds"), which was saved and read by nothing: players should see the
    break they get. The break counts from the end of each player's OWN last
    match rather than from the end of the whole round, so a quick match does
    not wait on a slow one. Never earlier than now, so a result recorded late
    does not open a check-in window that has already closed, and never
    earlier than the stage's own start.

    Only fills an empty time: a time the organiser set by hand is theirs, and
    a first-round match (nobody has played yet) keeps the stage's start.
    """
    from django.db.models import Max, Q
    from . import options as tournament_options
    if match.scheduled_at or match.status != 'scheduled':
        return None
    sides = [s for s in (match.participant_1_id, match.participant_2_id) if s]
    if len(sides) != 2:
        return None
    last = (type(match).objects
            .filter(tournament_id=match.tournament_id, completed_at__isnull=False,
                    status__in=('completed', 'bye'))
            .filter(Q(participant_1_id__in=sides) | Q(participant_2_id__in=sides))
            .exclude(pk=match.pk)
            .aggregate(at=Max('completed_at'))['at'])
    if last is None:
        return None
    minutes = tournament_options.clean(match.tournament.options)['match_interval_minutes']
    start = max(last + timedelta(minutes=minutes), timezone.now())
    # A stage with a start of its own (a playoff on Sunday) waits for it. The
    # tournament's start is not a floor: matches are already being played.
    if match.stage_id and match.stage.starts_at and match.stage.starts_at > start:
        start = match.stage.starts_at
    start = start.replace(second=0, microsecond=0) + timedelta(
        minutes=1 if start.second or start.microsecond else 0)
    match.scheduled_at = start
    type(match).objects.filter(pk=match.pk).update(scheduled_at=start)
    return start


def arm_check_in(match):
    """Fix the check-in deadline once both sides are known."""
    schedule(match)
    settings = stage_settings.of_match(match)
    minutes = int(settings.get('check_in_minutes') or 0)
    if not minutes or match.check_in_deadline or match.status != 'scheduled':
        return
    if not (match.participant_1_id and match.participant_2_id):
        return
    start = match.scheduled_at or timezone.now()
    # A match with a time of its own (the break after its players' last
    # matches, or one the organiser set) checks in against that time. Only a
    # match with no time waits for the stage, or the tournament, to start.
    if match.stage_id and not match.scheduled_at:
        stage_start = match.stage.effective_when()[0]
        if stage_start and stage_start > start:
            start = stage_start
    match.check_in_deadline = start + timedelta(minutes=minutes)
    type(match).objects.filter(pk=match.pk).update(check_in_deadline=match.check_in_deadline)


def check_in(match, user):
    """The side `user` plays for checks in. Returns the slot."""
    slot = match.participant_owned_by(user)
    if slot is None:
        raise StageEngineError('NOT_IN_THIS_MATCH')
    if match.status not in ('scheduled', 'in_progress'):
        raise StageEngineError('MATCH_NOT_OPEN')
    field = 'checked_in_p%s_at' % slot
    if getattr(match, field) is None:
        setattr(match, field, timezone.now())
        match.save(update_fields=[field])
    return slot


FORFEIT_SCORE = 3   # a no-show loses 0-3, as EA's FC Pro rules and most leagues have it


def settle_no_show(match, now=None):
    """If the check-in deadline has passed with exactly one side checked in,
    that side wins by forfeit. Neither checked in is left for the organiser:
    ejecting both is a decision, not a timer."""
    from . import results
    from .models import BracketMatch

    now = now or timezone.now()
    if match.status != 'scheduled' or match.check_in_deadline is None:
        return False
    if match.check_in_deadline > now:
        return False
    in1, in2 = match.checked_in_p1_at is not None, match.checked_in_p2_at is not None
    if in1 == in2:
        return False
    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        if locked.status != 'scheduled':
            return False
        winner = locked.participant_1 if in1 else locked.participant_2
        decision = results.Decision(
            winner=winner,
            score_p1=FORFEIT_SCORE if in1 else 0,
            score_p2=0 if in1 else FORFEIT_SCORE,
            penalties_p1=None, penalties_p2=None, games=[])
        results.apply(locked, decision, forfeit_reason='no_show')
    return True


def sweep_no_shows(tournament=None, now=None):
    from .models import BracketMatch

    now = now or timezone.now()
    qs = BracketMatch.objects.filter(status='scheduled', check_in_deadline__lte=now)
    if tournament is not None:
        qs = qs.filter(tournament=tournament)
    return sum(1 for m in qs.select_related('participant_1', 'participant_2') if settle_no_show(m, now))


# ---------------------------------------------------------------------------
# Final positions for a tournament played in stages
# ---------------------------------------------------------------------------

def assign_final_positions(tournament):
    """The last stage places its entrants; everybody knocked out earlier is
    placed below them, later stages first, by their rank in the stage they
    went out in."""
    from .models import TournamentRegistration

    stages = _stages(tournament)
    placed = {}
    pos = 1
    for stage in reversed(stages):
        rows = standings(stage)
        carried = {row['registration_id'] for row in (stage.advanced or [])
                   if isinstance(row, dict)}
        for row in sorted(rows, key=lambda r: (r['rank'] is None, r['rank'] or 0,
                                                r['group'] or 0)):
            rid = row['registration_id']
            if rid in placed or rid in carried:
                continue
            placed[rid] = pos
            pos += 1
    for rid, p in placed.items():
        TournamentRegistration.objects.filter(pk=rid).update(final_position=p)
    return placed

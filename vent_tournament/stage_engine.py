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

from . import formats, ranking, stage_settings


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


# The stages are a graph joined by progression lines (P1b, design section 6),
# not a chain by `order`. These two keep their old names so every caller that
# asks "the stage before" or "the stage after" follows the lines: the first
# line in and the first line out.

def previous_stage(stage):
    from . import progressions
    feeding = progressions.incoming(stage)
    return feeding[0].source if feeding else None


def next_stage(stage):
    from . import progressions
    out = progressions.outgoing(stage)
    return out[0].target if out else None


def _later_direct(stage):
    """Registration ids seeded straight into any other stage: a starting stage
    leaves them to the stage they were placed in."""
    ids = set()
    for other in stage.tournament.stages.exclude(pk=stage.pk):
        ids.update(int(i) for i in (other.direct_entrants or []))
    return ids


def entrants_for(stage, seed_strategy='registration', manual_order=None):
    """Who plays in this stage, best seed first (the upper bracket's, when
    some come in on the lower side: see `entrants_and_lower`)."""
    return entrants_and_lower(stage, seed_strategy, manual_order)[0]


def entrants_and_lower(stage, seed_strategy='registration', manual_order=None):
    """(entrants, lower side entrants), each best seed first.

    A stage no line feeds starts the tournament: its own invited entrants when
    it has them (one of two pools), else the confirmed registrations nobody
    was placed past. A fed stage takes what its lines sent, in line order,
    the lines into a double elimination's lower bracket kept apart.
    """
    from .models import TournamentRegistration
    from .services import bracket
    from . import progressions

    tournament = stage.tournament
    direct = [int(i) for i in (stage.direct_entrants or [])]
    feeding = progressions.incoming(stage)
    lower = []

    if not feeding:
        if direct:
            by_id = {r.id: r for r in bracket.confirmed_registrations(tournament)}
            return [by_id[i] for i in direct if i in by_id], []
        skip = _later_direct(stage)
        regs = [r for r in bracket.confirmed_registrations(tournament) if r.id not in skip]
        ordered = bracket.seed_registrations(regs, seed_strategy, manual_order)
    else:
        if any(line.fired_at is None for line in feeding):
            raise StageEngineError('PREVIOUS_STAGE_NOT_FINISHED')
        carried = [int(row['registration_id']) for line in feeding if line.side != 'losers'
                   for row in (line.sent or []) if row.get('registration_id')]
        low_ids = [int(row['registration_id']) for line in feeding if line.side == 'losers'
                   for row in (line.sent or []) if row.get('registration_id')]
        by_id = {r.id: r for r in TournamentRegistration.objects.filter(
            id__in=carried + low_ids + direct).select_related('user', 'team')}
        ordered = [by_id[i] for i in carried if i in by_id]
        lower = [by_id[i] for i in low_ids if i in by_id]
        if stage.placement == 'random':
            random.shuffle(ordered)
        elif stage.placement == 'manual' and manual_order:
            wanted = [int(i) for i in manual_order]
            ordered = [by_id[i] for i in wanted if i in by_id and i in carried] + \
                      [r for r in ordered if r.id not in wanted]
        # 'cross' and 'by_record' are already the order the lines sent.

    if direct:
        from .models import TournamentRegistration as TR
        by_id = {r.id: r for r in TR.objects.filter(id__in=direct)}
        # Seeded straight in means seeded at the top: that is what an invited
        # side or last year's champion is for.
        front = [by_id[i] for i in direct if i in by_id
                 and by_id[i].status == 'confirmed']
        ordered = front + [r for r in ordered if r.id not in set(direct)]
    return ordered, lower


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def early_tokens(stage):
    """(upper, lower) tokens for a stage drawn before its links send: one per
    place each link will send, in the order `progressions.rows_for` sends
    them (place by place, group by group), so a seat waits for exactly the
    place the cross puts there."""
    from . import progressions
    upper, lower = [], []
    for line in progressions.incoming(stage):
        groups = line.source.groups or 0
        wanted = ([line.group] if line.group is not None
                  else list(range(1, groups + 1)) if groups > 1 else [None])
        out = lower if line.side == 'losers' else upper
        for place in range(line.place_from, line.place_to + 1):
            for group in wanted:
                out.append({'line': line, 'group': group, 'place': place})
    return upper, lower


EARLY_FORMATS = ('single_elimination', 'double_elimination')


def _draw_early(stage, user):
    """Draw a knockout before the stages that feed it finish (P1d, design
    section 4): every seat waits for its place and fills when its link sends."""
    from .models import BracketGeneration, TournamentRegistration
    from .services import bracket
    fmt = formats.get(stage.format)
    if fmt is None or fmt.key not in EARLY_FORMATS:
        raise StageEngineError('STAGE_EARLY_NOT_FOR_FORMAT', 'format')
    if (stage.placement or 'cross') != 'cross':
        raise StageEngineError('STAGE_EARLY_NEEDS_CROSS', 'placement')
    upper, lower = early_tokens(stage)
    direct = [int(i) for i in (stage.direct_entrants or [])]
    by_id = {r.id: r for r in TournamentRegistration.objects.filter(id__in=direct, status='confirmed')}
    upper = [by_id[i] for i in direct if i in by_id] + upper
    if len(upper) + len(lower) < fmt.min_participants:
        raise StageEngineError('NOT_ENOUGH_ENTRANTS', need=fmt.min_participants,
                               have=len(upper) + len(lower))
    settings = stage.settings or stage_settings.clean(fmt.key, {})
    try:
        summary = bracket.draw_into(stage.tournament, stage, fmt.key, upper, settings,
                                    groups=0, lower=lower, early=True)
    except bracket.BracketError as exc:
        raise StageEngineError(exc.code.upper())
    stage.entrants = [i for i in direct if i in by_id]
    stage.drawn_at = timezone.now()
    stage.settings = settings
    stage.save(update_fields=['entrants', 'drawn_at', 'settings'])
    BracketGeneration.objects.create(
        tournament=stage.tournament, generated_by=user, seed_strategy='registration',
        seed_payload={'registration_ids': stage.entrants, 'stage_id': stage.id, 'early': True},
        match_count=summary['matches_created'], rounds_count=summary['rounds_count'],
        notes='stage=%s format=%s early' % (stage.order, fmt.key))
    summary.update({'stage_id': stage.id, 'format': fmt.key, 'early': True})
    return summary


def draw(stage, user, seed_strategy='registration', manual_order=None, early=False):
    """Draw one stage. Runs inside transaction.atomic().

    `early` draws a knockout before the stages that feed it have sent their
    people, with every seat waiting for its place (P1d)."""
    if early and stage.drawn_at is None and not stage.matches.exists():
        from . import progressions
        if any(line.fired_at is None for line in progressions.incoming(stage)):
            return _draw_early(stage, user)
    from .models import BracketGeneration
    from .services import bracket

    from . import br_engine

    if stage.drawn_at is not None or stage.matches.exists() or stage.br_lobbies.exists():
        raise StageEngineError('STAGE_ALREADY_DRAWN')
    fmt = formats.get(stage.format)
    if fmt is None:
        raise StageEngineError('UNKNOWN_FORMAT', 'format')
    key = fmt.key
    if key not in bracket.DRAWABLE and key != 'battle_royale':
        raise StageEngineError('FORMAT_HAS_NO_BRACKET', 'format')

    ordered, lower = entrants_and_lower(stage, seed_strategy, manual_order)
    everybody = ordered + lower
    if len(everybody) < fmt.min_participants:
        raise StageEngineError('NOT_ENOUGH_ENTRANTS', need=fmt.min_participants,
                               have=len(everybody))
    if fmt.max_participants and len(everybody) > fmt.max_participants:
        raise StageEngineError('TOO_MANY_ENTRANTS', need=fmt.max_participants,
                               have=len(everybody))

    from . import progressions
    settings = stage.settings or stage_settings.clean(key, {})
    if key == 'battle_royale':
        # The head start the stages before gave each squad, if they gave one.
        carried = {int(row['registration_id']): row.get('carry') or 0
                   for line in progressions.incoming(stage) for row in (line.sent or [])
                   if isinstance(row, dict) and row.get('registration_id')}
        stage.settings = settings
        try:
            summary = br_engine.draw(stage, ordered, carried)
        except br_engine.BRError as exc:
            raise StageEngineError(exc.code)
    else:
        try:
            summary = bracket.draw_into(stage.tournament, stage, key, ordered, settings,
                                        groups=stage.groups, lower=lower)
        except bracket.BracketError as exc:
            raise StageEngineError(exc.code.upper())

    stage.entrants = [r.id for r in everybody]
    stage.drawn_at = timezone.now()
    stage.status = 'running'
    stage.settings = settings
    stage.save(update_fields=['entrants', 'drawn_at', 'status', 'settings'])
    from . import happenings
    happenings.started(stage)

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
    rules = ranking.stage_rules(stage)
    return (rules['points_win'], rules['points_draw'], rules['points_loss'])


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


def _rank_table(rows, matches, pts, tiebreakers=None):
    """Points, then the organiser's tie-breakers in their order (by default
    the result between the tied sides, goal difference, goals scored, wins,
    seed). Each row says which one separated it."""
    for row in rows:
        row['goal_difference'] = row['goals_for'] - row['goals_against']
    return ranking.order(rows, tiebreakers or ranking.TABLE_DEFAULT,
                         h2h=lambda ids: _head_to_head_points(ids, matches, pts))


def _table_standings(stage):
    from .models import TournamentRegistration

    rules = ranking.stage_rules(stage)
    pts = (rules['points_win'], rules['points_draw'], rules['points_loss'])
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    groups = {}
    for m in stage.matches.all():
        g = m.group_number or 0
        for pid in (m.participant_1_id, m.participant_2_id):
            if pid in regs:
                groups.setdefault(g, {}).setdefault(pid, _blank(regs[pid], m.group_number))
    played = _played(stage)
    carried = _carried_points(stage)
    out = []
    for g in sorted(groups):
        rows = groups[g]
        group_matches = [m for m in played if (m.group_number or 0) == g]
        for m in group_matches:
            _tally(rows, m, pts)
        # A split league starts each half with the points of the table before
        # (P1b, a line that carries points).
        for pid, row in rows.items():
            row['points'] += carried.get(pid, 0)
        out.extend(_rank_table(list(rows.values()), group_matches, pts, rules['tiebreakers']))
    return out


def _carried_points(stage):
    """{registration id: points} brought in by lines that carry points."""
    from . import progressions
    out = {}
    for line in progressions.incoming(stage):
        if line.carry_points:
            for row in (line.sent or []):
                out[int(row['registration_id'])] = out.get(int(row['registration_id']), 0) \
                    + (row.get('carry') or 0)
    return out


def _swiss_standings(stage):
    from .models import TournamentRegistration

    settings = stage.settings or {}
    rules = ranking.stage_rules(stage)
    pts = (rules['points_win'], rules['points_draw'], rules['points_loss'])
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    rows = {i: _blank(r) for i, r in regs.items()}
    opponents = {i: [] for i in regs}
    played = []
    for m in stage.matches.all():
        if m.status == 'bye' and m.winner_id in rows:
            rows[m.winner_id]['wins'] += 1
            rows[m.winner_id]['points'] += rules['points_bye']
            continue
        if m.status not in ('completed', 'walkover_p1', 'walkover_p2'):
            continue
        a, b = m.participant_1_id, m.participant_2_id
        if a in opponents and b in opponents:
            opponents[a].append(b)
            opponents[b].append(a)
        _tally(rows, m, pts)
        played.append(m)
    strength = 'points' if rules['buchholz'] == 'points' else 'wins'
    for i, row in rows.items():
        row['buchholz'] = sum(rows[o][strength] for o in opponents[i] if o in rows)
        row['goal_difference'] = row['goals_for'] - row['goals_against']
        target, limit = settings.get('win_target') or 0, settings.get('loss_limit') or 0
        if target and row['wins'] >= target:
            row['status'] = 'qualified'
        elif limit and row['losses'] >= limit:
            row['status'] = 'eliminated'
    return ranking.order(list(rows.values()), rules['tiebreakers'] or ranking.SWISS_DEFAULT,
                         h2h=lambda ids: _head_to_head_points(ids, played, pts))


def _loser(m):
    if m.winner_id is None:
        return None
    return m.participant_2_id if m.winner_id == m.participant_1_id else m.participant_1_id


def knockout_places(matches):
    """Finishing places from a knockout's matches: {registration id: place}.

    The final decides first and second, a third-place match third and fourth,
    and everybody else by the round they went out in, later rounds first.
    Losers of the same round SHARE their place (P0, 16 October 2026: "these
    are fine."): both semi-final losers are third, all four quarter-final
    losers fifth. They were given different places by match number, and prizes
    were paid by those places. A bracket whose matches state places (every
    place, stepladder, page playoff) keeps them. In double elimination a loss
    on the winners side is not an elimination.
    """
    from .services.bracket import places_from_matches

    matches = list(matches)
    stated = places_from_matches(matches)
    if stated is not None:
        return stated
    place = {}
    final = next((m for m in matches if m.is_final), None)
    if final is None and matches:
        final = max(matches, key=lambda m: (m.round_number, m.match_number))
    if final is not None and final.status in TERMINAL and final.winner_id:
        place[final.winner_id] = 1
        runner = _loser(final)
        if runner is None and final.bracket_side == 'grand_final':
            first = next((m for m in matches if m.bracket_side == 'grand_final'
                          and not m.is_final), None)
            runner = _loser(first) if first else None
        if runner:
            place[runner] = 2
    third = next((m for m in matches if final is not None and m is not final and not m.is_final
                  and m.round_number == final.round_number
                  and m.bracket_side == final.bracket_side and m.status == 'completed'), None)
    nxt = 3
    if third is not None:
        if third.winner_id:
            place.setdefault(third.winner_id, 3)
        if _loser(third):
            place.setdefault(_loser(third), 4)
        nxt = 5
    rounds = {}
    for m in matches:
        if m.status != 'completed' or m is third or m is final:
            continue
        if m.bracket_side == 'winners' and m.loser_to_match_id:
            continue
        rounds.setdefault((m.round_number, m.bracket_side), []).append(m)
    for key in sorted(rounds, key=lambda k: (-k[0], k[1])):
        out = [lost for lost in (_loser(m) for m in rounds[key]) if lost and lost not in place]
        for lost in out:
            place[lost] = nxt
        nxt += len(out)
    return place


def _knockout_standings(stage):
    """Finishing places, as `knockout_places` decides them, with the record."""
    from .models import TournamentRegistration

    matches = list(stage.matches.all())
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=stage.entrants or []).select_related('user', 'team', 'squad')}
    place = knockout_places(matches)
    rows = []
    for rid, reg in regs.items():
        row = _blank(reg)
        row['rank'] = place.get(rid)
        row['status'] = 'placed' if rid in place else 'playing'
        rows.append(row)
    by_id = {r['registration_id']: r for r in rows}
    for m in matches:
        if m.status == 'completed' and m.participant_1_id in regs and m.participant_2_id in regs:
            _tally(by_id, m, (1, 0, 0))
    rows.sort(key=lambda r: (r['rank'] is None, r['rank'] or 0, r['seed'] or 10 ** 6))
    return rows


def seeds_of(tournament, stage):
    """Registration ids best seed first, as the draw froze them."""
    if stage is not None:
        return [int(i) for i in (stage.entrants or [])]
    regs = tournament.registrations.filter(status='confirmed').order_by('seed', 'id')
    return [r.id for r in regs]


def streak_rows(tournament, stage, matches, settings):
    """Winner stays on: placed by wins, then the longest streak, then seed."""
    from .models import TournamentRegistration
    from .services import bracket

    seeds = seeds_of(tournament, stage)
    state = bracket.streak_state(matches, seeds, settings)
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=seeds)
            .select_related('user', 'team', 'squad')}
    rows = []
    for rid in seeds:
        if rid not in regs:
            continue
        row = _blank(regs[rid])
        row['wins'] = state['wins'].get(rid, 0)
        row['losses'] = state['losses'].get(rid, 0)
        row['played'] = state['played'].get(rid, 0)
        row['points'] = row['wins']
        row['longest_streak'] = state['best_streak'].get(rid, 0)
        row['holder'] = rid == state['holder'] and not state['finished']
        cur = state['current']
        row['challenger'] = bool(cur is not None and rid != state['holder']
                                 and rid in (cur.participant_1_id, cur.participant_2_id))
        row['queue_position'] = (state['queue'].index(rid) + 1
                                 if rid in state['queue'] and not state['finished'] else None)
        row['status'] = ('holder' if row['holder'] else 'challenger' if row['challenger']
                         else 'queued' if row['queue_position']
                         else 'placed' if state['finished'] else 'out'
                         if row['losses'] and not state['target'] else 'playing')
        rows.append(row)
    # Points here are wins (set above), then the longest streak, then seed.
    rows = ranking.order(rows, ['longest_streak', 'seed'],
                         value=lambda r, k: r['longest_streak'] if k == 'longest_streak' else None)
    for row in rows:
        if row['decided_by'] == 'points':
            row['decided_by'] = 'wins'
    return rows, state


def tournament_standings(tournament):
    """Rows for a tournament that runs as ONE format, where a table means
    something: winner stays on (who holds the spot, the queue, wins and
    streaks) and any bracket whose matches state places (stepladder, page
    playoff, every place). None for a plain knockout or a league, whose pages
    read their own tables."""
    from .models import TournamentRegistration
    from .services.bracket import normalize_bracket_type, places_from_matches

    key = normalize_bracket_type(tournament.bracket_type)
    matches = list(tournament.bracket_matches.all())
    if not matches:
        return None
    if key == 'winner_stays_on':
        return streak_rows(tournament, None, matches,
                           stage_settings.for_tournament(tournament))[0]
    stated = places_from_matches(matches)
    if stated is None:
        return None
    regs = TournamentRegistration.objects.filter(
        tournament=tournament, status='confirmed').select_related('user', 'team', 'squad')
    rows = []
    for reg in regs:
        row = _blank(reg)
        row['rank'] = stated.get(reg.id)
        row['status'] = 'placed' if reg.id in stated else 'playing'
        rows.append(row)
    rows.sort(key=lambda r: (r['rank'] is None, r['rank'] or 0, r['seed'] or 10 ** 6))
    return rows


def _streak_standings(stage):
    rows, _state = streak_rows(stage.tournament, stage, list(stage.matches.all()),
                               stage.settings or stage_settings.clean(stage.format, {}))
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
    if key == 'battle_royale':
        from . import br_engine
        return br_engine.standings(stage)
    if key == 'winner_stays_on':
        return _streak_standings(stage)
    if key == 'swiss':
        return _swiss_standings(stage)
    if key == 'gsl':
        return _gsl_standings(stage)
    if key in ('single_elimination', 'double_elimination', 'stepladder', 'page_playoff'):
        return _knockout_standings(stage)
    return _table_standings(stage)


def stage_finished(stage):
    """Every match in the stage is decided, and a Swiss has run its rounds."""
    if stage.drawn_at is None:
        return False
    if stage.format == 'battle_royale':
        from . import br_engine
        return br_engine.finished(stage)
    if stage.matches.exclude(status__in=TERMINAL).exists():
        return False
    if stage.format == 'swiss':
        return swiss_finished(stage)
    if stage.format == 'winner_stays_on':
        return streak_rows(stage.tournament, stage, list(stage.matches.all()),
                           stage.settings or {})[1]['finished']
    return True


# ---------------------------------------------------------------------------
# Advancing
# ---------------------------------------------------------------------------

def advancing(stage):
    """Who would go through, best first: every line that is not waiting for a
    press, in line order, each row saying which line sends it (P1b).

    Groups go rank by rank across groups (1A, 1B, 1C, 1D, 2A, 2B, ...): seeded
    into a knockout that way, every group winner meets a runner-up from a
    different group, which is the cross. `progressions.rows_for` decides a
    line's rows, the same function that sends them, so the preview and what is
    sent cannot differ.
    """
    from . import progressions
    if stage.drawn_at is None:
        return []
    out, seen = [], set()
    for line in progressions.outgoing(stage):
        if line.fire == 'manual' or line.fired_at is not None:
            continue
        for row in progressions.sending(line):
            if row['registration_id'] in seen:
                continue
            seen.add(row['registration_id'])
            out.append(dict(row, line_id=line.id, side=line.side))
    return out


def advance(stage, user, order=None, orders=None, ignore_disputes=False, draw_next=True,
            modes=('after_preview', 'on_close')):
    """Close `stage`, send its lines, and draw every stage they fill.

    `modes` are the lines this press sends: the organiser's Advance sends the
    lines that wait for a preview and those that go on close; the stage ending
    by itself sends only the second kind. A line that waits for "send" waits.
    `order` reorders or swaps what a single line sends, among the stage's own
    entrants; `orders` does it per line ({line id: [registration ids]}).
    """
    from . import progressions
    tournament = stage.tournament
    if stage.status == 'complete':
        raise StageEngineError('ALREADY_ADVANCED')
    out = progressions.outgoing(stage)
    if not out:
        raise StageEngineError('LAST_STAGE')
    if not stage_finished(stage):
        raise StageEngineError('STAGE_NOT_FINISHED', open=open_count(stage))
    open_disputes = tournament.disputes.filter(
        status__in=('open', 'under_review'), match__stage=stage).count()
    if open_disputes and not ignore_disputes:
        raise StageEngineError('DISPUTES_OPEN', count=open_disputes)

    due = [line for line in out if line.fire in modes and line.fired_at is None]
    for line in due:
        progressions.fire(line, user, order if (order and len(due) == 1)
                          else (orders or {}).get(str(line.id)))
    if due and not any(line.sent for line in due):
        raise StageEngineError('NOBODY_ADVANCES')
    return after_fire(stage, user, due, draw_next=draw_next)


def after_fire(stage, user, fired, draw_next=True):
    """What follows lines being sent: the stage's own record of who went
    through, the stage closed once nothing but a press is left, and every
    stage those lines fill drawn once all of its lines have arrived."""
    from . import progressions
    stage.advanced = progressions.sent_union(stage)
    fields = ['advanced']
    out = progressions.outgoing(stage)
    if stage.status != 'complete' and all(line.fired_at or line.fire == 'manual' for line in out):
        stage.status = 'complete'
        stage.completed_at = timezone.now()
        fields += ['status', 'completed_at']
    stage.save(update_fields=fields)
    if 'status' in fields:
        from . import happenings
        happenings.closed(stage)

    draws, waiting, targets = {}, [], []
    for line in (fired or out):
        if line.target_id not in [t.id for t in targets]:
            targets.append(line.target)
    for target in targets:
        target.refresh_from_db()
        if target.drawn_at is not None and target.matches.filter(slots__kind='place').exists():
            # Drawn early (P1d): the seats waiting on these lines fill now.
            _fill_early(target, [line for line in fired if line.target_id == target.id])
            if not progressions.ready(target):
                waiting.append(target.id)
            continue
        if not progressions.ready(target):
            waiting.append(target.id)
            continue
        if draw_next and target.drawn_at is None:
            draws[target.id] = draw(target, user)
    first = targets[0] if targets else None
    return {
        'advanced': stage.advanced,
        'next_stage_id': first.id if first else None,
        'draw': draws.get(first.id) if first else None,
        'draws': draws,
        'waiting': waiting,
        'lines': [{'id': line.id, 'target': line.target_id, 'sent': len(line.sent or [])}
                  for line in fired],
    }


def _fill_early(target, lines):
    """Fill a stage drawn early from the lines that just sent, start it, and
    keep its entrants: everybody seated in it, in seat order."""
    from . import match_slots
    for line in lines:
        match_slots.fill(target, line)
    seated = []
    for m in target.matches.order_by('bracket_side', 'round_number', 'match_number'):
        for pid in (m.participant_1_id, m.participant_2_id):
            if pid and pid not in seated:
                seated.append(pid)
    target.entrants = seated
    fields = ['entrants']
    if target.status == 'pending':
        target.status = 'running'
        fields.append('status')
    target.save(update_fields=fields)
    if 'status' in fields:
        from . import happenings
        happenings.started(target)
    tournament = target.tournament
    if tournament.status != 'live':
        tournament.status = 'live'
        tournament.save(update_fields=['status'])


def _close_by_itself(stage):
    """The lines that go "as soon as the stage ends" go now, if the stage has
    ended and no dispute is open. Never raises into a result being recorded:
    a refusal here is logged and the organiser can still press Advance."""
    import logging
    from . import progressions
    try:
        due = [line for line in progressions.outgoing(stage)
               if line.fire == 'on_close' and line.fired_at is None]
        if not due or stage.status == 'complete' or not stage_finished(stage):
            return None
        if stage.tournament.disputes.filter(status__in=('open', 'under_review'),
                                            match__stage=stage).exists():
            return None
        with transaction.atomic():
            return advance(stage, stage.tournament.tournament_creator, modes=('on_close',))
    except Exception:                                       # noqa: BLE001
        logging.getLogger(__name__).exception('stage %s could not close by itself', stage.pk)
        return None


def open_count(stage):
    """What is still to be played: matches, or battle royale maps."""
    if stage.format == 'battle_royale':
        from . import br_engine
        return br_engine.open_maps(stage)
    return stage.matches.exclude(status__in=TERMINAL).count()


def finish_last_stage(stage, user):
    """Close the LAST stage of a tournament whose last stage has no bracket to
    finish it by itself (a battle royale), and write the final places.

    A knockout ends the tournament when its final is played. A battle royale
    has no final match, and ending it on the last result entered would shut
    the door on correcting that result, so the organiser presses it.
    """
    from django.db.models import Q
    from .services.advance import NON_TERMINAL_STATUSES

    tournament = stage.tournament
    if next_stage(stage) is not None:
        raise StageEngineError('NOT_THE_LAST_STAGE')
    if stage.status == 'complete' or tournament.completed_at is not None:
        raise StageEngineError('ALREADY_FINISHED')
    if not stage_finished(stage):
        raise StageEngineError('STAGE_NOT_FINISHED', open=open_count(stage))
    if tournament.bracket_matches.filter(status__in=NON_TERMINAL_STATUSES).exists():
        raise StageEngineError('STAGE_NOT_FINISHED')
    assign_final_positions(tournament)
    now = timezone.now()
    stage.status = 'complete'
    stage.completed_at = now
    stage.save(update_fields=['status', 'completed_at'])
    tournament.status = 'completed'
    tournament.completed_at = now
    tournament.save(update_fields=['status', 'completed_at'])
    from . import happenings
    happenings.closed(stage)
    happenings.won(tournament)
    return tournament


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
    # Each new round's seats say who was paired into them, with their seed.
    from . import match_slots
    match_slots.complete(stage.matches.filter(round_number=round_number),
                       {rid: i for i, rid in enumerate(stage.entrants or [], start=1)})
    for m in stage.matches.filter(round_number=round_number, status='scheduled'):
        arm_check_in(m)
    return {'round_number': round_number, 'matches_created': created}


def on_match_resolved(match):
    stage = match.stage
    if stage is None:
        from .services.bracket import normalize_bracket_type
        if normalize_bracket_type(match.tournament.bracket_type) == 'winner_stays_on':
            _next_challenge(match.tournament, None)
        return
    if stage.format == 'swiss':
        next_swiss_round(stage)
    elif stage.format == 'winner_stays_on':
        _next_challenge(stage.tournament, stage)
    # A line set to go as soon as the stage ends goes now (P1b, design 477).
    _close_by_itself(stage)


def _next_challenge(tournament, stage):
    from .services import bracket
    if stage is not None:
        settings = stage.settings or stage_settings.clean(stage.format, {})
        matches = list(stage.matches.all())
    else:
        settings = stage_settings.for_tournament(tournament)
        matches = list(tournament.bracket_matches.all())
    created = bracket.next_challenge(tournament, stage, settings,
                                     seeds_of(tournament, stage), matches)
    if created is not None:
        if stage is not None:
            arm_check_in(created)
        else:
            schedule(created)
    return created


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
    """Final places for a tournament run in stages.

    The stages are placed in `progressions.placement_order`: the stage that
    decides the top places first, then each stage below it. Within a group of
    stages, everybody who went out on the same rank shares one place: the
    third of every group, both semi-final losers (P0), the thirds of two
    parallel pools (P1b). Somebody a stage sent on is placed by the stage they
    went to.
    """
    from .models import TournamentRegistration
    from . import progressions

    placed = {}
    pos = 1
    for group in progressions.placement_order(tournament):
        by_rank = {}
        for stage in group:
            rows = standings(stage)
            carried = {row['registration_id'] for row in (stage.advanced or [])
                       if isinstance(row, dict)}
            for row in rows:
                rid = row['registration_id']
                if rid in placed or rid in carried:
                    continue
                by_rank.setdefault(row['rank'], []).append(rid)
        for rank in sorted(by_rank, key=lambda r: (r is None, r or 0)):
            for rid in by_rank[rank]:
                placed[rid] = pos
            pos += len(by_rank[rank])
    for rid, p in placed.items():
        TournamentRegistration.objects.filter(pk=rid).update(final_position=p)
    return placed

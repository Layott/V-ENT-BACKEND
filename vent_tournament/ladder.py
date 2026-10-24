"""The challenge ladder and the pyramid (P3 W3d, research catalogue 5.1).

Everybody holds a place, first at the top. "Any player can challenge a player
above them on the ladder", up to `reach` places above; when the challenger
wins, "the two players swap places"; a loser may not challenge the same player
again until they have challenged somebody else. A pyramid is the same ladder
drawn in levels (one at the top, two below, three below that), where a player
challenges anybody above them on their own level or anybody on the level
directly above.

The places are the stage's `entrants`, in order: drawn in seed order, and
rewritten by every challenge the challenger wins. Nothing is drawn up front;
each match is made by a challenge. The ladder runs until the stage's end
time, or for good when it has none.

CEO, 24 October 2026: "make all brackets and formats live today and function
properly".
"""
from django.utils import timezone

SHAPES = ('ladder', 'pyramid')


class LadderError(ValueError):
    def __init__(self, code, field=None):
        super().__init__(code)
        self.code = code
        self.field = field


def level_of(place):
    """The pyramid level of a 1-based place: 1 is level 1, 2 and 3 level 2,
    4 to 6 level 3."""
    level, last = 1, 1
    while place > last:
        level += 1
        last += level
    return level


def _open(stage):
    from .stage_engine import TERMINAL
    return stage.matches.exclude(status__in=TERMINAL)


def may_challenge(stage, challenger_id, target_id):
    """None when `challenger_id` may challenge `target_id` now, else the code
    that says why not."""
    if stage.format != 'challenge_ladder' or stage.drawn_at is None:
        return 'LADDER_NOT_RUNNING'
    if finished(stage):
        return 'LADDER_CLOSED'
    places = list(stage.entrants or [])
    if challenger_id not in places or target_id not in places:
        return 'NOT_IN_THIS_LADDER'
    mine, theirs = places.index(challenger_id) + 1, places.index(target_id) + 1
    if theirs >= mine:
        return 'LADDER_NOT_ABOVE'
    settings = stage.settings or {}
    if settings.get('shape') == 'pyramid':
        if level_of(theirs) not in (level_of(mine), level_of(mine) - 1):
            return 'LADDER_OUT_OF_REACH'
    elif mine - theirs > int(settings.get('reach') or 3):
        return 'LADDER_OUT_OF_REACH'
    busy = _open(stage).filter(participant_1_id__in=(challenger_id, target_id)).exists() or \
        _open(stage).filter(participant_2_id__in=(challenger_id, target_id)).exists()
    if busy:
        return 'LADDER_BUSY'
    last = (stage.matches.filter(participant_1_id=challenger_id)
            .exclude(status__in=('scheduled', 'in_progress')).order_by('-round_number').first())
    if last is not None and last.participant_2_id == target_id and last.winner_id == target_id:
        return 'LADDER_REMATCH'
    return None


def challenge(stage, challenger, target):
    """Make the match. The challenger is participant 1, which is how the swap
    on the result knows who challenged."""
    from .services import bracket

    code = may_challenge(stage, challenger.id, target.id)
    if code:
        raise LadderError(code, 'target_registration_id')
    number = (max((m.round_number for m in stage.matches.all()), default=0)) + 1
    return bracket._new_match(stage.tournament, stage, stage.settings, number, 1, 'winners',
                              rounds_total=number, table=True,
                              participant_1=challenger, participant_2=target,
                              status='scheduled')


def on_resolved(match):
    """A challenger who wins takes the place they challenged for, and the
    player they beat takes theirs."""
    stage = match.stage
    if match.winner_id is None or match.winner_id != match.participant_1_id:
        return
    places = list(stage.entrants or [])
    a, b = match.participant_1_id, match.participant_2_id
    if a not in places or b not in places or places.index(a) < places.index(b):
        return
    i, j = places.index(a), places.index(b)
    places[i], places[j] = b, a
    stage.entrants = places
    stage.save(update_fields=['entrants'])


def finished(stage):
    return bool(stage.ends_at and timezone.now() >= stage.ends_at
                and not _open(stage).exists())


def standings(stage):
    """One row per place, top first, with the record from challenges."""
    from .models import TournamentRegistration
    from .stage_engine import _blank

    places = list(stage.entrants or [])
    regs = {r.id: r for r in TournamentRegistration.objects.filter(id__in=places)
            .select_related('user', 'team', 'squad')}
    rows = {rid: _blank(regs[rid]) for rid in places if rid in regs}
    for m in stage.matches.filter(status__in=('completed', 'walkover_p1', 'walkover_p2')):
        for rid in (m.participant_1_id, m.participant_2_id):
            if rid in rows:
                rows[rid]['played'] += 1
                if m.winner_id == rid:
                    rows[rid]['wins'] += 1
                elif m.winner_id:
                    rows[rid]['losses'] += 1
    pyramid = (stage.settings or {}).get('shape') == 'pyramid'
    out = []
    for place, rid in enumerate(places, start=1):
        if rid not in rows:
            continue
        row = rows[rid]
        row['rank'] = place
        row['position'] = place
        row['points'] = row['wins']
        row['decided_by'] = 'ladder' if place > 1 else None
        if pyramid:
            row['level'] = level_of(place)
        out.append(row)
    return out


def view(stage, viewer):
    """What the bracket page needs to offer challenges: the rules, the place
    the viewer acts for, and who they may challenge right now. Decided here,
    so the page never works out the rules for itself."""
    from .models import TournamentRegistration

    settings = stage.settings or {}
    mine = None
    if viewer is not None:
        mine = next((r.id for r in TournamentRegistration.objects
                     .filter(id__in=stage.entrants or [])
                     .select_related('user', 'team__team_owner', 'squad')
                     if r.acting_user == viewer), None)
    open_to = []
    if mine is not None:
        open_to = [rid for rid in (stage.entrants or [])
                   if rid != mine and may_challenge(stage, mine, rid) is None]
    return {'reach': int(settings.get('reach') or 3),
            'shape': settings.get('shape') or 'ladder',
            'mine': mine, 'challengeable': open_to,
            'closed': finished(stage)}

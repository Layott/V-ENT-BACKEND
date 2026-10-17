"""Where each side of a match comes from, written in one place (P1a).

CEO, 16 October 2026: "i aprove the design". Section 8 of the bracket
structure design: every slot knows where it came from, and there is one link
store. Until now a link lived only on the SENDING match (`winner_to_match`,
`loser_to_match` and their slots), so a seat nobody had reached could not say
whose it would be, and a bracket could not be shown whole before it was
played.

This module is the only code that writes those pointers. It writes each one
together with a `MatchSlot` row on the receiving side, so the two cannot
disagree, and `tools/check-one-link-writer.py` fails any other file that
assigns one. The readers (`services/advance.py` and a few more) still follow
the pointers; they move over to the slots one at a time, and the catcher's
count of them may only fall.
"""
from django.db.models import Q

OUTCOMES = ('winner', 'loser')


def _put(match, slot, kind, source=None, seed=None, **extra):
    from .models import MatchSlot
    MatchSlot.objects.update_or_create(
        match=match, slot=slot,
        defaults=dict({'kind': kind, 'source_match': source, 'seed': seed}, **extra))


def link(source, outcome, target, slot):
    """Send the winner (or loser) of `source` to `slot` of `target`."""
    if outcome not in OUTCOMES:
        raise ValueError('outcome is winner or loser, not %r' % (outcome,))
    setattr(source, '%s_to_match' % outcome, target)
    setattr(source, '%s_to_slot' % outcome, slot)
    source.save(update_fields=['%s_to_match' % outcome, '%s_to_slot' % outcome])
    _put(target, slot, outcome, source)


def link_reset(first, second):
    """The grand final played again if the lower bracket's champion wins the first.

    The pointer sends the first grand final's winner on, which is how
    `advance._grand_final_reset` knows to look; both seats of the second come
    from the first, whoever won it.
    """
    first.winner_to_match = second
    first.winner_to_slot = 1
    first.save(update_fields=['winner_to_match', 'winner_to_slot'])
    for slot in (1, 2):
        _put(second, slot, 'reset', first)


def wait_for(match, slot, line, group, place):
    """A seat in a stage drawn early (P1d): it waits for `place` of `group` of
    the stage `line` comes from, and fills when that line sends."""
    _put(match, slot, 'place', progression=line, source_group=group, source_place=place)


def wait_reseed(match, slot, rank):
    """A seat of a reseeded knockout (P3 W1): the survivor ranked `rank` by
    seed once the round before is decided."""
    _put(match, slot, 'reseed', source_place=rank)


PLAYED = ('completed', 'walkover_p1', 'walkover_p2', 'in_progress',
          'pending_opponent_confirm', 'disputed')


def sync_reseeded(stage):
    """Seat every round of a reseeded knockout from the round before; return
    how many matches changed.

    A round whose matches are all decided sends its survivors, best seed
    first (their order in `stage.entrants`), into the seats waiting on them:
    rank 1 against the last rank, 2 against the one before (research
    catalogue 2.1, the NFL's "the No. 1 seed will always host the lowest
    remaining seed"). A round not yet decided leaves the next one empty, so a
    reopened result takes its pairing back out. A round with a match already
    played is never rewritten: the result guards refuse the change first.
    """
    from django.utils import timezone
    from .models import BracketMatch, MatchSlot
    order = {int(rid): i for i, rid in enumerate(stage.entrants or [])}
    changed = 0
    waiting = (MatchSlot.objects.filter(match__stage=stage, kind='reseed')
               .values_list('match__round_number', flat=True).distinct())
    for rnd in sorted(set(waiting)):
        seats = {}
        for slot in MatchSlot.objects.filter(match__stage=stage, kind='reseed',
                                             match__round_number=rnd):
            seats.setdefault(slot.match_id, {})[slot.slot] = slot.source_place
        nxt = list(BracketMatch.objects.filter(pk__in=seats))
        if any(m.status in PLAYED for m in nxt):
            continue
        # The round before: its title path, not a match for third place.
        before = [m for m in BracketMatch.objects.filter(
            stage=stage, round_number=rnd - 1, bracket_side='winners')
            if not m.slots.filter(kind='loser').exists()]
        decided = bool(before) and all(m.status in ('completed', 'bye', 'walkover_p1', 'walkover_p2')
                                       for m in before)
        survivors = []
        if decided:
            survivors = sorted((m.winner for m in before if m.winner_id),
                               key=lambda reg: (order.get(reg.id, 10 ** 6),
                                                reg.seed if reg.seed is not None else 10 ** 6,
                                                reg.id))
        for match in nxt:
            want = [survivors[seats[match.id][s] - 1].id
                    if decided and seats[match.id].get(s) and seats[match.id][s] <= len(survivors)
                    else None for s in (1, 2)]
            present = [w for w in want if w]
            update = {}
            if [match.participant_1_id, match.participant_2_id] != want:
                update.update(participant_1_id=want[0], participant_2_id=want[1],
                              check_in_deadline=None, checked_in_p1_at=None,
                              checked_in_p2_at=None)
            if decided and len(present) < 2:
                # A seat whose survivor never came (a place that did not
                # arrive): a walkover, as a fixed bracket settles one.
                lone = present[0] if present else None
                if match.status != 'bye' or match.winner_id != lone:
                    update.update(status='bye', winner_id=lone,
                                  completed_at=timezone.now() if lone else None)
            elif match.status == 'bye':
                update.update(status='scheduled', winner_id=None, completed_at=None)
            if update:
                BracketMatch.objects.filter(pk=match.pk).update(**update)
                changed += 1
                if len(present) == 2:
                    from . import stage_engine
                    match.refresh_from_db()
                    stage_engine.arm_check_in(match)
    return changed


def mark_bye(match, slot):
    """A seat that stays empty by design: nobody is sent there."""
    _put(match, slot, 'bye')


def seat(match, slot, registration, seed=None):
    """An entrant placed in this seat at the draw."""
    if seed is None and registration is not None:
        seed = getattr(registration, 'seed', None)
    _put(match, slot, 'entrant', None, seed)


def complete(matches, seeds=None):
    """Write every slot row still missing for these matches; return how many.

    First every seat a pointer fills (winner, loser, or both seats of a reset
    grand final), then the rest: a seat with somebody in it is an entrant, an
    empty seat of a bye is a bye, anything else is open. An entrant's seed is
    `seeds[registration id]` when given (a draw knows its order), else the
    registration's own seed. Read fresh, because byes settled during a draw
    have moved people since the caller's copies were made.

    A draw calls it after the generator, when the links have written their
    own rows; the conversion of brackets drawn before P1a (`backfill`) calls
    it with nothing written, and the pointers say the rest.
    """
    from .models import BracketMatch, MatchSlot
    ids = [m.id if hasattr(m, 'id') else m for m in matches]
    wanted = set(ids)
    taken = set(MatchSlot.objects.filter(match_id__in=ids).values_list('match_id', 'slot'))
    current = {m.id: m for m in BracketMatch.objects.filter(pk__in=ids).select_related(
        'participant_1', 'participant_2')}
    new = []

    def add(match_id, slot, kind, source_id=None, seed=None):
        if (match_id, slot) in taken:
            return
        taken.add((match_id, slot))
        new.append(MatchSlot(match_id=match_id, slot=slot, kind=kind,
                             source_match_id=source_id, seed=seed))

    feeders = BracketMatch.objects.filter(
        Q(winner_to_match_id__in=ids) | Q(loser_to_match_id__in=ids)).values(
        'id', 'bracket_side', 'winner_to_match_id', 'winner_to_slot',
        'loser_to_match_id', 'loser_to_slot')
    for f in feeders:
        target = f['winner_to_match_id']
        if target in wanted:
            if f['bracket_side'] == 'grand_final' and current[target].bracket_side == 'grand_final':
                add(target, 1, 'reset', f['id'])
                add(target, 2, 'reset', f['id'])
            else:
                add(target, f['winner_to_slot'] or 1, 'winner', f['id'])
        target = f['loser_to_match_id']
        if target in wanted:
            add(target, f['loser_to_slot'] or 1, 'loser', f['id'])

    for m in current.values():
        for slot in (1, 2):
            reg = getattr(m, 'participant_%d' % slot)
            if reg is not None:
                add(m.id, slot, 'entrant',
                    seed=(seeds or {}).get(reg.id, None if seeds else reg.seed))
            elif m.status == 'bye':
                add(m.id, slot, 'bye')
            else:
                add(m.id, slot, 'open')
    MatchSlot.objects.bulk_create(new)
    return len(new)


def backfill(matches):
    """Slots for matches drawn before P1a, read off their pointers and seats."""
    return complete(matches)


def disagreements(matches):
    """[(match id, slot, reason)] wherever the slots and the pointers part.

    Every match has exactly two slots; every pointer has its slot row; every
    winner or loser slot has the pointer that fills it. A reset slot answers
    for the first grand final's pointer.
    """
    from .models import BracketMatch, MatchSlot
    ids = [m.id if hasattr(m, 'id') else m for m in matches]
    wanted = set(ids)
    rows = {}
    for row in MatchSlot.objects.filter(match_id__in=ids):
        rows.setdefault(row.match_id, {})[row.slot] = row
    out = []
    for mid in ids:
        count = len(rows.get(mid, {}))
        if count != 2:
            out.append((mid, 0, 'slots: %d, expected 2' % count))

    expected = {}
    feeders = BracketMatch.objects.filter(
        Q(winner_to_match_id__in=ids) | Q(loser_to_match_id__in=ids)).values(
        'id', 'winner_to_match_id', 'winner_to_slot', 'loser_to_match_id', 'loser_to_slot')
    for f in feeders:
        for outcome in OUTCOMES:
            target = f['%s_to_match_id' % outcome]
            if target in wanted:
                expected[(target, f['%s_to_slot' % outcome] or 1)] = (outcome, f['id'])

    for (target, slot), (outcome, source) in expected.items():
        row = rows.get(target, {}).get(slot)
        if row is None:
            out.append((target, slot, 'no slot for the %s of match %s' % (outcome, source)))
        elif row.kind == 'reset' and outcome == 'winner' and row.source_match_id == source:
            continue
        elif (row.kind, row.source_match_id) != (outcome, source):
            out.append((target, slot, 'the slot says %s of %s, the bracket says %s of %s'
                        % (row.kind, row.source_match_id, outcome, source)))
    for mid, slots in rows.items():
        for slot, row in slots.items():
            if row.kind in OUTCOMES and expected.get((mid, slot)) != (row.kind, row.source_match_id):
                out.append((mid, slot, 'the slot says %s of %s and no match sends anybody there'
                            % (row.kind, row.source_match_id)))
    return sorted(out)


def fill(stage, line):
    """Put what `line` sent into the seats of `stage` that wait on it (P1d).

    A seat waits for one place of one group; the line's sent rows come place by
    place, group by group, so counting each group's rows from the line's first
    place names the place each row fills. A seat whose place never comes (a
    short group) stays empty, and its match resolves as a walkover once the
    other side is known.
    """
    from .models import BracketMatch, MatchSlot
    grouped = (line.source.groups or 0) > 1
    counters, by_seat = {}, {}
    for row in (line.sent or []):
        group = row.get('group') if grouped or line.group is not None else None
        place = counters.get(group, line.place_from)
        counters[group] = place + 1
        by_seat[(group, place)] = int(row['registration_id'])
    touched = set()
    for slot in MatchSlot.objects.filter(match__stage=stage, kind='place', progression=line):
        rid = by_seat.get((slot.source_group, slot.source_place))
        if rid:
            BracketMatch.objects.filter(pk=slot.match_id).update(
                **{'participant_%d_id' % slot.slot: rid})
        touched.add(slot.match_id)
    for match in BracketMatch.objects.filter(pk__in=touched).order_by('round_number', 'match_number'):
        _settle(match)
    return len(touched)


def _decided(slot, match):
    """Whether a seat of a bracket drawn early will hold anybody new."""
    if getattr(match, 'participant_%d_id' % slot.slot):
        return True
    if slot.kind == 'bye':
        return True
    return slot.kind == 'place' and slot.progression is not None \
        and slot.progression.fired_at is not None


def _settle(match):
    """A round one match of a bracket drawn early, once both seats are known:
    played if both are filled, a walkover if one is, a dead bye if neither."""
    from . import stage_engine
    from .services import advance
    from django.utils import timezone
    if match.status not in ('scheduled',):
        return
    slots = list(match.slots.select_related('progression'))
    if len(slots) < 2 or not all(_decided(s, match) for s in slots):
        return
    present = [p for p in (match.participant_1, match.participant_2) if p is not None]
    if len(present) == 2:
        stage_engine.arm_check_in(match)
        return
    with advance.suspend_advance():
        match.status = 'bye'
        if present:
            match.winner = present[0]
            match.completed_at = timezone.now()
            match.save(update_fields=['status', 'winner', 'completed_at'])
        else:
            match.save(update_fields=['status'])
        advance.cascade(match)

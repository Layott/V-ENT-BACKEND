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


def _put(match, slot, kind, source=None, seed=None):
    from .models import MatchSlot
    MatchSlot.objects.update_or_create(
        match=match, slot=slot,
        defaults={'kind': kind, 'source_match': source, 'seed': seed})


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

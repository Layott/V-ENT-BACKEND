"""Read only rehearsal of the P1a slot conversion on real data (design section 17).

    cd /srv/vent/backend && ./venv/bin/python manage.py shell < tools/p1a_rehearsal.py

Writes nothing. The MatchSlot table does not exist before the deploy, and on
MySQL creating a table commits by itself, so this cannot be the P0 trick of a
transaction rolled back. Instead it applies the conversion's rules
(migration 0065, `match_slots.complete`) in memory to every match and reports
what the migration would write and anything it could not write cleanly:
two pointers claiming the same seat, or a match left without two slots.
"""
from collections import Counter

from django.db.models import Q

from vent_tournament.models import BracketMatch

matches = {m.id: m for m in BracketMatch.objects.all()}
seats = {}
clashes = []


def put(match_id, slot, kind, source=None):
    key = (match_id, slot)
    if key in seats:
        if seats[key] != (kind, source):
            clashes.append((match_id, slot, seats[key], (kind, source)))
        return
    seats[key] = (kind, source)


for f in BracketMatch.objects.filter(Q(winner_to_match__isnull=False) | Q(loser_to_match__isnull=False)):
    if f.winner_to_match_id in matches:
        if f.bracket_side == 'grand_final' and matches[f.winner_to_match_id].bracket_side == 'grand_final':
            put(f.winner_to_match_id, 1, 'reset', f.id)
            put(f.winner_to_match_id, 2, 'reset', f.id)
        else:
            put(f.winner_to_match_id, f.winner_to_slot or 1, 'winner', f.id)
    if f.loser_to_match_id in matches:
        put(f.loser_to_match_id, f.loser_to_slot or 1, 'loser', f.id)

for m in matches.values():
    for slot in (1, 2):
        if (m.id, slot) in seats:
            continue
        if getattr(m, 'participant_%d_id' % slot):
            put(m.id, slot, 'entrant')
        elif m.status == 'bye':
            put(m.id, slot, 'bye')
        else:
            put(m.id, slot, 'open')

short = [mid for mid in matches if not ((mid, 1) in seats and (mid, 2) in seats)]
print('matches', len(matches))
print('slots the conversion would write', len(seats))
print('by kind', dict(sorted(Counter(k for k, _s in seats.values()).items())))
print('seats two pointers claim', len(clashes), clashes[:10])
print('matches left without two slots', len(short), short[:10])

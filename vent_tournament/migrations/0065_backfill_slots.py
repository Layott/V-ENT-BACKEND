"""Give every bracket drawn before P1a its slots (design section 8 and 17).

The same rules as `match_slots.complete`, copied here because a migration must
not import live code: the pointers say which seats are a winner, a loser or a
reset grand final; the rest are entrants (with the registration's seed), byes
or open. Idempotent: a match that already has a slot keeps it.
"""
from django.db import migrations
from django.db.models import Q


def forwards(apps, schema_editor):
    BracketMatch = apps.get_model('vent_tournament', 'BracketMatch')
    MatchSlot = apps.get_model('vent_tournament', 'MatchSlot')
    TournamentRegistration = apps.get_model('vent_tournament', 'TournamentRegistration')

    ids = list(BracketMatch.objects.values_list('id', flat=True))
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        wanted = set(chunk)
        taken = set(MatchSlot.objects.filter(match_id__in=chunk).values_list('match_id', 'slot'))
        current = {m.id: m for m in BracketMatch.objects.filter(pk__in=chunk)}
        seeds = dict(TournamentRegistration.objects.filter(
            Q(matches_as_p1__in=chunk) | Q(matches_as_p2__in=chunk)).values_list('id', 'seed'))
        new = []

        def add(match_id, slot, kind, source_id=None, seed=None):
            if (match_id, slot) in taken:
                return
            taken.add((match_id, slot))
            new.append(MatchSlot(match_id=match_id, slot=slot, kind=kind,
                                 source_match_id=source_id, seed=seed))

        feeders = BracketMatch.objects.filter(
            Q(winner_to_match_id__in=chunk) | Q(loser_to_match_id__in=chunk)).values(
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
                reg_id = getattr(m, 'participant_%d_id' % slot)
                if reg_id:
                    add(m.id, slot, 'entrant', seed=seeds.get(reg_id))
                elif m.status == 'bye':
                    add(m.id, slot, 'bye')
                else:
                    add(m.id, slot, 'open')
        MatchSlot.objects.bulk_create(new)


def backwards(apps, schema_editor):
    apps.get_model('vent_tournament', 'MatchSlot').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0064_matchslot'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

#!/usr/bin/env python3
"""P0 rehearsal: what the ranking changes would do to the tournaments that exist.

Runs on a copy of the code against a database, prints one JSON object, and
writes nothing: everything happens inside a transaction that is rolled back,
including anything a reader creates on the way (league rules rows).

    python tools/p0_rehearsal.py OUT.json            # with whichever code is first on the path
    PYTHONPATH=/tmp/p0r/pkg python tools/p0_rehearsal.py OUT.json

Run it once with the code before P0 and once with P0, then compare the two
files with --diff OLD.json NEW.json. The output carries ids, slugs, ranks and
positions only: no names, no emails, nothing about a person.
"""
import json
import os
import sys


def collect():
    # The project, last on the path, so a copy of vent_tournament given
    # first through PYTHONPATH is the one imported.
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
    django.setup()
    from django.db import transaction

    import vent_tournament
    from vent_tournament import stage_engine
    from vent_tournament.models import Tournament, TournamentStage
    from vent_tournament.services import advance

    out = {'code': os.path.dirname(vent_tournament.__file__), 'stages': {}, 'finals': {}, 'errors': []}
    with transaction.atomic():
        for stage in TournamentStage.objects.exclude(drawn_at=None).select_related('tournament'):
            try:
                rows = stage_engine.standings(stage)
            except Exception as exc:                       # noqa: BLE001
                out['errors'].append('stage %s: %s' % (stage.id, type(exc).__name__))
                continue
            out['stages'][str(stage.id)] = {
                'tournament': stage.tournament.slug or str(stage.tournament_id),
                'format': stage.format,
                'rows': [[r['registration_id'], r.get('rank'), r.get('group')] for r in rows],
            }
        for t in Tournament.objects.filter(status='completed'):
            stored = dict(t.registrations.exclude(final_position=None)
                          .values_list('id', 'final_position'))
            try:
                if t.stages.exists():
                    stage_engine.assign_final_positions(t)
                else:
                    advance.assign_final_positions(t)
            except Exception as exc:                       # noqa: BLE001
                out['errors'].append('tournament %s: %s' % (t.tournament_id, type(exc).__name__))
                continue
            again = dict(t.registrations.exclude(final_position=None)
                         .values_list('id', 'final_position'))
            out['finals'][str(t.tournament_id)] = {
                'slug': t.slug or '', 'bracket_type': t.bracket_type, 'staged': t.stages.exists(),
                'stored': {str(k): v for k, v in stored.items()},
                'recomputed': {str(k): v for k, v in again.items()},
            }
        transaction.set_rollback(True)
    return out


def diff(old_path, new_path):
    old = json.load(open(old_path))
    new = json.load(open(new_path))
    print('old code:', old['code'])
    print('new code:', new['code'])
    changed_stages = []
    for sid, row in new['stages'].items():
        before = old['stages'].get(sid)
        if before is None:
            continue
        if [r[:2] for r in before['rows']] != [r[:2] for r in row['rows']]:
            changed_stages.append((sid, row['tournament'], row['format'], before['rows'], row['rows']))
    print('stages compared: %d, standings changed: %d' % (len(new['stages']), len(changed_stages)))
    for sid, slug, fmt, a, b in changed_stages:
        print('  stage %s (%s, %s)' % (sid, slug, fmt))
        print('    before', [r[:2] for r in a])
        print('    after ', [r[:2] for r in b])
    changed_finals = []
    for tid, row in new['finals'].items():
        before = old['finals'].get(tid)
        if before is None:
            continue
        if before['recomputed'] != row['recomputed']:
            changed_finals.append((tid, row, before))
    print('completed tournaments compared: %d, final places changed: %d' % (len(new['finals']), len(changed_finals)))
    for tid, row, before in changed_finals:
        print('  tournament %s (%s, %s%s)' % (tid, row['slug'], row['bracket_type'], ', staged' if row['staged'] else ''))
        print('    stored (paid by)', sorted(row['stored'].values()))
        print('    old code        ', sorted(before['recomputed'].values()))
        print('    new code        ', sorted(row['recomputed'].values()))
    print('errors old: %s' % old['errors'])
    print('errors new: %s' % new['errors'])


if __name__ == '__main__':
    if sys.argv[1:2] == ['--diff']:
        diff(sys.argv[2], sys.argv[3])
    else:
        json.dump(collect(), open(sys.argv[1], 'w'))
        print('written', sys.argv[1])

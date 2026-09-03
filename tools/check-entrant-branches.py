# -*- coding: utf-8 -*-
"""Nothing decides what a side is called by hand.

A tournament entrant has three kinds now: a club, a lone player, and a squad
assembled for one tournament out of people from several clubs. There is one
accessor for all three, `TournamentRegistration.entrant` and `.entrant_name`.

On 3 September 2026 squads were added and FOUR places kept their own copy of
the old two-way branch:

    if reg.team_id: ... elif reg.user_id: ... else: ''

  * `league._entrant_name`  every nation in a Rivalry Series table read
                            "Entrant 3" instead of "Nigeria"
  * `bracket._seat_players` every seat of every nation was empty, so the player
                            table had no rows and the results desk could not
                            say who was playing
  * `bracket._display_name` a squad sorted as the empty string
  * `mvp.side_name`         a squad's players had no side in the stats

All four were invisible to the whole test suite, because each was internally
consistent and nothing had a squad in it. Four in one commit is not a slip, it
is a shape, so this catches the shape.

    python tools/check-entrant-branches.py
    python tools/check-entrant-branches.py --self-test
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)

#: Where a registration is read. Tests are excluded: a test asserting the
#: branch behaviour is doing its job.
APPS = ['vent_tournament', 'vent_event', 'vent_auth']

#: `reg.team_id`, `registration.team_id`, `r.team_id` and so on.
TEAM_ID = re.compile(r'\b([a-z_]*reg[a-z_]*|r)\.team_id\b')
USER_ID = re.compile(r'\b([a-z_]*reg[a-z_]*|r)\.user_id\b')
SQUAD_ID = re.compile(r'\bsquad_id\b|\bsquad\b')

#: Lines that are allowed to name team_id: the accessor itself, a database
#: filter, and a fallback that runs only after `entrant_name` came back empty.
ALLOWED = re.compile(
    r'entrant_name|entrant\b|objects\.|filter\(|exclude\(|values_list|'
    r'unique_together|update_fields|select_related|prefetch_related')


def files():
    for app in APPS:
        root = os.path.join(BACKEND, app)
        for folder, _dirs, names in os.walk(root):
            if 'migrations' in folder or '__pycache__' in folder:
                continue
            for name in names:
                if not name.endswith('.py') or name.startswith('tests'):
                    continue
                yield os.path.join(folder, name)


def findings_in(source, path='<source>'):
    """A function that branches on team_id and user_id and never on a squad."""
    out = []
    blocks = re.split(r'\n(?=def |    def )', source)
    for block in blocks:
        if not (TEAM_ID.search(block) and USER_ID.search(block)):
            continue
        if SQUAD_ID.search(block):
            continue
        # Every mention of team_id is on an allowed line: a query, or a
        # fallback under the shared accessor.
        lines = [ln for ln in block.split('\n') if TEAM_ID.search(ln)]
        if lines and all(ALLOWED.search(ln) for ln in lines):
            continue
        name = re.match(r'\s*def ([a-zA-Z_0-9]+)', block)
        out.append({'file': path, 'func': name.group(1) if name else '?'})
    return out


# --------------------------------------------------------------- self-test

BAD = '''
def side_name(registration):
    if registration.team_id:
        return registration.team.team_name
    if registration.user_id:
        return registration.user.username
    return ''
'''

GOOD_ACCESSOR = '''
def side_name(registration):
    return getattr(registration, 'entrant_name', '') or ''
'''

GOOD_HANDLES_SQUAD = '''
def seat_players(registration, seats):
    if registration.squad_id:
        return [m.user for m in registration.squad.members.all()[:seats]]
    if registration.user_id:
        return [registration.user]
    if registration.team_id:
        return []
    return []
'''

GOOD_QUERY_ONLY = '''
def rows(tournament):
    return Registration.objects.filter(tournament=tournament).exclude(
        team_id=None).values_list('user_id', flat=True)
'''

GOOD_FALLBACK_UNDER_ACCESSOR = '''
def entrant_name(reg):
    name = getattr(reg, 'entrant_name', '')
    if name:
        return name
    if reg.team_id:
        return reg.team.team_name
    return reg.user.username
'''


def self_test():
    cases = [
        ('a hand-built two-way branch', BAD, 1),
        ('the shared accessor', GOOD_ACCESSOR, 0),
        ('a branch that handles a squad', GOOD_HANDLES_SQUAD, 0),
        ('a database query, not a branch', GOOD_QUERY_ONLY, 0),
        ('a fallback under the accessor', GOOD_FALLBACK_UNDER_ACCESSOR, 0),
    ]
    bad = 0
    for what, source, expected in cases:
        got = len(findings_in(source))
        if got != expected:
            print('SELF-TEST %s: expected %d, got %d' % (what, expected, got))
            bad += 1
        else:
            print('ok: %s -> %d' % (what, got))
    if bad:
        return 1
    print('self-test: catches the shape and does not flag the fixes for it')
    return 0


def main():
    if '--self-test' in sys.argv:
        return self_test()

    found = []
    for path in files():
        with open(path, encoding='utf-8', errors='replace') as handle:
            found.extend(findings_in(handle.read(),
                                     os.path.relpath(path, BACKEND)))

    if found:
        print('%d place(s) deciding what a side is by hand:\n' % len(found))
        for row in found:
            print('  %s  %s()' % (row['file'], row['func']))
        print('\nUse registration.entrant or .entrant_name. A branch that knows')
        print('teams and lone players silently answers wrong for a squad, which')
        print('is how a Rivalry Series table read "Entrant 3" for Nigeria.')
        return 1

    print('0 hand-built entrant branches')
    return 0


if __name__ == '__main__':
    sys.exit(main())

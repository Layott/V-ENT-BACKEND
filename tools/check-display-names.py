#!/usr/bin/env python3
"""Where code reads `entrant_name`, a screen may show a username (17 Oct 2026).

`TournamentRegistration.entrant_name` is the account's username for a lone
player. Every screen shows a person by `match_shape.names` (the full name, the
@username beside it). Twice in one day a new feature reached for the wrong one:
the list of what happened in a tournament (the stream ticker read "demo_amara"
beside a bracket reading "Amara Nwosu") and the feeds panel ("1. demo_amara").
A fault that happens twice gets a catcher (owner rule R28).

Counts every attribute read of `.entrant_name` in the backend apps outside
tests and migrations (comments and strings do not count; the property itself
does not count). Some reads are right (an export column, a lookup key), so it
does not fail: the debt ledger holds the count, and a new read makes it rise,
which blocks the commit. Use `match_shape.names(reg)[0]` for anything shown.

    python tools/check-display-names.py
    python tools/check-display-names.py --self-test
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = ('vent_tournament', 'vent_auth', 'vent_event', 'vent_partners')
ATTR = 'entrant_name'


def reads(source, filename='<source>'):
    """Line numbers of each `.entrant_name` attribute read in `source`."""
    tree = ast.parse(source, filename)
    return sorted(node.lineno for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute) and node.attr == ATTR)


def files():
    for app in APPS:
        root = os.path.join(REPO, app)
        for here, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ('migrations', '__pycache__', 'tests')]
            for name in names:
                if name.endswith('.py') and not name.startswith('tests'):
                    yield os.path.join(here, name)


def self_test():
    cases = [
        ('a read counts', 'name = reg.entrant_name\n', 1),
        ('a comment does not', '# reg.entrant_name was the username\nx = 1\n', 0),
        ('a string does not', "key = 'entrant_name'\n", 0),
        ('the property itself does not', 'class R:\n    @property\n    def entrant_name(self):\n        return 1\n', 0),
        ('two reads count twice', 'a = r.entrant_name\nb = s.participant_1.entrant_name\n', 2),
        ('a getattr by name does not', "x = getattr(r, 'entrant_name')\n", 0),
    ]
    failed = 0
    for label, source, want in cases:
        got = len(reads(source))
        if got != want:
            failed += 1
            print('  FAIL %s: expected %d, got %d' % (label, want, got))
    print('%d self-test case(s), %d failed' % (len(cases), failed))
    return 1 if failed else 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    hits = []
    for path in files():
        with open(path, encoding='utf-8') as fh:
            for line in reads(fh.read(), path):
                hits.append('%s:%d' % (os.path.relpath(path, REPO), line))
    for hit in hits:
        print('  ' + hit)
    print('%d place(s) reading entrant_name, where a screen may show a username' % len(hits))
    return 0


if __name__ == '__main__':
    sys.exit(main())

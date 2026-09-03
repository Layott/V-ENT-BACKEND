# -*- coding: utf-8 -*-
"""One catalogue of tournament formats, across both repos.

The formats drifted twice, and both times the symptom was a tournament quietly
becoming a different kind of tournament:

  - `views.py` kept its own alias map beside `formats.py`. Three of the eight
    formats resolved through it to `single_elimination`, so a league created as
    an aggregate tie was STORED as a knockout. The organiser's rules panel then
    read "One loss and you are out" on a league, which is how it was found.
  - The wizard's picker offered five of the eight, and `swiss-system` resolved
    to nothing at all.

Neither is visible in a test of either repo alone, because each side is
internally consistent. The fault is the gap between them.

So: every key the frontend can produce must be a key the backend defines, and
every key the backend defines must be reachable from the frontend. A format the
server supports and the wizard never offers is a feature nobody can use; a key
the wizard sends that the server does not know becomes a silent fallback.

    python tools/check-format-catalogue.py
    python tools/check-format-catalogue.py --self-test
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FRONTEND = os.path.join(os.path.dirname(BACKEND), 'V-ENT-FRONTEND')
FORMAT_LABEL = os.path.join(FRONTEND, 'src', 'lib', 'formatLabel.js')


def backend_keys():
    """Every key `formats.py` defines, read from the source rather than Django.

    Read as text so this runs without a settings module or a database, which is
    what lets it be a pre-commit check rather than a test.
    """
    path = os.path.join(BACKEND, 'vent_tournament', 'formats.py')
    with open(path, encoding='utf-8') as handle:
        source = handle.read()
    return set(re.findall(r"^\s*key='([a-z0-9_]+)',", source, re.M))


def frontend_lists(source=None):
    """`FORMAT_KEYS` and the alias map from the frontend's one label list."""
    if source is None:
        with open(FORMAT_LABEL, encoding='utf-8') as handle:
            source = handle.read()

    keys = set()
    block = re.search(r'FORMAT_KEYS\s*=\s*\[(.*?)\]', source, re.S)
    if block:
        keys = set(re.findall(r"'([a-z0-9_]+)'", block.group(1)))

    aliases = {}
    alias_block = re.search(r'ALIASES\s*=\s*\{(.*?)\n\}', source, re.S)
    if alias_block:
        for left, right in re.findall(r"'([a-z0-9_-]+)'\s*:\s*'([a-z0-9_]+)'",
                                      alias_block.group(1)):
            aliases[left] = right
    return keys, aliases


def findings(back, front, aliases):
    out = []
    for key in sorted(front - back):
        out.append('the frontend can send %r and the backend does not define it'
                   % key)
    for key in sorted(back - front):
        out.append('the backend defines %r and no frontend picker offers it'
                   % key)
    for alias, target in sorted(aliases.items()):
        if target not in back:
            out.append('the alias %r points at %r, which the backend does not '
                       'define' % (alias, target))
    return out


# --------------------------------------------------------------- self-test

_FIXTURE_GOOD = """
export const FORMAT_KEYS = ['single_elimination', 'round_robin'];
const ALIASES = {
  'single-elimination': 'single_elimination',
  'swiss-system': 'round_robin',
};
"""

_FIXTURE_MISSING = """
export const FORMAT_KEYS = ['single_elimination'];
const ALIASES = {
};
"""

_FIXTURE_INVENTED = """
export const FORMAT_KEYS = ['single_elimination', 'round_robin', 'pyramid'];
const ALIASES = {
};
"""

_FIXTURE_DEAD_ALIAS = """
export const FORMAT_KEYS = ['single_elimination', 'round_robin'];
const ALIASES = {
  'swiss-system': 'swiss',
};
"""


def self_test():
    back = {'single_elimination', 'round_robin'}
    cases = [
        ('both sides agree', _FIXTURE_GOOD, 0),
        ('a format the wizard never offers', _FIXTURE_MISSING, 1),
        ('a key the backend does not define', _FIXTURE_INVENTED, 1),
        ('an alias pointing at nothing', _FIXTURE_DEAD_ALIAS, 1),
    ]
    bad = 0
    for what, source, expected in cases:
        front, aliases = frontend_lists(source)
        got = len(findings(back, front, aliases))
        if got != expected:
            print('SELF-TEST %s: expected %d, got %d' % (what, expected, got))
            bad += 1
        else:
            print('ok: %s -> %d' % (what, got))
    if bad:
        return 1
    print('self-test: catches drift in both directions and a dead alias')
    return 0


def main():
    if '--self-test' in sys.argv:
        return self_test()

    if not os.path.exists(FORMAT_LABEL):
        print('cannot find %s' % FORMAT_LABEL)
        return 1

    back = backend_keys()
    front, aliases = frontend_lists()
    if not back or not front:
        print('read %d backend and %d frontend keys; one of the lists moved'
              % (len(back), len(front)))
        return 1

    problems = findings(back, front, aliases)
    if problems:
        print('%d disagreement(s) between the two format catalogues:\n'
              % len(problems))
        for line in problems:
            print('  - %s' % line)
        print('\nA key one side does not know becomes a silent fallback, and a '
              'tournament\n  quietly becomes a different kind of tournament.')
        return 1

    print('%d formats, and both repos agree on all of them' % len(back))
    return 0


if __name__ == '__main__':
    sys.exit(main())

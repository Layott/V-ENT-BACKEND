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
LEAGUE_SETUP = os.path.join(
    FRONTEND, 'src', 'components', 'create-tournament-component',
    'format-participants', 'league-setup', 'LeagueSetup.js')


def backend_keys():
    """Every key `formats.py` defines, read from the source rather than Django.

    Read as text so this runs without a settings module or a database, which is
    what lets it be a pre-commit check rather than a test.
    """
    path = os.path.join(BACKEND, 'vent_tournament', 'formats.py')
    with open(path, encoding='utf-8') as handle:
        source = handle.read()
    return set(re.findall(r"^\s*key='([a-z0-9_]+)',", source, re.M))


def backend_table_keys(source=None):
    """The formats decided by a TABLE rather than by a bracket.

    Third occurrence of this drift class, so it gets a check rather than a
    sentence. `LeagueSetup` asks the questions only a league has: what a win is
    worth, what separates two sides level on points, and how many players a
    side fields inside one fixture. It decides that from its own list, and a
    table format missing from that list is a league created with no points
    system at all.
    """
    if source is None:
        path = os.path.join(BACKEND, 'vent_tournament', 'formats.py')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
    table = set()
    for block in source.split('Format(')[1:]:
        key = re.search(r"key='([a-z0-9_]+)'", block)
        advancement = re.search(r"advancement='([a-z_]+)'", block)
        if key and advancement and advancement.group(1) == 'table':
            table.add(key.group(1))
    return table


def frontend_table_keys(source=None, aliases=None):
    """`TABLE_FORMATS` from the wizard's league step, resolved to catalogue keys."""
    if source is None:
        with open(LEAGUE_SETUP, encoding='utf-8') as handle:
            source = handle.read()
    block = re.search(r'TABLE_FORMATS\s*=\s*new Set\(\[(.*?)\]\)', source, re.S)
    if not block:
        return set()
    aliases = aliases or {}
    out = set()
    for raw in re.findall(r"'([a-z0-9_-]+)'", block.group(1)):
        slug = raw.replace('-', '_')
        out.add(aliases.get(slug, slug))
    return out


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
        # The key may be quoted or bare. `formatLabel.js` writes it bare, so
        # the quoted-only pattern this used to have read ZERO aliases from the
        # real file and the dead-alias rule only ever fired on its own fixture.
        for left, right in re.findall(r"'?([a-z0-9_-]+)'?\s*:\s*'([a-z0-9_]+)'",
                                      alias_block.group(1)):
            aliases[left] = right
    return keys, aliases


def findings(back, front, aliases, back_table=None, front_table=None):
    out = []
    if back_table is not None and front_table is not None:
        for key in sorted(back_table - front_table):
            out.append('%r is decided by a table and the league step does not '
                       'know it, so it asks no points or tie-break questions'
                       % key)
        for key in sorted(front_table - back_table):
            out.append('the league step treats %r as a table format and the '
                       'catalogue does not' % key)
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


_FIXTURE_LEAGUE_GOOD = """
const TABLE_FORMATS = new Set(['round-robin', 'round_robin', 'ladder']);
"""

_FIXTURE_LEAGUE_MISSING = """
const TABLE_FORMATS = new Set(['round_robin']);
"""

_FIXTURE_LEAGUE_EXTRA = """
const TABLE_FORMATS = new Set(['round_robin', 'ladder', 'single_elimination']);
"""

_FORMATS_FIXTURE = """
    'single_elimination': Format(
        key='single_elimination',
        advancement='knockout',
    ),
    'round_robin': Format(
        key='round_robin',
        advancement='table',
    ),
    'ladder': Format(
        key='ladder',
        advancement='table',
    ),
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
    back_table = backend_table_keys(_FORMATS_FIXTURE)
    if back_table != {'round_robin', 'ladder'}:
        print('SELF-TEST reading table formats: got %r' % (back_table,))
        bad += 1
    else:
        print('ok: table formats read from the catalogue -> %d' % len(back_table))

    table_cases = [
        ('the league step knows every table format', _FIXTURE_LEAGUE_GOOD, 0),
        ('a table format the league step misses', _FIXTURE_LEAGUE_MISSING, 1),
        ('a knockout treated as a league', _FIXTURE_LEAGUE_EXTRA, 1),
    ]
    for what, source, expected in table_cases:
        front_table = frontend_table_keys(source, aliases={})
        got = len(findings(back_table, back_table, {}, back_table, front_table))
        if got != expected:
            print('SELF-TEST %s: expected %d, got %d' % (what, expected, got))
            bad += 1
        else:
            print('ok: %s -> %d' % (what, got))

    if bad:
        return 1
    print('self-test: catches drift in both directions, a dead alias, and a '
          'league step that disagrees about which formats are tables')
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

    problems = findings(back, front, aliases,
                        backend_table_keys(), frontend_table_keys(aliases=aliases))
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

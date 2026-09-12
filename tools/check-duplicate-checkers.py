#!/usr/bin/env python3
"""One copy of each checker, in the repo that is version controlled.

CEO, 4 September 2026: "i thought a check was built for frontend so that any
backend design that needs a frontend ui, gets the ui built. we hvae seen this
eror like 3 times now. why is it still happening?"

Part of the answer was that there were TWO copies of `endpoint-callers.py`:
one at the workspace root and one in `V-ENT-BACKEND/tools/`. They had drifted
by a day and a half. The documentation named both, so which answer you got
depended on which directory you happened to be in, and an improvement made to
one was invisible from the other.

The workspace root is not a git repository. Anything kept only there is lost
with the machine, so the backend repo is the only place a checker may live.
Files at the root are forwarders: six lines that run the real one.

    python V-ENT-BACKEND/tools/check-duplicate-checkers.py
    python V-ENT-BACKEND/tools/check-duplicate-checkers.py --self-test

This is the "a fault that happens twice gets a catcher" rule applied to the
catchers themselves. The same-list-in-two-places fault has now been recorded
four times in this codebase: the format catalogue, the event console tabs, the
five label maps, and this.
"""

import argparse
import os
import sys


def _workspace_root(start):
    here = os.path.abspath(start)
    for _ in range(6):
        here = os.path.dirname(here)
        if (os.path.isdir(os.path.join(here, 'V-ENT-BACKEND'))
                and os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND'))):
            return here
    raise SystemExit('cannot find the workspace root from ' + start)


ROOT = _workspace_root(__file__)
CANONICAL = os.path.join(ROOT, 'V-ENT-BACKEND', 'tools')
ROOT_TOOLS = os.path.join(ROOT, 'tools')

#: A forwarder is short and says so. Anything longer at the root that shares a
#: name with a real checker is a second implementation.
FORWARDER_LINES = 12
FORWARDER_MARK = '_forward'


def is_forwarder(path):
    """Whether a root file just runs the real one.

    Two signals, and both are needed. Length alone would call a small genuine
    checker a forwarder; the marker alone would be satisfied by a file that
    imports the helper and then goes on to do its own thing.
    """
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
    except OSError:
        return False
    lines = [line for line in text.splitlines()
             if line.strip() and not line.strip().startswith('#')]
    return FORWARDER_MARK in text and len(lines) <= FORWARDER_LINES


def duplicates(canonical_dir=None, root_dir=None):
    """Root files that share a name with a real checker and are not forwarders."""
    canonical_dir = canonical_dir or CANONICAL
    root_dir = root_dir or ROOT_TOOLS
    if not os.path.isdir(root_dir):
        return []

    real = {name for name in os.listdir(canonical_dir)
            if name.endswith(('.py', '.mjs')) and not name.startswith('_')}

    found = []
    for name in sorted(os.listdir(root_dir)):
        if name not in real:
            continue
        path = os.path.join(root_dir, name)
        if not os.path.isfile(path):
            continue
        if not is_forwarder(path):
            found.append(name)
    return found


SELF_TEST_FILES = {
    'forwarder': ('#!/usr/bin/env python3\n'
                  '"""Forwarder."""\n'
                  'import os, sys\n'
                  'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
                  'from _forward import forward\n'
                  'forward(__file__)\n'),
    'second copy': ('#!/usr/bin/env python3\n'
                    '"""A whole second implementation."""\n'
                    + 'x = 1\n' * 40),
    'a forwarder that grew a tail': (
        'import os, sys\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from _forward import forward\n'
        + 'do_something_extra()\n' * 20
        + 'forward(__file__)\n'),
}


def self_test():
    """Prove it tells a forwarder from a second copy, in a temporary tree."""
    import shutil
    import tempfile

    bad = 0
    work = tempfile.mkdtemp(prefix='vent-dupe-')
    try:
        canonical = os.path.join(work, 'canonical')
        rooted = os.path.join(work, 'root')
        os.makedirs(canonical)
        os.makedirs(rooted)
        with open(os.path.join(canonical, 'check-thing.py'), 'w',
                  encoding='utf-8') as handle:
            handle.write('# the real one\n')

        for label, body in SELF_TEST_FILES.items():
            with open(os.path.join(rooted, 'check-thing.py'), 'w',
                      encoding='utf-8') as handle:
                handle.write(body)
            found = duplicates(canonical, rooted)
            want = label != 'forwarder'
            ok = bool(found) == want
            bad += 0 if ok else 1
            print('%s  %s' % ('ok  ' if ok else 'FAIL', label))

        # A root file with no twin in the canonical directory is somebody's own
        # tool, not a duplicate, and must never be reported.
        os.remove(os.path.join(rooted, 'check-thing.py'))
        with open(os.path.join(rooted, 'gate-run.py'), 'w',
                  encoding='utf-8') as handle:
            handle.write('# only exists at the root\n' * 30)
        ok = duplicates(canonical, rooted) == []
        bad += 0 if ok else 1
        print('%s  a root-only tool is not a duplicate' % ('ok  ' if ok else 'FAIL'))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    total = len(SELF_TEST_FILES) + 1
    print('\n%d of %d fixtures behaved.' % (total - bad, total))
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    found = duplicates()
    if found:
        print('%d checker(s) exist twice. The root copy is not version '
              'controlled and will drift:\n' % len(found))
        for name in found:
            print('  tools/%s  is a second copy of  V-ENT-BACKEND/tools/%s'
                  % (name, name))
        print('\nKeep the backend copy. Replace the root one with a forwarder:')
        print('  see tools/_forward.py')
        return 1

    print('One copy of each checker. No drift.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

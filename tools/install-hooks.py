#!/usr/bin/env python3
"""Put the catchers in front of every commit, in both repos.

CEO, 7 September 2026: "Add an update to make it that all checkers are seen each
time they submit something... if checkers just report the issues and those
issues are not acted upon as they are seen, then what is the point?"

The answer to their question is: there was no point, and `check-seo` proved it.
It sat at 60 problems for weeks. `check-all` printed the number every single
time somebody ran it, and the header of that file has claimed since the day it
was written that "a rising number is a regression" while nothing checked whether
it rose.

Two things had to change, and only one of them is this file:

  1. `check-all` now RECORDS every debt count in `tools/debt-ledger.json`, fails
     when one rises, and names any that has not moved in a week. A number with a
     history is a number somebody can be held to.

  2. This installs a `pre-commit` hook in both repos so the whole table is in
     front of whoever is committing, every time, without anybody choosing to
     look. That is the "seen each time they submit something" half.

    python tools/install-hooks.py            install into both repos
    python tools/install-hooks.py --remove   take them out again

## What the hook does and does not do

It runs the full table and shows it. It BLOCKS on a blocking breach or on debt
that went up, because both mean something was just broken. It does not block on
debt that merely exists, because a check that always fails is a check people
learn to skip with `--no-verify`, and then the blocking ones get skipped with
it. That trade-off is written down in check-all's own header and this respects
it.

It is deliberately not a wall in front of every commit. It is a mirror.
"""
import os
import stat
import subprocess
import sys

HOOK = r'''#!/bin/sh
# Installed by V-ENT-BACKEND/tools/install-hooks.py
#
# Every catcher, in front of every commit. See that file for why.
echo ""
echo "V-ENT catchers ------------------------------------------------------"
python "%s" --record
status=$?
echo "---------------------------------------------------------------------"
if [ $status -ne 0 ]; then
  echo ""
  echo "Commit stopped: a blocking catcher broke, or debt went up."
  echo "Fix it, or commit with --no-verify and say why in the message."
  echo ""
fi
exit $status
'''


def repos(root):
    for name in ('V-ENT-BACKEND', 'V-ENT-FRONTEND'):
        path = os.path.join(root, name, '.git', 'hooks')
        if os.path.isdir(path):
            yield name, path
        else:
            print('  %-16s no .git/hooks, skipped' % name)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    checker = os.path.join(here, 'check-all.py')
    root = os.path.dirname(os.path.dirname(here))

    remove = '--remove' in sys.argv
    done = 0

    for name, hooks in repos(root):
        target = os.path.join(hooks, 'pre-commit')
        if remove:
            if os.path.exists(target):
                os.remove(target)
                print('  %-16s hook removed' % name)
            else:
                print('  %-16s nothing to remove' % name)
            continue

        with open(target, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(HOOK % checker.replace('\\', '/'))
        os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC)
        print('  %-16s pre-commit installed' % name)
        done += 1

    if not remove and done:
        print('')
        print('Every commit in either repo now shows the full catcher table,')
        print('and stops on a blocking breach or on debt that went up.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

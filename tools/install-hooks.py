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
# The catchers of the tree being committed: a worktree carries its own
# tools/check-all.py. A frontend commit has none and uses the backend's.
# Git exports GIT_DIR to a hook, so a plain `git -C <other repo>` would answer
# about THIS repo: the lookups below clear it first. Without that a backend
# commit named itself as the frontend and a frontend commit never found its
# backend and fell back to the stale main checkout (9 October 2026).
top=$(git rev-parse --show-toplevel)
branch=$(git rev-parse --abbrev-ref HEAD)
if [ -f "$top/tools/check-all.py" ]; then
  checker="$top/tools/check-all.py"
  # A backend commit is judged with the frontend on the same branch, when a
  # worktree of it exists, so a pair is checked as a pair.
  fe=$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git -C "$top/../V-ENT-FRONTEND" worktree list --porcelain 2>/dev/null \
       | awk -v b="branch refs/heads/$branch" '/^worktree /{w=$2} $0==b{print w}')
  if [ -n "$fe" ]; then VENT_FRONTEND="$fe"; export VENT_FRONTEND; fi
else
  # A frontend commit is judged as itself: the tree being committed.
  VENT_FRONTEND="$top"; export VENT_FRONTEND
  # A frontend commit is judged with the backend on the same branch, when a
  # worktree of it exists (feature/x beside feature/x), else the main checkout.
  be=$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git -C "$top/../V-ENT-BACKEND" worktree list --porcelain 2>/dev/null \
       | awk -v b="branch refs/heads/$branch" '/^worktree /{w=$2} $0==b{print w}')
  if [ -n "$be" ] && [ -f "$be/tools/check-all.py" ]; then checker="$be/tools/check-all.py"; else checker="%s"; fi
fi
python "$checker" --record
status=$?
echo "---------------------------------------------------------------------"
if [ $status -ne 0 ]; then
  echo ""
  echo "Commit stopped: a blocking catcher broke, or debt went up. Fix it."
  echo "(The owner rules forbid --no-verify.)"
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

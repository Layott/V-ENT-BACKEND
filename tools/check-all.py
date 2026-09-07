"""Every rule that has a catcher, run in one pass.

    CEO, 2 September 2026: "EVERY SINGLE RULE MUST HAVE A CATCHER BUILT THAT
    SCANS ANY CODE BEING EDITED OR BUILT TO ENSURE IT FOLLOWS."

    python tools/check-all.py            everything
    python tools/check-all.py --blocking only the ones that must be clean

Two tiers, deliberately.

**Blocking** catchers are at zero today and must stay there. A new breach is
something somebody just wrote, and it is cheap to fix while they still
remember why they wrote it.

**Debt** catchers report real breaches that predate them, in numbers too large
to clear in one pass. They are still run, and their counts are printed, because
a number that goes UP is a regression even when it cannot yet go to zero. What
they must never do is block, because a check that always fails is a check
everybody learns to ignore, and then the blocking ones get ignored with it.

Move a catcher from debt to blocking the day its count reaches zero.
"""
import datetime
import json
import os
import re
import subprocess
import sys


def _workspace_root():
    """The directory holding V-ENT-BACKEND and V-ENT-FRONTEND.

    Walked for rather than computed from a fixed number of `dirname` calls, so
    this file works whether it sits in the workspace `tools/` or inside the
    backend repo's. It lives in the repo because the workspace root is not
    version controlled, and a checker that exists on one machine only is not a
    rule anybody else is held to.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND')):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        here = parent


ROOT = _workspace_root()

FRONTEND = os.path.join(ROOT, 'V-ENT-FRONTEND')

# (name, rule it enforces, working directory, command, blocking)
CATCHERS = [
    ('parity',
     'built for events or tournaments but not both',
     ROOT, [sys.executable, 'tools/check-parity.py'], True),

    ('one model',
     'one Tournament, one Event, one Team, one User',
     ROOT, [sys.executable, 'tools/check-one-model.py'], True),

    ('wizard round trip',
     'every setting the wizard sends survives create, edit and reopen',
     ROOT, [sys.executable, 'tools/check-wizard-roundtrip.py'], False),

    ('prose',
     'no em or en dashes, and no npm',
     ROOT, [sys.executable, 'tools/check-prose.py'], False),

    ('signed out',
     'a signed-out visitor never sees a control they cannot use',
     FRONTEND, ['node', 'scripts/check-signed-out.mjs'], True),

    ('control bytes',
     'no escape sequence turned into a literal control character',
     FRONTEND, ['node', 'scripts/check-control-bytes.mjs'], True),

    ('dangling refs',
     'no ref read but never attached',
     FRONTEND, ['node', 'scripts/check-dangling-refs.mjs'], True),

    ('css classes',
     'no undefined class on a control somebody has to press',
     FRONTEND, ['node', 'scripts/check-css-classes.mjs'], True),

    ('translation keys',
     'every key exists, in en, fr and pt',
     FRONTEND, ['node', 'scripts/check-keys.mjs'], True),

    ('dictionary parity',
     'en, fr and pt hold the same keys',
     FRONTEND, ['node', 'scripts/dict-parity.mjs'], True),

    ('avatars',
     'a name on screen can always show the face beside it',
     FRONTEND, ['node', 'scripts/check-avatars.mjs'], False),

    # Written on 30 August, never run by anything until 4 September. That is
    # the third time a catcher has sat on disk outside this table, and it is
    # the reason the table exists.
    ('user chips',
     'every name goes through UserChip, so the badge and the link come with it',
     FRONTEND, ['node', 'scripts/check-user-chips.mjs'], True),

    ('slugs',
     'no numeric id in an address a person can see',
     FRONTEND, ['node', 'scripts/check-slugs.mjs'], False),

    ('seo',
     'every public page can be found and read',
     FRONTEND, ['node', 'scripts/check-seo.mjs'], False),

    ('design bans',
     'no hairline borders, no glow, no vibecoded defaults',
     FRONTEND, ['node', 'scripts/check-design.mjs'], False),

    ('timing model',
     'every date renders in the reader own zone and chosen language',
     FRONTEND, ['node', 'scripts/check-datetime.mjs'], False),

    ('api paths',
     'every path the frontend fetches is one the backend serves',
     FRONTEND, ['node', 'scripts/check-api-paths.mjs'], True),

    ('timezone picker',
     'every zone is offered, and every date format value resolves',
     FRONTEND, ['node', 'scripts/check-timezone-picker.mjs'], True),

    ('live updates',
     'a refresh timer a re-render cannot tear down before it fires',
     FRONTEND, ['node', 'scripts/check-live-updates.mjs'], False),

    ('raw errors',
     'no developer exception is ever shown to a person',
     FRONTEND, ['node', 'scripts/check-raw-errors.mjs'], False),

    ('colour variables',
     'no undefined token, and --primary-bg is never a background',
     FRONTEND, ['node', 'scripts/check-css-vars.mjs'], True),

    # A ticket that has been given away gets a new code and the old one stops
    # resolving. Six endpoints resolve a ticket by code; the transferred answer
    # was added to one of them and the scanner still said "Not on the list",
    # because the scanner posts to a different endpoint. Twice in one hour on
    # 7 September, so it gets a catcher.
    # Five row numbers each had TWO rows on 7 September, an early "todo" and a
    # later "done", which left the register unable to answer what the state of
    # row 50 was. A register that cannot be read is not a register.
    ('ask register',
     'one row per ask, every row says what became of it',
     ROOT, ["python", "tools/check-inbox.py"], True),

    ('dead ticket codes',
     'a transferred code is named, not called unknown, at every door',
     ROOT, ["python", "tools/check-dead-codes.py"], True),
]


# ---------------------------------------------------------------------------
# The debt ledger
# ---------------------------------------------------------------------------
#
# CEO, 7 September 2026: "if checkers just report the issues and those issues
# are not acted upon as they are seen, then what is the point?"
#
# They are right, and `check-seo` proved it: it sat at 60 problems for weeks
# while `check-all` printed the number every time and nothing happened. The
# header of this file has said "a rising number is a regression" since the day
# it was written, and NOTHING CHECKED THAT EITHER. A rule nobody enforces is a
# rule, and a number nobody acts on is decoration.
#
# So the number is now recorded, and three things follow from the record:
#
#   1. A count that RISES fails, blocking or not. That is the promise the
#      header made and never kept.
#   2. A count that has not moved in `STALE_DAYS` is called out by name, with
#      how long it has been sitting there. "60 problems, unchanged for 14 days"
#      is a sentence somebody acts on; "60 problems" is not.
#   3. A count that FALLS is written back immediately, so the new, lower number
#      becomes the ceiling and the debt cannot quietly grow back.

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debt-ledger.json')

# How long a debt may sit at the same number before it is named as stuck.
STALE_DAYS = 7


def _count(line):
    """The number a catcher is reporting, or None.

    Catchers say their count in different words - "60 problem(s)", "22
    place(s)", "0 new" - so the first integer on the line is the honest
    reading, and a catcher whose line has no number is simply not tracked
    rather than guessed at.
    """
    m = re.search(r'[0-9]+', line or '')
    return int(m.group(0)) if m else None


def _load_ledger():
    try:
        with open(LEDGER, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_ledger(data):
    with open(LEDGER, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write('\n')



def compare(tracked, ledger, today):
    """What changed since the last run.

    `tracked` is (name, last_line, count) for EVERY checker, clean ones
    included at 0 - see the classifier in main() for why that matters.

    Returns (risen, stuck, fell, ledger). `ledger` is mutated and returned so
    the caller saves one object. `fell` carries None for a first sighting: it
    has to be saved, but there is nothing to announce.

    A risen count is deliberately NOT written back. The ceiling stays where it
    was so the next run fails too, until somebody actually brings it down.
    That is the promise the header makes and the reason this is not simply
    "record the latest number".
    """
    risen, stuck, fell = [], [], []
    for name, last, now in tracked:
        was = ledger.get(name)
        if was is None:
            ledger[name] = {'count': now, 'since': today, 'first_seen': today}
            fell.append(None)
            continue
        if now > was['count']:
            risen.append((name, was['count'], now, last))
        elif now < was['count']:
            fell.append((name, was['count'], now))
            ledger[name] = {'count': now, 'since': today,
                            'first_seen': was.get('first_seen', today)}
        else:
            days = (datetime.date.fromisoformat(today)
                    - datetime.date.fromisoformat(was['since'])).days
            if days >= STALE_DAYS:
                stuck.append((name, now, days, last))
    return risen, stuck, fell, ledger


def run(cwd, command):
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True,
                              text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, str(err)
    output = (done.stdout or '') + (done.stderr or '')
    return done.returncode, output.strip().split('\n')[-1] if output else ''


def main():
    only_blocking = '--blocking' in sys.argv

    failed_blocking = []
    debt = []
    # Every checker's number, clean ones included. See the note in the
    # classifier below for why a clean checker is recorded rather than skipped.
    tracked = []

    print('%-20s %-9s %s' % ('CATCHER', 'RESULT', 'LAST LINE'))
    print('-' * 96)

    for name, rule, cwd, command, blocking in CATCHERS:
        if only_blocking and not blocking:
            continue

        code, last = run(cwd, command)

        if code is None:
            state = 'ERROR'
            failed_blocking.append((name, rule, last))
        elif code == 0:
            state = 'clean'
            # Recorded at ZERO, which is the whole point. A checker that
            # reaches 0 used to drop out of the ledger entirely and freeze at
            # whatever it last failed with, so climbing back to that number
            # read as "unchanged" and twenty-one real breaches could return in
            # silence. The count is not PARSED here: exit 0 is the statement
            # that there are no faults, and it is exact where reading the
            # first integer off "650 route(s) checked, 0 fetch(es)" is a guess.
            tracked.append((name, last, 0))
        elif blocking:
            state = 'BREACH'
            failed_blocking.append((name, rule, last))
        else:
            state = 'debt'
            debt.append((name, last))
            n = _count(last)
            if n is not None:
                tracked.append((name, last, n))

        print('%-20s %-9s %s' % (name, state, last[:66]))

    print('')

    # ---------------------------------------------------------------- debt
    #
    # Recorded rather than merely printed. See the note above the ledger.
    ledger = _load_ledger()
    today = datetime.date.today().isoformat()

    risen, stuck, fell, ledger = compare(tracked, ledger, today)

    if '--record' in sys.argv or fell:
        _save_ledger(ledger)

    if debt:
        print('Debt. Every one of these is work somebody has to do:')
        for name, last in debt:
            was = ledger.get(name, {})
            since = was.get('since')
            age = ''
            if since:
                days = (datetime.date.today()
                        - datetime.date.fromisoformat(since)).days
                age = ' (unchanged for %d day%s)' % (days, '' if days == 1 else 's')
            print('  %-18s %s%s' % (name, last[:60], age))
        print('')

    dropped = [row for row in fell if row is not None]
    if dropped:
        print('Down since the last run, and the new number is now the ceiling:')
        for name, was, now in dropped:
            print('  %-18s %d -> %d' % (name, was, now))
        print('')

    if stuck:
        print('STUCK. These have not moved in %d days or more:' % STALE_DAYS)
        for name, now, days, last in stuck:
            print('  %-18s %s' % (name, last[:60]))
            print('  %-18s at %d for %d days' % ('', now, days))
        print('  Pick one and bring it down, or say out loud why it stays.')
        print('')

    if risen:
        print('DEBT WENT UP. This is a regression and it blocks:')
        for name, was, now, last in risen:
            print('  %-18s %d -> %d' % (name, was, now))
            print('  %-18s %s' % ('', last[:70]))
        print('')
        print('The ceiling was not moved, so this keeps failing until the number')
        print('comes back down. That is the whole point of recording it.')
        return 1

    if failed_blocking:
        print('BREACHES that must be fixed before this ships:')
        for name, rule, last in failed_blocking:
            print('  %s - %s' % (name, rule))
            print('      %s' % last)
        return 1

    print('Every blocking catcher is clean.')
    if debt:
        print('%d catcher(s) still carrying debt. None of it went up.' % len(debt))
    return 0


if __name__ == '__main__':
    sys.exit(main())

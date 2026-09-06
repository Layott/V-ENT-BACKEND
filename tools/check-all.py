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
import os
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
]


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
        elif blocking:
            state = 'BREACH'
            failed_blocking.append((name, rule, last))
        else:
            state = 'debt'
            debt.append((name, last))

        print('%-20s %-9s %s' % (name, state, last[:66]))

    print('')

    if debt:
        print('Debt, reported and not blocking. A rising number is a regression:')
        for name, last in debt:
            print('  %-18s %s' % (name, last))
        print('')

    if failed_blocking:
        print('BREACHES that must be fixed before this ships:')
        for name, rule, last in failed_blocking:
            print('  %s - %s' % (name, rule))
            print('      %s' % last)
        return 1

    print('Every blocking catcher is clean.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

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

    # The admin sidebar used to decide access from a `roles` array of its own,
    # ORed with the permission map, and the two disagreed in four places. This
    # also catches a finished admin page in no navigation list, which is what
    # /admin/kyc was for weeks.
    ('admin nav',
     'the admin sidebar and the admin API read one permission table',
     ROOT, [sys.executable, 'tools/check-admin-nav.py'], True),

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

    ('tap targets',
     'nothing pressable is under 44px on a phone',
     FRONTEND, ['node', 'scripts/check-tap-targets.mjs'], False),

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

    # Third occurrence on 8 September of "built on the organiser side, forgotten
    # on the buyer side". A group rate of 16 VC at four or more was charged by
    # the server while the panel said 20 x 4 = 80 and took 64; an early bird
    # price the serializer carries a comment about was never drawn; and an
    # access code tier could be bought by nobody, because `ticket_types` has
    # always read `?code=` and no screen had a box to type one into.
    ('offer surface',
     'every organiser setting has a screen the buyer can read it on',
     FRONTEND, ['node', 'scripts/check-offer-surface.mjs'], True),

    # Three times on the afternoon of 8 September the frontend answered
    # "Cannot find module './vendor-chunks/next-auth@4.24.13_next@14.2...'" for
    # everybody at once. `next.config.mjs` used a fixed `.next-dev` for
    # development and four of us were running dev servers on 3001, 3002, 3005
    # and 3007, all writing that one directory and overwriting each other's
    # chunks. The dev distDir now carries the port.
    #
    # Worth knowing: the error names webpack and next-auth and points at
    # neither. It was first blamed on `pnpm build`, which was wrong, and the
    # wrong diagnosis was passed to three agents before it was corrected.
    ('dev distdir',
     'two dev servers on one checkout cannot share a build directory',
     FRONTEND, ['node', 'scripts/check-dev-distdir.mjs'], True),

    # The cost of that fix, which nobody was paying: one build directory per
    # port anybody has ever run a dev server on, about a gigabyte each. Eight
    # had built up holding 5.6 GB before the CEO noticed them in Explorer.
    #
    # NOT blocking, because a full disk is not a reason to refuse a commit and
    # because deleting is the fix rather than a code change. It is debt, so the
    # number is on the table every time and cannot quietly grow.
    ('stale builds',
     'a dev build directory nobody is serving is deleted',
     FRONTEND, ['node', 'scripts/check-stale-builds.mjs'], False),

    # A page that can show "Loading..." for ever.
    #
    # Fourth occurrence of one fault: three admin pages in August with a bare
    # `await fetch`, and /admin/settings on 9 September, which caught the
    # exception and raised a TOAST. The toast is gone in four seconds and the
    # loading state is still there behind it.
    #
    # Debt rather than blocking, because 42 files share the shape and clearing
    # them is a pass of its own. The number is on the table every commit and
    # cannot rise.
    ('spinner for ever',
     'a failed load says so on the page, rather than spinning',
     FRONTEND, ['node', 'scripts/check-spinner-forever.mjs'], False),

    # The pnpm store, gutted. Third occurrence on 9 September, each within
    # seconds of building while a dev server was serving the same tree. The
    # error names a module, so it reads like a missing dependency and gets
    # treated as one; nothing in package.json changed.
    #
    # Blocking, because a damaged install means nothing else here can be
    # trusted, and the fix is four commands.
    ('pnpm store',
     'the install is whole, so a build can actually run',
     FRONTEND, ['node', 'scripts/check-pnpm-store.mjs'], True),

    # Where every link this machine builds points.
    #
    # Second time the value has been wrong: production carried a retired test
    # host in August and every emailed link 404d, and the local .env still
    # carried the same host on 9 September, so studio URLs and share links
    # built here pointed at nothing. Silent both ways, because nothing on the
    # machine that builds a link ever fetches it.
    ('frontend url',
     'links built here carry a host that exists',
     os.path.join(ROOT, 'V-ENT-BACKEND'), [sys.executable, 'tools/check-frontend-url.py'], True),

    # Backend code with no screen in front of it.
    #
    # This checker has existed for weeks and was never in this table, and it
    # only failed on endpoints that were NEW since a baseline. So it answered
    # "No new ones" while seventeen endpoints had no way in, and the CEO found
    # them by looking at production and asking. Debt rather than blocking,
    # because some of the seventeen are legacy duplicates that want deleting
    # rather than a screen, and the ledger stops the number rising while they
    # are worked through.
    ('endpoints with no screen',
     'every endpoint has a screen that can reach it, or a written reason',
     ROOT, [sys.executable, 'tools/endpoint-callers.py'], False),

    # The ledger reads a number off each line above, and on 8 September it was
    # reading the wrong one: 311 stylesheets scanned instead of 145 tap targets
    # broken. A checker that is not in this table is a checker nobody runs, so
    # the parser's own test sits in it, and the pre-commit hook therefore runs
    # it on every commit.
    ('ledger parse',
     'the ledger records what a catcher counted, not what it scanned',
     os.path.dirname(os.path.abspath(__file__)),
     [sys.executable, 'test-count-parse.py'], True),

    # Written 7 September and never run by anything, which is the third time
    # that has happened. It earned its place within the hour: fixing the parser
    # inverted an assertion inside it, and nothing would have said so.
    ('ledger writeback',
     'a fall becomes the ceiling, a rise fails and is not written back',
     os.path.dirname(os.path.abspath(__file__)),
     [sys.executable, 'test-ledger-writeback.py'], True),

    # Second occurrence of the class on 8 September: row 176 on the 7th found
    # 17 gates unmet for work that was done, and row 215 today found two more
    # whole files, 66 boxes between them, unticked while the models shipped.
    # Debt rather than blocking, because it reports boxes other people own and
    # a check that always fails is a check everybody learns to skip.
    ('stale gates',
     'a gate box unticked while its own check already passes',
     ROOT, [sys.executable, 'V-ENT-BACKEND/tools/check-stale-gates.py'], False),
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


# A standalone whole number. "44px", "390x844" and the "16016" glued into a
# word are not counts, so a digit run only counts when a word character sits on
# neither side of it. A decimal reads as its whole part, which no catcher prints
# today and which is recorded here so the next person knows rather than guesses.
_NUMBER = re.compile(r'(?<![\w.])(\d+)(?![\w])')

# The verb beside a number that means it is the SIZE OF THE SCAN. "311
# stylesheet(s) CHECKED" is how much was read, never how much is wrong.
# "read" is deliberately absent: "0 ref(s) read but never attached" is a fault
# count, and the word appears in it.
_SCANNED = re.compile(
    r'\b(checked|scanned|inspected|examined|defined|sends|considered'
    r'|walked|crawled|visited|in total)\b')

# A number introduced by one of these is a denominator: "across 5 file(s)",
# "0 of 14 case(s)". The thing being counted is on the other side of it.
_DENOMINATOR = {'across', 'of', 'in', 'out', 'from', 'over', 'within',
                'among', 'under', 'per', 'than'}

# Debt a catcher has baselined. It is real work, but it is not what this line
# is reporting as NEW, and adding it to the new count would make every
# baselined catcher look like it had just regressed.
#
# "already" was in this list for one revision and came straight back out. No
# catcher writes it, and it broke the first line written after the change:
# "10 gate box(es) unticked while their own check already passes" read as a
# baseline and reported nothing. A word nobody uses is a guess, and a guess in
# a classifier is the fault the calibration rule exists to stop.
_BASELINE = re.compile(r'\b(known|baseline|being worked down)\b')

# A number the catcher itself says is not a failure. check-datetime prints
# "0 number-formatting notes, which do not fail the build."
_NOT_DEBT = re.compile(r'\bdo(es)? not fail\b')


def _count(line):
    """The number a catcher is COUNTING on its summary line, or None.

    The old rule took the first integer on the line, and that was wrong for
    seven of the twenty-four catchers in the table, because a catcher usually
    says how much it read before it says how much is broken:

        311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone

    The first integer there is 311, so the ledger stored the ceiling as 311
    while the real debt was 145, and the number could have climbed by 166
    without a single run failing. That is the ledger not doing the one thing
    it exists to do.

    So every standalone number on the line is classified by the words around
    it and only the FAULT ones are counted:

      SCANNED      the phrase after it holds a scanning verb - "checked",
                   "scanned", "defined", "the wizard sends".
      DENOMINATOR  the word before it is a preposition - "across 0 file(s)".
      BASELINE     the phrase after it says "known" or "being worked down",
                   which is debt this catcher has already accepted.
      NOT DEBT     the catcher says in words that it does not fail the build.
      FAULT        everything else.

    The answer is the SUM of the fault numbers, because a line can report two
    independent faults - "0 em/en dash(es) and 0 npm command(s) outstanding" -
    and taking only the first would let the second climb unseen, which is the
    same hole one level down.

    A line with no fault number at all returns None and the catcher is simply
    not tracked, rather than recorded at a number nobody can defend.
    """
    line = line or ''
    hits = list(_NUMBER.finditer(line))
    if not hits:
        return None

    total = None
    # Once a line reaches its baseline, everything after it belongs to the
    # baseline. check-css-vars prints "0 new colour variable faults. 5 known:
    # 3 undefined-token, 2 primary-bg", and the 3 and the 2 are the breakdown
    # of the 5, not three separate faults.
    in_baseline = False

    for i, m in enumerate(hits):
        before = line[:m.start()].rstrip()
        words = before.split()
        preceding = words[-1].strip('.,;:()[]').lower() if words else ''
        after = line[m.end():hits[i + 1].start()] if i + 1 < len(hits) else line[m.end():]

        if in_baseline:
            continue
        if preceding in _DENOMINATOR:
            continue
        if _SCANNED.search(after):
            continue
        if _BASELINE.search(after):
            in_baseline = True
            continue
        if _NOT_DEBT.search(after):
            continue

        total = (total or 0) + int(m.group(1))

    return total


# A Node warning block is printed on stderr after the summary and carries a
# process id, so it looks like a numbered summary line and is not one.
_NODE_NOISE = re.compile(
    r'^\(node:\d+\)'
    r'|^\(Use `node'
    r'|^Reparsing as '
    r'|^To eliminate this warning'
    r'|\] Warning: ')


def _summary_line(output):
    """The line a catcher means as its summary.

    Not simply the last line. check-design ends with an indented breakdown of
    its baseline by rule, so the literal last line is "    15  glow" and
    reading it says the design debt is 15 when the line above says 119 known
    and 0 new. check-keys and dict-parity end with a Node module warning that
    carries the process id, which reads as a count and is not one.

    So: the last line that is not blank, not an indented detail row, and not
    part of a Node warning.
    """
    lines = (output or '').split('\n')
    for line in reversed(lines):
        if not line.strip():
            continue
        if line[:1].isspace():
            continue
        if _NODE_NOISE.search(line):
            continue
        return line.strip()
    return ''


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
    return done.returncode, _summary_line(output)


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

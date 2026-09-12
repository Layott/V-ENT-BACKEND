# -*- coding: utf-8 -*-
"""A gate box that is unticked while its own CHECK already passes.

CEO hard rule, 3 September: the second time a class of fault turns up, the fix
is not finished until a check exists that would have caught it.

This class has now turned up twice.

  * 7 September, inbox row 176: "17 gates read unmet for work that is done".
    Fifteen were ticked that day against a re-run of their own check.
  * 8 September, inbox row 215: GATES-EAFC-CARDS.md at 29 boxes and 0 ticked
    while `vent_cards` ships GameCard, LineupRules, Lineup and LineupSlot, and
    GATES-RUN-OF-SHOW.md at 37 and 0 while `RunSheet` ships.

Both times somebody finished the work and never went back to the ledger, and
both times nobody could tell from the ledger what was outstanding. A gate file
that is wrong in this direction is worse than no gate file, because it hides
the real remaining work inside a list of things already done.

## What it does, and what it deliberately does not

It re-runs the box's OWN `CHECK:` line. It never guesses from the code whether
something is built - that is the mistake this whole class is made of.

    python V-ENT-BACKEND/tools/check-stale-gates.py
    python V-ENT-BACKEND/tools/check-stale-gates.py --slow       also run the
                                                                 Django tests
    python V-ENT-BACKEND/tools/check-stale-gates.py --self-test

A box is reported when it is UNTICKED and its own check exits 0 and its
`EXPECT:` text is in the output. The answer is one of two things and a person
has to say which: either the work is built and the box wants ticking with that
output beside it, or the CHECK is the wrong one for the gate and wants
rewriting. Both are work; neither is something a script may decide.

Checks it will not run, and says so rather than pretending:

  * a browser walk, an emulator walk, `pnpm build`, an ssh, a curl at
    production. These are not cheap and not repeatable from here.
  * a Django test, unless `--slow`. Twenty-three of them at twenty seconds of
    startup each is four minutes, and a catcher that takes four minutes is a
    catcher people stop running.
  * anything not on the allowlist, because these commands come out of a
    markdown file and running arbitrary text would be its own fault class.

An environment failure is not counted as a genuine failure. Several gate files
write `python manage.py test`, and the system Python on this machine has no
Django; reading that as "the work is not built" would be exactly the wrong
conclusion, so it is reported apart.
"""
import glob
import io
import os
import re
import shutil
import subprocess
import sys


def _workspace_root():
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND')):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        here = parent


ROOT = _workspace_root()

BOX = re.compile(r'^- \[( |x|X)\]\s*(.*)$')
CHECK = re.compile(r'^\s*CHECK:\s*(.*)$')
EXPECT = re.compile(r'^\s*EXPECT:\s*(.*)$')

# A gate id is written either `**A1**` or `A1:` in these files. Both forms are
# in use and neither is worth normalising, because renaming somebody's gate ids
# to satisfy a parser is the same mistake as rewording their statuses.
GATE_ID = re.compile(r'^\*\*([A-Z][A-Za-z0-9.]*)\*\*|^([A-Z][A-Za-z0-9.]*):')

# Commands cheap enough to run every time. Matched against the command with
# any leading `cd <dir> &&` removed.
CHEAP = re.compile(
    r'^(grep|ls|wc|find|cat|echo)\b'
    r'|^node\s+(scripts|tools)/(check|dict)-'
    r'|^python\s+tools/'
    r'|^\S*python\S*\s+manage\.py\s+makemigrations\s+--check')

SLOW = re.compile(r'manage\.py\s+test\b')

# Named so the report can say WHY a box was not decided, rather than leaving a
# silent gap. A count nobody can account for is how a checker loses trust.
MANUAL = re.compile(
    r'pnpm\s+build|npm\s+|yarn\s'
    r'|\bcurl\b|\bssh\b|\bscp\b'
    r'|Chrome|chrome|emulator|adb\b|screenshot|measured in|walked|by hand')

ENV_FAILURE = re.compile(
    r"Couldn't import Django"
    r"|is not recognized as an internal or external command"
    r"|command not found"
    r"|No such file or directory")

# A Node warning block prints after a checker's summary and carries a process
# id, so the literal last line of a passing check is often "(Use `node
# --trace-warnings ...`)", which tells a reader nothing. Same reason
# check-all.py has to skip it before reading a count.
_NODE_NOISE = re.compile(
    r'^\(node:\d+\)'
    r'|^\(Use `node'
    r'|^Reparsing as '
    r'|^To eliminate this warning'
    r'|\] Warning: ')


def last_useful_line(output):
    """The line worth quoting back, not simply the last one."""
    for line in reversed((output or '').split('\n')):
        if not line.strip():
            continue
        if _NODE_NOISE.search(line):
            continue
        return line.strip()
    return ''


def _bash():
    """Git bash, by absolute path.

    `bash` on PATH here is a WSL relay that answers
    `execvpe(/bin/bash) failed`, so resolving the name is not enough.
    """
    for candidate in (r'C:\Program Files\Git\bin\bash.exe',
                      r'C:\Program Files\Git\usr\bin\bash.exe'):
        if os.path.exists(candidate):
            return candidate
    return shutil.which('bash')


def gate_files(root=ROOT):
    found = sorted(glob.glob(os.path.join(root, 'gates', '*.md')))
    found += sorted(glob.glob(os.path.join(root, 'GATES-*.md')))
    return found


def parse(path):
    """Every box in one gate file, with its check and its expectation."""
    text = io.open(path, encoding='utf-8').read()
    lines = text.split('\n')
    boxes = []
    for i, line in enumerate(lines):
        m = BOX.match(line)
        if not m:
            continue
        ticked = m.group(1).lower() == 'x'
        rest = m.group(2)
        idm = GATE_ID.match(rest)
        gid = (idm.group(1) or idm.group(2)) if idm else rest[:24]

        check = expect = ''
        for j in range(i + 1, len(lines)):
            if BOX.match(lines[j]):
                break
            if not check:
                cm = CHECK.match(lines[j])
                if cm:
                    check = cm.group(1).strip().strip('`').strip()
                    continue
            em = EXPECT.match(lines[j])
            if em and check and not expect:
                # The backticks are KEPT: they are what says "literal" to
                # passes(), and stripping them here is why a six word
                # literal was waved through as a sentence.
                expect = em.group(1).strip()
        boxes.append({'file': os.path.relpath(path, ROOT).replace('\\', '/'),
                      'id': gid, 'ticked': ticked,
                      'check': check, 'expect': expect})
    return boxes


def payload(command):
    """The command with its leading directory change taken off."""
    return re.sub(r'^\s*cd\s+\S+\s*&&\s*', '', command).strip()


def classify(command):
    if not command:
        return 'no check'
    body = payload(command)
    # A gate whose check IS this suite cannot be run from inside this suite.
    # `check-all` runs this checker, which would run `check-all` again, which
    # runs this checker: on 9 September that hit the 300 second timeout and
    # reported a BREACH on a file with nothing wrong with it. The suite decides
    # that box by running at all.
    if 'check-all' in body:
        return 'manual'
    if MANUAL.search(command):
        return 'manual'
    if SLOW.search(body):
        return 'slow'
    if CHEAP.match(body):
        return 'cheap'
    return 'unknown'


def run(command, timeout=300):
    bash = _bash()
    if not bash:
        return None, 'no bash'
    try:
        done = subprocess.run([bash, '-c', command], cwd=ROOT,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, str(err)
    return done.returncode, ((done.stdout or '') + (done.stderr or '')).strip()


def passes(code, output, expect):
    """The box's own check says the work is there.

    Exit 0 alone is not enough: `grep -c` answers 0 with a count of 0, and a
    gate expecting `3` is not met by `0`. So when the box states an
    expectation, the output has to carry it.
    """
    if code != 0:
        return False
    if not expect:
        return True
    raw = expect.strip()
    want = raw.strip('`').strip('.')
    # Written in backticks, it is a literal the output has to carry, however
    # many words it has. Found on 12 September: a gate expecting
    # `0 that can spin for ever` was read as "passing" against an output of
    # "41 that can spin for ever", because six words looked like a sentence
    # and a sentence was waved through on exit 0, and `tail -1` had already
    # turned the checker's exit 1 into a 0.
    if raw.startswith('`') and raw.endswith('`'):
        # A number is a whole number: `0 that can spin for ever` is not
        # inside "20 that can spin for ever", which plain containment said
        # it was, ten minutes after the first fix.
        pattern = re.escape(want.lower())
        if want[:1].isdigit():
            pattern = r'(?<![0-9.])' + pattern
        if want[-1:].isdigit():
            pattern = pattern + r'(?![0-9.])'
        return re.search(pattern, (output or '').lower()) is not None
    # An expectation written as a sentence rather than a literal cannot be
    # matched, and pretending otherwise is how a checker earns a number nobody
    # believes. Exit 0 is all there is in that case.
    if len(want.split()) > 4:
        return True
    return want.lower() in (output or '').lower()


def survey(files=None, slow=False):
    files = files or gate_files()
    stale, unrunnable, genuine, env = [], [], [], []
    for path in files:
        for box in parse(path):
            if box['ticked']:
                continue
            kind = classify(box['check'])
            if kind in ('no check', 'manual', 'unknown'):
                unrunnable.append((box, kind))
                continue
            if kind == 'slow' and not slow:
                unrunnable.append((box, 'slow, needs --slow'))
                continue
            code, output = run(box['check'])
            if code is None or ENV_FAILURE.search(output or ''):
                env.append((box, last_useful_line(output)))
                continue
            if passes(code, output, box['expect']):
                stale.append((box, last_useful_line(output)))
            else:
                genuine.append(box)
    return stale, genuine, unrunnable, env


# ---------------------------------------------------------------------------
# The self-test. A checker reporting 0 means "clean" OR "broken", and only
# fixtures of the real fault tell the two apart.
# ---------------------------------------------------------------------------

FIXTURE_STALE = """# Fixture: a gate whose work is built

- [ ] **A1** The workspace plan exists.
  CHECK: `grep -c "File ownership" PLAN.md`
  EXPECT: `1`
  EVIDENCE: pending
"""

FIXTURE_GENUINE = """# Fixture: a gate whose work is genuinely not built

- [ ] **B1** A file nobody has written yet.
  CHECK: `grep -c "this string is in no file in this repository at all" PLAN.md`
  EXPECT: `1`
  EVIDENCE: pending
"""

FIXTURE_TICKED = """# Fixture: an already ticked gate is never reported

- [x] **C1** The workspace plan exists.
  CHECK: `grep -c "File ownership" PLAN.md`
  EXPECT: `1`
  EVIDENCE: done
"""

FIXTURE_LONG_LITERAL = """# Fixture: a long literal in backticks is matched, not waved through

- [ ] **E1** The count reads zero.
  CHECK: `echo "116 file(s) checked, 41 that can spin for ever"`
  EXPECT: `0 that can spin for ever`
  EVIDENCE: pending
"""

FIXTURE_NUMBER_INSIDE = """# Fixture: a number is a whole number, not a substring of a bigger one

- [ ] **F1** The count reads zero.
  CHECK: `echo "95 file(s) checked, 20 that can spin for ever"`
  EXPECT: `0 that can spin for ever`
  EVIDENCE: pending
"""

FIXTURE_MANUAL = """# Fixture: a walk cannot be re-run from here

- [ ] **D1** Walked in Chrome at 390x844.
  CHECK: measured in Chrome in the documented same-origin iframe
  EXPECT: `scrollWidth <= 390`
  EVIDENCE: pending
"""


def self_test():
    import tempfile
    bad = []
    with tempfile.TemporaryDirectory() as tmp:
        cases = [('stale.md', FIXTURE_STALE, 'stale'),
                 ('genuine.md', FIXTURE_GENUINE, 'genuine'),
                 ('ticked.md', FIXTURE_TICKED, 'nothing'),
                 ('manual.md', FIXTURE_MANUAL, 'unrunnable'),
                 ('long-literal.md', FIXTURE_LONG_LITERAL, 'genuine'),
                 ('number-inside.md', FIXTURE_NUMBER_INSIDE, 'genuine')]
        for name, body, want in cases:
            path = os.path.join(tmp, name)
            io.open(path, 'w', encoding='utf-8').write(body)
            stale, genuine, unrunnable, env = survey([path])
            got = ('stale' if stale else 'genuine' if genuine
                   else 'unrunnable' if unrunnable else 'nothing')
            if got != want:
                bad.append((name, want, got))
            print('%-4s %-12s expected %-11s got %s'
                  % ('ok' if got == want else 'BAD', name, want, got))

    if bad:
        print('')
        print('%d self-test case(s) failed. This checker cannot be trusted '
              'until they pass.' % len(bad))
        return 1
    print('')
    print('self-test passed: %d cases, both directions. It reports a box whose '
          'own check passes, leaves a box whose check genuinely fails, never '
          'reports a ticked box, and names a walk as unrunnable rather than '
          'guessing.' % len(cases))
    return 0


def main():
    if '--self-test' in sys.argv:
        return self_test()

    slow = '--slow' in sys.argv
    stale, genuine, unrunnable, env = survey(slow=slow)

    if stale:
        print('Gate boxes that are UNTICKED while their own CHECK passes:')
        print('')
        for box, last in stale:
            print('  %s  %s' % (box['file'], box['id']))
            print('      CHECK: %s' % box['check'][:96])
            print('      says:  %s' % last[:96])
        print('')
        print('Either the work is built and the box wants ticking with that')
        print('output beside it, or the CHECK is the wrong one for the gate.')
        print('Both are work. Neither is something this script may decide.')
        print('')

    if env:
        print('%d box(es) could not be decided because the command did not run '
              'here (usually the system python has no Django - use the repo '
              'venv in the CHECK line):' % len(env))
        for box, last in env[:8]:
            print('  %s  %s  %s' % (box['file'], box['id'], last[:60]))
        print('')

    # Two lines, and the order matters. The debt ledger in check-all.py reads
    # the LAST line of a catcher and sums the fault numbers on it, so the
    # breakdown goes above and the last line carries one number: the debt.
    # Writing all four numbers on one line is how the tap target ceiling ended
    # up recording 311 stylesheets scanned instead of 145 breaches.
    total = len(stale) + len(genuine) + len(unrunnable) + len(env)
    print('%d box(es) checked: %d genuinely failing, %d not runnable from '
          'here, %d undecided%s'
          % (total, len(genuine), len(unrunnable), len(env),
             '' if slow else ' (--slow also runs the Django tests)'))
    print('%d gate box(es) unticked while their own check already passes'
          % len(stale))

    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main())

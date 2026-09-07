# -*- coding: utf-8 -*-
"""The debt ledger, proven in both directions.

CEO, 7 September 2026: "A stale ceiling means those four could climb back to 21
without failing anything. That defeats the point of the ledger."

They were right, and the cause was that the write-back loop only ever saw
checkers that were STILL FAILING. A checker reaching 0 went clean, dropped out
of the comparison, and froze at its last failing number - so climbing back to
that number read as "unchanged" rather than "risen", and twenty-one real
breaches could return in silence.

That fault was invisible to every existing check because the ledger had none of
its own. This is that missing test. It drives `compare()` directly rather than
running the 22 subprocesses, which is why `compare()` was pulled out of main().

Run:  python tools/test-ledger-writeback.py
"""
import datetime
import sys

sys.path.insert(0, __file__.rsplit('\\', 1)[0].rsplit('/', 1)[0])

import importlib.util
import os

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    'check_all', os.path.join(_here, 'check-all.py'))
check_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_all)

TODAY = '2026-09-07'
LONG_AGO = '2026-08-01'

failures = []


def case(label, tracked, ledger, expect_risen=0, expect_fell=0,
         expect_stuck=0, expect_count=None, name='probe'):
    risen, stuck, fell, out = check_all.compare(tracked, dict(ledger), TODAY)
    got_fell = len([r for r in fell if r is not None])
    problems = []
    if len(risen) != expect_risen:
        problems.append('risen %d, wanted %d' % (len(risen), expect_risen))
    if got_fell != expect_fell:
        problems.append('fell %d, wanted %d' % (got_fell, expect_fell))
    if len(stuck) != expect_stuck:
        problems.append('stuck %d, wanted %d' % (len(stuck), expect_stuck))
    if expect_count is not None and out.get(name, {}).get('count') != expect_count:
        problems.append('recorded %s, wanted %s'
                        % (out.get(name, {}).get('count'), expect_count))
    if problems:
        failures.append((label, '; '.join(problems)))
        print('FAIL %-52s %s' % (label, '; '.join(problems)))
    else:
        print('ok   %s' % label)


# --------------------------------------------------------------- the fault
# This is the exact shape that went wrong: a checker sitting at 21 is fixed,
# reaches 0, and must be RECORDED at 0 rather than left at 21.
case('a checker reaching zero is written back to zero',
     [('probe', '0 place(s)', 0)],
     {'probe': {'count': 21, 'since': LONG_AGO, 'first_seen': LONG_AGO}},
     expect_fell=1, expect_count=0)

# And the consequence: once it is 0, coming back is a RISE and must be caught.
case('coming back from zero is a rise, not "unchanged"',
     [('probe', '21 place(s)', 21)],
     {'probe': {'count': 0, 'since': TODAY, 'first_seen': LONG_AGO}},
     expect_risen=1)

# The old behaviour, stated so the test says what it is protecting against:
# against a STALE ceiling of 21, a return to 21 reads as no change at all.
case('against a stale ceiling the same rise is invisible',
     [('probe', '21 place(s)', 21)],
     {'probe': {'count': 21, 'since': TODAY, 'first_seen': LONG_AGO}},
     expect_risen=0)

# ------------------------------------------------------- the ordinary cases
case('a fall becomes the new ceiling',
     [('probe', '9 problem(s)', 9)],
     {'probe': {'count': 60, 'since': LONG_AGO, 'first_seen': LONG_AGO}},
     expect_fell=1, expect_count=9)

case('a rise is NOT written back, so it keeps failing',
     [('probe', '80 problem(s)', 80)],
     {'probe': {'count': 60, 'since': LONG_AGO, 'first_seen': LONG_AGO}},
     expect_risen=1, expect_count=60)

case('a first sighting is saved but announces nothing',
     [('probe', '5 problem(s)', 5)],
     {},
     expect_fell=0, expect_count=5)

case('a number that has not moved in weeks is named as stuck',
     [('probe', '60 problem(s)', 60)],
     {'probe': {'count': 60, 'since': LONG_AGO, 'first_seen': LONG_AGO}},
     expect_stuck=1)

case('a number that has not moved since today is not stuck yet',
     [('probe', '60 problem(s)', 60)],
     {'probe': {'count': 60, 'since': TODAY, 'first_seen': TODAY}},
     expect_stuck=0)

# ------------------------------------------------------------- calibration
# Why a clean checker's count is never PARSED. These are real last lines from
# real catchers, and the first integer on each is not the fault count.
MISREADS = [
    '650 route(s) checked, 0 fetch(es) that go nowhere',
    '5953 keys checked, 0 missing',
    '169 model(s) defined, 0 duplicate name(s), 0 hand-built person dicts',
    '16 capability pair(s) checked, 0 built on one side only',
]
for line in MISREADS:
    first = check_all._count(line)
    if first == 0:
        failures.append(('calibration', 'expected a misleading first integer'))
        print('FAIL calibration: %r read as 0, so this case proves nothing' % line)
    else:
        print('ok   a clean line would misread as %-5s so it is not parsed  %s'
              % (first, line[:44]))

print('')
if failures:
    print('%d FAILED' % len(failures))
    sys.exit(1)
print('OK - the ledger records a fall to zero and catches the climb back')

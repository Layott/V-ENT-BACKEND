"""The debt ledger reads the right number off a catcher's summary line.

    CEO, 8 September 2026: "check-all.py _count() takes the first integer on
    the summary line ... Build it end 2 end with full extensive test."

Why this file exists. `check-tap-targets.mjs` prints

    311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone

and the old rule took the first integer, so `tools/debt-ledger.json` stored
the ceiling as 311 while the real debt was 145. The one thing the ledger
exists to do is fail when a number goes up, and that ceiling let the number
climb by 166 without a single run complaining.

Seven of the twenty-four catchers in the table say how much they READ before
they say how much is WRONG, so this was never one catcher's problem.

Every CASE below is a real line. The ones marked `real` were captured on
8 September 2026 by running the catcher and taking its summary line verbatim;
the ones marked `failing` are the same catcher's own format string with a
non-zero count in it, which is the state that actually matters, because a
clean catcher is recorded at zero by exit code and never parsed at all.

    python tools/test-count-parse.py
    python tools/test-count-parse.py --prove-old-rule-fails

The second one is the calibration half. A checker that reports a pass has two
meanings - "the rule is right" and "the test is asleep" - and the only thing
that tells them apart is proving the BROKEN rule fails the same cases.
"""
import importlib.util
import os
import re
import sys


HERE = os.path.dirname(os.path.abspath(__file__))


def _load_check_all():
    spec = importlib.util.spec_from_file_location(
        'check_all', os.path.join(HERE, 'check-all.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def old_rule(line):
    """The rule this file exists to retire: the first integer on the line."""
    m = re.search(r'[0-9]+', line or '')
    return int(m.group(0)) if m else None


# (catcher, provenance, line, the honest count)
#
# provenance: `real` is a line this catcher printed on 8 September 2026.
#             `failing` is the same catcher's format string carrying a real
#             number, which is the state the ledger is for.
CASES = [
    # ---------------------------------------------------------- real lines
    ('parity', 'real',
     '16 capability pair(s) checked, 0 built on one side only', 0),
    ('one model', 'real',
     '174 model(s) defined, 0 duplicate name(s), 0 hand-built person dict(s)', 0),
    ('wizard round trip', 'real',
     '49 field(s) the wizard sends, 0 broken link(s)', 0),
    ('prose', 'real',
     '0 em/en dash(es) and 0 npm command(s) outstanding', 0),
    ('signed out', 'real',
     '0 live write controls at risk', 0),
    ('control bytes', 'real',
     '0 control byte(s) in source', 0),
    ('dangling refs', 'real',
     '0 ref(s) read but never attached', 0),
    ('css classes', 'real',
     '0 undefined class reference(s) across 0 file(s)', 0),
    ('translation keys', 'real',
     '6034 keys checked, 0 missing', 0),
    ('dictionary parity', 'real',
     'en=fr=pt and 0 missing', 0),
    ('avatars', 'real',
     '0 place(s) showing a name with no way to show the picture', 0),
    ('user chips', 'real',
     'every name renders through UserChip', None),
    ('slugs', 'real',
     '0 numeric id(s) in a visible address', 0),
    ('seo', 'real',
     '85 public route(s) checked, 0 problem(s)', 0),
    ('design bans', 'real',
     '0 new design breaches. 119 known, being worked down:', 0),
    ('timing model', 'real',
     '0 new timing breaches. 0 known severe, being worked down.', 0),
    ('timing model', 'real',
     '0 number-formatting notes, which do not fail the build.', None),
    ('tap targets', 'real',
     '311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone', 145),
    ('api paths', 'real',
     '650 route(s) checked, 0 fetch(es) that go nowhere', 0),
    ('timezone picker', 'real',
     '0 timezone or date-format fault(s)', 0),
    ('live updates', 'real',
     '0 dead refresh timers, 0 pages that never refresh. 0 known, being worked down.', 0),
    ('raw errors', 'real',
     '0 raw exceptions on screen. 0 known, being worked down.', 0),
    ('colour variables', 'real',
     '0 new colour variable faults. 0 known:', 0),
    ('ask register', 'real',
     '0 problem(s) in the ask register', 0),
    ('dead ticket codes', 'real',
     '0 endpoint(s) that refuse a code without saying it was transferred', 0),
    ('prose', 'real',
     '5736 file(s) scanned', None),

    # ------------------------------------------------------- failing lines
    #
    # The one that started this. 145 today, and the ledger has to notice 146.
    ('tap targets', 'failing',
     '311 stylesheet(s) checked, 146 tap target(s) under 44px on a phone', 146),
    # check-seo sat at 60 problems for weeks while check-all printed the
    # number every run. Under the old rule the ledger held 85, the count of
    # routes, so all sixty could have become a hundred in silence.
    ('seo', 'failing',
     '85 public route(s) checked, 60 problem(s)', 60),
    ('parity', 'failing',
     '16 capability pair(s) checked, 3 built on one side only', 3),
    ('one model', 'failing',
     '174 model(s) defined, 1 duplicate name(s), 2 hand-built person dict(s)', 3),
    ('wizard round trip', 'failing',
     '49 field(s) the wizard sends, 2 broken link(s)', 2),
    # Two independent faults on one line. Taking only the first would let npm
    # commands climb behind a steady dash count, which is the same hole one
    # level down, so the answer is the sum.
    ('prose', 'failing',
     '3 em/en dash(es) and 2 npm command(s) outstanding', 5),
    ('css classes', 'failing',
     '214 undefined class reference(s) across 37 file(s)', 214),
    ('translation keys', 'failing',
     '6034 keys checked, 12 missing', 12),
    ('api paths', 'failing',
     '650 route(s) checked, 4 fetch(es) that go nowhere', 4),
    ('slugs', 'failing',
     '22 numeric id(s) in a visible address', 22),
    ('avatars', 'failing',
     '9 place(s) showing a name with no way to show the picture', 9),
    ('signed out', 'failing',
     '14 live write controls at risk', 14),
    ('control bytes', 'failing',
     '7 control byte(s) in source', 7),
    ('dangling refs', 'failing',
     '1 ref(s) read but never attached', 1),
    ('timezone picker', 'failing',
     '3 timezone or date-format fault(s)', 3),
    ('ask register', 'failing',
     '5 problem(s) in the ask register', 5),
    ('dead ticket codes', 'failing',
     '2 endpoint(s) that refuse a code without saying it was transferred', 2),
    # A baselined catcher reports NEW faults, and its known count follows with
    # a breakdown. Every number after "known" belongs to the baseline.
    ('design bans', 'failing',
     '0 new design breaches. 231 known, being worked down:', 0),
    ('colour variables', 'failing',
     '0 new colour variable faults. 5 known: 3 undefined-token, 2 primary-bg', 0),
    ('live updates', 'failing',
     '0 dead refresh timers, 0 pages that never refresh. 14 known, being worked down.', 0),
    ('raw errors', 'failing',
     '0 raw exceptions on screen. 26 known, being worked down.', 0),
    ('timing model', 'failing',
     '0 new timing breaches. 25 known severe, being worked down.', 0),
    ('timing model', 'failing',
     '260 number-formatting notes, which do not fail the build.', None),
    # The self-test line both check-api-paths and check-timezone-picker print
    # when a case goes wrong. "of 14" is a denominator, never the debt.
    ('api paths', 'failing', '0 of 14 case(s) wrong', 0),
    ('api paths', 'failing', '3 of 14 case(s) wrong', 3),
    ('check-all', 'real', 'Every blocking catcher is clean.', None),

    # Written the same day as the parser, and it caught the parser out: the
    # word "already" was in the baseline list on a guess, so this line read as
    # a baseline and reported nothing. The word came out; the line stays.
    ('stale gates', 'real',
     '10 gate box(es) unticked while their own check already passes', 10),
    # The breakdown line, which is NEVER the summary: check-stale-gates prints
    # it above the one-number line for exactly this reason. Read on its own it
    # honestly reports 178 boxes that are not passing, and that is the right
    # answer for the line. The TAILS case below is what proves the parser
    # never reads this one.
    ('stale gates', 'real',
     '188 box(es) checked: 7 genuinely failing, 171 not runnable from here, '
     '0 undecided', 178),
    ('stale gates', 'failing',
     '23 gate box(es) unticked while their own check already passes', 23),

    # Registered 8 September. Another line that says how much it READ before it
    # says how much is wrong, so the old rule would have set its ceiling at 7,
    # the number of settings, and all seven could have lost their buyer screen
    # without a single run failing. It is blocking rather than debt, so this is
    # belt and braces, but the next one like it may not be.
    ('offer surface', 'real',
     '7 organiser setting(s) checked, 0 with no buyer surface', 0),
    ('offer surface', 'failing',
     '7 organiser setting(s) checked, 3 with no buyer surface', 3),

    # Registered 8 September. The same shape once more: the scanned count comes
    # first, so the old rule would read the ceiling as 1, the number of config
    # files, and a clash could appear without the number moving.
    ('dev distdir', 'real',
     '1 config checked, 0 build directory clash(es) possible', 0),
    ('dev distdir', 'failing',
     '1 config checked, 1 build directory clash(es) possible', 1),
]


# The summary line is not simply the last line of a catcher's output. These
# are real tails, captured 8 September 2026, where the last line lies.
#
# (catcher, the real output tail, the line that is actually the summary)
TAILS = [
    ('design bans',
     '0 new design breaches. 119 known, being worked down:\n'
     '    35  hairline\n'
     '    30  pure-black-or-white\n'
     '    23  glass\n'
     '    16  ambient-motion\n'
     '    15  glow\n',
     '0 new design breaches. 119 known, being worked down:'),

    ('translation keys',
     '6034 keys checked, 0 missing\n'
     '(node:16016) [MODULE_TYPELESS_PACKAGE_JSON] Warning: Module type of '
     'file:///C:/Users/Sweez/Desktop/LAYO/CLAUDE/V-ENT/V-ENT-FRONTEND/src/i18n/'
     'dictionaries.js is not specified and it doesn\'t parse as CommonJS.\n'
     'Reparsing as ES module because module syntax was detected. This incurs a '
     'performance overhead.\n'
     'To eliminate this warning, add "type": "module" to '
     'C:\\Users\\Sweez\\Desktop\\LAYO\\CLAUDE\\V-ENT\\V-ENT-FRONTEND\\package.json.\n'
     '(Use `node --trace-warnings ...` to show where the warning was created)\n',
     '6034 keys checked, 0 missing'),

    ('dictionary parity',
     'en=6865 fr=6865 pt=6865, missing fr=0 pt=0\n'
     'en=fr=pt and 0 missing\n'
     '(node:26512) [MODULE_TYPELESS_PACKAGE_JSON] Warning: Module type is not '
     'specified.\n'
     '(Use `node --trace-warnings ...` to show where the warning was created)\n',
     'en=fr=pt and 0 missing'),

    ('tap targets',
     'src\\components\\view-tournament\\tournament-register\\payment\\payment.module.css\n'
     '  .radioButton is 24px and nothing raises it on a phone. Anything '
     'pressable is 44px there.\n'
     '\n'
     '311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone\n',
     '311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone'),

    ('slugs',
     'exempt  src/app/search/page.js (11) - links into marketplace, shop and '
     'anime, all still behind ComingSoon\n'
     '\n'
     '0 numeric id(s) in a visible address\n',
     '0 numeric id(s) in a visible address'),

    ('user chips',
     'files rendering a name: 13\n'
     'every name renders through UserChip\n',
     'every name renders through UserChip'),

    ('css classes',
     '0 undefined class(es) on an interactive element  <-- these break something\n'
     '0 undefined class reference(s) across 0 file(s)\n',
     '0 undefined class reference(s) across 0 file(s)'),

    # A catcher written after this parser, and written to suit it: the
    # breakdown goes above and the last line carries the one number that is
    # the debt. Any new catcher should be written the same way.
    ('stale gates',
     '188 box(es) checked: 7 genuinely failing, 171 not runnable from here, '
     '0 undecided (--slow also runs the Django tests)\n'
     '10 gate box(es) unticked while their own check already passes\n',
     '10 gate box(es) unticked while their own check already passes'),
]


def run_cases(mod):
    bad = []
    for catcher, kind, line, want in CASES:
        got = mod._count(line)
        if got != want:
            bad.append((catcher, kind, line, want, got))
    return bad


def run_tails(mod):
    bad = []
    for catcher, output, want in TAILS:
        got = mod._summary_line(output)
        if got != want:
            bad.append((catcher, output, want, got))
    return bad


def main():
    mod = _load_check_all()

    if '--prove-old-rule-fails' in sys.argv:
        # A pass means nothing unless the broken rule fails the same cases.
        # This is the calibration half of the rule in CLAUDE.md: prove it both
        # ways, or a green run has two meanings.
        missed = []
        for catcher, kind, line, want in CASES:
            if old_rule(line) != want:
                missed.append((catcher, line, want, old_rule(line)))

        if not missed:
            print('The old first-integer rule passed every case.')
            print('That means these cases cannot tell the two rules apart, so')
            print('this test proves nothing. Add a line where they differ.')
            return 1

        print('The old first-integer rule gets %d of %d case(s) WRONG:'
              % (len(missed), len(CASES)))
        print('')
        for catcher, line, want, got in missed:
            print('  %-18s wanted %-6s got %-6s' % (catcher, want, got))
            print('  %-18s %s' % ('', line))
        print('')
        print('That is what the ledger was recording. The tap target ceiling')
        print('read 311 stylesheets scanned while the debt was 145 breaches,')
        print('so the number could climb by 166 and nothing would fail.')
        return 0

    bad_cases = run_cases(mod)
    bad_tails = run_tails(mod)

    for catcher, kind, line, want, got in bad_cases:
        print('WRONG  %s (%s)' % (catcher, kind))
        print('       %s' % line)
        print('       wanted %s, got %s' % (want, got))
    for catcher, output, want, got in bad_tails:
        print('WRONG SUMMARY LINE  %s' % catcher)
        print('       wanted %r' % want)
        print('       got    %r' % got)

    if bad_cases or bad_tails:
        print('')
        print('%d of %d count case(s) and %d of %d summary line(s) wrong'
              % (len(bad_cases), len(CASES), len(bad_tails), len(TAILS)))
        return 1

    real = sum(1 for c in CASES if c[1] == 'real')
    print('%d case(s) pass: %d real summary line(s) from the catchers '
          'themselves, %d in the failing state.'
          % (len(CASES), real, len(CASES) - real))
    print('%d output tail(s) pass: the summary line is found past the '
          'indented breakdown and the Node warning.' % len(TAILS))
    print('')
    print('Run with --prove-old-rule-fails to see what the old rule did to '
          'the same lines.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

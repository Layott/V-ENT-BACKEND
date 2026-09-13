#!/usr/bin/env python3
"""Every platform price is set on the admin dashboard, and nowhere else.

CEO, 13 September 2026:

    "the 5% + NGN100 is something admins should be able to set on the admin
    dashboard, they should be able to set what prices it is now for any premium
    feature or option and it updates everywhere on the platform, please create
    a checker for this that makes sure it applies each time a new feature is
    built or added that has pricing."

What was found the day this was written: the dashboard carried eight money
controls and four of them changed nothing. The payout minimum showed 0 while
the real minimum was 5, read from an environment variable. The withdraw screen
told people "Withdrawal fee (2% + N50)" while the server took nothing. A fee
key billing read was in no defaults and on no screen.

    python tools/check-pricing.py              the tree; exit 1 on any problem
    python tools/check-pricing.py --self-test  proves each rule on a fixture

The rule, in four parts. A platform rate or price:

  1. LIVES in `DEFAULT_ADMIN_SETTINGS` (`vent_auth/models.py`), in the
     `platform_fees` or `premium` section. That dict is the one list.
  2. HAS A CONTROL on the admin settings page that writes exactly that key
     (`patch('<section>', '<key>'`). A key with no control cannot be set.
  3. HAS A READER in the backend code that charges it. A key with no reader is
     a control that changes nothing, which is the fault this was written for.
     And every key READ from a section must be in the defaults: a reader of a
     key nobody defined is charging a number nobody can see.
  4. APPEARS NOWHERE ELSE AS A NUMBER. Not `* 5 / 100` in a view, not
     `FEE_PCT = 5` at the top of a module, not "Service fee (5% + 100 naira)"
     in a dictionary string, not `fee_pct ?? 5` on a screen. Copy that names
     a rate is a template filled from the server's quote.

The one number that is NOT a platform fee: 1,000 naira to a VENT COIN is the
unit, not a price, and it is allowed in prose that explains the unit.

Calibrated on 13 September: the real tree reports 0 after the fixes, and the
self-test fails on a fixture of each of the eight faults.
"""
import ast
import os
import re
import sys
import tempfile

# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------


def _workspace_root():
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND')):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        here = parent


MONEY_SECTIONS = ('platform_fees', 'premium')

#: Backend directories whose code charges people. Tests, migrations, tools and
#: seed commands are not readers: a key named only in a test has no reader.
SKIP_DIRS = {'venv', 'vent', '__pycache__', 'migrations', '.git', 'node_modules',
             'tools', 'management', 'docs', 'media', 'staticfiles'}
SKIP_FILE = re.compile(r'^(tests?_.*|tests?|factories|conftest|test_.*)\.py$')

#: What a money key looks like. Anything read from a money section must be one
#: of these shapes, so a typo like `ticket_fee_percent` is still caught by the
#: "not in defaults" rule rather than slipping past as an unrelated `.get`.
KEY_SHAPE = re.compile(r'^(?:[a-z]+_)*(?:fee_pct|fee_flat_ngn|pct|ngn|vc|ngn_per_day)$|^price_vc_')

# ---------------------------------------------------------------------------
# 1. The one list
# ---------------------------------------------------------------------------


def read_defaults(models_path):
    """{section: {key: default}} for the money sections, from the source."""
    tree = ast.parse(open(models_path, encoding='utf-8').read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'DEFAULT_ADMIN_SETTINGS' for t in node.targets):
            blob = ast.literal_eval(node.value)
            return {s: dict(blob.get(s) or {}) for s in MONEY_SECTIONS}
    raise SystemExit('DEFAULT_ADMIN_SETTINGS not found in %s' % models_path)


# ---------------------------------------------------------------------------
# 2 and 3. Controls and readers
# ---------------------------------------------------------------------------

CONTROL = re.compile(r"""patch\(\s*['"](platform_fees|premium)['"]\s*,\s*['"]([a-z_]+)['"]""")


def controls_on(page_path):
    """{(section, key)} the admin settings page writes."""
    if not os.path.exists(page_path):
        return set()
    return set(CONTROL.findall(open(page_path, encoding='utf-8').read()))


#: `fees = AdminSetting.load().merged().get('platform_fees') or {}` and the
#: bracket form. The variable on the left is then a section for the rest of
#: the file.
SECTION_ASSIGN = re.compile(
    r"""(\w+)\s*=\s*[^\n]*?merged\(\)\s*(?:\.get\(\s*['"](platform_fees|premium)['"]|\[\s*['"](platform_fees|premium)['"]\s*\])""")
#: `return AdminSetting.load().merged().get('platform_fees') or {}` inside a
#: def: that function is a section getter, and `x = getter()` marks x.
SECTION_RETURN = re.compile(
    r"""return\s+[^\n]*?merged\(\)\s*(?:\.get\(\s*['"](platform_fees|premium)['"]|\[\s*['"](platform_fees|premium)['"]\s*\])""")
DEF_LINE = re.compile(r'^\s*def\s+(\w+)\s*\(')
INLINE_READ = re.compile(
    r"""merged\(\)\s*(?:\.get\(\s*['"](platform_fees|premium)['"]\s*\)|\[\s*['"](platform_fees|premium)['"]\s*\])\s*(?:or\s*\{\}\s*)?[\)\s]*(?:\.get\(\s*['"]([a-z_]+)['"]|\[\s*['"]([a-z_]+)['"]\s*\])""")


def backend_files(backend):
    for root, dirs, files in os.walk(backend):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for name in files:
            if name.endswith('.py') and not SKIP_FILE.match(name):
                yield os.path.join(root, name)


def reads_in(path):
    """[(section, key, line_no)] every money key this file reads from a section."""
    text = open(path, encoding='utf-8', errors='replace').read()
    lines = text.split('\n')
    out = []
    # Which names hold a section in this file, and which functions return one.
    holders = {}
    getters = {}
    current_def = None
    for i, line in enumerate(lines, 1):
        m = DEF_LINE.match(line)
        if m:
            current_def = m.group(1)
        m = SECTION_ASSIGN.search(line)
        if m:
            holders[m.group(1)] = m.group(2) or m.group(3)
        m = SECTION_RETURN.search(line)
        if m and current_def:
            getters[current_def] = m.group(1) or m.group(2)
        m = INLINE_READ.search(line)
        if m:
            out.append((m.group(1) or m.group(2), m.group(3) or m.group(4), i))
    for name, section in getters.items():
        for m in re.finditer(r'(\w+)\s*=\s*%s\(\)' % re.escape(name), text):
            holders[m.group(1)] = section
    for i, line in enumerate(lines, 1):
        for var, section in holders.items():
            for m in re.finditer(r"""\b%s\s*(?:\.get\(\s*['"]([a-z_]+)['"]|\[\s*['"]([a-z_]+)['"]\s*\])""" % re.escape(var), line):
                out.append((section, m.group(1) or m.group(2), i))
    return out


# ---------------------------------------------------------------------------
# 4. Nowhere else as a number
# ---------------------------------------------------------------------------

FEE_WORD = re.compile(r'(?i)\b(fee|fees|commission|service charge|platform cut|frais|taxa|comiss)')
#: `x * 5 / 100`, `x * 0.05`, `Decimal('0.05') * x`, `x * Decimal('5') / 100`
#: on a line that talks about a fee. 0 and 100 are not rates.
RATE_MATHS = re.compile(
    r"""(?:\*\s*(?:Decimal\(\s*['"])?(?!0(?:\.0+)?['"]?\)?\s*/)(?:[1-9]\d?(?:\.\d+)?|0\.\d*[1-9]\d*)['"]?\)?\s*/\s*100\b)"""
    r"""|(?:\*\s*(?:Decimal\(\s*['"])?0\.\d*[1-9]\d*['"]?\)?)"""
    r"""|(?:(?:Decimal\(\s*['"])?0\.\d*[1-9]\d*['"]?\)?\s*\*)""")
#: `FEE_PCT = 5`, `commission_rate = 0.02`, `fee_flat_ngn = 100` as a literal.
#: A zero is an initialiser and is allowed.
#: "fee" has to be a whole segment of the name: `wb_feeder_round = 2` is a
#: bracket's plumbing, not a rate.
RATE_ASSIGN = re.compile(
    r"""(?i)\b(?:[a-z0-9]+_)*(?:fee|fees|commission)(?:_[a-z0-9]+)*\s*=\s*(?:Decimal\(\s*['"])?(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*)['"]?\)?\s*(?:#.*)?$""")

COIN_UNIT = re.compile(r'(?i)1[,. ]?000\s?(?:naira|nairas|ngn|₦)|₦\s?1[,. ]?000\b|NGN\s?1[,. ]?000\b')
#: A rate or a naira amount inside copy: "5%", "2 %", "₦50", "100 naira", "N100".
MONEY_IN_COPY = re.compile(r'(?:\b\d{1,2}(?:[.,]\d)?\s?%)|(?:₦\s?\d)|(?:\b\d[\d,.]*\s?nairas?\b)|(?:\bN\d{2,}\b)|(?:\bNGN\s?\d)')
FRONT_FALLBACK = re.compile(r'(?:fee_pct|fee_flat_ngn|price_vc_\w+|_fee_\w+|_pct|_flat_ngn)\s*(?:\?\?|\|\|)\s*[1-9]')
FRONT_MATHS = re.compile(r'\*\s*0\.\d*[1-9]\d*\b|\b[1-9]\d?\s*/\s*100\b')

FRONT_SKIP_DIRS = {'node_modules', '.next', '.next-dev', 'public', 'docs', 'scripts'}
FRONT_SKIP_DIRS_RE = re.compile(r'\.next')


def strip_py_comment(line):
    """The code before a `#`, outside strings (good enough for these rules)."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            out.append(ch)
        elif ch == '#':
            break
        else:
            out.append(ch)
    return ''.join(out)


def is_js_comment(line):
    s = line.strip()
    return s.startswith('//') or s.startswith('*') or s.startswith('/*') or s.startswith('{/*')


def frontend_files(frontend):
    src = os.path.join(frontend, 'src')
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in FRONT_SKIP_DIRS and not FRONT_SKIP_DIRS_RE.match(d)]
        for name in files:
            if name.endswith('.js') or name.endswith('.jsx'):
                yield os.path.join(root, name)


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def check(backend, frontend, models_rel='vent_auth/models.py',
          page_rel='src/app/(admin)/admin/settings/page.js'):
    problems = []
    models_path = os.path.join(backend, models_rel)
    defaults = read_defaults(models_path)
    keys = {(s, k) for s, block in defaults.items() for k in block}

    # 2. controls
    have_control = controls_on(os.path.join(frontend, page_rel))
    for section, key in sorted(keys):
        if (section, key) not in have_control:
            problems.append(('no control', '%s.%s has no field on the admin settings page' % (section, key),
                             page_rel, 0))
    for section, key in sorted(have_control - keys):
        problems.append(('control for nothing', "the admin page writes %s.%s and nothing defines it" % (section, key),
                         page_rel, 0))

    # 3. readers, both ways
    read = {}
    for path in backend_files(backend):
        rel = os.path.relpath(path, backend).replace('\\', '/')
        if rel == models_rel:
            continue
        for section, key, line in reads_in(path):
            read.setdefault((section, key), []).append((rel, line))
    for section, key in sorted(keys):
        if (section, key) not in read:
            problems.append(('no reader', '%s.%s is read by nothing; a control that changes nothing' % (section, key),
                             models_rel, 0))
    for (section, key), places in sorted(read.items()):
        if (section, key) not in keys:
            for rel, line in places:
                problems.append(('reads a key nobody defined',
                                 '%s.%s is not in DEFAULT_ADMIN_SETTINGS' % (section, key), rel, line))

    # 4a. backend literals
    for path in backend_files(backend):
        rel = os.path.relpath(path, backend).replace('\\', '/')
        if rel == models_rel:
            continue
        for i, raw in enumerate(open(path, encoding='utf-8', errors='replace').read().split('\n'), 1):
            line = strip_py_comment(raw)
            if not line.strip():
                continue
            if RATE_ASSIGN.search(line):
                problems.append(('rate as a literal', line.strip()[:90], rel, i))
            elif FEE_WORD.search(line) and RATE_MATHS.search(line):
                problems.append(('rate as a literal', line.strip()[:90], rel, i))

    # 4b. frontend copy, helpers and fallbacks
    for path in frontend_files(frontend):
        rel = os.path.relpath(path, frontend).replace('\\', '/')
        text = open(path, encoding='utf-8', errors='replace').read()
        talks_fees = bool(FEE_WORD.search(text))
        for i, raw in enumerate(text.split('\n'), 1):
            if is_js_comment(raw):
                continue
            if FRONT_FALLBACK.search(raw):
                problems.append(('rate as a fallback', raw.strip()[:90], rel, i))
                continue
            if FEE_WORD.search(raw):
                stripped = COIN_UNIT.sub('', raw)
                if MONEY_IN_COPY.search(stripped):
                    problems.append(('rate in copy', raw.strip()[:90], rel, i))
                    continue
            if talks_fees and FRONT_MATHS.search(raw) and not is_js_comment(raw):
                # The maths is inside a function whose name or comment says
                # fee, so look a few lines up: `calcWithdrawFee` names it on
                # its first line, and the `* 0.02` comes three lines later.
                if re.search(r'(?i)fee|commission', ' '.join(text.split('\n')[max(0, i - 6):i])):
                    problems.append(('rate as maths', raw.strip()[:90], rel, i))
    return problems


def report(problems):
    for kind, what, rel, line in problems:
        where = '%s:%s' % (rel, line) if line else rel
        print('  %-26s %s  (%s)' % (kind, what, where))
    print('pricing: %d problem(s)' % len(problems))
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Self-test: a fixture of each fault, and a clean one
# ---------------------------------------------------------------------------

CLEAN_MODELS = """
DEFAULT_ADMIN_SETTINGS = {
    'platform_fees': {
        'ticket_fee_pct': 5,
        'ticket_fee_flat_ngn': 100,
    },
    'premium': {
        'price_vc_monthly': 0,
    },
    'banner': {'enabled': False},
}
"""

CLEAN_READER = """
from decimal import Decimal
from vent_auth.models import AdminSetting


def _fees():
    return AdminSetting.load().merged().get('platform_fees') or {}


def platform_fee():
    fees = _fees()
    pct = Decimal(str(fees.get('ticket_fee_pct') or 0))
    flat = Decimal(str(fees['ticket_fee_flat_ngn'] or 0))
    return pct, flat


def fee_for(unit, pct, flat):
    fee = unit * pct / Decimal('100') + flat   # the rate comes in
    return fee
"""

CLEAN_PREMIUM = """
from vent_auth.models import AdminSetting


def offer():
    blob = AdminSetting.load().merged().get('premium') or {}
    return max(0, int(blob.get('price_vc_monthly') or 0))
"""

CLEAN_PAGE = """
<input onChange={e => patch('platform_fees', 'ticket_fee_pct', parseFloat(e.target.value || '0'))} />
<input onChange={e => patch('platform_fees', 'ticket_fee_flat_ngn', parseFloat(e.target.value || '0'))} />
<input onChange={e => patch('premium', 'price_vc_monthly', parseInt(e.target.value || '0', 10))} />
"""

CLEAN_COPY = """
export const en = {
  'stall.feeLine': 'Service fee ({pct}% + {flat} naira a unit)',
  'guide.wallets.note': '1,000 naira is 1 VENT COIN. Every fee and prize on the platform is counted in coins.',
  'stats.winRate': '53% win rate',
};
// Withdrawal fee: 2% of NGN payout + 50 NGN flat.   (a comment is not copy)
const feeLine = t('stall.feeLine').replace('{pct}', String(stall.fee_pct ?? 0));
"""


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def _fixture(tmp, name, *, models=CLEAN_MODELS, reader=CLEAN_READER, premium=CLEAN_PREMIUM,
             page=CLEAN_PAGE, copy=CLEAN_COPY, extra_backend=None, extra_front=None):
    backend = os.path.join(tmp, name, 'V-ENT-BACKEND')
    frontend = os.path.join(tmp, name, 'V-ENT-FRONTEND')
    _write(backend, 'vent_auth/models.py', models)
    _write(backend, 'vent_event/ledger.py', reader)
    _write(backend, 'vent_auth/premium_sale.py', premium)
    _write(frontend, 'src/app/(admin)/admin/settings/page.js', page)
    _write(frontend, 'src/i18n/dictionaries.js', copy)
    for rel, text in (extra_backend or {}).items():
        _write(backend, rel, text)
    for rel, text in (extra_front or {}).items():
        _write(frontend, rel, text)
    return backend, frontend


def self_test():
    passed = 0
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        # 0. clean
        b, f = _fixture(tmp, 'clean')
        cases.append(('a clean tree reports 0', check(b, f), []))

        # 1. a key with no reader
        b, f = _fixture(tmp, 'noreader', models=CLEAN_MODELS.replace(
            "'ticket_fee_flat_ngn': 100,", "'ticket_fee_flat_ngn': 100,\n        'tournament_fee_pct': 0,"),
            page=CLEAN_PAGE + "<input onChange={e => patch('platform_fees', 'tournament_fee_pct', 0)} />")
        cases.append(('a key nothing reads', check(b, f), ['no reader']))

        # 2. a key with no control
        b, f = _fixture(tmp, 'nocontrol', page=CLEAN_PAGE.replace(
            "<input onChange={e => patch('premium', 'price_vc_monthly', parseInt(e.target.value || '0', 10))} />", ''))
        cases.append(('a key with no field on the page', check(b, f), ['no control']))

        # 3. a read of a key nobody defined
        b, f = _fixture(tmp, 'undefinedkey', extra_backend={'vent_billing/charging.py': """
from vent_auth.models import AdminSetting
def platform_rate():
    fees = AdminSetting.load().merged().get('platform_fees') or {}
    return float(fees.get('subscription_fee_pct') or 0)
"""})
        cases.append(('a reader of a key not in the defaults', check(b, f), ['reads a key nobody defined']))

        # 4. a rate computed from a literal
        b, f = _fixture(tmp, 'literal', extra_backend={'vent_event/views_guest.py': """
def price(unit):
    fee = unit * 5 / 100 + 100
    return fee
"""})
        cases.append(('a fee computed from 5 / 100', check(b, f), ['rate as a literal']))

        # 5. a module constant
        b, f = _fixture(tmp, 'constant', extra_backend={'vent_marketplace/holds.py': """
COMMISSION_PCT = 2
def cut(amount):
    return amount * COMMISSION_PCT / 100
"""})
        cases.append(('FEE_PCT = 2 at the top of a module', check(b, f), ['rate as a literal']))

        # 6. a rate written into copy
        b, f = _fixture(tmp, 'copy', copy=CLEAN_COPY + """
export const fr = { 'ui.withdrawal.fee': 'Frais de retrait (2 % + 50 ₦)' };
""")
        cases.append(('"Withdrawal fee (2% + 50 naira)" in a dictionary', check(b, f), ['rate in copy']))

        # 7. a fallback on a screen, and maths in a helper
        b, f = _fixture(tmp, 'fallback', extra_front={
            'src/app/my-stalls/page.js': "const pct = String(stall.fee_pct ?? 5);\n",
            'src/components/wallet/walletHelpers.js':
                "// Withdrawal fee\nexport const calcWithdrawFee = (ngn) => Math.round(ngn * 0.02) + 50;\n",
        })
        cases.append(('fee_pct ?? 5 on a screen and * 0.02 in a helper', check(b, f),
                      ['rate as a fallback', 'rate as maths']))

        # 8. the coin unit in prose is allowed, and so is a template
        b, f = _fixture(tmp, 'unit', copy=CLEAN_COPY + """
export const pt = { 'terms.coins': 'VENT COINS: 1.000 nairas é 1 VENT COIN. Toda taxa é contada em moedas.',
  'buy.fee': 'Taxa de serviço ({pct}% + {flat} nairas)' };
""")
        cases.append(('the coin unit and a template are not rates', check(b, f), []))

    for name, problems, wanted in cases:
        kinds = sorted({p[0] for p in problems})
        ok = kinds == sorted(set(wanted))
        passed += ok
        print('  %s  %s -> %s' % ('ok  ' if ok else 'FAIL', name, kinds or '0 problems'))
        if not ok:
            for p in problems:
                print('        ', p)
    print('self-test: %d/%d' % (passed, len(cases)))
    return 0 if passed == len(cases) else 1


if __name__ == '__main__':
    # Copy carries the naira sign, and a Windows console defaults to cp1252.
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    root = _workspace_root()
    sys.exit(report(check(os.path.join(root, 'V-ENT-BACKEND'), os.path.join(root, 'V-ENT-FRONTEND'))))

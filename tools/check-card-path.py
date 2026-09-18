#!/usr/bin/env python3
"""Every door that can refuse for want of coins offers a card instead.

CEO, 13 September 2026:

    "Also i hope people can still bu stuff directly on the platform without
    having to buy V-ENT coins, that option must always be vaailable."

"Always" is the part a checker exists for. On the day it was asked, ONE door
in the platform took a card (a guest buying a ticket) and the tournament
register had hand-built its own way through. Five other doors refused and sent
people to the wallet to buy coins as an errand. The rule this holds:

    a backend view that refuses with INSUFFICIENT_BALANCE (or
    INSUFFICIENT_FUNDS) must have a screen that mounts <PayShortfall>

    python tools/check-card-path.py              the tree; exit 1 on a gap
    python tools/check-card-path.py --self-test  proves each rule on fixtures

The door-to-screen map is written down here on purpose. A checker that tried
to infer which screen presses which endpoint would be guessing, and a wrong
guess in either direction is worse than a table somebody has to add a line to:
adding the line is the moment you notice the new door needs a card.
"""
import os
import re
import sys
import tempfile

MARKER = 'PayShortfall'
REFUSALS = re.compile(r"'(INSUFFICIENT_BALANCE|INSUFFICIENT_FUNDS)'")

#: backend file -> the screens that press it. A door with more than one screen
#: needs the card on every one of them: somebody buying from the stall page and
#: somebody buying from the cart are the same purchase and the same refusal.
DOORS = {
    'vent_event/views_tickets.py': ['src/app/events/view-event/page.js'],
    'vent_event/views_vendors.py': ['src/app/events/vendor-shop/vendor/page.js'],
    'vent_event/views_vendor_slots.py': ['src/components/vendor-slots/TradeHere.js'],
    'vent_tournament/views.py': [
        'src/components/view-tournament/tournament-register/payment/Payment.js'],
    # Premium and the comics refuse from a service module and answer from a
    # view, so both files name the code and both are listed: the pair is the
    # door, and a card on one screen is what covers both.
    'vent_auth/premium_sale.py': ['src/app/premium/PremiumClient.js'],
    'vent_auth/views_premium.py': ['src/app/premium/PremiumClient.js'],
    'vent_anime/money.py': [
        'src/app/anime/read/[slug]/ReaderClient.js',
        'src/app/anime/manga/[slug]/SeriesClient.js'],
    'vent_anime/views_reader.py': [
        'src/app/anime/read/[slug]/ReaderClient.js',
        'src/app/anime/manga/[slug]/SeriesClient.js'],
}

#: Refusals that are NOT a purchase, with the reason. Each one is a place
#: somebody is moving their own coins or being paid, where "pay with a card"
#: would mean something else or nothing at all.
NOT_A_PURCHASE = {
    'vent_auth/wallets.py':
        'sending coins to somebody else IS coins; a card would be a top-up '
        'followed by a send, which the wallet already offers as two steps',
    'vent_auth/views_wallet.py':
        'a withdrawal is money leaving, not a purchase',
    'vent_event/views_vendor_shop.py':
        'refunding a cancelled order out of the stallholder is the seller side',
    'vent_marketplace/holds.py':
        'Vermillion City is closed behind MARKETPLACE_ENABLED; it gets the '
        'card the day it opens, and this line is the reminder',
    'vent_tournament/services/prizes.py':
        'an organiser funding prizes is the seller side, and the shortfall is '
        'named in the refusal so they know what to top up',
    'vent_billing/charging.py':
        'a subscription already charges a saved card by itself',
}

SKIP_DIRS = {'venv', 'vent', '__pycache__', 'migrations', '.git', 'node_modules',
             'tools', 'docs', 'media', 'staticfiles', 'management'}
SKIP_FILE = re.compile(r'^(tests?_.*|tests?|factories|conftest)\.py$')


def _workspace_root():
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND')):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        here = parent


def refusing_files(backend):
    """Backend files that refuse a buyer for want of coins."""
    out = []
    for root, dirs, files in os.walk(backend):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for name in files:
            if not name.endswith('.py') or SKIP_FILE.match(name):
                continue
            path = os.path.join(root, name)
            text = open(path, encoding='utf-8', errors='replace').read()
            if REFUSALS.search(text):
                out.append(os.path.relpath(path, backend).replace('\\', '/'))
    return sorted(out)


def check(backend, frontend):
    problems = []
    doors = refusing_files(backend)

    for rel in doors:
        if rel in NOT_A_PURCHASE:
            continue
        screens = DOORS.get(rel)
        if screens is None:
            problems.append((
                'a door nobody has placed', rel,
                'refuses for want of coins and is in neither DOORS nor '
                'NOT_A_PURCHASE in this checker. Say which it is.'))
            continue
        for screen in screens:
            path = os.path.join(frontend, screen)
            if not os.path.exists(path):
                problems.append(('a screen that is not there', screen,
                                 'named as the screen for %s' % rel))
                continue
            if MARKER not in open(path, encoding='utf-8', errors='replace').read():
                problems.append((
                    'no card option', screen,
                    'presses %s, which can refuse for want of coins, and does '
                    'not mount %s' % (rel, MARKER)))

    # The other way: a screen listed here that no longer presses a refusing
    # door is a line to delete, not a rule to keep passing.
    for rel in DOORS:
        if rel not in doors:
            problems.append((
                'a door that no longer refuses', rel,
                'is in the DOORS table but refuses nothing for want of coins. '
                'Take the line out.'))

    return problems


def report(problems):
    for kind, where, why in problems:
        print('  %-28s %s' % (kind, where))
        print('  %-28s %s' % ('', why))
    print('card path: %d door(s) with no card option' % len(problems))
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

REFUSING_VIEW = """
def buy(request):
    if wallet.wallet_balance < total_vc:
        return _error('not enough', 'INSUFFICIENT_BALANCE', 400)
"""
QUIET_VIEW = "def look(request):\n    return _ok({})\n"
SCREEN_WITH = "import PayShortfall from '@/components/pay/PayShortfall';\n<PayShortfall needVc={n} />\n"
SCREEN_WITHOUT = "<button onClick={buy}>Pay</button>\n"


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def _fixture(tmp, name, *, door=REFUSING_VIEW, screen=SCREEN_WITH,
             door_rel='vent_event/views_tickets.py',
             screen_rel='src/app/events/view-event/page.js', extra=None):
    backend = os.path.join(tmp, name, 'V-ENT-BACKEND')
    frontend = os.path.join(tmp, name, 'V-ENT-FRONTEND')
    _write(backend, door_rel, door)
    if screen is not None:
        _write(frontend, screen_rel, screen)
    for rel, text in (extra or {}).items():
        target = backend if rel.endswith('.py') else frontend
        _write(target, rel, text)
    return backend, frontend


def self_test():
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        # Only the ticket door exists in these fixtures, so every other row of
        # DOORS is "no longer refuses". Checked by kind rather than by count.
        def kinds(problems, ignore=('a door that no longer refuses',)):
            return sorted({k for k, _w, _y in problems if k not in ignore})

        b, f = _fixture(tmp, 'ok')
        cases.append(('a door with the card mounted passes', kinds(check(b, f)), []))

        b, f = _fixture(tmp, 'missing', screen=SCREEN_WITHOUT)
        cases.append(('a door whose screen has no card fails',
                      kinds(check(b, f)), ['no card option']))

        b, f = _fixture(tmp, 'noscreen', screen=None)
        cases.append(('a screen named and not there fails',
                      kinds(check(b, f)), ['a screen that is not there']))

        b, f = _fixture(tmp, 'unplaced', extra={
            'vent_shop/views_buy.py': REFUSING_VIEW})
        cases.append(('a NEW door nobody placed fails',
                      kinds(check(b, f)), ['a door nobody has placed']))

        b, f = _fixture(tmp, 'exempt', extra={
            'vent_auth/wallets.py': REFUSING_VIEW})
        cases.append(('a refusal named as not-a-purchase is allowed',
                      kinds(check(b, f)), []))

        b, f = _fixture(tmp, 'stale', door=QUIET_VIEW)
        cases.append(('a door that stopped refusing is reported',
                      sorted({k for k, _w, _y in check(b, f)}),
                      ['a door that no longer refuses']))

    passed = 0
    for name, got, want in cases:
        ok = got == sorted(set(want))
        passed += ok
        print('  %s  %s -> %s' % ('ok  ' if ok else 'FAIL', name, got or '0 problems'))
    print('self-test: %d/%d' % (passed, len(cases)))
    return 0 if passed == len(cases) else 1


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    root = _workspace_root()
    sys.exit(report(check(os.path.join(root, 'V-ENT-BACKEND'),
                          os.path.join(root, 'V-ENT-FRONTEND'))))

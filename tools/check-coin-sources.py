#!/usr/bin/env python3
"""Every place that adds VENT COINS to a wallet, each with the payment behind it.

CEO, 29 September 2026: "the only way coins should exist on the site is if
someone buys them, cause coins will soon be equivalent to real money."

That day production held 47,500 coins nobody bought: the demo seeder wrote
balances straight onto wallets, a `grant_founding_bonus` command could credit
every founding member, and a waitlist claim could pay a bonus from a setting.

A coin may only ARRIVE in a wallet by being bought, or by MOVING from where
somebody paid it (a transfer, a refund of a payment, a prize from a pool of
entry fees, a seller paid for an order). So every function that adds to a
balance (`wallet_balance +=`) or calls the credit primitive (`.credit(`) is
listed below with the reason its coins were paid for. A new one fails this
check until somebody lists it and says where its coins came from; that is the
point at which a mint gets noticed.

    python tools/check-coin-sources.py
    python tools/check-coin-sources.py --list        every site found
    python tools/check-coin-sources.py --self-test
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, function): where the coins came from.
ALLOWED = {
    ('vent_auth/pay.py', '_credit'): 'a card payment confirmed by the gateway',
    ('vent_auth/views_wallet.py', 'settle_flutterwave_topup'): 'a Flutterwave payment verified with Flutterwave',
    ('vent_auth/views_wallet.py', 'topup_verify'): 'a Paystack payment verified with Paystack',
    ('vent_auth/wallets.py', 'transfer'): 'moved from the sender, debited in the same transaction',
    ('vent_auth/wallets.py', 'return_payout'): 'a withdrawal returned: the coins were already the owner\'s',
    ('vent_billing/charging.py', '_collect_from_card'): 'a saved card charged by Paystack',
    ('vent_billing/charging.py', 'refund'): 'a paid subscription invoice refunded',
    ('vent_billing/ledger.py', 'settle'): 'subscribers\' paid invoices paid to the plan owner',
    ('vent_event/ledger.py', 'settle'): 'ticket and entry sales paid to the organiser',
    ('vent_event/refunds.py', 'refund_ticket'): 'a paid ticket refunded to whoever paid',
    ('vent_event/views_vendors.py', 'create_order'): "the vendor's share of an order the buyer paid for",
    ('vent_event/views_vendor_review.py', 'decide_stall'): 'a paid stall order refunded when the stall is refused',
    ('vent_event/views_vendor_shop.py', '_refund_cancelled'): 'a paid order refunded to the buyer',
    ('vent_event/views_vendor_slots.py', 'buy_slot'): "the vendor's debit for the slot, paid to the organiser in the same transaction",
    ('vent_tournament/services/wallet.py', 'credit'): 'the transfer primitive: every caller is listed here too',
    ('vent_tournament/services/prizes.py', 'distribute'): 'a prize from the entry-fee pool or the organiser\'s own wallet',
    ('vent_tournament/views_bracket.py', 'cancel_tournament'): 'paid entry fees refunded',
    ('vent_tournament/views_checkin.py', '_refund_no_show'): 'a paid entry fee refunded (organiser option)',
    ('vent_auth/views_admin.py', 'admin_cancel_tournament'): 'paid entry fees refunded',
    ('vent_anime/money.py', '_move'): 'a reader\'s payment, less the fee, to the author',
    ('vent_marketplace/holds.py', 'release'): 'an escrowed payment released to the seller',
    ('vent_marketplace/holds.py', 'refund'): 'an escrowed payment returned to the buyer',
    # Demo balances, written only when settings.DEBUG (self.coins): a
    # development box, never production.
    ('vent_auth/management/commands/seed_demo.py', 'make_user'): 'development only (self.coins is DEBUG)',
    ('vent_auth/management/commands/seed_demo.py', 'make_wallet_history'): 'development only (self.coins is DEBUG)',
    ('vent_auth/management/commands/seed_demo.py', 'make_admin_queues'): 'development only (self.coins is DEBUG)',
}


def _cannot_raise(value):
    """An assignment that can only lower a balance or zero it: `= 0`, `= x - y`."""
    if isinstance(value, ast.Constant) and value.value == 0:
        return True
    return isinstance(value, ast.BinOp) and isinstance(value.op, ast.Sub)


def sites_in(tree, rel):
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add)
                    and getattr(node.target, 'attr', '') == 'wallet_balance'):
                found.append((rel, fn.name, node.lineno, 'wallet_balance +='))
            elif (isinstance(node, ast.Assign)
                  and any(getattr(t, 'attr', '') == 'wallet_balance' for t in node.targets)
                  and not _cannot_raise(node.value)):
                # The seeder minted this way: `wallet.wallet_balance = 2500`.
                found.append((rel, fn.name, node.lineno, 'wallet_balance ='))
            elif (isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'credit'
                  and isinstance(node.func, ast.Attribute)):
                found.append((rel, fn.name, node.lineno, '.credit('))
    # An inner function is walked twice (as itself and inside its parent);
    # the innermost name is the one that owns the line.
    best = {}
    for rel_, name, line, kind in found:
        best[(line, kind)] = (rel_, name, line, kind)
    return list(best.values())


def sources():
    for top in sorted(os.listdir(REPO)):
        if not top.startswith('vent') or not os.path.isdir(os.path.join(REPO, top)):
            continue
        for dirpath, dirnames, files in os.walk(os.path.join(REPO, top)):
            dirnames[:] = [d for d in dirnames if d not in ('migrations', '__pycache__')]
            for f in files:
                if f.endswith('.py') and not f.startswith('tests'):
                    full = os.path.join(dirpath, f)
                    yield os.path.relpath(full, REPO).replace(os.sep, '/'), full


def self_test():
    cases = [
        ('a balance written up in an unknown function', 'def gift(w):\n    w.wallet_balance += 5\n', 1),
        ('a credit call in an unknown function', 'def bonus(w):\n    svc.credit(w, 2, tx_type="prize")\n', 1),
        ('a balance assigned a number (the seeder)', 'def seed(w):\n    w.wallet_balance = 2500\n', 1),
        ('lowering by assignment is not a mint', 'def take(w, n):\n    w.wallet_balance = w.wallet_balance - n\n', 0),
        ('setting a balance to zero is not a mint', 'def clear(w):\n    w.wallet_balance = 0\n', 0),
        ('a debit is not a mint', 'def spend(w):\n    w.wallet_balance -= 5\n', 0),
    ]
    failed = 0
    for label, src, want in cases:
        got = len([s for s in sites_in(ast.parse(src), 'x.py') if (s[0], s[1]) not in ALLOWED])
        ok = got == want
        failed += not ok
        print('%s %s: %d (want %d)' % ('ok  ' if ok else 'FAIL', label, got, want))
    listed = sites_in(ast.parse('def transfer(w):\n    w.wallet_balance += 1\n'), 'vent_auth/wallets.py')
    ok = all((s[0], s[1]) in ALLOWED for s in listed)
    failed += not ok
    print('%s a listed site passes' % ('ok  ' if ok else 'FAIL'))
    print('%d self-test case(s) pass' % (len(cases) + 1 - failed) if not failed else '%d FAILED' % failed)
    return failed == 0


def main():
    if '--self-test' in sys.argv:
        sys.exit(0 if self_test() else 1)
    found = []
    for rel, full in sources():
        try:
            tree = ast.parse(open(full, encoding='utf-8').read())
        except SyntaxError:
            continue
        found += sites_in(tree, rel)
    if '--list' in sys.argv:
        for rel, name, line, kind in sorted(found):
            print('%s:%d %s %s' % (rel, line, name, kind))
    unlisted = [s for s in found if (s[0], s[1]) not in ALLOWED]
    for rel, name, line, kind in sorted(unlisted):
        print('  %s:%d  %s() adds coins with no payment named (%s)' % (rel, line, name, kind))
    used = {(s[0], s[1]) for s in found}
    for key in sorted(set(ALLOWED) - used):
        print('  listed but not found: %s %s() (renamed or removed? update the list)' % key)
    stale = len(set(ALLOWED) - used)
    print('%d place(s) add coins, %d with no payment named, %d stale entr(y/ies)'
          % (len(found), len(unlisted), stale))
    sys.exit(1 if unlisted or stale else 0)


if __name__ == '__main__':
    main()

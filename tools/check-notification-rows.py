#!/usr/bin/env python3
"""Every notification category belongs to a row a person can switch.

CEO, 30 September 2026: "All the switches must work as listed or shown in
their profiles." A category with no row in `vent_auth/notify_prefs.py` falls
into the always-on "account" row: it would reach the inbox and email of people
who switched that kind of thing off. So every category passed to
`create_notification` must be listed in CATEGORY_ROW, and every row it names
must exist.

    python tools/check-notification-rows.py
    python tools/check-notification-rows.py --self-test
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def prefs():
    tree = ast.parse(open(os.path.join(REPO, 'vent_auth', 'notify_prefs.py'), encoding='utf-8').read())
    mapping, rows = {}, []
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'CATEGORY_ROW':
            mapping = ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'ROWS':
            rows = [ast.literal_eval(elt)['id'] for elt in node.value.elts]
    return mapping, rows


def categories_in(tree):
    """Literal categories passed to create_notification: (category, line)."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'id', getattr(node.func, 'attr', '')) != 'create_notification':
            continue
        value = next((k.value for k in node.keywords if k.arg == 'category'), None)
        if value is None and len(node.args) >= 2:
            value = node.args[1]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.append((value.value, node.lineno))
        elif value is not None:
            out.append((None, node.lineno))
    return out


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


def problems(found, mapping, rows):
    out = []
    for rel, category, line in found:
        if category is None:
            out.append('%s:%d a category that is not a literal; name it so it can be mapped' % (rel, line))
        elif category not in mapping:
            out.append('%s:%d category %r has no row in notify_prefs.CATEGORY_ROW' % (rel, line, category))
    for category, row in mapping.items():
        if row not in rows:
            out.append('CATEGORY_ROW sends %r to row %r, which does not exist' % (category, row))
    return out


def self_test():
    mapping, rows = {'tournament': 'tournaments'}, ['tournaments']
    cases = [
        ('a mapped category', "create_notification(u, 'tournament', 't')", 0),
        ('an unmapped category', "create_notification(u, 'raffle', 't')", 1),
        ('a keyword category', "create_notification(user=u, category='raffle', title='t')", 1),
        ('a variable category', "create_notification(u, kind, 't')", 1),
    ]
    failed = 0
    for label, src, want in cases:
        found = [('x.py', c, l) for c, l in categories_in(ast.parse(src))]
        got = len(problems(found, mapping, rows))
        failed += got != want
        print('%s %s: %d (want %d)' % ('ok  ' if got == want else 'FAIL', label, got, want))
    got = len(problems([], {'tournament': 'gone'}, rows))
    failed += got != 1
    print('%s a row that does not exist: %d (want 1)' % ('ok  ' if got == 1 else 'FAIL', got))
    print('%d self-test case(s) pass' % (len(cases) + 1 - failed) if not failed else '%d FAILED' % failed)
    return failed == 0


def main():
    if '--self-test' in sys.argv:
        sys.exit(0 if self_test() else 1)
    mapping, rows = prefs()
    found = []
    for rel, full in sources():
        try:
            tree = ast.parse(open(full, encoding='utf-8').read())
        except SyntaxError:
            continue
        found += [(rel, c, l) for c, l in categories_in(tree)]
    bad = problems(found, mapping, rows)
    for line in bad:
        print('  ' + line)
    print('%d notification call(s) read, %d without a row a person can switch' % (len(found), len(bad)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()

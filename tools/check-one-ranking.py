#!/usr/bin/env python3
"""A standings list is ranked in one place: vent_tournament/ranking.py.

CEO, 16 October 2026, agreeing the wrong results go first: "these are fine."
Seven places in the code each sorted a table their own way (a stage group
table, a stage Swiss, the league table, the league player table, battle
royale, winner stays on, seeding the next stage by record), and three of them
gave wrong results: the organiser's tie-breakers ignored, a fixed 3-1-0 Swiss,
one-format tables ranked by wins. They all call `ranking.order` now. This
catcher fails the commit that brings an eighth back.

What it flags, in vent_tournament (tests and migrations excluded): a `sorted()`
or `.sort()` whose key puts points first, negated, the shape
six of the seven had (winner stays on sorted by wins, and its tests hold it):
`-r['points']`, `-row['points']`, `-row.points`, directly in a lambda or in a key
function named in the same scope. Run against the code before 16 October it
finds those six.

    python tools/check-one-ranking.py
    python tools/check-one-ranking.py --self-test
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, 'vent_tournament')
ALLOWED = {'ranking.py'}


def _negated_points(node):
    """Whether this expression negates a points value somewhere."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.UnaryOp) and isinstance(sub.op, ast.USub):
            operand = sub.operand
            if isinstance(operand, ast.Subscript):
                key = operand.slice
                if isinstance(key, ast.Constant) and key.value == 'points':
                    return True
            if isinstance(operand, ast.Attribute) and operand.attr == 'points':
                return True
    return False


def _helpers_in(scope):
    """Key functions and key lambdas named in this scope, not in nested ones."""
    found = {}
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
            continue                     # its insides are its own scope
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
        stack.extend(ast.iter_child_nodes(node))
    return found


def offenders(source, name='<source>'):
    """Line numbers of sorts by points in this source.

    A key named rather than written inline is looked up in the function the
    sort sits in first, then outwards: one file had five functions each with
    its own local `key`, and a file-wide lookup saw only the last of them.
    """
    tree = ast.parse(source, name)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    scopes = {}

    def resolve(call, wanted):
        node = call
        while node is not None:
            node = parents.get(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)) or node is None:
                scope = node if node is not None else tree
                if scope not in scopes:
                    scopes[scope] = _helpers_in(scope)
                if wanted in scopes[scope]:
                    return scopes[scope][wanted]
                if node is None:
                    return None
        return None

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_sort = ((isinstance(func, ast.Name) and func.id == 'sorted')
                   or (isinstance(func, ast.Attribute) and func.attr == 'sort'))
        if not is_sort:
            continue
        for kw in node.keywords:
            if kw.arg != 'key':
                continue
            target = kw.value
            if isinstance(target, ast.Name):
                target = resolve(node, target.id) or target
            if _negated_points(target):
                found.append(node.lineno)
    return found


def scan():
    hits = []
    for folder, _dirs, files in os.walk(ROOT):
        if 'migrations' in folder or '__pycache__' in folder:
            continue
        for fname in files:
            if not fname.endswith('.py') or fname in ALLOWED or fname.startswith('tests'):
                continue
            if fname == 'test_settings.py':
                continue
            path = os.path.join(folder, fname)
            with open(path, encoding='utf-8') as fh:
                for line in offenders(fh.read(), path):
                    hits.append('%s:%d' % (os.path.relpath(path, REPO).replace(os.sep, '/'), line))
    return hits


OLD_TABLE = '''
def _rank_table(rows, matches, pts):
    key = lambda r: (-r['points'], -h2h.get(r['registration_id'], 0), -r['goal_difference'])
    ranked = sorted(rows, key=key)
    return ranked
'''

OLD_LEAGUE = '''
def _order(table, rules, beat):
    def key(row):
        parts = [-row['points']]
        return parts
    table.sort(key=key)
'''

OLD_INLINE = '''
chosen.sort(key=lambda r: (-r['points'], -r['goal_difference'], r['seed'] or 10 ** 6))
'''

TWO_SCOPES = '''
def streak(rows):
    key = lambda r: (-r['wins'], r['seed'])
    rows.sort(key=key)

def swiss(rows):
    key = lambda r: (-r['points'], -r['buchholz'])
    return sorted(rows, key=key)

def later(rows):
    key = lambda r: (r['rank'], r['seed'])
    rows.sort(key=key)
'''

NOW = '''
from . import ranking
rows = ranking.order(rows, ['goal_difference'])
rows.sort(key=lambda r: (r['rank'] is None, r['rank'] or 0, r['seed'] or 10 ** 6))
ordered = sorted(items, key=lambda kv: -kv[1])
'''


def self_test():
    cases = [
        ('the old stage table key', OLD_TABLE, 1),
        ('the old league key function', OLD_LEAGUE, 1),
        ('the old seeding by record', OLD_INLINE, 1),
        ('a local key beside other keys', TWO_SCOPES, 1),
        ('ranking.order and sorts by rank', NOW, 0),
    ]
    failed = 0
    for label, source, expected in cases:
        got = len(offenders(source, label))
        mark = 'ok  ' if got == expected else 'FAIL'
        if got != expected:
            failed += 1
        print('%s %-34s expected %d, found %d' % (mark, label, expected, got))
    if failed:
        print('self-test FAILED: %d case(s)' % failed)
        return 1
    print('self-test passed')
    return 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    hits = scan()
    for hit in hits:
        print('  ' + hit + '  sorts a standings list by points: call ranking.order instead')
    print('%d place(s) ranking a table outside ranking.py' % len(hits))
    return 1 if hits else 0


if __name__ == '__main__':
    sys.exit(main())

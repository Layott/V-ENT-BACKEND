#!/usr/bin/env python3
"""A bracket link is written in one place: vent_tournament/match_slots.py (P1a).

CEO, 16 October 2026: "i aprove the design". Section 8 of the bracket
structure design: one link store, the source on the receiving slot. Until P1a
the generators set `winner_to_match` and `loser_to_match` (and their slots) by
hand in 47 places, and nothing said where a seat's player would come from.
`match_slots.link` now writes each pointer together with its `MatchSlot` row,
so the two cannot disagree.

Fails when any file other than match_slots.py WRITES one of the six pointer
fields: an assignment, a `setattr` with the field's name, a keyword in
`.create()`/`.update()`/`get_or_create()`/`update_or_create()` (and its
`defaults`), or the field named in `save(update_fields=...)`.

Counts, without failing, every place that still READS a pointer outside
match_slots.py (an attribute read, a lookup keyword, a `.values()` name). The
design moves the readers onto the slots one at a time; the debt ledger holds
that count so it can only fall.

    python tools/check-one-link-writer.py
    python tools/check-one-link-writer.py --self-test
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = ('vent_tournament', 'vent_auth', 'vent_event')
ALLOWED = {'match_slots.py'}
FIELDS = {'winner_to_match', 'winner_to_match_id', 'winner_to_slot',
          'loser_to_match', 'loser_to_match_id', 'loser_to_slot'}
WRITE_CALLS = {'create', 'update', 'get_or_create', 'update_or_create'}


def _field_of(name):
    """The pointer field a name or lookup is about ('winner_to_match_id__in'), else None."""
    if not isinstance(name, str):
        return None
    head = name.split('__', 1)[0]
    return head if head in FIELDS else None


def scan(source, filename):
    """(writes, reads) in this source: lists of (line, what)."""
    tree = ast.parse(source, filename)
    writes, reads = [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Attribute) and sub.attr in FIELDS:
                        writes.append((node.lineno, 'assigns %s' % sub.attr))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
            if name == 'setattr' and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and _field_of(node.args[1].value):
                writes.append((node.lineno, 'setattr %s' % node.args[1].value))
            for kw in node.keywords:
                field = _field_of(kw.arg)
                if field and name in WRITE_CALLS:
                    writes.append((node.lineno, '%s(%s=...)' % (name, kw.arg)))
                elif field:
                    reads.append((node.lineno, '%s(%s=...)' % (name, kw.arg)))
                if kw.arg == 'defaults' and name in WRITE_CALLS and isinstance(kw.value, ast.Dict):
                    for key in kw.value.keys:
                        if isinstance(key, ast.Constant) and _field_of(key.value):
                            writes.append((node.lineno, 'defaults %s' % key.value))
                if kw.arg == 'update_fields' and isinstance(kw.value, (ast.List, ast.Tuple)):
                    for item in kw.value.elts:
                        if isinstance(item, ast.Constant) and _field_of(item.value):
                            writes.append((node.lineno, 'saves %s' % item.value))
            if name in ('values', 'values_list', 'only', 'select_related', 'order_by'):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and _field_of(arg.value):
                        reads.append((node.lineno, '%s(%r)' % (name, arg.value)))
        elif isinstance(node, ast.Attribute) and node.attr in FIELDS and isinstance(node.ctx, ast.Load):
            reads.append((node.lineno, 'reads %s' % node.attr))
    return writes, reads


def files():
    for app in APPS:
        root = os.path.join(REPO, app)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ('migrations', '__pycache__')]
            for fn in filenames:
                if fn.endswith('.py') and not fn.startswith('tests'):
                    yield os.path.join(dirpath, fn)


def run():
    writers, readers = [], 0
    for path in files():
        with open(path, encoding='utf-8') as fh:
            writes, reads = scan(fh.read(), path)
        rel = os.path.relpath(path, REPO).replace(os.sep, '/')
        if os.path.basename(path) in ALLOWED:
            continue
        writers += ['%s:%d %s' % (rel, line, what) for line, what in writes]
        readers += len(reads)
    for w in writers:
        print('  writes a bracket link outside match_slots.py: %s' % w)
    print('%d pointer writer(s) outside match_slots.py, %d pointer reader(s) still to move'
          % (len(writers), readers))
    return 1 if writers else 0


CASES = [
    ('other.py', 'm.winner_to_match = t\n', 1, 0),
    ('other.py', 'BracketMatch.objects.filter(pk=1).update(loser_to_match_id=3)\n', 1, 0),
    ('other.py', 'if m.winner_to_match_id:\n    pass\n', 0, 1),
    ('other.py', 'm.save(update_fields=["winner_to_slot"])\n', 1, 0),
    ('other.py', 'setattr(m, "loser_to_slot", 2)\n', 1, 0),
    ('other.py', 'BracketMatch.objects.filter(winner_to_match_id__in=ids)\n', 0, 1),
    ('other.py', 'MatchSlot.objects.update_or_create(match=m, defaults={"winner_to_match": t})\n', 1, 0),
    ('match_slots.py', 'm.winner_to_match = t\n', 1, 0),
]


def self_test():
    failed = 0
    for name, source, want_w, want_r in CASES:
        writes, reads = scan(source, name)
        got = (len(writes), len(reads))
        if got != (want_w, want_r):
            failed += 1
            print('  FAIL %s %r: writes %d reads %d, wanted %d %d'
                  % (name, source.strip(), got[0], got[1], want_w, want_r))
    # match_slots.py is the one file allowed to write: run() skips it.
    print('%d/%d self-test cases' % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(self_test() if '--self-test' in sys.argv else run())

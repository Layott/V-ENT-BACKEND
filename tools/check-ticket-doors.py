#!/usr/bin/env python3
"""Every door that makes a ticket asks the room first.

A ticket is a place in a finite room. `vent_event/availability.py` is the one
answer to "is there a place": the type's own stock, the venue's capacity (the
lower wins) and everything held back. A door that makes tickets without asking
it oversells, and this has shipped three times:

    28 August 2026     the capacity was enforced by no door at all
    12 September 2026  the paid guest checkout held nothing while at the gateway
    27 October 2026    comps read the type's stock and never the room, and a
                       guest at the gateway held their type but not the room
                       (inbox 564, CEO: "check it let's know what we'll do")

The rule this holds:

    a function that creates a Ticket either asks `availability` (itself, or
    through a function in the same file that does), or is named in ELSEWHERE
    below with the reason the room was already asked

    python tools/check-ticket-doors.py              the tree; exit 1 on a gap
    python tools/check-ticket-doors.py --self-test  proves each rule on fixtures
"""
import ast
import os
import re
import sys
import tempfile

#: file::function -> why this door does not ask the room itself. Adding a line
#: here is the moment somebody has to say where the room WAS asked.
ELSEWHERE = {
    'vent_event/views_guest.py::_issue':
        'turns an order into tickets; guest_buy asked _room_or_error under the '
        'lock before it, and a gateway return issues what was already paid '
        'for while its seats were held in flight',
    'vent_event/views_holds.py::issue_hold':
        'issues seats the hold already took out of the room when it was made '
        '(holds, on POST, asks availability)',
    'vent_tournament/rewards.py::issue':
        'a ticket earned by a tournament result is never refused for want of '
        'room; the organiser chose the tier when linking the tournament',
}

TICKET_NAME = re.compile(r'^_?Ticket$')
MAKERS = {'create', 'bulk_create', 'get_or_create'}

SKIP_DIRS = {'venv', 'vent', '__pycache__', 'migrations', '.git', 'node_modules',
             'tools', 'docs', 'media', 'staticfiles', 'management', 'security'}
SKIP_FILE = re.compile(r'^(tests?_.*|tests?|factories|conftest)\.py$')


def _makes_ticket(call):
    """Ticket.objects.create(...) and its kin, or event.tickets.create(...)."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in MAKERS:
        return False
    owner = func.value
    if isinstance(owner, ast.Attribute) and owner.attr == 'objects':
        return isinstance(owner.value, ast.Name) and bool(TICKET_NAME.match(owner.value.id))
    return isinstance(owner, ast.Attribute) and owner.attr == 'tickets'


def _asks_room(node):
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id == 'availability'):
            return True
    return False


def _called_names(node):
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            out.add(sub.func.id)
    return out


def doors_in(path):
    """(function, asks the room) for each function in `path` making tickets."""
    try:
        tree = ast.parse(open(path, encoding='utf-8', errors='replace').read())
    except SyntaxError:
        return []
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    asking = {f.name for f in funcs if _asks_room(f)}
    out = []
    for f in funcs:
        if not any(isinstance(s, ast.Call) and _makes_ticket(s) for s in ast.walk(f)):
            continue
        out.append((f.name, f.name in asking or bool(_called_names(f) & asking)))
    return out


def check(backend):
    problems = []
    seen = set()
    for root, dirs, files in os.walk(backend):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for name in files:
            if not name.endswith('.py') or SKIP_FILE.match(name):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, backend).replace('\\', '/')
            for func, asks in doors_in(path):
                key = '%s::%s' % (rel, func)
                seen.add(key)
                if asks or key in ELSEWHERE:
                    continue
                problems.append((
                    'a door that never asks the room', key,
                    'creates tickets without asking vent_event/availability.py. '
                    'Ask it, or name it in ELSEWHERE with where the room was asked.'))
    for key in ELSEWHERE:
        if key not in seen:
            problems.append((
                'a line for a door that is gone', key,
                'is in ELSEWHERE but makes no tickets now. Take the line out.'))
    return problems, len(seen)


def report(problems, doors):
    for kind, where, why in problems:
        print('  %-32s %s' % (kind, where))
        print('  %-32s %s' % ('', why))
    print('ticket doors: %d of %d door(s) do not ask the room' % (len(problems), doors))
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

ASKS = """
from . import availability
def buy(request):
    if availability.available(tier) < 1:
        return refuse()
    Ticket.objects.create(event=event)
"""
ASKS_THROUGH_A_HELPER = """
from . import availability
def _room_or_error(event, tier, n):
    return availability.available(tier) < n
def guest_buy(request):
    if _room_or_error(event, tier, 1):
        return refuse()
    event.tickets.create(code='x')
"""
NEVER_ASKS = """
def comp(request):
    left = tier.quantity - tier.sold
    Ticket.objects.create(event=event)
"""
NOT_A_TICKET = """
def tiers(request):
    TicketTier.objects.create(event=event)
"""


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def self_test():
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        def tree(name, files):
            backend = os.path.join(tmp, name)
            for rel, text in files.items():
                _write(backend, rel, text)
            return backend

        # The ELSEWHERE rows are absent from every fixture, so "gone" is
        # ignored except in the case written for it.
        def kinds(problems, ignore=('a line for a door that is gone',)):
            return sorted({k for k, _w, _y in problems if k not in ignore})

        b = tree('asks', {'vent_event/views_tickets.py': ASKS})
        cases.append(('a door that asks the room passes', kinds(check(b)[0]), []))

        b = tree('helper', {'vent_event/views_guest2.py': ASKS_THROUGH_A_HELPER})
        cases.append(('a door asking through a helper in its file passes',
                      kinds(check(b)[0]), []))

        b = tree('never', {'vent_event/views_comp.py': NEVER_ASKS})
        cases.append(('a door that reads the type stock alone fails',
                      kinds(check(b)[0]), ['a door that never asks the room']))

        b = tree('tier', {'vent_event/views_tiers.py': NOT_A_TICKET})
        cases.append(('making a TicketTier is not making a ticket',
                      kinds(check(b)[0]), []))

        b = tree('tests', {'vent_event/tests_comp.py': NEVER_ASKS})
        cases.append(('a test file is not a door', kinds(check(b)[0]), []))

        b = tree('named', {'vent_tournament/rewards.py': NEVER_ASKS.replace('def comp', 'def issue')})
        cases.append(('a door named in ELSEWHERE is allowed', kinds(check(b)[0]), []))

        b = tree('gone', {'vent_event/views_tickets.py': ASKS})
        cases.append(('an ELSEWHERE line for a door that is gone is reported',
                      sorted({k for k, _w, _y in check(b)[0]}),
                      ['a line for a door that is gone']))

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
    backend = (os.environ.get('VENT_BACKEND')
               or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(report(*check(backend)))

"""Every admin view is gated by the permission TABLE, never by a list beside it.

CEO, 3 September 2026: "ensure to create catchers for errors that have
happended more than once pleasse, add this as a rule."

This is the same fault as `tests_permission_names.py` seen from the other end,
and it is the second time this class has been found.

`tests_permission_names` catches a name that does not exist. This catches a
name that exists and is never asked. On 8 September the console had a written
permission table, a screen that BUILT the role matrix out of that table, and
twenty-two endpoints that ignored it:

    @admin_role_required(ADMIN_ROLES)              # the whole ladder
    def admin_list_users(request):

`ROLE_PERMISSIONS['view_users']` was sitting three files away, carefully
excluding the roles the spec excludes, and nothing read it. So the
administrators screen told a Super Admin that a Financial Manager cannot see
the user list, and a Financial Manager could see the user list. Two tables, one
of them decorative, and the decorative one was the one people read.

The same shape appeared as a literal beside the view:

    @admin_role_required(['super_admin', 'mod_admin'])
    def admin_cancel_tournament(request, tournament_id):

while `ROLE_PERMISSIONS['cancel_tournament']` also named `admin` and
`tournament_admin`. A Tournament Organizer could not cancel a tournament, which
is the one thing their role is named after.

## The rule

The argument to `@admin_role_required` is either

    ROLE_PERMISSIONS['some_name']

or a module-level constant in the SAME file defined as exactly that. Anything
else - a list, a tuple, a set, `ADMIN_ROLES` - is a second permission table,
and a second table is a table that disagrees.

The check is a grep of the source rather than an import graph, for the same
reason as its sibling: the fault is a literal somebody typed, and it is visible
in the text.
"""
import os
import re

from django.test import SimpleTestCase

from .decorators import ROLE_PERMISSIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'venv', 'vent', '.git', '__pycache__', 'node_modules', 'media',
             'static', 'staticfiles'}

# `@admin_role_required( ... )` up to the closing bracket on the same line.
# Every one in this codebase is written on one line, and a decorator that is
# not is caught by the count assertion below rather than passing unseen.
DECORATOR = re.compile(r"@admin_role_required\(\s*(.+?)\s*\)\s*$")

# The only two acceptable shapes.
TABLE_LOOKUP = re.compile(r"^ROLE_PERMISSIONS\[['\"]([a-z_]+)['\"]\]$")
CONSTANT = re.compile(r"^[A-Z][A-Z0-9_]*$")

# `NAME = ROLE_PERMISSIONS['x']` at the top of a module.
BINDING = re.compile(
    r"^([A-Z][A-Z0-9_]*)\s*=\s*ROLE_PERMISSIONS\[['\"]([a-z_]+)['\"]\]\s*$")


def _sources():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(base, name)


def _bindings(text):
    """Module constants in this file that ARE a permission set."""
    found = {}
    for line in text.split('\n'):
        match = BINDING.match(line.strip('\r'))
        if match:
            found[match.group(1)] = match.group(2)
    return found


def audit(text, where='<memory>'):
    """(checked, problems). Shared by the walk and by the self-test."""
    bindings = _bindings(text)
    checked = 0
    problems = []
    for line_no, line in enumerate(text.split('\n'), 1):
        match = DECORATOR.match(line.strip('\r').strip())
        if not match:
            continue
        checked += 1
        argument = match.group(1)

        lookup = TABLE_LOOKUP.match(argument)
        if lookup:
            if lookup.group(1) not in ROLE_PERMISSIONS:
                problems.append('%s:%s names %r, which is not a permission'
                                % (where, line_no, lookup.group(1)))
            continue

        if CONSTANT.match(argument):
            if argument not in bindings:
                problems.append(
                    '%s:%s is gated by %s, which this file does not define as '
                    'ROLE_PERMISSIONS[...]' % (where, line_no, argument))
            elif bindings[argument] not in ROLE_PERMISSIONS:
                problems.append('%s:%s %s names %r, which is not a permission'
                                % (where, line_no, argument,
                                   bindings[argument]))
            continue

        problems.append(
            '%s:%s carries its own role list: %s. Put it in ROLE_PERMISSIONS '
            'and name it here.' % (where, line_no, argument))
    return checked, problems


class AdminViewsReadThePermissionTableTests(SimpleTestCase):
    def test_no_admin_view_carries_its_own_role_list(self):
        checked = 0
        problems = []
        for path in _sources():
            if os.path.basename(path) == os.path.basename(__file__):
                continue
            with open(path, encoding='utf-8', errors='ignore') as handle:
                text = handle.read()
            if '@admin_role_required' not in text:
                continue
            seen, found = audit(text, os.path.relpath(path, ROOT))
            checked += seen
            problems.extend(found)
        self.assertGreaterEqual(
            checked, 50,
            'the matcher found only %d decorators, so it is broken rather '
            'than clean' % checked)
        self.assertEqual(problems, [], '\n'.join([''] + problems))

    # ---- the self-test. A 0 has two meanings without one. -----------------

    def test_it_catches_a_literal_list(self):
        _, problems = audit(
            "@admin_role_required(['super_admin', 'mod_admin'])\n"
            "def admin_cancel_tournament(request, tournament_id):\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('carries its own role list', problems[0])

    def test_it_catches_the_whole_ladder(self):
        _, problems = audit("@admin_role_required(ADMIN_ROLES)\n"
                            "def admin_list_users(request):\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('does not define as', problems[0])

    def test_it_catches_a_permission_that_does_not_exist(self):
        _, problems = audit("@admin_role_required(ROLE_PERMISSIONS['manage_the_moon'])\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('not a permission', problems[0])

    def test_it_catches_a_constant_bound_to_a_literal(self):
        _, problems = audit("READ_ROLES = ['super_admin']\n"
                            "@admin_role_required(READ_ROLES)\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('does not define as', problems[0])

    def test_it_accepts_a_direct_lookup(self):
        checked, problems = audit(
            "@admin_role_required(ROLE_PERMISSIONS['view_users'])\n"
            "def admin_list_users(request):\n")
        self.assertEqual((checked, problems), (1, []))

    def test_it_accepts_a_constant_bound_to_the_table(self):
        checked, problems = audit(
            "READ_ROLES = ROLE_PERMISSIONS['manage_events']\n"
            "@admin_role_required(READ_ROLES)\n"
            "def admin_event_detail(request, event_ref):\n")
        self.assertEqual((checked, problems), (1, []))

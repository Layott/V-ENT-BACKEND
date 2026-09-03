"""Every permission name passed to `may_override` exists.

CEO, 3 September 2026: "ensure to create catchers for errors that have
happended more than once pleasse, add this as a rule."

This one happened twice.

`may_override(user, 'X')` returns False for any X that is not a key of
ROLE_PERMISSIONS, so a misspelt or invented name does not raise: it silently
means "nobody may". The admin path through that code then does not exist, and
nothing says so.

  * `views_standings._organiser_or_admin` carried a comment about exactly this,
    having already been caught once: "There is no 'manage_tournaments'
    permission; naming one that does not exist means may_override always says
    no, and the admin path quietly stops working."
  * And then `production_access.may_run_production`, written on 2 September,
    named `manage_tournaments` anyway. For a day, an admin could not run the
    studio for somebody else's tournament, and the code read as though they
    could. `manage_tournaments` is now a real permission; this test is what
    stops the third time.

The check is a grep of the source rather than an import graph on purpose: a
name is a string, and the fault is a string nobody looked up.
"""
import os
import re

from django.test import SimpleTestCase

from .decorators import ROLE_PERMISSIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'venv', 'vent', '.git', '__pycache__', 'node_modules', 'media', 'static'}

# `may_override(user, 'name')` and `may_override(viewer, "name")`.
CALL = re.compile(r"may_override\(\s*[A-Za-z_][A-Za-z0-9_.]*\s*,\s*['\"]([a-z_]+)['\"]")


def _sources():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(base, name)


class PermissionNamesExistTests(SimpleTestCase):
    def test_every_name_passed_to_may_override_is_a_real_permission(self):
        unknown = []
        seen = 0
        for path in _sources():
            if os.path.basename(path) == os.path.basename(__file__):
                continue
            with open(path, encoding='utf-8', errors='ignore') as handle:
                for line_no, line in enumerate(handle, 1):
                    for name in CALL.findall(line):
                        seen += 1
                        if name not in ROLE_PERMISSIONS:
                            unknown.append('%s:%s %s' % (
                                os.path.relpath(path, ROOT), line_no, name))
        self.assertTrue(seen >= 3, 'the matcher found almost nothing: %d' % seen)
        self.assertEqual(unknown, [], 'permission names that do not exist: %s' % unknown)

    def test_the_matcher_would_catch_an_invented_name(self):
        """The self-test. A checker that passes has two meanings without one."""
        line = "    if may_override(user, 'manage_the_moon'):\n"
        found = CALL.findall(line)
        self.assertEqual(found, ['manage_the_moon'])
        self.assertNotIn(found[0], ROLE_PERMISSIONS)

    def test_the_matcher_reads_the_names_that_are_really_there(self):
        for line, expected in (
            ("if may_override(user, 'manage_tournaments'):\n", 'manage_tournaments'),
            ('return bool(may_override(viewer, "manage_events"))\n', 'manage_events'),
            ("    if may_override(actor, 'cancel_tournament'):\n", 'cancel_tournament'),
        ):
            self.assertEqual(CALL.findall(line), [expected])
            self.assertIn(expected, ROLE_PERMISSIONS)

    def test_the_permissions_the_studio_and_the_results_desk_rely_on_exist(self):
        for name in ('manage_tournaments', 'manage_events', 'cancel_tournament',
                     'override_match_score'):
            self.assertIn(name, ROLE_PERMISSIONS, name)

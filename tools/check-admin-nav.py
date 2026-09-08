"""The admin sidebar and the admin API answer to ONE permission table.

    python tools/check-admin-nav.py
    python tools/check-admin-nav.py --self-test

Two faults, both found on 8 September 2026 and both second occurrences, which
is what the "a fault that happens twice gets a catcher" rule asks for.

## Fault 1: a link that opens a refusal

`AdminNav.js` decided what to show from a `roles` array written beside every
item, ORed with the permission map. Two tables for one question, and they
disagreed in four places: a Financial Manager was offered the Users link the
API refuses, an Admin was offered Rates, a Tournament Organizer was offered
Games. A link that opens a 403 is worse than no link at all, because the person
cannot tell whether they lack the permission or the console is broken.

The same shape, one layer down, was the twenty-two endpoints decorated with
`ADMIN_ROLES` while `ROLE_PERMISSIONS` sat three files away carefully excluding
the roles the spec excludes. `vent_auth/tests_admin_decorator_names.py` holds
that half. This holds the half the frontend owns: every `perms` key in the nav
must be a real permission name.

## Fault 2: a finished page nothing links to

`/admin/kyc` was complete, gated, translated, and in no navigation list
anywhere, so the only way to reach it was to type the address. Nobody typed it.
`/admin/finance`, `/admin/content`, `/admin/admins` and the two detail screens
were about to be the same. So every directory under `src/app/(admin)/admin`
must either appear in the nav or be named here as deliberately unlisted, with
the reason.
"""
import os
import re
import sys


def _workspace_root():
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND')):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        here = parent


ROOT = _workspace_root()
BACKEND = os.path.join(ROOT, 'V-ENT-BACKEND')
FRONTEND = os.path.join(ROOT, 'V-ENT-FRONTEND')

NAV = os.path.join(FRONTEND, 'src', 'components', 'admin', 'AdminNav.js')
ADMIN_PAGES = os.path.join(FRONTEND, 'src', 'app', '(admin)', 'admin')
DECORATORS = os.path.join(BACKEND, 'vent_auth', 'decorators.py')

# A route reached from inside another screen rather than from the sidebar, and
# why. A detail page is opened from its own list; adding it to the sidebar
# would ask somebody to pick a record before they have one.
UNLISTED = {
    'organizations/[slug]': 'opened from a row on /admin/organizations',
    'communities/[slug]': 'opened from a row on /admin/communities',
    'events/[slug]': 'opened from a row on /admin/events',
    'users/[id]': 'opened from a row on /admin/users',
}

PERMS_IN_NAV = re.compile(r"perms:\s*\[([^\]]*)\]")
KEY = re.compile(r"'([a-z_]+)'")
HREF = re.compile(r"href:\s*'/admin/?([^']*)'")
TABLE_KEY = re.compile(r"^\s*'([a-z_]+)':\s*", re.M)


def permission_names(text):
    """Every key of ROLE_PERMISSIONS, read out of the source.

    Read rather than imported: this runs without Django configured, and the
    table is a literal.
    """
    start = text.find('ROLE_PERMISSIONS = {')
    if start < 0:
        return set()
    depth = 0
    end = start
    for index in range(start, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                end = index
                break
    return set(TABLE_KEY.findall(text[start:end]))


def audit(nav_text, known, routes):
    """(problems, checked). Shared by the run and by the self-test."""
    problems = []

    used = []
    for group in PERMS_IN_NAV.findall(nav_text):
        used.extend(KEY.findall(group))
    for name in used:
        if name not in known:
            problems.append(
                'AdminNav asks for %r, which is not a permission. The endpoint '
                'behind that link answers to a different name.' % name)

    if 'roles:' in nav_text:
        problems.append(
            'AdminNav still carries a `roles:` array. That is a second '
            'permission table, and it is how a link that opens a 403 gets '
            'shipped. One key per item, from ROLE_PERMISSIONS.')

    linked = set()
    for href in HREF.findall(nav_text):
        if href:
            linked.add(href.strip('/'))

    for route in sorted(routes):
        if route in linked or route in UNLISTED:
            continue
        problems.append(
            '/admin/%s is a page with no way to reach it: it is in no '
            'navigation list. Add it to AdminNav, or name it in UNLISTED with '
            'the screen it opens from.' % route)

    return problems, len(used)


def routes_on_disk():
    found = set()
    for base, dirs, files in os.walk(ADMIN_PAGES):
        dirs[:] = [d for d in dirs if d != 'node_modules']
        if 'page.js' not in files:
            continue
        relative = os.path.relpath(base, ADMIN_PAGES).replace(os.sep, '/')
        if relative == '.':
            continue
        found.add(relative)
    return found


def self_test():
    """Prove it both ways. A 0 has two meanings without this."""
    known = {'view_users', 'manage_events'}
    cases = [
        ("an invented permission",
         "perms: ['manage_the_moon']\nhref: '/admin/users'",
         known, {'users'}, 'not a permission'),
        ("the second table coming back",
         "roles: ['super'],\nperms: ['view_users']\nhref: '/admin/users'",
         known, {'users'}, 'second permission table'),
        ("a page nothing links to",
         "perms: ['view_users']\nhref: '/admin/users'",
         known, {'users', 'kyc'}, 'no way to reach it'),
    ]
    failures = []
    for name, text, table, routes, expected in cases:
        problems, _ = audit(text, table, routes)
        if not any(expected in p for p in problems):
            failures.append('%s: expected %r, got %s' % (name, expected, problems))

    clean, checked = audit(
        "perms: ['view_users']\nhref: '/admin/users'\n"
        "perms: ['manage_events']\nhref: '/admin/events'\n"
        "href: '/admin'\nperms: null",
        known, {'users', 'events'})
    if clean:
        failures.append('a clean nav was reported as broken: %s' % clean)
    if checked != 2:
        failures.append('counted %d permission keys, expected 2' % checked)

    # A detail route named in UNLISTED is not a fault.
    exempt, _ = audit("perms: ['view_users']\nhref: '/admin/users'",
                      known, {'users', 'users/[id]'})
    if exempt:
        failures.append('an exempt detail route was reported: %s' % exempt)

    for line in failures:
        print('SELF-TEST FAIL  ' + line)
    print('%d self-test case(s), %d failed' % (len(cases) + 2, len(failures)))
    return 1 if failures else 0


def main():
    if '--self-test' in sys.argv:
        return self_test()

    for path in (NAV, DECORATORS):
        if not os.path.exists(path):
            print('missing: %s' % path)
            return 1

    with open(NAV, encoding='utf-8') as handle:
        nav_text = handle.read()
    with open(DECORATORS, encoding='utf-8') as handle:
        known = permission_names(handle.read())

    if len(known) < 20:
        print('read only %d permission names out of decorators.py, so this '
              'check is broken rather than clean' % len(known))
        return 1

    problems, checked = audit(nav_text, known, routes_on_disk())
    for line in problems:
        print(line)
    print('%d nav permission key(s) checked against %d real permissions, '
          '%d problem(s)' % (checked, len(known), len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())

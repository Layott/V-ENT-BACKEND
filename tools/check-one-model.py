"""One Tournament, one Event, one Team, one User.

    V-ENT/CLAUDE.md: "Everything that touches a tournament reads the same model
    through the same serializer: the organiser's tools, the page a player sees,
    the admin dashboard, the partner API, the mobile view."

The fault this exists to stop: adding a feature to a tournament for organisers,
and a normal user creating a tournament cannot see it - not because it was
hidden from them, but because their screen is fed by a different model, or a
partial copy of the same one. The feature was only ever built for half the
product and nobody notices until somebody asks why their tournament looks
different.

    python tools/check-one-model.py

Known history, named in the rule: `Teams` was once defined in BOTH `vent_auth`
and `vent_team` with different `related_name`s. It took a migration to unpick.
This is the check that would have caught it the day it was written.

It also flags a person assembled by hand, which is the same fault one size
down: `_person` is the shared shape, and a dict with three of its keys is a
copy that has already drifted. That is how the organiser card lost the avatar
and the founder badge.
"""
import os
import re
import sys


def _workspace_root():
    """The directory holding V-ENT-BACKEND and V-ENT-FRONTEND.

    Walked for rather than computed from a fixed number of `dirname` calls, so
    this file works whether it sits in the workspace `tools/` or inside the
    backend repo's. It lives in the repo because the workspace root is not
    version controlled, and a checker that exists on one machine only is not a
    rule anybody else is held to.
    """
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

SKIP_DIRS = {'venv', '__pycache__', 'migrations', '.git', 'node_modules'}

# A model name defined more than once is allowed only with a reason.
DUPLICATE_ALLOWED = {
    # Two genuinely different records that happen to share a name: a person's
    # links live on a user, an event's links live on an event, and nothing
    # reads one expecting the other. This is NOT the Teams fault, which was two
    # definitions of ONE concept with different related_names.
    #
    # Listed rather than silently skipped, because the next person to read this
    # deserves to know somebody looked and decided, rather than wondering
    # whether the check is simply wrong.
    'SocialLink': 'a user social link and an event social link are different '
                  'records that share a name',
}


def python_files():
    for base, dirs, names in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.endswith('.py') and not name.startswith('tests_'):
                yield os.path.join(base, name)


def main():
    # model name -> [(file, line)]
    models = {}
    for path in python_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
        with open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
        for match in re.finditer(
                r'^class\s+(\w+)\s*\(\s*models\.Model\s*\)', text, re.M):
            line = text.count('\n', 0, match.start()) + 1
            models.setdefault(match.group(1), []).append((rel, line))

    problems = []

    for name, places in sorted(models.items()):
        if len(places) > 1 and name not in DUPLICATE_ALLOWED:
            problems.append((
                'two models called %s' % name,
                'Everything reading one of them is reading a different record '
                'from everything reading the other.',
                places))

    # A person assembled by hand rather than through the shared shape.
    hand_built = []
    person_keys = ("'username'", '"username"')
    for path in python_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
        if rel.endswith('views_community.py'):
            continue                       # where _person lives
        with open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
        for match in re.finditer(
                r"\{[^{}]{0,400}?'user_id'\s*:[^{}]{0,400}?\}", text, re.S):
            body = match.group(0)
            if not any(k in body for k in person_keys):
                continue
            if 'avatar' in body and 'founder_badge' in body:
                continue                   # carries the whole shape already
            line = text.count('\n', 0, match.start()) + 1
            hand_built.append((rel, line, body.replace('\n', ' ')[:90]))

    for title, why, places in problems:
        print('DUPLICATE  %s' % title)
        print('  %s' % why)
        for rel, line in places:
            print('    %s:%d' % (rel, line))
        print('')

    for rel, line, body in hand_built:
        print('HAND-BUILT PERSON  %s:%d' % (rel, line))
        print('    %s' % body)
        print('    Missing avatar or founder_badge. Use _person(request, user):')
        print('    a dict with some of its keys is a copy that has drifted.')
        print('')

    print('%d model(s) defined, %d duplicate name(s), %d hand-built person dict(s)'
          % (len(models), len(problems), len(hand_built)))

    # Only a duplicate MODEL blocks. A hand-built person dict is reported
    # because that is how the organiser card lost its avatar and badge, but
    # plenty of them are internal payloads that never draw a person on screen
    # and a scanner cannot tell those apart. Blocking on all of them would make
    # this the check everybody skips, and take the duplicate-model rule with
    # it.
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())

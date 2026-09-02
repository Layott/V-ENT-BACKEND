"""Fix it for the model, not for the one record that was reported.

    CEO, 2 September 2026: "WHEN I SEND YOU BUGS TO FIX YOU DONT JUST FIX IT
    FOR THAT EVENT, YOU ENSURE IT IS FIXED FOR THAT MODEL AND OTHER MODELS
    INVOLVED SO THAT OTHER VENTS OR TOURNAMENTS OR PARTS OF THE WEBSITE WONT BE
    FACING THE SAME ISSUE AGAIN. MAKE THIS A RULE AND BUILD A CATCHER FOR IT."

Every one of these was reported against a single record and was really a fault
in a whole surface:

    "0/32 slots"          the edit endpoint ignored eight wizard fields, on
                          every tournament ever continued from a draft
    "no image or badge"   the organiser was described by a hand-built dict, on
                          every tournament page
    "upload not working"  a ref that was never attached, in BOTH wizards
    "no shorten option"   short links existed for events and not tournaments
    "tie breaker for
     battle royale"       the whole catalogue was sent to every game
    "0/64"                a flat cap unrelated to the tournament

The shape is always the same: **two surfaces that should behave alike, and one
of them was built and the other forgotten.** Usually events and tournaments,
because they are the two things V-ENT runs and they are written separately.

So this checks pairs. Each row is one capability that must exist on BOTH sides.
If a probe matches on one side and not the other, the capability was built once
and the other half is the next bug report.

    python tools/check-parity.py

It cannot prove two implementations behave identically - only a test does that.
What it catches is the case that keeps happening: built here, missing there.
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
FRONTEND = os.path.join(ROOT, 'V-ENT-FRONTEND')


def read(*parts):
    path = os.path.join(*parts)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8', errors='replace') as handle:
        return handle.read()


def present(text, pattern):
    return text is not None and re.search(pattern, text) is not None


# (capability, why it matters, left label, left probe, right label, right probe)
# A probe is (path relative to its repo, regex).
PAIRS = [
    (
        'short links',
        'A long address is worth shortening whatever it points at.',
        'event', (BACKEND, 'vent_event/urls.py', r'short-links'),
        'tournament', (BACKEND, 'vent_tournament/urls.py', r'short-links'),
    ),
    (
        'the share card can shorten',
        'The button only appears when a screen hands ShareCard a shorten fn.',
        'event', (FRONTEND, 'src/app/events/view-event/page.js', r'shorten='),
        'tournament', (FRONTEND, 'src/app/tournaments/view-tournament/page.js',
                       r'shorten='),
    ),
    (
        'an edit screen exists',
        'An endpoint nothing calls is the same as the thing being uneditable.',
        'event', (FRONTEND, 'src/app/events/edit-event/page.js', r'.'),
        'tournament', (FRONTEND, 'src/app/tournaments/edit-tournament/page.js',
                       r'.'),
    ),
    (
        'a write-once audit test',
        'Catches any column settable at creation and frozen afterwards.',
        'event', (BACKEND, 'vent_event/tests_availability_agreement.py',
                  r'write_once|deliberately_fixed'),
        'tournament', (BACKEND, 'vent_tournament/tests_edit_everything.py',
                       r'never_editable|deliberately fixed|editable_here'),
    ),
    (
        'the game can be changed after creation',
        'Picking the wrong game once should not mean building it again.',
        'event', (BACKEND, 'vent_event/views.py', r"'game' in data"),
        'tournament', (BACKEND, 'vent_tournament/views.py',
                       r"'tournament_game' in request\.data"),
    ),
    (
        'the console tab comes from the URL',
        'Otherwise nothing can deep-link into it and a reload loses the tab.',
        'event', (FRONTEND, 'src/app/events/manage/page.js',
                  r"searchParams\.get\('tab'\)"),
        'tournament', (FRONTEND, 'src/app/tournaments/manage/page.js',
                       r"searchParams\.get\('tab'\)"),
    ),
    (
        'people are described by the shared shape',
        'Hand-built person dicts lose the avatar and the founder badge.',
        'community', (BACKEND, 'vent_auth/views_community.py', r'def _person'),
        'tournament', (BACKEND, 'vent_tournament/views.py',
                       r'import _person|_person\(request'),
    ),
    (
        'the sponsor logo input is attached',
        'A hidden input with no ref throws on click and does nothing.',
        'tournament wizard',
        (FRONTEND,
         'src/components/create-tournament-component/sponsors-links/sponsors/Sponsors.js',
         r'ref=\{el'),
        'event wizard',
        (FRONTEND,
         'src/components/create-event-component/sponsors-links/sponsors/Sponsors.js',
         r'ref=\{el'),
    ),
]


def main():
    problems = []
    checked = 0

    for name, why, left_label, left, right_label, right in PAIRS:
        left_text = read(left[0], left[1])
        right_text = read(right[0], right[1])
        left_has = present(left_text, left[2])
        right_has = present(right_text, right[2])
        checked += 1

        if left_has == right_has:
            continue

        have, lack = ((left_label, right_label) if left_has
                      else (right_label, left_label))
        missing = right if left_has else left
        problems.append({
            'name': name, 'why': why, 'have': have, 'lack': lack,
            'where': os.path.relpath(os.path.join(missing[0], missing[1]), ROOT),
        })

    for p in problems:
        print('MISSING  %s' % p['name'])
        print('  built for %s, not for %s' % (p['have'], p['lack']))
        print('  %s' % p['why'])
        print('  looked in %s' % p['where'].replace(os.sep, '/'))
        print('')

    print('%d capability pair(s) checked, %d built on one side only'
          % (checked, len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())

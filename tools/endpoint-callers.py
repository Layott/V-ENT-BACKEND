#!/usr/bin/env python3
"""Every backend endpoint, and whether anything in the frontend calls it.

Four separate faults in one night had the same shape: the endpoint was built,
tested and green, and no screen ever called it.

  * `PUT /event/edit-event/` existed for weeks. An organiser who mistyped a
    venue had nothing to press.
  * Ticket tiers could be read and never written after creation.
  * `redirect_uris` was accepted by the API and editable on no screen that an
    approved partner could reach.
  * `Format.can_feed_into` was recorded and read by nothing.

Each was invisible to every check we had, because a passing test suite says the
endpoint works and says nothing about whether anybody can reach it. This is the
check that would have caught all four on the day they landed.

Deliberately crude, in one specific way: it matches on the URL PATH SEGMENTS,
not on a parsed request. A frontend that builds a URL from a variable will still
match if the literal parts appear somewhere in the file. That is the right
trade: a false "called" is a missed warning, a false "orphaned" is a broken
build and a person losing an hour, and the second is much worse.

Usage:
    python tools/endpoint-callers.py             # fails on a NEW orphan
    python tools/endpoint-callers.py --json      # machine readable
    python tools/endpoint-callers.py --list      # every orphan, for reading
    python tools/endpoint-callers.py --baseline  # record today's orphans
"""
import argparse
import json
import os
import re
import sys

def _workspace_root(start):
    """The directory holding both repos, wherever this file is run from.

    These checkers lived at the workspace root, which is not a git repository,
    so nothing that enforces the structural rules was version controlled. They
    live in the backend repo now and still have to find the frontend, so the
    root is discovered rather than assumed to be one level up.
    """
    import os
    here = os.path.abspath(start)
    for _ in range(6):
        here = os.path.dirname(here)
        if (os.path.isdir(os.path.join(here, 'V-ENT-BACKEND'))
                and os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND'))):
            return here
    raise SystemExit('cannot find the workspace root from ' + start)


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = _workspace_root(__file__)
BACKEND = os.path.join(ROOT, 'V-ENT-BACKEND')
FRONTEND = os.path.join(ROOT, 'V-ENT-FRONTEND', 'src')
BASELINE = os.path.join(HERE, 'endpoint-callers-baseline.json')

# A path segment that is a parameter rather than a literal.
PARAM = re.compile(r'<[^>]+>')
ROUTE = re.compile(r"""\bpath\(\s*['"]([^'"]*)['"]""")
INCLUDE = re.compile(r"""\bpath\(\s*['"]([^'"]*)['"]\s*,\s*include\(\s*['"]([^'"]+)['"]""")

# Endpoints nothing in the frontend should call, with the reason. A route in
# here is a decision, not an oversight, which is the whole difference.
DELIBERATE = {
    'admin/': 'Django admin',
    'api/v1/': 'the partner API, called by partners rather than by us',
    'partners/sso/token/': 'called by a partner server, never by a browser',
    'partners/sso/userinfo/': 'called by a partner server',
    'partners/inbound/': 'OAuth callback, hit by the provider',
    'auth/verify/': 'a link in an email',
    'auth/google-callback/': 'hit by Google',
    'accounts/': 'allauth',
    'dj-rest-auth/': 'library routes',
    'media/': 'files',
    'static/': 'files',
    # Fetched by `static/overlay-runtime.js` inside a browser source in OBS
    # or vMix, which is a page this repo serves but not one this checker
    # scans. Proved end to end by `scripts/overlay-probe.mjs`.
    'tournament/<str:tournament_id>/overlay-feed/':
        'fetched by the overlay runtime inside OBS, not by the site',
    # The event twin of the line above, missed when events got a studio. It is
    # fetched by exactly the same runtime for exactly the same reason.
    'event/<str:event_id>/overlay-feed/':
        'fetched by the overlay runtime inside OBS, not by the site',
    # The scraper posts card rows here from a desktop with a real browser
    # profile, because Futbin sits behind Cloudflare. See
    # `tools/scrape-futbin.mjs`. Nothing in the site should ever write to the
    # card catalogue.
    'cards/ingest/':
        'posted to by tools/scrape-futbin.mjs, never by the site',
}


def read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError:
        return ''


def backend_routes():
    """Every path in every urls.py, with its app prefix."""
    routes = []

    root_urls = os.path.join(BACKEND, 'vent', 'urls.py')
    prefixes = {}
    for prefix, module in INCLUDE.findall(read(root_urls)):
        app = module.split('.')[0]
        prefixes[app] = prefix

    for app, prefix in sorted(prefixes.items()):
        urls_py = os.path.join(BACKEND, app, 'urls.py')
        if not os.path.exists(urls_py):
            continue
        for route in ROUTE.findall(read(urls_py)):
            if route.strip() in ('', 'admin/'):
                continue
            routes.append({
                'app': app,
                'route': route,
                'full': (prefix + route),
            })
    return routes


def frontend_text():
    """Every line of the frontend, as one blob. Small enough to hold."""
    chunks = []
    for folder, _dirs, files in os.walk(FRONTEND):
        for name in files:
            if name.endswith(('.js', '.jsx', '.ts', '.tsx')):
                chunks.append(read(os.path.join(folder, name)))
    return '\n'.join(chunks)


def frontend_files():
    """Every frontend source file, kept SEPARATE.

    Separate is the fix for what shipped on 4 September 2026. The blob above
    asked whether each word of a route appeared ANYWHERE in the frontend, and
    on a codebase this size almost every word does. So
    `tournament/<id>/lineup/submit/` was reported as called, because `lineup`
    is in the picker and `submit` is in the KYC page. Two unrelated files, and
    the checker said the endpoint had a caller. It did not: the player had no
    Submit button at all, which is the exact class of fault this tool exists
    to catch, and it was the third time this class reached the CEO.

    A file is the right unit. One line is too strict and was measured and
    abandoned on 3 September (see `called_from`): almost every screen builds
    URLs through a helper, so `studio` and `sessions` legitimately sit on
    different lines OF THE SAME FILE. The whole frontend is too loose. The
    file the caller lives in is neither.
    """
    out = {}
    for folder, _dirs, files in os.walk(FRONTEND):
        for name in files:
            if name.endswith(('.js', '.jsx', '.ts', '.tsx')):
                full = os.path.join(folder, name)
                out[os.path.relpath(full, FRONTEND)] = read(full)
    return out


def literal_segments(route):
    """The parts of a path that a caller must contain, whatever it interpolates."""
    return [s for s in PARAM.sub('\x00', route).split('/')
            if s and '\x00' not in s]


def matchable_segments(route):
    """The segments a caller's own line actually has to show.

    The app prefix is dropped when there is anything after it. Almost every
    screen builds its URLs through a helper that already knows the prefix:

        const call = (path) => fetch(`${API}/event/${eventRef}${path}`)
        call('/money/')

    so the word `event` is never on the same line as `money`. Requiring it
    reported 49 endpoints as uncalled that are in fact called on every load of
    the event console, and a checker reporting 49 things nobody should act on
    is one people stop reading.

    What is kept is everything that tells one endpoint from another, which is
    where the fault this exists to catch actually lives:
    `tournament/tie/<id>/record/` still needs `tie` and then `record` on one
    line, and nothing built that URL for weeks.
    """
    segments = literal_segments(route)
    return segments[1:] if len(segments) > 1 else segments


#: A word a path segment could be, for the candidate index.
WORD = re.compile(r'[a-z0-9-]{3,}')


def frontend_strings(haystack):
    """The frontend as lines.

    Lines, not string literals extracted by regex: a pattern matching balanced
    quotes across a multi-megabyte blob backtracks pathologically and took this
    check from seconds to over ten minutes. A URL is built on one line in
    practice, so a line is both cheap and precise enough.
    """
    return [line for line in haystack.splitlines() if '/' in line]


def index_by_segment(strings):
    """Which lines contain each word, so a route only scans plausible ones.

    Scanning every line for every route is quadratic. The first literal segment
    of a path is a strong filter: only lines containing it can match all of them.
    """
    index = {}
    for text in strings:
        for word in set(WORD.findall(text)):
            index.setdefault(word, []).append(text)
    return index


def called_from(segments, haystack, strings=None, index=None):
    """Do all these segments appear IN ORDER on ONE line.

    The old test was that each segment appeared SOMEWHERE in the whole
    frontend. On a codebase this size almost every word appears somewhere, so a
    multi-segment path passed on coincidence: `tournament/tie/<id>/record/` was
    reported as called because the words "tournament", "tie" and "record" all
    exist, while nothing had ever built that URL. The endpoint had no screen
    for weeks.

    A caller writes one line, `${API}/tournament/tie/${id}/record/`, so the
    segments must appear in order on a single line. Interpolations sit between
    them, which is why this is a subsequence test and not an equality.

    NOT USED, and kept here with the reason.

    Tried on 3 September 2026 to close gate E2 and abandoned after measuring
    it. Almost every screen builds URLs through a helper that already holds
    part of the path:

        const base = `${API}/${kind}/${ownerRef}/studio/`;
        fetch(`${base}sessions/`)

    so `studio` and `sessions` are on different lines for an endpoint that is
    called on every load of the studio. Requiring the app prefix reported 49
    such endpoints; dropping the prefix still reported 22. Every one sampled was
    a helper, not an orphan.

    A false "orphaned" costs somebody an hour chasing a working endpoint; a
    false "called" costs a missed warning. The second is much cheaper, which is
    the trade this file has always made, and a stricter rule that cries wolf 22
    times is worse than the loose one. Closing this properly needs the frontend
    parsed rather than grepped, which is a different tool.
    """
    if strings is None:
        strings = frontend_strings(haystack)
    candidates = strings if index is None else index.get(segments[0], ())
    for text in candidates:
        at = 0
        for seg in segments:
            found = text.find(seg, at)
            if found < 0:
                break
            at = found + len(seg)
        else:
            return True
    return False


def is_deliberate(full):
    for prefix, reason in DELIBERATE.items():
        if full.startswith(prefix):
            return reason
    return None


def used_as_path(segment):
    """A segment sitting where a URL segment sits, not merely mentioned.

    A word in a comment, a variable of the same name, a translation key: all
    contain the letters and none of them calls anything. So the segment has to
    have a path boundary on its left and a slash, a query or a closing quote on
    its right, which is what a URL looks like and a sentence does not.
    """
    return re.compile(r"""(?:^|[/'"`(])%s(?:[/?'"`)]|\\)""" % re.escape(segment))


def caller_of(route, files):
    """The file that calls this route, or None.

    Two things have to be true of ONE file:

      1. its last literal segment is used AS A PATH somewhere in that file
      2. every other literal segment appears in that same file

    The last segment decides because it is what tells one endpoint from
    another; the earlier ones are usually the console or the app the screen
    already lives in. Both together are what the whole-frontend test was
    missing, and the fixtures in SELF_TEST are each a real shape that has to
    keep working.
    """
    segments = matchable_segments(route)
    if not segments:
        return None
    tail = used_as_path(segments[-1])
    head = segments[:-1]
    for name, text in sorted(files.items()):
        if not tail.search(text):
            continue
        if all(seg in text for seg in head):
            return name
    return None


def analyse(files=None, routes=None):
    files = frontend_files() if files is None else files
    called, orphaned, skipped = [], [], []

    for row in (backend_routes() if routes is None else routes):
        reason = is_deliberate(row['full'])
        if reason:
            skipped.append(dict(row, reason=reason))
            continue

        if not matchable_segments(row['full']):
            # A route that is entirely parameters cannot be matched this way,
            # and guessing would produce exactly the false orphan this must not
            # produce.
            skipped.append(dict(row, reason='no literal segment to match on'))
            continue

        where = caller_of(row['full'], files)
        if where:
            called.append(dict(row, caller=where))
        else:
            orphaned.append(row)

    # The same endpoint is often registered twice, once taking an id and once a
    # slug, so the console can send either (see project_slug_or_id_routes). They
    # have identical literal segments and are one endpoint: if either is called,
    # both are. Without this the slug twin of every called route is reported as
    # an orphan, which is four false findings today and is how a checker stops
    # being read.
    reachable = {tuple(matchable_segments(r['full'])) for r in called}
    still_orphaned = []
    for row in orphaned:
        if tuple(matchable_segments(row['full'])) in reachable:
            skipped.append(dict(row, reason='the slug or id twin of a called route'))
        else:
            still_orphaned.append(row)

    return called, still_orphaned, skipped


# --------------------------------------------------------------- the self-test
# A checker reporting 0 means "clean" OR "broken", and nothing tells them apart.
# These fixtures are the thing that does. Each is a real shape from this
# codebase: the fault that shipped, and the ways a legitimate caller is written
# that must never be reported as orphans.

SELF_TEST = [
    ('the fault that shipped: the words in unrelated files',
     'tournament/<str:tournament_id>/lineup/submit/',
     {'picker.js': 'fetch(`${API}/tournament/${ref}/lineup/`)',
      'kyc.js': "fetch(`${API}/auth/wallet/kyc/submit/`)"},
     False),
    ('a real caller, the whole path in one template',
     'tournament/<str:tournament_id>/lineup/submit/',
     {'p.js': 'fetch(`${API}/tournament/${ref}/lineup/submit/`, {method: "POST"})'},
     True),
    ('a caller built off a base URL held in a variable',
     'tournament/<str:tournament_id>/lineup/submit/',
     {'p.js': ('const base = `${API}/tournament/${ref}`;\n'
               'fetch(`${base}/lineup/submit/`, {method: "POST"})')},
     True),
    # The shape that made the one-line rule unusable on 3 September. It has to
    # keep passing, or this check goes back to crying wolf 22 times.
    ('one helper holding half the path, the rest at the call',
     'tournament/<str:tournament_id>/studio/sessions/',
     {'StudioPanel.js': ('const call = (path) => '
                         'fetch(`${API}/${kind}/${ref}/studio${path}`);\n'
                         "await call('/sessions/');")},
     True),
    ('a path kept in a constants file, fetched from another',
     'tournament/<str:tournament_id>/squad-rules/',
     {'constants.js': "export const SQUAD_RULES = (t) => `tournament/${t}/squad-rules/`;",
      'page.js': 'fetch(`${API}/${SQUAD_RULES(ref)}`)'},
     True),
    ('a single-segment route is still matched',
     'tournament/<str:tournament_id>/lineups/',
     {'p.js': 'fetch(`${API}/tournament/${ref}/lineups/`)'},
     True),
    ('and reported when nothing carries it',
     'tournament/<str:tournament_id>/lineups/',
     {'p.js': 'fetch(`${API}/tournament/${ref}/lineup/`)'},
     False),
    ('a word in a comment is a mention, not a call',
     'tournament/<str:tournament_id>/lineups/',
     {'other.js': 'const rows = useRows(); // lineups go here later'},
     False),
    ('two routes differing only by a trailing segment are told apart',
     'tournament/<str:tournament_id>/lineups/<str:username>/review/',
     {'p.js': 'fetch(`${API}/tournament/${ref}/lineups/${who}/`)',
      'q.js': 'const review = "open"; // a dispute review elsewhere'},
     False),
]


def self_test():
    bad = 0
    for label, route, files, want in SELF_TEST:
        got = caller_of(route, files) is not None
        ok = got == want
        if not ok:
            bad += 1
        print('%s  %s' % ('ok  ' if ok else 'FAIL', label))
        if not ok:
            print('       %s: expected %s, got %s'
                  % (route, 'a caller' if want else 'an orphan',
                     'a caller' if got else 'an orphan'))
    print('\n%d of %d fixtures behaved.' % (len(SELF_TEST) - bad, len(SELF_TEST)))
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--self-test', action='store_true',
                        help='prove the checker still catches what it claims')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    called, orphaned, skipped = analyse()
    names = sorted(r['full'] for r in orphaned)

    if args.baseline:
        with open(BASELINE, 'w', encoding='utf-8') as handle:
            json.dump({'orphaned': names}, handle, indent=2)
            handle.write('\n')
        print('Recorded %d orphaned endpoints as the baseline.' % len(names))
        return 0

    if args.json:
        print(json.dumps({
            'total': len(called) + len(orphaned) + len(skipped),
            'called': len(called),
            'orphaned': len(orphaned),
            'skipped': len(skipped),
            'orphaned_routes': names,
        }, indent=2))
        return 0

    if args.list:
        print('%d endpoints nothing in the frontend calls:\n' % len(names))
        for name in names:
            print('  %s' % name)
        print('\n%d called, %d deliberately not called.' % (len(called), len(skipped)))
        return 0

    known = set()
    if os.path.exists(BASELINE):
        known = set(json.load(open(BASELINE, encoding='utf-8')).get('orphaned', []))

    fresh = sorted(set(names) - known)
    fixed = sorted(known - set(names))

    if fixed:
        print('%d endpoint(s) now have a caller. Re-record the baseline:' % len(fixed))
        for name in fixed:
            print('  + %s' % name)
        print('    python tools/endpoint-callers.py --baseline\n')

    if fresh:
        print('%d NEW endpoint(s) with nothing calling them:\n' % len(fresh))
        for name in fresh:
            print('  %s' % name)
        print('\nAn endpoint nobody can reach is not built. Either call it from a')
        print('screen, or add it to DELIBERATE in tools/endpoint-callers.py with')
        print('the reason it is not meant to be called.')
        return 1

    print('%d endpoints, %d called, %d known orphans, %d deliberate. No new ones.'
          % (len(called) + len(orphaned) + len(skipped),
             len(called), len(names), len(skipped)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

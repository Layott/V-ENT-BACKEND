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


def analyse():
    haystack = frontend_text()
    strings = frontend_strings(haystack)
    index = index_by_segment(strings)
    called, orphaned, skipped = [], [], []

    for row in backend_routes():
        reason = is_deliberate(row['full'])
        if reason:
            skipped.append(dict(row, reason=reason))
            continue

        segments = matchable_segments(row['full'])
        if not segments:
            # A route that is entirely parameters cannot be matched this way,
            # and guessing would produce exactly the false orphan this must not
            # produce.
            skipped.append(dict(row, reason='no literal segment to match on'))
            continue

        # Deliberately the loose rule: every literal segment somewhere, and the
        # last one in a path-like position. A stricter rule was tried on
        # 3 September 2026 and abandoned, see `called_from`.
        tail = segments[-1]
        if all(seg in haystack for seg in segments) and (
                '%s/' % tail in haystack
                or "'%s'" % tail in haystack
                or '"%s"' % tail in haystack
                or '/%s' % tail in haystack):
            called.append(row)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()

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

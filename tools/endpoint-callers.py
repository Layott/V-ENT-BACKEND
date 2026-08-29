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


def is_deliberate(full):
    for prefix, reason in DELIBERATE.items():
        if full.startswith(prefix):
            return reason
    return None


def analyse():
    haystack = frontend_text()
    called, orphaned, skipped = [], [], []

    for row in backend_routes():
        reason = is_deliberate(row['full'])
        if reason:
            skipped.append(dict(row, reason=reason))
            continue

        segments = literal_segments(row['full'])
        if not segments:
            # A route that is entirely parameters cannot be matched this way,
            # and guessing would produce exactly the false orphan this must not
            # produce.
            skipped.append(dict(row, reason='no literal segment to match on'))
            continue

        # Every literal segment has to appear somewhere. The last one carries
        # the most meaning, so it is required verbatim with its slash.
        tail = segments[-1]
        if all(s in haystack for s in segments) and ('%s/' % tail in haystack
                                                     or "'%s'" % tail in haystack
                                                     or '"%s"' % tail in haystack
                                                     or '/%s' % tail in haystack):
            called.append(row)
        else:
            orphaned.append(row)

    return called, orphaned, skipped


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

"""What the API insists on, and what the page actually sends.

Send and Withdraw were both broken in production for as long as they have
existed. Each endpoint refuses a request without a `pin`; neither page ever
collected one. Every attempt came back "recipient_username, amount, and pin are
required", which the page dutifully displayed, so it read like a form complaint
rather than a screen that could not succeed.

Nothing caught it because both halves were individually correct and individually
tested. The backend tests sent a PIN, because the person writing them knew the
endpoint wanted one. The frontend was never run against a real endpoint.

So this reads the guard in the view and the body in the page and compares them.
It is deliberately narrow: it looks for the `if not all([...])` form the wallet
views use, matches each endpoint to the `fetch` that calls it, and reports a
field the view demands that the caller never mentions.

Usage: python tools/check-required-fields.py
"""

import pathlib
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


ROOT = pathlib.Path(_workspace_root(__file__))
BACKEND = ROOT / 'V-ENT-BACKEND'
FRONTEND = ROOT / 'V-ENT-FRONTEND' / 'src'

# `path('wallet/send/', views.send_funds)` -> which view answers which address.
ROUTE = re.compile(r"path\(\s*['\"]([^'\"]+)['\"]\s*,\s*(?:\w+\.)?(\w+)")

# The guard the wallet views use, and the assignments above it.
GUARD = re.compile(r'if not all\(\[([^\]]*)\]\)')
ASSIGN = re.compile(
    r"^\s*(\w+)\s*=\s*request\.data\.get\(\s*['\"]([^'\"]+)['\"]", re.M)


def view_requirements():
    """view name -> the request fields it refuses to run without."""
    out = {}
    for path in BACKEND.rglob('views*.py'):
        if 'venv' in path.parts:
            continue
        text = path.read_text(encoding='utf-8')
        for func in re.finditer(r'^def (\w+)\(request[^)]*\):', text, re.M):
            start = func.end()
            nxt = re.compile(r'^def \w+\(', re.M).search(text, start)
            body = text[start:(nxt.start() if nxt else len(text))]

            names = dict(ASSIGN.findall(body))
            names = {local: field for local, field in names.items()}

            required = set()
            for guard in GUARD.finditer(body):
                for token in guard.group(1).split(','):
                    token = token.strip()
                    if token in names:
                        required.add(names[token])
            if required:
                out[func.group(1)] = required
    return out


def routes():
    """url path -> view name."""
    out = {}
    for path in BACKEND.rglob('urls*.py'):
        if 'venv' in path.parts:
            continue
        for address, view in ROUTE.findall(path.read_text(encoding='utf-8')):
            out.setdefault(address.rstrip('/'), view)
    return out


def callers():
    """url path -> list of (file, the text of the fetch call)."""
    out = {}
    for path in FRONTEND.rglob('*.js'):
        text = path.read_text(encoding='utf-8')
        for m in re.finditer(r'fetch\(\s*`\$\{[^`]*?\}(/[^`?]*)`', text):
            address = m.group(1).strip('/')
            # A window either side of the call. The body is usually the object
            # literal just after it, but several pages build `payload` in a
            # statement above, so looking only forwards reports fields that are
            # in fact sent. A window is a heuristic; it errs towards silence
            # rather than towards a false alarm nobody will trust twice.
            chunk = text[max(0, m.start() - 1600):m.start() + 900]
            out.setdefault(address, []).append((path, chunk))
    return out


def is_sent(field, chunk):
    """Is `field` written as a key of an object in this window?

    Matches `pin:` and the shorthand `pin,` / `pin }`, and the quoted forms.
    Does not match the word appearing in a comment, a regex or a message.
    """
    key = re.escape(field)
    pattern = (
        r'(?:^|[\s{,])'          # start of a line, or after a brace or comma
        r'[\'"]?' + key + r'[\'"]?'
        r'\s*(?::|,|\}|$)'       # `pin:`, or the shorthand `pin,` / `pin }`
    )
    return re.search(pattern, chunk, re.M) is not None


def main():
    reqs = view_requirements()
    url_to_view = routes()
    called = callers()

    problems = []
    checked = 0

    for address, calls in sorted(called.items()):
        # `auth/wallet/send` is mounted under the `auth/` include as `wallet/send`.
        for candidate in (address, address.split('/', 1)[-1]):
            view = url_to_view.get(candidate)
            if view:
                break
        if not view or view not in reqs:
            continue
        checked += 1
        for path, chunk in calls:
            # As an object key, not merely as a substring. The first version of
            # this looked for the bare word, and `/pin/i.test(message)` in the
            # error handling three lines below was enough to make a page that
            # never sent a PIN look as though it did. A checker that passes for
            # the wrong reason is worse than no checker.
            missing = [f for f in sorted(reqs[view]) if not is_sent(f, chunk)]
            if missing:
                rel = path.relative_to(ROOT)
                problems.append(
                    '%s\n    calls /%s (%s)\n    which requires %s\n'
                    '    and never sends %s'
                    % (rel, address, view, ', '.join(sorted(reqs[view])), ', '.join(missing)))

    print('endpoints with a required-field guard and a caller: %d' % checked)
    if checked == 0:
        print('\nNOTHING WAS CHECKED. That is a broken checker, not a clean run.')
        return 2
    if problems:
        print('\nTHE PAGE OMITS A FIELD THE ENDPOINT DEMANDS (%d):' % len(problems))
        for p in problems:
            print('  ' + p.replace('\n', '\n  '))
        return 1
    print('every caller sends what its endpoint requires')
    return 0


sys.exit(main())

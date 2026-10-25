#!/usr/bin/env python3
"""A read that refuses a draft without the organiser's token is always called WITH it.

CEO, 24 October 2026 (inbox 539), on a fix that passed the token on two
fetches of a draft's bracket and participants: "I dont like a workflow like
this that doesnt make other things around it that are linked to that
particular code run automatically."

The fault: a backend read hides a draft from everybody but the people running
it (`_may_read_draft`), and the frontend asked for it with a bare fetch. The
organiser opened their own draft and saw an empty bracket, a missing page or
"not found", one screen at a time, and each fix found one caller and left the
next. This reads both repos so the next caller cannot be missed:

  1. Every backend view that guards a draft read: a view whose body calls
     `_may_read_draft(` or tests `is_draft and not`, or calls a helper in its
     own module that does. Read from the source with ast, not a list kept here.
  2. Every address those views answer, from the urls.py files and the include
     prefixes in vent/urls.py.
  3. Every `fetch(` and `ventFetch(` in the frontend whose address is one of
     them, including `API.TOURNAMENT.X(...)` from tournamentApi.js's own map.
     A fetch must carry the token in its options (an Authorization header, a
     headers object or helper, `auth`, `token`); a ventFetch must pass `token`.

A caller that is right to go without one (an embed, which never shows a draft)
says so on its line or the line above:  // draft-read: public, <reason>

    python tools/check-draft-reads.py              blocking: callers with no token
    python tools/check-draft-reads.py --headers    debt: hand-built Bearer headers
    python tools/check-draft-reads.py --self-test

The second mode counts `Bearer ${...}` written by hand outside the two helpers
(src/lib/authHeader.js and ventFetch). It is debt, not a fault: each is a
place the scheme can drift, and the ledger keeps the number from rising while
callers move onto the helpers.

Known blind spot, declared: an address held in a variable and fetched later
(`const url = ...; fetch(url)`) is not resolved. Every guarded read today is
written inline, and the self-test asserts the real tree still yields them.
"""
import ast
import os
import re
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
FRONTEND = os.environ.get('VENT_FRONTEND') or os.path.join(ROOT, 'V-ENT-FRONTEND')

GUARD = re.compile(r'_may_read_draft\(|is_draft and not\b')
MARKER = re.compile(r'draft-read:\s*public\s*,\s*\S')
HELPERS = ('src/lib/authHeader.js', 'src/components/tournament-lib/tournamentApi.js')


# ---------------------------------------------------------------- backend

def _is_view(fn):
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, 'id', '')
        if name == 'api_view':
            return True
    return False


def guarded_views(sources):
    """{(module, view name)} for every view that guards a draft read.

    `sources` maps a dotted module name to its text."""
    out = set()
    for module, text in sources.items():
        tree = ast.parse(text)
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        body = {fn.name: ast.get_source_segment(text, fn) or '' for fn in fns}
        guarded = {fn.name for fn in fns if GUARD.search(body[fn.name])}
        helpers = {n for n in guarded if not _is_view(next(f for f in fns if f.name == n))}
        for fn in fns:
            if not _is_view(fn):
                continue
            src = body[fn.name]
            if fn.name in guarded or any(re.search(r'\b%s\(' % re.escape(h), src) for h in helpers):
                out.add((module, fn.name))
    return out


def _imports(tree, package):
    """name -> module for what a urls.py imports: aliases of modules and names."""
    modules, names = {}, {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        base = node.module or ''
        if node.level:
            base = package + ('.' + base if base else '')
        for a in node.names:
            local = a.asname or a.name
            if node.module is None or (node.level and not node.module):
                modules[local] = '%s.%s' % (package, a.name)   # from . import views_stats
            elif a.name == '*':
                names.setdefault('*', []).append(base)
            else:
                names[local] = base                            # from .views import x
    return modules, names


def _view_ref(expr, modules, names):
    """(module, name) a path() points at, or None."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        mod = modules.get(expr.value.id)
        return (mod, expr.attr) if mod else None
    if isinstance(expr, ast.Name):
        if expr.id in names:
            return (names[expr.id], expr.id)
        return ('*', expr.id)
    return None


def guarded_addresses(sources, root_urls, app_urls):
    """Every address a guarded view answers, as '/prefix/seg/<p>/'."""
    views = guarded_views(sources)
    prefixes = {}
    for m in re.finditer(r"path\(\s*['\"]([^'\"]*)['\"]\s*,\s*include\(\s*['\"]([\w.]+)['\"]", root_urls):
        prefixes.setdefault(m.group(2), m.group(1))
    out = set()
    for urls_module, text in app_urls.items():
        package = urls_module.rsplit('.', 1)[0]
        prefix = prefixes.get(urls_module)
        if prefix is None:
            continue
        tree = ast.parse(text)
        modules, names = _imports(tree, package)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, 'id', '') in ('path', 're_path')):
                continue
            if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
                continue
            ref = _view_ref(node.args[1], modules, names)
            if not ref:
                continue
            mod, name = ref
            if mod == '*':
                hit = any((star, name) in views for star in names.get('*', []))
            else:
                hit = (mod, name) in views
            if hit:
                out.add(normalise('/' + prefix + node.args[0].value))
    return out


def load_backend():
    sources, app_urls = {}, {}
    for app in sorted(os.listdir(BACKEND)):
        folder = os.path.join(BACKEND, app)
        if not (app.startswith('vent_') and os.path.isdir(folder)):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith('.py') or name.startswith('tests'):
                continue
            with open(os.path.join(folder, name), encoding='utf-8') as fh:
                text = fh.read()
            module = '%s.%s' % (app, name[:-3])
            if name == 'urls.py':
                app_urls[module] = text
            else:
                sources[module] = text
    with open(os.path.join(BACKEND, 'vent', 'urls.py'), encoding='utf-8') as fh:
        root_urls = fh.read()
    return sources, root_urls, app_urls


# ---------------------------------------------------------------- frontend

def normalise(address):
    """'/tournament/view-tournament/<str:id>/' and the frontend's
    '${API}/tournament/view-tournament/${id}/?x=1' both become
    '/tournament/view-tournament/<p>/'."""
    a = address.strip().strip('`\'"')
    a = re.sub(r'^\$\{[^}]*\}', '', a)               # ${API}, ${process.env.NEXT_PUBLIC_API_URL}
    a = a.split('?', 1)[0]
    a = re.sub(r'\$\{[^}]*\}', '<p>', a)
    a = re.sub(r'<[^>]*>', '<p>', a)
    a = re.sub(r'/+', '/', a)
    return a if a.startswith('/') else '/' + a


def _call_args(text, start):
    """The argument text of the call whose '(' is at `start`, split at top-level commas."""
    depth, i, args, cur, quote = 0, start, [], [], None
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == '\\':
                cur.append(text[i + 1:i + 2]); i += 2; continue
            if ch == quote:
                quote = None
            i += 1; continue
        if ch in '\'"`':
            quote = ch
        elif ch in '([{':
            depth += 1
            if depth == 1 and ch == '(':
                i += 1; continue
        elif ch in ')]}':
            depth -= 1
            if depth == 0:
                args.append(''.join(cur)); return args
        elif ch == ',' and depth == 1:
            args.append(''.join(cur)); cur = []; i += 1; continue
        cur.append(ch)
        i += 1
    return args


def api_map(text):
    """KEY -> address from tournamentApi.js's `API` object, e.g. VIEW."""
    out = {}
    for m in re.finditer(r"\b([A-Z_]+):\s*(?:\([^)]*\)\s*=>\s*)?[`'\"]([^`'\"]+)[`'\"]", text):
        out[m.group(1)] = normalise(m.group(2))
    return out


def carries_token(kind, options):
    if kind == 'ventFetch':
        return bool(re.search(r'\btoken\b', options))
    value = re.sub(r'\bheaders\s*:', ' ', options)
    return bool(re.search(r'Authorization|\bauth\w*|\w*Headers?\b|\bheaders\b|\btoken\w*', value))


def matches(address, guarded):
    if address in guarded:
        return True
    # `${base}/${query}` where query is '' or '?stage=1'.
    return address.endswith('/<p>') and address[:-3] in guarded


def scan(files, guarded, mapping):
    """[(path, line, address, kind)] for every guarded read with no token."""
    faults = []
    for path, text in files.items():
        lines = text.split('\n')
        for m in re.finditer(r'(?<![\w.])(fetch|ventFetch)\s*\(', text):
            kind = m.group(1)
            args = _call_args(text, m.end() - 1)
            if not args:
                continue
            first = args[0].strip()
            api = re.match(r'API\.TOURNAMENT\.([A-Z_]+)', first)
            address = mapping.get(api.group(1)) if api else (
                normalise(first) if first[:1] in '`\'"' else None)
            if not address or not matches(address, guarded):
                continue
            line = text.count('\n', 0, m.start()) + 1
            near = '\n'.join(lines[max(0, line - 2):line])
            if MARKER.search(near):
                continue
            if not carries_token(kind, ','.join(args[1:])):
                faults.append((path, line, address, kind))
    return faults


def load_frontend():
    files = {}
    src = os.path.join(FRONTEND, 'src')
    for folder, _, names in os.walk(src):
        for name in names:
            if name.endswith(('.js', '.jsx', '.mjs')):
                full = os.path.join(folder, name)
                with open(full, encoding='utf-8', errors='replace') as fh:
                    files[os.path.relpath(full, FRONTEND).replace('\\', '/')] = fh.read()
    return files


def hand_built_headers(files):
    return sum(len(re.findall(r'Bearer \$\{', text))
               for path, text in files.items() if path not in HELPERS)


# ---------------------------------------------------------------- self-test

BE_VIEWS = '''
from rest_framework.decorators import api_view

def _readable(request, t):
    return not t.is_draft or _may_read_draft(request, t)

@api_view(['GET'])
def view_tournament(request, tournament_id):
    if t.is_draft and not _may_read_draft(request, t):
        return None

@api_view(['GET'])
def stats(request, tournament_id):
    if not _readable(request, t):
        return None

@api_view(['GET'])
def match_stats(request, tournament_id, match_id):
    return None
'''
BE_URLS = '''
from django.urls import path
from . import views
from .views import view_tournament
urlpatterns = [
    path("view-tournament/<str:tournament_id>/", view_tournament),
    path("<str:tournament_id>/stats/", views.stats),
    path("<str:tournament_id>/matches/<int:match_id>/stats/", views.match_stats),
]
'''
ROOT_URLS = "urlpatterns = [path('tournament/', include('vent_x.urls'))]"
API_JS = "export const API = { TOURNAMENT: { VIEW: (id) => `/tournament/view-tournament/${id}/`, } };"
FE_CASES = [
    ('bare fetch of a guarded read', "fetch(`${API}/tournament/view-tournament/${id}/`)", 1),
    ('fetch with an auth header', "fetch(`${API}/tournament/view-tournament/${id}/`, { headers: authHeader(token) })", 0),
    ('fetch with a headers variable', "fetch(`${API}/tournament/${id}/stats/`, { headers })", 0),
    ('fetch with Content-Type only', "fetch(`${API}/tournament/${id}/stats/`, { headers: { 'Content-Type': 'application/json' } })", 1),
    ('guard reached through a helper', "fetch(`${process.env.NEXT_PUBLIC_API_URL}/tournament/${id}/stats/?a=1`)", 1),
    ('an unguarded neighbour', "fetch(`${API}/tournament/${id}/matches/${m}/stats/`)", 0),
    ('ventFetch with no token', "ventFetch(API.TOURNAMENT.VIEW(slug))", 1),
    ('ventFetch with the token', "ventFetch(API.TOURNAMENT.VIEW(slug), { token })", 0),
    ('a trailing query variable', "fetch(`${API}/tournament/view-tournament/${id}/${query}`, { signal })", 1),
    ('a marker with a reason', "// draft-read: public, an embed never shows a draft\nfetch(`${API}/tournament/${id}/stats/`)", 0),
    ('a marker with no reason', "// draft-read: public\nfetch(`${API}/tournament/${id}/stats/`)", 1),
    ('prefetch is not fetch', "router.prefetch(`/tournament/view-tournament/${id}/`)", 0),
]


def self_test():
    failed = 0
    guarded = guarded_addresses({'vent_x.views': BE_VIEWS}, ROOT_URLS, {'vent_x.urls': BE_URLS})
    want = {'/tournament/view-tournament/<p>/', '/tournament/<p>/stats/'}
    if guarded != want:
        print('FAIL guarded addresses: %r' % sorted(guarded)); failed += 1
    mapping = api_map(API_JS)
    for name, code, expected in FE_CASES:
        got = len(scan({'case.js': code}, guarded, mapping))
        ok = got == expected
        failed += not ok
        print('%s %s (expected %d, got %d)' % ('ok  ' if ok else 'FAIL', name, expected, got))
    # The real tree must still yield guarded reads. A parse that finds none
    # would pass every caller, and a passing zero has two meanings (R47).
    real = guarded_addresses(*load_backend())
    for need in ('/tournament/view-tournament/<p>/', '/tournament/get-tournament-brackets/<p>/',
                 '/tournament/get-tournament-participants/<p>/', '/tournament/<p>/stats/'):
        if need not in real:
            print('FAIL the real backend no longer yields %s' % need); failed += 1
    print('%d of %d self-test case(s) failed' % (failed, len(FE_CASES) + 2))
    return 1 if failed else 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    files = load_frontend()
    if '--headers' in sys.argv:
        print('%d hand-built Bearer header(s) outside authHeader.js and ventFetch' % hand_built_headers(files))
        return 0
    guarded = guarded_addresses(*load_backend())
    with open(os.path.join(FRONTEND, HELPERS[1]), encoding='utf-8') as fh:
        mapping = api_map(fh.read())
    faults = scan(files, guarded, mapping)
    for path, line, address, kind in sorted(faults):
        print('  %s:%d  %s %s with no token' % (path, line, kind, address))
    print('%d guarded address(es) checked in the backend, %d draft read(s) fetched with no token'
          % (len(guarded), len(faults)))
    return 1 if faults else 0


if __name__ == '__main__':
    sys.exit(main())

"""Every refusal the server sends can be read in the reader's language.

    python tools/check-server-codes.py
    python tools/check-server-codes.py --self-test

Inbox 423 (CEO, 8 October 2026). A refusal leaves the API as a code and an
English sentence. `apiMessage` on the frontend shows the translated `api.CODE`
when there is one, and the English sentence when there is not, so every code
without a key was English on a French or Portuguese page. On 10 October that
was 258 of the 561 codes the server sends.

Translating them raised two faults of its own, and this holds all three.

## 1. A code with no translation

Each code needs `api.CODE` in en, fr and pt, unless it is named in SPECIFIC
below. Those are codes the server sends with several DIFFERENT sentences
(`VALIDATION_ERROR` carries 178). A translated code wins over the server's
sentence, so one translation would replace "A day needs a name" and "A score
is a number" with the same generic line: the fact traded for the language,
which `apiMessage` says in its own header is the wrong trade. They keep the
server's sentence until their call sites get codes of their own.

A SPECIFIC code that gains a translation is a fault too, for the same reason,
and so is one the server no longer sends (the list would be holding nothing).

## 2. A placeholder the refusal does not publish

"Only {remaining} left of {name}." reads `remaining` and `name` from the
response body. A site that sends INSUFFICIENT_STOCK without publishing them
makes `fill` give up, and the reader gets the English sentence again: silent,
and in exactly the language the translation was written to replace. So every
site sending a code whose translation names `{x}` must publish `x`, as a dict
key or a keyword. A site that passes a variable (`extra=payload`, `**over`)
cannot be read from source and is left alone rather than guessed at.

## 3. Languages out of step

The same placeholders in en, fr and pt. A `{team}` in English and not in
French is a French reader told less.
"""
import io
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
# This file's own backend (a worktree is a different branch from the main
# checkout) and the frontend the commit hook names in VENT_FRONTEND.
_OWN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = (os.environ.get('VENT_BACKEND')
           or (_OWN if os.path.isfile(os.path.join(_OWN, 'manage.py')) else os.path.join(ROOT, 'V-ENT-BACKEND')))
FRONTEND = os.environ.get('VENT_FRONTEND') or os.path.join(ROOT, 'V-ENT-FRONTEND')
DICTIONARY = os.path.join(FRONTEND, 'src', 'i18n', 'dictionaries.js')

# Codes sent with several different sentences, so a translation would replace
# specific facts with one generic line. Counted on 10 October 2026.
SPECIFIC = {
    'VALIDATION_ERROR': '178 sentences, one per field and rule',
    'VALIDATION_FAILED': '73 sentences, one per field and rule',
    'VALIDATION': '17 sentences, one per field and rule',
    'NOT_ALLOWED': 'names who may do it, which differs per action',
    'NOT_OWNER': 'names the action only the owner may take',
    'UNKNOWN_FORMAT': 'names the formats each export accepts',
    'ALREADY_MEMBER': 'an organisation, a team owned, a team joined',
    'INVALID_DATE': 'names the field and the shape it wants',
    'NOT_APPROVED': 'a stall, a partner key, a partner',
    'NO_SCOPES': 'three different partner key problems',
    'ALREADY_LINKED': 'a club and a tournament, each naming its owner',
    'ALREADY_REQUESTED': 'a verification and a team request',
    'INVALID_MINUTES': 'a range and a type',
    'MISSING_FIELDS': 'names the fields each form needs',
    'NOT_ACCEPTED': 'a challenge offer and a challenge result',
    'NOT_MEMBER': 'about the new owner, or about the reader',
    'BAD_SCORE': 'a type and a range',
    'NO_LINEUP': 'about the reader, or about the opponent',
    'NOT_ELIGIBLE': 'the sentence tournament_options.entry_refusal composes for the rule missed',
    'NOT_GRANTED': 'the reason the Discord capability check gives',
    'DISCORD_REFUSED': "Discord's own answer, passed on",
}

SKIP_DIRS = {'vent', 'migrations', 'node_modules', '.git', '__pycache__', 'venv',
             'tools', 'deploy', 'docs', 'media', 'static', 'staticfiles'}
LANGS = ('en', 'fr', 'pt')

STR = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
# A refusal: one of the helpers every app writes for itself, or an exception
# class built to carry a code (WalletError, PayoutError, Refused).
CALL = re.compile(
    r"""\b(?:_err|_error|error|err|refuse|_refuse|fail|_fail|_bad|bad_request|_no|_problem"""
    r"""|problem|deny|_deny|[A-Z]\w*(?:Error|Refusal|Refused|Problem))\s*\(((?:[^()]|\([^()]*\))*?)\)""",
    re.S)
CODE = re.compile(r"""['"]([A-Z][A-Z0-9_]{3,})['"]""")
KEYED = re.compile(r"""['"]code['"]\s*:\s*['"]([A-Z][A-Z0-9_]{3,})['"]""")
PLACEHOLDER = re.compile(r'\{([a-z0-9_]+(?:\.[a-z0-9_]+)*)\}', re.I)
# Data handed over in a variable: the fields cannot be read from the source.
OPAQUE = re.compile(r"""\b(?:extra|data|params|detail)\s*=\s*[a-z_]\w*\s*(?:[,)]|$)|\*\*\s*[a-z_]\w*""", re.I)
# A dict literal is a refusal only when it says so. `{'code': 'PING_RECEIVED'}`
# is a notification's metadata, and a squad rule's violation row is drawn by
# its own keys (squad.v.*); neither goes through apiMessage.
REFUSAL_DICT = re.compile(r"""['"](?:status|message)['"]\s*:""")


def _sources(backend):
    for root, dirs, files in os.walk(backend):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith('.py') and not f.startswith('tests') and f != 'tests.py':
                p = os.path.join(root, f)
                yield os.path.relpath(p, backend).replace('\\', '/'), \
                    io.open(p, encoding='utf-8', errors='ignore').read()


def sites(backend):
    """[(code, where, text)] for every place a code is sent."""
    out = []
    for rel, s in _sources(backend):
        for m in CALL.finditer(s):
            args = m.group(1)
            strings = [a or b for a, b in re.findall(STR, args)]
            # A code is a whole quoted argument, not a word inside a sentence.
            for c in CODE.findall(args):
                if c in strings:
                    out.append((c, '%s:%d' % (rel, s.count('\n', 0, m.start()) + 1), args))
        for m in KEYED.finditer(s):
            text = _enclosing(s, m.start())
            if REFUSAL_DICT.search(text):
                out.append((m.group(1), '%s:%d' % (rel, s.count('\n', 0, m.start()) + 1), text))
    return out


def _enclosing(s, at):
    """The dict literal around position `at`, plus whatever the statement adds
    after it (`dict({...}, **over)` publishes its fields after the brace)."""
    depth, start = 0, at
    while start > 0:
        start -= 1
        if s[start] == '}':
            depth += 1
        elif s[start] == '{':
            if depth == 0:
                break
            depth -= 1
    depth, end = 0, at
    while end < len(s):
        if s[end] == '{':
            depth += 1
        elif s[end] == '}':
            if depth == 0:
                break
            depth -= 1
        end += 1
    tail = s[end:end + 200].split('\n\n')[0]
    return s[start:end + 1] + tail


def dictionaries(path):
    """{lang: {CODE: text}} for every api.* key."""
    s = io.open(path, encoding='utf-8').read()
    starts = {lang: s.index('\n  %s: {' % lang) for lang in LANGS}
    order = sorted(LANGS, key=starts.get)
    out = {}
    for i, lang in enumerate(order):
        block = s[starts[lang]:starts[order[i + 1]]] if i + 1 < len(order) else s[starts[lang]:]
        out[lang] = {m.group(1): (m.group(2) or m.group(3) or '')
                     for m in re.finditer(r"""['"]api\.([A-Z][A-Z0-9_]+)['"]\s*:\s*""" + STR, block)}
    return out


def _publishes(text, path):
    """Whether the site sends the value `{a.b}` reads. Any step of the path
    counts: an exception's keywords are wrapped as `detail` by the view that
    catches it, so `{detail.limit_mb}` is published by `limit_mb=` at the raise."""
    return any(re.search(r"""['"]%s['"]\s*:|\b%s\s*=(?!=)""" % (f, f), text)
               for f in path.split('.'))


def problems(found, dicts, specific):
    faults = []
    sent = sorted({c for c, _w, _t in found})
    for c in sent:
        have = [lang for lang in LANGS if c in dicts.get(lang, {})]
        if c in specific:
            if have:
                faults.append('%s carries several sentences (%s) and now has a translation, '
                              'which replaces every one of them' % (c, specific[c]))
            continue
        if len(have) < len(LANGS):
            missing = [lang for lang in LANGS if lang not in have]
            where = next(w for code, w, _t in found if code == c)
            faults.append('%s (%s) has no api.%s in %s' % (c, where, c, ', '.join(missing)))
    for c in sorted(specific):
        if c not in sent:
            faults.append('%s is listed as specific and the server no longer sends it' % c)

    for c in sent:
        texts = {lang: dicts[lang][c] for lang in LANGS if c in dicts.get(lang, {})}
        if not texts:
            continue
        names = {lang: set(PLACEHOLDER.findall(t)) for lang, t in texts.items()}
        if len({frozenset(v) for v in names.values()}) > 1:
            faults.append('api.%s names different values per language: %s' % (
                c, '; '.join('%s %s' % (lang, sorted(v)) for lang, v in sorted(names.items()))))
        wanted = set().union(*names.values())
        for code, where, text in found:
            if code != c or not wanted or OPAQUE.search(text):
                continue
            lost = sorted(f for f in wanted if not _publishes(text, f))
            if lost:
                faults.append('%s at %s does not publish %s, so api.%s cannot fill and '
                              'the reader gets English' % (c, where, ', '.join('{%s}' % f for f in lost), c))
    return faults, sent


def run(backend, dictionary, specific):
    return problems(sites(backend), dictionaries(dictionary), specific)


def self_test():
    import tempfile
    cases = []
    good_dict = (
        "export const dictionaries = {\n  en: {\n"
        "    'api.SOLD_OUT': '{name} is sold out.',\n    'api.PLAIN': 'Plain.',\n  },\n"
        "  fr: {\n    'api.SOLD_OUT': '{name} est épuisé.',\n    'api.PLAIN': 'Simple.',\n  },\n"
        "  pt: {\n    'api.SOLD_OUT': '{name} está esgotado.',\n    'api.PLAIN': 'Simples.',\n  },\n};\n")

    def tree(py, dictionary=good_dict, specific=None):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, 'app'))
        io.open(os.path.join(d, 'app', 'views.py'), 'w', encoding='utf-8').write(py)
        dp = os.path.join(d, 'dict.js')
        io.open(dp, 'w', encoding='utf-8').write(dictionary)
        return run(d, dp, specific or {})[0]

    ok = ("def v():\n    return _error(f'{t.name} is sold out.', 'SOLD_OUT', 409,\n"
          "                  extra={'name': t.name})\n"
          "def w():\n    return _err('Plain.', 'PLAIN')\n")
    cases.append(('a published placeholder and a plain code pass', tree(ok), 0))
    cases.append(('a keyword publishes too',
                  tree("def v():\n    raise WalletError('x is sold out', 'SOLD_OUT', name=x)\n"), 0))
    cases.append(('a placeholder the site does not publish is caught',
                  tree("def v():\n    return _error(f'{t.name} is sold out.', 'SOLD_OUT', 409)\n"), 1))
    cases.append(('a variable handed over is left alone',
                  tree("def v():\n    return _error('Sold out.', 'SOLD_OUT', 409, extra=payload)\n"), 0))
    cases.append(('a code with no translation is caught',
                  tree("def v():\n    return _err('Nope.', 'NEW_CODE')\n"), 1))
    cases.append(('a code word inside a sentence is not a code',
                  tree("def v():\n    return _err('Send the WORD here.', 'PLAIN')\n"), 0))
    cases.append(('a specific code without a key passes',
                  tree("def v():\n    return _err('A day needs a name.', 'VALIDATION_ERROR')\n",
                       specific={'VALIDATION_ERROR': 'many'}), 0))
    cases.append(('a specific code given a key is caught',
                  tree("def v():\n    return _err('Plain.', 'PLAIN')\n", specific={'PLAIN': 'many'}), 1))
    cases.append(('a specific code nobody sends is caught',
                  tree(ok, specific={'GONE': 'many'}), 1))
    cases.append(('a keyed dict counts as a site',
                  tree("def v():\n    return Response({'code': 'KEYED_ONE', 'message': 'x'})\n"), 1))
    cases.append(('a notification\'s metadata is not a refusal',
                  tree("def v():\n    notify(u, metadata={'code': 'PING_RECEIVED', 'event': e})\n"), 0))
    cases.append(('a wrapped keyword fills a dotted placeholder',
                  tree("def v():\n    raise OcrError('SOLD_OUT', 'images', name=n)\n",
                       good_dict.replace('{name}', '{detail.name}')), 0))
    skew = good_dict.replace("'{name} está esgotado.'", "'Esgotado.'")
    cases.append(('languages naming different values are caught', tree(ok, skew), 1))

    failures = [(name, got, want) for name, got, want in
                ((n, len(f), w) for n, f, w in cases) if got != want]
    for name, got, want in failures:
        print('FAIL %s: %d fault(s), wanted %d' % (name, got, want))
    print('%d self-test case(s), %d failed' % (len(cases), len(failures)))
    return 1 if failures else 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    faults, sent = run(BACKEND, DICTIONARY, SPECIFIC)
    for f in faults:
        print('  ' + f)
    print('%d server code fault(s); %d code(s) checked; %d known to carry several sentences'
          % (len(faults), len(sent), len(SPECIFIC)))
    return 1 if faults else 0


if __name__ == '__main__':
    sys.exit(main())

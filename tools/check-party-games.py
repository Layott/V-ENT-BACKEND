# -*- coding: utf-8 -*-
"""One list of party games, across both repos and three languages (inbox 434, 439).

The fifty games live in five places, on purpose:

  - the server's list, with the numbers (vent_event/party_games.py), because
    the server refuses a game that is not in the catalogue;
  - the frontend's list of keys, region and kind (src/lib/partyGames/catalogue.js);
  - the animations (src/lib/partyGames/stories.js);
  - the words in English, French and Portuguese (src/lib/partyGames/words/*.js),
    one file per language so the explainers load only on pages that show games.

Every one of those can drift on its own and each side would still look fine:
a game the server accepts and the frontend cannot draw, an animation with five
frames under four steps, a French explainer missing a step, a word drawn inside
an animation that French readers see in English. This reads all of them and
fails on any disagreement.

The JavaScript is loaded with node rather than read with a regex, so a comment
that happens to look like a game is never counted as one (R47).

    python tools/check-party-games.py
    python tools/check-party-games.py --self-test
"""

import ast
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FRONTEND = os.environ.get('VENT_FRONTEND') or os.path.join(os.path.dirname(BACKEND), 'V-ENT-FRONTEND')
SERVER = os.path.join(BACKEND, 'vent_event', 'party_games.py')
LIB = os.path.join(FRONTEND, 'src', 'lib', 'partyGames')
LANGUAGES = ('en', 'fr', 'pt')
FIELDS = ('name', 'origin', 'players', 'time', 'needs', 'age', 'summary', 'win')
DRAWN = ('label', 'n', 'cols', 'vals')
DASHES = (chr(0x2013), chr(0x2014))  # en and em dash


def server_games(source):
    """[(key, region, kind)] from party_games.py, in order, read with ast."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'GAMES' for t in node.targets):
            out = []
            for call in node.value.elts:
                args = [a.value for a in call.args[:3]]
                out.append(tuple(args))
            return out
    raise SystemExit('party_games.py has no GAMES list')


def load_js():
    """The four frontend modules as JSON, through node."""
    script = (
        "const {pathToFileURL} = require('node:url');"
        "const lib = process.argv[1];"
        "const load = (f) => import(pathToFileURL(lib + '/' + f).href);"
        "(async () => {"
        "  const cat = await load('catalogue.js');"
        "  const st = await load('stories.js');"
        "  const words = {};"
        "  for (const l of ['en', 'fr', 'pt']) words[l] = (await load('words/' + l + '.js')).default;"
        "  process.stdout.write(JSON.stringify({games: cat.GAMES, stories: st.STORIES, words}));"
        "})().catch((e) => { console.error(e); process.exit(2); });"
    )
    result = subprocess.run(['node', '-e', script, LIB], capture_output=True, text=True,
                            encoding='utf-8')
    if result.returncode != 0:
        raise SystemExit('could not load the frontend catalogue:\n' + result.stderr[-2000:])
    return json.loads(result.stdout)


def drawn_words(story):
    """Every word drawn inside one animation (anything with a letter in it)."""
    seen = []

    def visit(props):
        for key in DRAWN:
            value = (props or {}).get(key)
            if value is None:
                continue
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and any(c.isalpha() for c in item) and item not in seen:
                    seen.append(item)

    for actor in story.get('cast', {}).values():
        visit(actor[3] if len(actor) > 3 else {})
    for step in story.get('play', []):
        for props in step.values():
            visit(props)
    return seen


def problems(server, games, stories, words):
    out = []
    front = [(g.get('key'), g.get('region'), g.get('kind')) for g in games]
    if server != front:
        only_server = [s[0] for s in server if s not in front]
        only_front = [f[0] for f in front if f not in server]
        out.append('server and frontend lists differ (order, region or kind): '
                   'server only %s, frontend only %s' % (only_server, only_front))
    keys = [g[0] for g in front]
    if len(set(keys)) != len(keys):
        out.append('a key appears twice in catalogue.js')

    if sorted(stories) != sorted(keys):
        out.append('stories.js: no animation for %s; animation for no game %s' % (
            sorted(set(keys) - set(stories)), sorted(set(stories) - set(keys))))

    english = words.get('en', {})
    for lang in LANGUAGES:
        table = words.get(lang)
        if table is None:
            out.append('words/%s.js is missing' % lang)
            continue
        if sorted(table) != sorted(keys):
            out.append('words/%s.js: missing %s, extra %s' % (
                lang, sorted(set(keys) - set(table)), sorted(set(table) - set(keys))))
        for key in keys:
            entry = table.get(key)
            if not entry:
                continue
            for field in FIELDS:
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    out.append('words/%s.js %s: no %s' % (lang, key, field))
            steps = entry.get('steps') or []
            # A game with no animation is reported once, above; its frames and
            # drawn words are not counted against every language as well.
            frames = len(stories[key].get('play', [])) if key in stories else len(steps)
            if len(steps) != frames:
                out.append('words/%s.js %s: %d steps for %d frames of its animation' % (
                    lang, key, len(steps), frames))
            if lang != 'en':
                english_steps = len((english.get(key) or {}).get('steps') or [])
                if len(steps) != english_steps:
                    out.append('words/%s.js %s: %d steps, English has %d' % (
                        lang, key, len(steps), english_steps))
                art = entry.get('art') or {}
                drawn = drawn_words(stories[key]) if key in stories else list(art)
                for word in drawn:
                    if not art.get(word):
                        out.append('words/%s.js %s: the drawn word %r is not translated' % (
                            lang, key, word))
                for word in art:
                    if word not in drawn:
                        out.append('words/%s.js %s: art %r is drawn nowhere' % (lang, key, word))
            text = json.dumps(entry, ensure_ascii=False)
            if any(d in text for d in DASHES):
                out.append('words/%s.js %s: an en or em dash' % (lang, key))
    return out


def run():
    with open(SERVER, encoding='utf-8') as handle:
        server = server_games(handle.read())
    js = load_js()
    found = problems(server, js['games'], js['stories'], js['words'])
    for line in found:
        print('  ' + line)
    print('party games: %d games, %d problem%s' % (
        len(server), len(found), '' if len(found) == 1 else 's'))
    return 1 if found else 0


def self_test():
    """Each fault this exists for is caught, and the clean case passes."""
    story = {'cast': {'bub': ['bubble', 1, 2, {'label': ''}], 'die': ['die', 1, 1, {'n': 6}]},
             'play': [{'bub': {'label': 'Six!'}}, {'die': {'n': 2}}]}
    entry = {f: 'x' for f in FIELDS}
    entry['steps'] = ['one', 'two']

    def clean():
        server = [('ludo', 'africa', 'board')]
        games = [{'key': 'ludo', 'region': 'africa', 'kind': 'board'}]
        stories = {'ludo': json.loads(json.dumps(story))}
        words = {lang: {'ludo': dict(entry, steps=list(entry['steps']))} for lang in LANGUAGES}
        for lang in ('fr', 'pt'):
            words[lang]['ludo']['art'] = {'Six!': 'Six !'}
        return server, games, stories, words

    cases = []
    cases.append(('clean', clean(), 0))
    s, g, st, w = clean()
    cases.append(('the server has a game the frontend lacks',
                  (s + [('uno', 'world', 'cards')], g, st, w), 1))
    s, g, st, w = clean()
    g[0]['kind'] = 'cards'
    cases.append(('the kinds disagree', (s, g, st, w), 1))
    s, g, st, w = clean()
    cases.append(('a game with no animation', (s, g, {}, w), 1))
    s, g, st, w = clean()
    w['fr']['ludo']['steps'] = ['one']
    cases.append(('French is missing a step', (s, g, st, w), 2))
    s, g, st, w = clean()
    del w['pt']['ludo']['art']
    cases.append(('a drawn word Portuguese never translates', (s, g, st, w), 1))
    s, g, st, w = clean()
    w['fr']['ludo']['art']['Ten!'] = 'Dix !'
    cases.append(('a translation of a word drawn nowhere', (s, g, st, w), 1))
    s, g, st, w = clean()
    w['en']['ludo']['win'] = ''
    cases.append(('an empty field', (s, g, st, w), 1))
    s, g, st, w = clean()
    w['pt']['ludo']['summary'] = 'Corra ' + chr(0x2014) + ' depressa'
    cases.append(('a dash', (s, g, st, w), 1))
    s, g, st, w = clean()
    st['ludo']['play'].append({})
    cases.append(('an animation with more frames than steps', (s, g, st, w), 3))

    failed = 0
    for name, (s, g, st, w), expected in cases:
        got = len(problems(s, g, st, w))
        ok = got == expected
        failed += not ok
        print('  %s %s (%d problem%s, expected %d)' % (
            'ok  ' if ok else 'FAIL', name, got, '' if got == 1 else 's', expected))
    source = "GAMES = [\n    # G('fake', 'uk', 'cards', 2),\n    G('whot', 'africa', 'cards', 2, 8),\n]\n"
    parsed = server_games(source)
    ok = parsed == [('whot', 'africa', 'cards')]
    failed += not ok
    print('  %s a commented-out game is not counted' % ('ok  ' if ok else 'FAIL'))
    print('self-test: %d of %d passed' % (len(cases) + 1 - failed, len(cases) + 1))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(self_test() if '--self-test' in sys.argv else run())

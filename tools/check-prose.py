"""The two rules about what gets written down, as code.

    1. NO EM DASHES OR EN DASHES, anywhere. Prose, code, comments, commit
       messages, task names, docs, generated content. Use a hyphen, a comma, a
       colon, parentheses, or reword.

    2. NO npm. This machine has a confirmed npm virus. Every command is pnpm
       or bun. `npm ` must not appear in a script, a doc, or a shell command.

Both are absolute and both are trivially checkable, which is exactly why they
should never have been left to somebody remembering.

    python tools/check-prose.py

The dash rule is the one that slips, because an em dash is a character most
editors will happily insert and it reads as ordinary punctuation. It is not
visually distinct at a glance, so it is precisely the thing a scanner should
be doing rather than a person.
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


SKIP_DIRS = {
    'node_modules', '.next', '.git', 'venv', '__pycache__', 'media',
    '.pytest_cache', 'staticfiles', 'dist', 'build', '.turbo',
}

TEXT_EXT = {
    '.js', '.jsx', '.ts', '.tsx', '.py', '.css', '.scss', '.md', '.json',
    '.html', '.txt', '.yml', '.yaml', '.sh', '.mjs', '.cjs',
}

EM_DASH = '—'
EN_DASH = '–'

# Files that legitimately carry the characters, with the reason.
DASH_EXEMPT = (
    'CLAUDE.md',                 # the rule itself has to name the character
    'tools/check-prose.py',      # this file
    'src/i18n/dictionaries.js',  # translated copy from real sources
)

# npm may appear in these, per the ignore-list in the rule.
NPM_EXEMPT = (
    'CLAUDE.md',
    'tools/check-prose.py',
    'pnpm-lock.yaml',
    'package-lock.json',
)


def walk():
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if os.path.splitext(name)[1].lower() in TEXT_EXT:
                yield os.path.join(base, name)


def main():
    dash_hits = []
    npm_hits = []
    scanned = 0

    for path in walk():
        rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                lines = handle.read().split('\n')
        except OSError:
            continue
        scanned += 1

        exempt_dash = any(rel.endswith(e) for e in DASH_EXEMPT)
        exempt_npm = any(rel.endswith(e) for e in NPM_EXEMPT)

        for number, line in enumerate(lines, 1):
            if not exempt_dash and (EM_DASH in line or EN_DASH in line):
                which = 'em dash' if EM_DASH in line else 'en dash'
                dash_hits.append((rel, number, which, line.strip()[:90]))

            if exempt_npm:
                continue
            # `npm ` as a command, not the word inside pnpm or a URL.
            if re.search(r'(?<![\w.-])npm\s+(?:install|run|test|ci|create|i)\b',
                         line):
                npm_hits.append((rel, number, line.strip()[:90]))

    for rel, number, which, text in dash_hits[:40]:
        print('%s:%d  %s' % (rel, number, which))
        print('    %s' % text)
    if len(dash_hits) > 40:
        print('... and %d more' % (len(dash_hits) - 40))

    if dash_hits:
        print('')
    for rel, number, text in npm_hits[:20]:
        print('%s:%d  npm command' % (rel, number))
        print('    %s' % text)
        print('    use pnpm, or bun')

    print('')
    print('%d file(s) scanned' % scanned)
    print('%d em/en dash(es)' % len(dash_hits))
    print('%d npm command(s)' % len(npm_hits))
    return 1 if (dash_hits or npm_hits) else 0


if __name__ == '__main__':
    sys.exit(main())

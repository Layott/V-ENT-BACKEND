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
    'node_modules', '.next', '.next-dev', '.git', 'venv', '__pycache__',
    'media', '.pytest_cache', 'staticfiles', 'dist', 'build', '.turbo',
    # Per-machine tool configuration, not prose anybody wrote or reads.
    # `.claude/settings.local.json` records which shell commands have been
    # allowed on THIS machine, so it accumulates entries like "Bash(npm run:*)"
    # simply because somebody once declined or approved one. Counting those as
    # npm usage made the debt ledger report a regression on 7 September 2026
    # that no human had caused, which is precisely the sort of false alarm that
    # teaches people to ignore a checker.
    '.claude',
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

# An ARCHIVED plan records what was actually run at the time. Rewriting it to
# say pnpm would make the record claim something that did not happen, which is
# worse than the mention. A new plan is not an archive and is not exempt.
NPM_EXEMPT_PREFIX = (
    'tasks/todo-archive-',
)

# A line can NAME npm without telling anybody to run it: the rule itself, a
# lesson recording why it is banned, a risk register listing "someone runs npm
# install" as the risk it is guarding against.
#
# Five of the eight hits left on 7 September 2026 were exactly that, and a
# checker that cannot tell an instruction from a warning about the same thing
# produces a count nobody works down. That is how the fourteen real ones sat
# behind them for weeks while the number was printed on every commit.
DESCRIBES_THE_BAN = re.compile(
    r"\b(?:never|do not|don't|banned|ban|virus|supply-chain|contamination|"
    r"accidentally|must not|no longer|instead of|does not require|"
    r"rather than|vs\.? pnpm|pnpm vs)\b",
    re.IGNORECASE,
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
        exempt_npm = (any(rel.endswith(e) for e in NPM_EXEMPT)
                      or any(p in rel for p in NPM_EXEMPT_PREFIX))

        for number, line in enumerate(lines, 1):
            if not exempt_dash and (EM_DASH in line or EN_DASH in line):
                which = 'em dash' if EM_DASH in line else 'en dash'
                dash_hits.append((rel, number, which, line.strip()[:90]))

            if exempt_npm:
                continue
            # `npm ` as a command, not the word inside pnpm or a URL, and not
            # a line whose whole point is that npm is banned.
            if (re.search(r'(?<![\w.-])npm\s+(?:install|run|test|ci|create|i)\b', line)
                    and not DESCRIBES_THE_BAN.search(line)):
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
    # ONE closing line carrying BOTH numbers, because check-all reads the last
    # line and records the number it finds. It used to end on the npm count, so
    # once that reached 0 the ledger printed "prose  debt  0 npm command(s)" -
    # a debt row with nothing in it, beside 3128 dashes nobody was tracking.
    # A number nobody can act on is the thing the CEO objected to on
    # 7 September; a row saying zero is worse, because it looks acted on.
    print('%d em/en dash(es) and %d npm command(s) outstanding'
          % (len(dash_hits), len(npm_hits)))
    return 1 if (dash_hits or npm_hits) else 0


def _npm_hit(line):
    """Whether one line counts as telling somebody to run npm."""
    return bool(
        re.search(r'(?<![\w.-])npm\s+(?:install|run|test|ci|create|i)\b', line)
        and not DESCRIBES_THE_BAN.search(line)
    )


# Every fixture is a real line from this repository: the ones that had to be
# fixed, and the ones that must be left alone. A checker reporting 0 means
# "clean" or "broken", and only this tells them apart.
NPM_CASES = [
    (True,  'npm install -g @anthropic-ai/claude-code'),
    (True,  '- **Frontend:** `npm install sharp`'),
    (True,  'Install DOMPurify: `npm install dompurify`'),
    (True,  'npm run dev'),
    (True,  'Always check `npm run dev` output and sync `NEXTAUTH_URL`'),
    (False, 'CEO confirmed npm has a virus / supply-chain risk on this machine. '
            'Never invoke `npm`, `npm ci`, `npm install`, `npx`, or any other '
            'npm/npx command in any V-ENT repo.'),
    (False, '- Vercel CLI direct deploy still works, does not require local npm install.'),
    (False, '| 8 | npm contamination (someone runs `npm install`) | Medium | Low |'),
    (False, '| **R12: pnpm vs npm in dev environments**, junior devs accidentally '
            'run `npm install`, producing a package-lock.json |'),
    (False, 'pnpm install --frozen-lockfile'),
    (False, 'See https://npmjs.com/package/sharp for the install notes'),
]


def self_test():
    bad = 0
    for expected, line in NPM_CASES:
        got = _npm_hit(line)
        if got != expected:
            bad += 1
            print('FAIL  expected %s: %s' % (
                'a hit' if expected else 'no hit', line[:80]))
    if bad:
        print('%d of %d case(s) wrong' % (bad, len(NPM_CASES)))
        return 1
    print('%d cases, both directions: self-test passed' % len(NPM_CASES))
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    sys.exit(main())

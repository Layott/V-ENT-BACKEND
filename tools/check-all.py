"""Every rule that has a catcher, run in one pass.

    CEO, 2 September 2026: "EVERY SINGLE RULE MUST HAVE A CATCHER BUILT THAT
    SCANS ANY CODE BEING EDITED OR BUILT TO ENSURE IT FOLLOWS."

    python tools/check-all.py            everything
    python tools/check-all.py --blocking only the ones that must be clean

Two tiers, deliberately.

**Blocking** catchers are at zero today and must stay there. A new breach is
something somebody just wrote, and it is cheap to fix while they still
remember why they wrote it.

**Debt** catchers report real breaches that predate them, in numbers too large
to clear in one pass. They are still run, and their counts are printed, because
a number that goes UP is a regression even when it cannot yet go to zero. What
they must never do is block, because a check that always fails is a check
everybody learns to ignore, and then the blocking ones get ignored with it.

Move a catcher from debt to blocking the day its count reaches zero.
"""
import datetime
import json
import os
import re
import subprocess
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

FRONTEND = os.path.join(ROOT, 'V-ENT-FRONTEND')
# The backend this file belongs to. A git worktree (V-ENT-BACKEND-flw, say)
# is a second copy of the repo beside the first; checking the first from the
# second judged code that was not being committed (29 September 2026).
_OWN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = (_OWN if os.path.isfile(os.path.join(_OWN, 'manage.py'))
           else os.path.join(ROOT, 'V-ENT-BACKEND'))

def _django_python():
    """A python that can import Django: the backend's virtualenv, or the main
    checkout's when this is a worktree (which has none)."""
    pythons = [os.path.join(BACKEND, 'venv', 'Scripts', 'python.exe'),
               os.path.join(BACKEND, 'venv', 'bin', 'python'),
               os.path.join(ROOT, 'V-ENT-BACKEND', 'venv', 'Scripts', 'python.exe'),
               os.path.join(ROOT, 'V-ENT-BACKEND', 'venv', 'bin', 'python'),
               sys.executable]
    return next(p for p in pythons if os.path.exists(p))


DJANGO_PY = _django_python()


def _fresh_routes():
    """Every route of THIS backend, dumped now, for the api-paths row.

    Written by tools/dump-routes.py with the backend's own virtualenv (a
    worktree has none, so the main checkout's is used to import this code).
    """
    import tempfile
    out = os.path.join(tempfile.gettempdir(), 'vent-routes-%d.txt' % (abs(hash(BACKEND)) % 10 ** 8))
    subprocess.run([DJANGO_PY, os.path.join(BACKEND, 'tools', 'dump-routes.py'), '--out', out],
                   cwd=BACKEND, capture_output=True, text=True, timeout=300)
    return out


ROUTES = _fresh_routes()

# (name, rule it enforces, working directory, command, blocking)
CATCHERS = [
    ('parity',
     'built for events or tournaments but not both',
     ROOT, [sys.executable, 'tools/check-parity.py'], True),

    ('one model',
     'one Tournament, one Event, one Team, one User',
     ROOT, [sys.executable, 'tools/check-one-model.py'], True),

    ('wizard round trip',
     'every setting the wizard sends survives create, edit and reopen',
     ROOT, [sys.executable, 'tools/check-wizard-roundtrip.py'], False),

    # The admin sidebar used to decide access from a `roles` array of its own,
    # ORed with the permission map, and the two disagreed in four places. This
    # also catches a finished admin page in no navigation list, which is what
    # /admin/kyc was for weeks.
    ('admin nav',
     'the admin sidebar and the admin API read one permission table',
     ROOT, [sys.executable, 'tools/check-admin-nav.py'], True),

    ('prose',
     'no em or en dashes, and no npm',
     ROOT, [sys.executable, 'tools/check-prose.py'], False),

    ('signed out',
     'a signed-out visitor never sees a control they cannot use',
     FRONTEND, ['node', 'scripts/check-signed-out.mjs'], True),

    ('control bytes',
     'no escape sequence turned into a literal control character',
     FRONTEND, ['node', 'scripts/check-control-bytes.mjs'], True),

    ('dangling refs',
     'no ref read but never attached',
     FRONTEND, ['node', 'scripts/check-dangling-refs.mjs'], True),

    ('css classes',
     'no undefined class on a control somebody has to press',
     FRONTEND, ['node', 'scripts/check-css-classes.mjs'], True),

    ('translation keys',
     'every key exists, in en, fr and pt',
     FRONTEND, ['node', 'scripts/check-keys.mjs'], True),

    ('dictionary parity',
     'en, fr and pt hold the same keys',
     FRONTEND, ['node', 'scripts/dict-parity.mjs'], True),

    ('avatars',
     'a name on screen can always show the face beside it',
     FRONTEND, ['node', 'scripts/check-avatars.mjs'], False),

    # Written on 30 August, never run by anything until 4 September. That is
    # the third time a catcher has sat on disk outside this table, and it is
    # the reason the table exists.
    ('user chips',
     'every name goes through UserChip, so the badge and the link come with it',
     FRONTEND, ['node', 'scripts/check-user-chips.mjs'], True),

    ('slugs',
     'no numeric id in an address a person can see',
     FRONTEND, ['node', 'scripts/check-slugs.mjs'], False),

    ('seo',
     'every public page can be found and read',
     FRONTEND, ['node', 'scripts/check-seo.mjs'], False),

    # check-seo proves a page HAS structured data; this proves the numbers in
    # it are the record's. Every event's JSON-LD started at the registration-
    # open instant with no end and no offers for weeks (18 September 2026).
    ('structured data',
     'the JSON-LD is built from the payload the API sends',
     FRONTEND, ['node', '--no-warnings', 'scripts/check-ld-shape.mjs'], True),

    # "3 line(s)" reached a screen on 18 September 2026, the sweep after it
    # claimed there were no more, and there were thirty across both repos.
    # Reads the dictionaries, every component string and every backend
    # module; one/many keys and text.count() are what to write instead.
    ('plurals',
     'no unfinished plural, a bracketed s, in a sentence a person reads',
     FRONTEND, ['node', 'scripts/check-plurals.mjs'], True),

    # "PHYSICAL" on a French event page: a stored code drawn as a word. Found
    # 30 September 2026 on the embeds walk, then in 28 places (inbox 364).
    ('raw enums',
     'no status or type code drawn as text; it goes through src/lib/labels.js',
     FRONTEND, ['node', 'scripts/check-raw-enums.mjs'], True),
    ('raw enums self-test',
     'the raw-enum catcher catches a code drawn as text and leaves lookups alone',
     FRONTEND, ['node', 'scripts/check-raw-enums.mjs', '--self-test'], True),

    # "Start date is required." on a French page: English handed straight to
    # the screen from a validator or a setter. 47 in 14 files, 30 September
    # 2026 (inbox 364), the login and signup forms among them.
    ('literal messages',
     'no error or notice sentence handed to the screen without a key',
     FRONTEND, ['node', 'scripts/check-literal-messages.mjs'], True),
    ('literal messages self-test',
     'the literal-message catcher catches bare English and leaves tt() alone',
     FRONTEND, ['node', 'scripts/check-literal-messages.mjs', '--self-test'], True),

    # "Winlo" answered "No account found" on the Send page. Every search goes
    # through one forgiving matcher, on both sides (inbox 383, 30 Sept 2026).
    ('forgiving search',
     'no search term matched only exactly: fuzzy.js in the browser, fuzzy.py on the server',
     FRONTEND, ['node', 'scripts/check-forgiving-search.mjs'], True),
    ('forgiving search self-test',
     'the catcher catches includes() and __icontains on a search term and leaves the matcher',
     FRONTEND, ['node', 'scripts/check-forgiving-search.mjs', '--self-test'], True),
    ('matcher parity',
     'fuzzy.js passes every shared case and its fixtures equal the backend copy',
     FRONTEND, ['node', 'scripts/check-fuzzy.mjs'], True),
    ('history lines',
     'every wallet history line the server can name is said in en, fr and pt (inbox 388)',
     FRONTEND, ['node', 'scripts/check-statement-lines.mjs'], True),
    ('history lines self-test',
     'the catcher catches a missing key, a dropped placeholder and a code with no English',
     FRONTEND, ['node', 'scripts/check-statement-lines.mjs', '--self-test'], True),

    # "8/-", "8/0" and "8/undefined" for one uncapped tournament on four
    # screens, 28 September 2026, after "0/32 slots" and "0/64" before it.
    # Every count against a cap goes through slotsText in src/lib/slots.js.
    ('slots',
     'how full a tournament is, said one way, with no limit in words',
     FRONTEND, ['node', 'scripts/check-slots.mjs'], True),
    # All 25 native date controls were replaced in August 2026; six were back
    # by 28 September. Only DateField draws a date, in the page's language.
    ('date inputs',
     'no native date control, which draws its text in the browser language',
     FRONTEND, ['node', 'scripts/check-date-inputs.mjs'], True),
    # Every page shipped all three languages, 621 KB gzipped, before it could
    # do anything (28 September 2026). The browser now loads generated
    # per-language files; a stale copy would show last week's wording.
    # Thirty views fetched once on mount and never again (28 September 2026,
    # inbox 312). Each is on useAutoRefresh now, which also wakes on any write.
    ('load once',
     'a view refreshes by itself; only named forms and editors do not',
     FRONTEND, ['node', 'scripts/check-load-once.mjs'], True),
    ('load once self-test',
     'the load-once catcher still catches a view that never refreshes',
     FRONTEND, ['node', 'scripts/check-load-once.mjs', '--self-test'], True),
    # A page title or description in one language for every reader (52
    # layouts on 30 September 2026, inbox 375; the ticket page before it).
    ('metadata language',
     'no page or layout exports a title or description in one language only',
     FRONTEND, ['node', 'scripts/check-metadata-language.mjs'], True),
    ('metadata language self-test',
     'the metadata catcher still catches a fixed English title',
     FRONTEND, ['node', 'scripts/check-metadata-language.mjs', '--self-test'], True),
    ('dictionary split',
     'the per-language files match dictionaries.js',
     FRONTEND, ['node', 'scripts/split-dictionaries.mjs', '--check'], True),
    ('dictionary split self-test',
     'the split still notices an edited string',
     FRONTEND, ['node', 'scripts/split-dictionaries.mjs', '--self-test'], True),
    # French and Portuguese typed without accents: 939 strings ("equipe",
    # "evenement", "nao", "possivel"), found 28 September 2026, inbox 315.
    ('accents',
     'French and Portuguese carry their accents',
     FRONTEND, ['node', 'scripts/check-accents.mjs'], True),
    ('accents self-test',
     'the accent catcher still catches a bare word and leaves a verb alone',
     FRONTEND, ['node', 'scripts/check-accents.mjs', '--self-test'], True),
    # A key written twice in one language: the first copy is dropped without
    # a word. 31 per language on 28 September 2026 (inbox 316), several with
    # two meanings, so one screen of each pair showed the other's words.
    ('duplicate translation keys',
     'no key is written twice in one language',
     FRONTEND, ['node', 'scripts/check-dict-duplicates.mjs'], True),
    ('duplicate translation keys self-test',
     'the duplicate key catcher still catches a key written twice',
     FRONTEND, ['node', 'scripts/check-dict-duplicates.mjs', '--self-test'], True),
    # Text handed to tx() with no dictionary entry comes back in English on
    # a French or Portuguese page, silently (28 September 2026: seven, among
    # them the whole blurb of /tournaments/overlay).
    ('tx text',
     'every text passed to tx() has a dictionary entry',
     FRONTEND, ['node', 'scripts/check-tx-text.mjs'], True),
    ('tx text self-test',
     'the tx text catcher still catches an untranslated text',
     FRONTEND, ['node', 'scripts/check-tx-text.mjs', '--self-test'], True),
    # English written straight into a screen as a prop or between tags, where
    # no dictionary reaches it: four organiser hints, the landing email box and
    # the registration receipt (29 September 2026). Only files a page reaches.
    ('literal text',
     'no English prose written straight into a reachable screen',
     FRONTEND, ['node', 'scripts/check-literal-text.mjs'], True),
    ('literal text self-test',
     'the literal text catcher still catches a literal hint and a label',
     FRONTEND, ['node', 'scripts/check-literal-text.mjs', '--self-test'], True),
    # A name a screen calls that nothing defines: the Money tab and both
    # vendor-stall panels crashed on production (useAutoRefresh never imported),
    # 29 September 2026. ESLint no-undef over every reachable file.
    ('undefined names',
     'every name a reachable screen calls is defined or imported',
     FRONTEND, ['node', 'scripts/check-undefined.mjs'], True),
    ('undefined names self-test',
     'the undefined-name catcher still catches a missing import',
     FRONTEND, ['node', 'scripts/check-undefined.mjs', '--self-test'], True),
    # A page that loads a record by address and ignores a rename: the console
    # told its own organiser 'not yours', the register page failed, the draft
    # wizard stopped (29 September 2026).
    ('renames followed',
     'every page that loads a tournament or event by address follows a rename',
     FRONTEND, ['node', 'scripts/check-renames.mjs'], True),
    ('renames followed self-test',
     'the rename catcher still catches a loader that ignores a move',
     FRONTEND, ['node', 'scripts/check-renames.mjs', '--self-test'], True),
    # On Windows a rebuild deleted .next through its standalone symlinks and
    # emptied next, react and react-dom (28 September 2026, four times).
    # prebuild now unlinks them first; this keeps the unlinker honest.
    ('standalone unlink self-test',
     'removing a standalone link never touches the package it points at',
     FRONTEND, ['node', 'scripts/unlink-standalone.mjs', '--self-test'], True),
    ('date inputs self-test',
     'the date input catcher still catches a native control',
     FRONTEND, ['node', 'scripts/check-date-inputs.mjs', '--self-test'], True),
    ('slots self-test',
     'the slots catcher still catches a hand-written count',
     FRONTEND, ['node', 'scripts/check-slots.mjs', '--self-test'], True),

    # Eight controls deleted, ended, cancelled or refunded something on ONE
    # press on 18 September 2026 (a pitch was deleted mid-walk); four more
    # were found by the catcher once it was calibrated. Two presses, always:
    # the first asks, beside "Keep it".
    ('one press',
     'a destructive control asks before it acts',
     FRONTEND, ['node', 'scripts/check-one-press.mjs'], True),

    ('design bans',
     'no hairline borders, no glow, no vibecoded defaults',
     FRONTEND, ['node', 'scripts/check-design.mjs'], False),

    # Written 4 September 2026, merged 12 September. Every one of these
    # existed and was NOT in this list, which is the same as not existing:
    # the CEO asked why an endpoint with no screen reached them for the third
    # time, and the answer was that the catcher for it was written, was
    # correct enough to have caught two of the four, and was never run
    # because nothing ran it. A catcher outside this table is a catcher that
    # depends on somebody remembering. The block then sat unmerged on BE#150
    # for eight days, which is the same fault one level up.
    ('format catalogue',
     'one catalogue of formats, not a second copy drifting',
     ROOT, [sys.executable, 'V-ENT-BACKEND/tools/check-format-catalogue.py'], True),

    ('required fields',
     'every caller sends what its endpoint requires',
     ROOT, [sys.executable, 'V-ENT-BACKEND/tools/check-required-fields.py'], True),

    ('entrant branches',
     'no hand-built if team else user branch answering wrong for a squad',
     ROOT, [sys.executable, 'V-ENT-BACKEND/tools/check-entrant-branches.py'], True),

    ('duplicate checkers',
     'one copy of each checker, in the repo that is version controlled',
     ROOT, [sys.executable,
            'V-ENT-BACKEND/tools/check-duplicate-checkers.py'], True),

    ('overlay runtime',
     'the overlay runtime fills what it says it fills',
     ROOT, ['node', 'V-ENT-BACKEND/tools/check-overlay-runtime.mjs'], True),

    ('error copy',
     'no engineer sentence or raw server string shown to a person',
     FRONTEND, ['node', 'scripts/check-error-ui.mjs'], True),

    ('event tabs',
     'the console tabs and the shared strip carry the same ids',
     FRONTEND, ['node', 'scripts/check-event-tabs.mjs'], True),

    ('hover lift',
     'nothing rises, scales or glows on hover',
     FRONTEND, ['node', 'scripts/check-hover-lift.mjs'], True),

    ('inert controls',
     'no button that does nothing when pressed',
     FRONTEND, ['node', 'scripts/check-inert-controls.mjs'], True),

    ('language blocks',
     'no user-facing English written straight into a component',
     FRONTEND, ['node', 'scripts/check-language-blocks.mjs'], True),

    ('legal pages',
     'terms and privacy exist, are linked, and hold no placeholder',
     FRONTEND, ['node', 'scripts/check-legal.mjs'], True),

    ('null images',
     'no image that can render as a broken glyph',
     FRONTEND, ['node', 'scripts/check-null-images.mjs'], True),

    ('pollers',
     'nothing polls the API with no backoff',
     FRONTEND, ['node', 'scripts/check-pollers.mjs'], True),

    ('stale coming soon',
     'no coming-soon copy in front of something that is built',
     FRONTEND, ['node', 'scripts/check-stale-comingsoon.mjs'], True),

    ('status buckets',
     'every backend status belongs to a tab somebody can open',
     FRONTEND, ['node', 'scripts/check-status-buckets.mjs'], True),

    ('dependency order',
     'no dependency array reading a const declared further down',
     FRONTEND, ['node', 'scripts/check-tdz.mjs'], True),

    ('unbounded await',
     'no promise waiting on an event with no deadline',
     FRONTEND, ['node', 'scripts/check-unbounded-await.mjs'], True),

    ('guides',
     'every screen a person has to learn has a walkthrough',
     FRONTEND, ['node', 'scripts/check-guides.mjs'], False),

    ('tab strips',
     'one definition of a tab strip, not a second copy per console',
     FRONTEND, ['node', 'scripts/check-tabstrips.mjs'], False),

    # Needs a dev server on 127.0.0.1:3001 or :3005 (it tries both) and
    # reports "nothing was checked" without one, so it can never block. Run it
    # by hand during a Chrome walk.
    ('link embeds',
     'a pasted link shows a picture and a title',
     FRONTEND, ['node', 'scripts/check-embeds.mjs'], False),

    ('timing model',
     'every date renders in the reader own zone and chosen language',
     FRONTEND, ['node', 'scripts/check-datetime.mjs'], False),

    ('tap targets',
     'nothing pressable is under 44px on a phone',
     FRONTEND, ['node', 'scripts/check-tap-targets.mjs'], False),

    ('api paths',
     'every path the frontend fetches is one the backend serves',
     FRONTEND, ['node', 'scripts/check-api-paths.mjs', '--routes', ROUTES], True),

    ('timezone picker',
     'every zone is offered, and every date format value resolves',
     FRONTEND, ['node', 'scripts/check-timezone-picker.mjs'], True),

    ('live updates',
     'a refresh timer a re-render cannot tear down before it fires',
     FRONTEND, ['node', 'scripts/check-live-updates.mjs'], False),

    ('raw errors',
     'no developer exception is ever shown to a person',
     FRONTEND, ['node', 'scripts/check-raw-errors.mjs'], False),

    ('colour variables',
     'no undefined token, and --primary-bg is never a background',
     FRONTEND, ['node', 'scripts/check-css-vars.mjs'], True),

    # A ticket that has been given away gets a new code and the old one stops
    # resolving. Six endpoints resolve a ticket by code; the transferred answer
    # was added to one of them and the scanner still said "Not on the list",
    # because the scanner posts to a different endpoint. Twice in one hour on
    # 7 September, so it gets a catcher.
    # Five row numbers each had TWO rows on 7 September, an early "todo" and a
    # later "done", which left the register unable to answer what the state of
    # row 50 was. A register that cannot be read is not a register.
    ('ask register',
     'one row per ask, every row says what became of it',
     ROOT, ["python", "tools/check-inbox.py"], True),

    ('dead ticket codes',
     'a transferred code is named, not called unknown, at every door',
     ROOT, ["python", "tools/check-dead-codes.py"], True),

    # CEO, 13 September: "the 5% + NGN100 is something admins should be able
    # to set on the admin dashboard ... and it updates everywhere on the
    # platform, please create a checker for this that makes sure it applies
    # each time a new feature is built or added that has pricing." The day it
    # was written, four of the dashboard's eight money controls changed
    # nothing and the withdraw screen quoted a fee the server did not take.
    ('pricing',
     'every platform price has a default, a dashboard field and a reader, and is a number nowhere else',
     ROOT, ["python", "tools/check-pricing.py"], True),

    # CEO, 13 September: "i hope people can still bu stuff directly on the
    # platform without having to buy V-ENT coins, that option must always be
    # vaailable." One door in the platform took a card that morning.
    ('card path',
     'every purchase that can refuse for want of coins offers a card instead',
     ROOT, ["python", "tools/check-card-path.py"], True),

    # Owner rules R55 to R61 (17 September 2026): secrets, the admin key, RLS,
    # ownership, rate limits, billing caps, parameterised queries. One global
    # checker, configured per repo in security-rules.json; `--ledger` fails
    # when a HIGH count rises above security/debt.json. The runtime halves
    # (`--idor`, `--live`) need a server and are run by hand; see gates/40.
    ('security rules (backend)',
     'R55 to R61 on the Django side, against the ledger',
     BACKEND, ['node', os.path.expanduser('~/.claude/skills/security-rules/scripts/check-security.mjs'), '--ledger'], True),

    ('security rules (frontend)',
     'R55 to R61 on the Next side, against the ledger',
     FRONTEND, ['node', os.path.expanduser('~/.claude/skills/security-rules/scripts/check-security.mjs'), '--ledger'], True),

    # Third occurrence on 8 September of "built on the organiser side, forgotten
    # on the buyer side". A group rate of 16 VC at four or more was charged by
    # the server while the panel said 20 x 4 = 80 and took 64; an early bird
    # price the serializer carries a comment about was never drawn; and an
    # access code tier could be bought by nobody, because `ticket_types` has
    # always read `?code=` and no screen had a box to type one into.
    ('offer surface',
     'every organiser setting has a screen the buyer can read it on',
     FRONTEND, ['node', 'scripts/check-offer-surface.mjs'], True),

    # Three times on the afternoon of 8 September the frontend answered
    # "Cannot find module './vendor-chunks/next-auth@4.24.13_next@14.2...'" for
    # everybody at once. `next.config.mjs` used a fixed `.next-dev` for
    # development and four of us were running dev servers on 3001, 3002, 3005
    # and 3007, all writing that one directory and overwriting each other's
    # chunks. The dev distDir now carries the port.
    #
    # Worth knowing: the error names webpack and next-auth and points at
    # neither. It was first blamed on `pnpm build`, which was wrong, and the
    # wrong diagnosis was passed to three agents before it was corrected.
    ('dev distdir',
     'two dev servers on one checkout cannot share a build directory',
     FRONTEND, ['node', 'scripts/check-dev-distdir.mjs'], True),

    # The cost of that fix, which nobody was paying: one build directory per
    # port anybody has ever run a dev server on, about a gigabyte each. Eight
    # had built up holding 5.6 GB before the CEO noticed them in Explorer.
    #
    # NOT blocking, because a full disk is not a reason to refuse a commit and
    # because deleting is the fix rather than a code change. It is debt, so the
    # number is on the table every time and cannot quietly grow.
    ('stale builds',
     'a dev build directory nobody is serving is deleted',
     FRONTEND, ['node', 'scripts/check-stale-builds.mjs'], False),

    # The guard that stops a production build while a dev server is serving the
    # same tree, which gutted node_modules/next twice on 10 September.
    #
    # Its SELF-TEST runs here, not the guard itself. The guard lives in
    # `prebuild`, where it fires on every `pnpm build` without anybody
    # remembering it, and running it here would fail every check-all done with
    # a dev server up, which is the normal way to work. So what this holds is
    # that the guard still works, which is the part that can rot.
    ('build guard',
     'a build refuses to run while a dev server is on this tree',
     FRONTEND, ['node', 'scripts/check-before-build.mjs', '--self-test'], True),

    # A page that can show "Loading..." for ever.
    #
    # Fourth occurrence of one fault: three admin pages in August with a bare
    # `await fetch`, and /admin/settings on 9 September, which caught the
    # exception and raised a TOAST. The toast is gone in four seconds and the
    # loading state is still there behind it.
    #
    # Debt rather than blocking, because 42 files share the shape and clearing
    # them is a pass of its own. The number is on the table every commit and
    # cannot rise.
    ('spinner for ever',
     'a failed load says so on the page, rather than spinning',
     FRONTEND, ['node', 'scripts/check-spinner-forever.mjs'], False),

    # The server half of the same fault. `fetchForMetadata` answered null for
    # "does not exist" and for "never came back" alike, and every record route
    # read null as the first: an API outage served each event, tournament,
    # team and player page with the title "Event not found" and noindex, the
    # one instruction a crawler acts on at once. Found on 12 September while
    # walking the client fix with the API stopped; second occurrence of the
    # class, so it gets a catcher.
    #
    # Blocking, because the routes were fixed in the same commit and there is
    # nothing left to work down.
    ('metadata outage',
     'a record page describes an outage as unavailable, never as not found',
     FRONTEND, ['node', 'scripts/check-metadata-outage.mjs'], True),

    # The pnpm store, gutted. Third occurrence on 9 September, each within
    # seconds of building while a dev server was serving the same tree. The
    # error names a module, so it reads like a missing dependency and gets
    # treated as one; nothing in package.json changed.
    #
    # Blocking, because a damaged install means nothing else here can be
    # trusted, and the fix is four commands.
    ('pnpm store',
     'the install is whole, so a build can actually run',
     FRONTEND, ['node', 'scripts/check-pnpm-store.mjs'], True),

    # Where every link this machine builds points.
    #
    # Second time the value has been wrong: production carried a retired test
    # host in August and every emailed link 404d, and the local .env still
    # carried the same host on 9 September, so studio URLs and share links
    # built here pointed at nothing. Silent both ways, because nothing on the
    # machine that builds a link ever fetches it.
    ('frontend url',
     'links built here carry a host that exists',
     BACKEND, [sys.executable, 'tools/check-frontend-url.py'], True),
    # A gateway's or an exception's own words shown to a person (CEO, 29
    # September 2026: "Format is Authorization Bearer [secret key]"; inbox 354).
    ('gateway text',
     'no view sends exception or gateway text to a person',
     BACKEND, [sys.executable, 'tools/check-raw-errors.py'], True),
    ('gateway text self-test',
     'the raw-error catcher still catches str(exc) in a response',
     BACKEND, [sys.executable, 'tools/check-raw-errors.py', '--self-test'], True),
    # Two blocks in an email with no space between them (CEO, 29 September
    # 2026, the sign-in alert's button touching its paragraph; inbox 355).
    ('email spacing',
     'no two blocks in any email touch',
     BACKEND, [DJANGO_PY, 'tools/check-email-spacing.py'], True),
    # Coins exist only when somebody buys them (CEO, 29 September 2026;
    # inbox 366). Every place that adds to a balance names the payment.
    # Every notification kind has a row a person can switch (CEO, 30
    # September 2026; inbox 374, 377).
    ('notification rows',
     'every notification category belongs to a switchable row',
     BACKEND, [sys.executable, 'tools/check-notification-rows.py'], True),
    ('notification rows self-test',
     'the notification catcher still catches a category with no row',
     BACKEND, [sys.executable, 'tools/check-notification-rows.py', '--self-test'], True),
    # Every walk log names every role and screen (CEO, 29 September 2026;
    # inbox 335). The rule is in V-ENT/CLAUDE.md.
    ('role walks',
     'every walk log names every role and screen, or why not',
     ROOT, [sys.executable, 'tools/check-role-walks.py'], True),
    ('role walks self-test',
     'the walk-log checker still catches a skipped role',
     ROOT, [sys.executable, 'tools/check-role-walks.py', '--self-test'], True),
    ('coin sources',
     'every place that adds coins names the payment behind them',
     BACKEND, [sys.executable, 'tools/check-coin-sources.py'], True),
    ('coin sources self-test',
     'the coin catcher still catches a balance written up from nothing',
     BACKEND, [sys.executable, 'tools/check-coin-sources.py', '--self-test'], True),
    ('email spacing self-test',
     'the email spacing catcher still catches a button against its paragraph',
     BACKEND, [sys.executable, 'tools/check-email-spacing.py', '--self-test'], True),

    # Backend code with no screen in front of it.
    #
    # This checker has existed for weeks and was never in this table, and it
    # only failed on endpoints that were NEW since a baseline. So it answered
    # "No new ones" while seventeen endpoints had no way in, and the CEO found
    # them by looking at production and asking. Debt rather than blocking,
    # because some of the seventeen are legacy duplicates that want deleting
    # rather than a screen, and the ledger stops the number rising while they
    # are worked through.
    ('endpoints with no screen',
     'every endpoint has a screen that can reach it, or a written reason',
     ROOT, [sys.executable, 'tools/endpoint-callers.py'], False),

    # The ledger reads a number off each line above, and on 8 September it was
    # reading the wrong one: 311 stylesheets scanned instead of 145 tap targets
    # broken. A checker that is not in this table is a checker nobody runs, so
    # the parser's own test sits in it, and the pre-commit hook therefore runs
    # it on every commit.
    ('ledger parse',
     'the ledger records what a catcher counted, not what it scanned',
     os.path.dirname(os.path.abspath(__file__)),
     [sys.executable, 'test-count-parse.py'], True),

    # Written 7 September and never run by anything, which is the third time
    # that has happened. It earned its place within the hour: fixing the parser
    # inverted an assertion inside it, and nothing would have said so.
    ('ledger writeback',
     'a fall becomes the ceiling, a rise fails and is not written back',
     os.path.dirname(os.path.abspath(__file__)),
     [sys.executable, 'test-ledger-writeback.py'], True),

    # Second occurrence of the class on 8 September: row 176 on the 7th found
    # 17 gates unmet for work that was done, and row 215 today found two more
    # whole files, 66 boxes between them, unticked while the models shipped.
    # Debt rather than blocking, because it reports boxes other people own and
    # a check that always fails is a check everybody learns to skip.
    ('stale gates',
     'a gate box unticked while its own check already passes',
     ROOT, [sys.executable, 'V-ENT-BACKEND/tools/check-stale-gates.py'], False),
]


# ---------------------------------------------------------------------------
# The debt ledger
# ---------------------------------------------------------------------------
#
# CEO, 7 September 2026: "if checkers just report the issues and those issues
# are not acted upon as they are seen, then what is the point?"
#
# They are right, and `check-seo` proved it: it sat at 60 problems for weeks
# while `check-all` printed the number every time and nothing happened. The
# header of this file has said "a rising number is a regression" since the day
# it was written, and NOTHING CHECKED THAT EITHER. A rule nobody enforces is a
# rule, and a number nobody acts on is decoration.
#
# So the number is now recorded, and three things follow from the record:
#
#   1. A count that RISES fails, blocking or not. That is the promise the
#      header made and never kept.
#   2. A count that has not moved in `STALE_DAYS` is called out by name, with
#      how long it has been sitting there. "60 problems, unchanged for 14 days"
#      is a sentence somebody acts on; "60 problems" is not.
#   3. A count that FALLS is written back immediately, so the new, lower number
#      becomes the ceiling and the debt cannot quietly grow back.

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debt-ledger.json')

# How long a debt may sit at the same number before it is named as stuck.
STALE_DAYS = 7


# A standalone whole number. "44px", "390x844" and the "16016" glued into a
# word are not counts, so a digit run only counts when a word character sits on
# neither side of it. A decimal reads as its whole part, which no catcher prints
# today and which is recorded here so the next person knows rather than guesses.
_NUMBER = re.compile(r'(?<![\w.])(\d+)(?![\w])')
_URL = re.compile(r'https?://\S+')

# The verb beside a number that means it is the SIZE OF THE SCAN. "311
# stylesheet(s) CHECKED" is how much was read, never how much is wrong.
# "read" is deliberately absent: "0 ref(s) read but never attached" is a fault
# count, and the word appears in it.
_SCANNED = re.compile(
    r'\b(checked|scanned|inspected|examined|defined|sends|considered'
    r'|walked|crawled|visited|in total)\b')

# A number introduced by one of these is a denominator: "across 5 file(s)",
# "0 of 14 case(s)". The thing being counted is on the other side of it.
_DENOMINATOR = {'across', 'of', 'in', 'out', 'from', 'over', 'within',
                'among', 'under', 'per', 'than'}

# Debt a catcher has baselined. It is real work, but it is not what this line
# is reporting as NEW, and adding it to the new count would make every
# baselined catcher look like it had just regressed.
#
# "already" was in this list for one revision and came straight back out. No
# catcher writes it, and it broke the first line written after the change:
# "10 gate box(es) unticked while their own check already passes" read as a
# baseline and reported nothing. A word nobody uses is a guess, and a guess in
# a classifier is the fault the calibration rule exists to stop.
_BASELINE = re.compile(r'\b(known|baseline|being worked down)\b')

# A number the catcher itself says is not a failure. check-datetime prints
# "0 number-formatting notes, which do not fail the build."
_NOT_DEBT = re.compile(r'\bdo(es)? not fail\b')


def _count(line):
    """The number a catcher is COUNTING on its summary line, or None.

    The old rule took the first integer on the line, and that was wrong for
    seven of the twenty-four catchers in the table, because a catcher usually
    says how much it read before it says how much is broken:

        311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone

    The first integer there is 311, so the ledger stored the ceiling as 311
    while the real debt was 145, and the number could have climbed by 166
    without a single run failing. That is the ledger not doing the one thing
    it exists to do.

    So every standalone number on the line is classified by the words around
    it and only the FAULT ones are counted:

      SCANNED      the phrase after it holds a scanning verb - "checked",
                   "scanned", "defined", "the wizard sends".
      DENOMINATOR  the word before it is a preposition - "across 0 file(s)".
      BASELINE     the phrase after it says "known" or "being worked down",
                   which is debt this catcher has already accepted.
      NOT DEBT     the catcher says in words that it does not fail the build.
      FAULT        everything else.

    The answer is the SUM of the fault numbers, because a line can report two
    independent faults - "0 em/en dash(es) and 0 npm command(s) outstanding" -
    and taking only the first would let the second climb unseen, which is the
    same hole one level down.

    A line with no fault number at all returns None and the catcher is simply
    not tracked, rather than recorded at a number nobody can defend.
    """
    line = line or ''
    # An address is not a count. The embeds checker's "NOTHING WAS CHECKED.
    # Is the dev server running on http://127.0.0.1:3001?" was read as
    # 127 + 3001 = 3128 and sat in the ledger as debt for five days.
    line = _URL.sub('', line)
    hits = list(_NUMBER.finditer(line))
    if not hits:
        return None

    total = None
    # Once a line reaches its baseline, everything after it belongs to the
    # baseline. check-css-vars prints "0 new colour variable faults. 5 known:
    # 3 undefined-token, 2 primary-bg", and the 3 and the 2 are the breakdown
    # of the 5, not three separate faults.
    in_baseline = False

    for i, m in enumerate(hits):
        before = line[:m.start()].rstrip()
        words = before.split()
        preceding = words[-1].strip('.,;:()[]').lower() if words else ''
        after = line[m.end():hits[i + 1].start()] if i + 1 < len(hits) else line[m.end():]

        if in_baseline:
            continue
        if preceding in _DENOMINATOR:
            continue
        if _SCANNED.search(after):
            continue
        if _BASELINE.search(after):
            in_baseline = True
            continue
        if _NOT_DEBT.search(after):
            continue

        total = (total or 0) + int(m.group(1))

    return total


# A Node warning block is printed on stderr after the summary and carries a
# process id, so it looks like a numbered summary line and is not one.
_NODE_NOISE = re.compile(
    r'^\(node:\d+\)'
    r'|^\(Use `node'
    r'|^Reparsing as '
    r'|^To eliminate this warning'
    r'|\] Warning: ')


def _summary_line(output):
    """The line a catcher means as its summary.

    Not simply the last line. check-design ends with an indented breakdown of
    its baseline by rule, so the literal last line is "    15  glow" and
    reading it says the design debt is 15 when the line above says 119 known
    and 0 new. check-keys and dict-parity end with a Node module warning that
    carries the process id, which reads as a count and is not one.

    So: the last line that is not blank, not an indented detail row, and not
    part of a Node warning.
    """
    lines = (output or '').split('\n')
    for line in reversed(lines):
        if not line.strip():
            continue
        if line[:1].isspace():
            continue
        if _NODE_NOISE.search(line):
            continue
        return line.strip()
    return ''


def _load_ledger():
    try:
        with open(LEDGER, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_ledger(data):
    with open(LEDGER, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write('\n')



def compare(tracked, ledger, today):
    """What changed since the last run.

    `tracked` is (name, last_line, count) for EVERY checker, clean ones
    included at 0 - see the classifier in main() for why that matters.

    Returns (risen, stuck, fell, ledger). `ledger` is mutated and returned so
    the caller saves one object. `fell` carries None for a first sighting: it
    has to be saved, but there is nothing to announce.

    A risen count is deliberately NOT written back. The ceiling stays where it
    was so the next run fails too, until somebody actually brings it down.
    That is the promise the header makes and the reason this is not simply
    "record the latest number".
    """
    risen, stuck, fell = [], [], []
    for name, last, now in tracked:
        was = ledger.get(name)
        if was is None:
            ledger[name] = {'count': now, 'since': today, 'first_seen': today}
            fell.append(None)
            continue
        if now > was['count']:
            risen.append((name, was['count'], now, last))
        elif now < was['count']:
            fell.append((name, was['count'], now))
            ledger[name] = {'count': now, 'since': today,
                            'first_seen': was.get('first_seen', today)}
        else:
            days = (datetime.date.fromisoformat(today)
                    - datetime.date.fromisoformat(was['since'])).days
            if days >= STALE_DAYS:
                stuck.append((name, now, days, last))
    return risen, stuck, fell, ledger


# Every catcher judges the backend being committed. The workspace tools/*.py
# are forwarders to the MAIN checkout, so a row that ran them from a worktree
# checked other code (29 September 2026); run this backend's own copy, and
# tell every script which backend that is.
os.environ['VENT_BACKEND'] = BACKEND
CATCHERS = [
    (row[0], row[1], BACKEND, [row[3][0], os.path.join(BACKEND, row[3][1])] + list(row[3][2:]), row[4])
    if (row[2] == ROOT and len(row[3]) > 1 and str(row[3][1]).startswith('tools/')
        and os.path.isfile(os.path.join(BACKEND, row[3][1])))
    else row
    for row in CATCHERS
]

def run(cwd, command):
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True,
                              text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, str(err)
    output = (done.stdout or '') + (done.stderr or '')
    return done.returncode, _summary_line(output)


def main():
    only_blocking = '--blocking' in sys.argv

    failed_blocking = []
    debt = []
    # Every checker's number, clean ones included. See the note in the
    # classifier below for why a clean checker is recorded rather than skipped.
    tracked = []

    print('%-20s %-9s %s' % ('CATCHER', 'RESULT', 'LAST LINE'))
    print('-' * 96)

    for name, rule, cwd, command, blocking in CATCHERS:
        if only_blocking and not blocking:
            continue

        code, last = run(cwd, command)

        if code is None:
            state = 'ERROR'
            failed_blocking.append((name, rule, last))
        elif code == 0:
            state = 'clean'
            # Recorded at ZERO, which is the whole point. A checker that
            # reaches 0 used to drop out of the ledger entirely and freeze at
            # whatever it last failed with, so climbing back to that number
            # read as "unchanged" and twenty-one real breaches could return in
            # silence. The count is not PARSED here: exit 0 is the statement
            # that there are no faults, and it is exact where reading the
            # first integer off "650 route(s) checked, 0 fetch(es)" is a guess.
            tracked.append((name, last, 0))
        elif blocking:
            state = 'BREACH'
            failed_blocking.append((name, rule, last))
        else:
            state = 'debt'
            debt.append((name, last))
            n = _count(last)
            if n is not None:
                tracked.append((name, last, n))

        print('%-20s %-9s %s' % (name, state, last[:66]))

    print('')

    # ---------------------------------------------------------------- debt
    #
    # Recorded rather than merely printed. See the note above the ledger.
    ledger = _load_ledger()
    today = datetime.date.today().isoformat()

    risen, stuck, fell, ledger = compare(tracked, ledger, today)

    if '--record' in sys.argv or fell:
        _save_ledger(ledger)

    if debt:
        print('Debt. Every one of these is work somebody has to do:')
        for name, last in debt:
            was = ledger.get(name, {})
            since = was.get('since')
            age = ''
            if since:
                days = (datetime.date.today()
                        - datetime.date.fromisoformat(since)).days
                age = ' (unchanged for %d day%s)' % (days, '' if days == 1 else 's')
            print('  %-18s %s%s' % (name, last[:60], age))
        print('')

    dropped = [row for row in fell if row is not None]
    if dropped:
        print('Down since the last run, and the new number is now the ceiling:')
        for name, was, now in dropped:
            print('  %-18s %d -> %d' % (name, was, now))
        print('')

    if stuck:
        print('STUCK. These have not moved in %d days or more:' % STALE_DAYS)
        for name, now, days, last in stuck:
            print('  %-18s %s' % (name, last[:60]))
            print('  %-18s at %d for %d days' % ('', now, days))
        print('  Pick one and bring it down, or say out loud why it stays.')
        print('')

    if risen:
        print('DEBT WENT UP. This is a regression and it blocks:')
        for name, was, now, last in risen:
            print('  %-18s %d -> %d' % (name, was, now))
            print('  %-18s %s' % ('', last[:70]))
        print('')
        print('The ceiling was not moved, so this keeps failing until the number')
        print('comes back down. That is the whole point of recording it.')
        return 1

    if failed_blocking:
        print('BREACHES that must be fixed before this ships:')
        for name, rule, last in failed_blocking:
            print('  %s - %s' % (name, rule))
            print('      %s' % last)
        return 1

    print('Every blocking catcher is clean.')
    if debt:
        print('%d catcher(s) still carrying debt. None of it went up.' % len(debt))
    return 0


if __name__ == '__main__':
    sys.exit(main())

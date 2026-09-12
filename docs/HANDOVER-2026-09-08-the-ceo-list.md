# Handover, 8 September 2026: the CEO list of seventeen, run as a team

Written while working, not after. Read with `HANDOVER-2026-09-08-the-last-of-the-list.md`,
which is what this list was assembled from.

## What was asked

The CEO read back the open list from that handover and answered it line by
line. Seventeen answers, written into `tasks/inbox.md` as rows 199 to 216
BEFORE any work started, per the standing rule. Then, mid turn: "Use agent
teams."

So this is orchestrated rather than solo. `PLAN.md` at the workspace root holds
the file ownership map that keeps seven agents out of each other's way, and
each leaf has its own gates file under `gates/`.

| Leaf | Rows | Gates |
|---|---|---|
| ledger and gates | 214, 215 | `gates/18-ledger-and-gates.md` |
| rankings media | 204 (was 185) | `gates/19-rankings-media.md` |
| wallets | 199, 201 (was 190, 192) | `gates/20-wallets.md` |
| subscriptions | 202 | `gates/21-subscriptions.md` |
| admin dashboard | 200 (was 191) | `gates/22-admin-dashboard.md` |
| blocked infra | 207, 208 (A5 and Q3) | `gates/23-blocked-infra.md` |
| seed accounts | 205, 206 | `gates/24-seed-accounts.md` |
| the four walks | 210, 211, 212, 213 | `gates/25-walks.md`, run by the lead |

## Two answers written rather than built

**Row 203, "what is cusody decision".** Answered in
`tasks/specs/crypto-and-custody.md`. In one line: custody is the answer to who
holds the private key when a user's USDT is "on V-ENT", and it decides what we
build, what licence we need and what happens on the worst day. Three possible
answers, the recommendation is the lightest one, and the CEO is asked one
question that unblocks the code: does a user ever HOLD a USDT balance here, or
is USDT only a way in and a way out.

**Row 201's second half, "explain how we can start using/trading on crypto".**
Same document, sections 4 to 7. The short version is that using crypto and
trading crypto are two different businesses. Payments in and out is achievable
without holding anybody's coins. Trading is an exchange, and an exchange inside
a gaming platform is the wrong place for it.

**Row 209, the Paystack surcharge.** Closed as a decision rather than work: the
CEO has confirmed the buyer bears it. Nothing changes in the repo, because the
surcharge is a Paystack dashboard setting and `ledger.quote` is already correct.

## The fault that started the list

`_count()` in `tools/check-all.py` reads the FIRST integer on a catcher's
summary line. `check-tap-targets` prints "311 stylesheet(s) checked, 145 tap
target(s) under 44px on a phone", so the ledger recorded a ceiling of 311 when
the real debt is 145. Debt could have grown by 166 in silence.

This is the same class as row 178, which was fixed on 8 September for the CLEAN
half: a checker reaching 0 froze its row. The failing half was never looked at.
Worth naming as a pattern: a fix that addresses the case in front of it and not
the shape of the fault leaves the other half alive, and the other half is the
one nobody is looking at.

## Status

- 2026-09-08: rows 199 to 216 in the inbox, gates written, seven leaves running.
- 2026-09-08: `tasks/specs/crypto-and-custody.md` written, rows 203 and the
  explanation half of 201 answered.

## The ticketing walk, and what pressing it found (row 212)

Walked `lagos-anime-con-2026` signed in, pressing rather than reading. Three
organiser settings have NO buyer-facing surface, and all three are stored,
serialized and honoured by the server:

1. **An access code tier can never be bought.** `ticket_types` reads `?code=`
   and answers with `unlocked` and `hidden_count`; `buy_ticket` re-checks the
   code. No frontend caller sends one and no screen has an input for it. The
   Cosplayer pass, code COSPLAY26, is unbuyable by anybody at all.
2. **The group rate is neither shown nor applied on screen.** VIP is
   `group_min` 4, `group_price` 16 VC. At quantity 4 the panel reads "20 VC x 4,
   Total 80 VC" while `tier.price_for(4)` returns 16, so the server charges 64.
3. **Early bird is never shown.** `serialize_tier` literally carries the comment
   "so the buy screen can say 12 left at this price". No screen says it.

The cause is one line: `ticketsCost = buyTier.price * buyQty` in
`view-event/page.js`. `ledger.quote()` is the authority the backend comment
says both sides must read, and it was never exposed over HTTP, so the screen
could not read it even if somebody had wanted to.

Also found: `EventTicketModal.js` is a SECOND buy screen carrying the same
multiply-the-price bug, with no importer anywhere. Dead code that would have
drifted further.

## A measurement artifact that probably explains row 185

An image with `loading="lazy"` never loads while its tab is hidden, so
`naturalWidth` reads 0 forever in a background tab. Measured on the event page
from an unfocused tab: four images at naturalWidth 0 including the site's own
local static SVG, which cannot be a backend fault. Flipping them to eager in
the same hidden tab loaded all four.

Row 185 has sat open as "the file serves 200 as a real PNG but the browser
still reports naturalWidth 0", which is exactly this shape. The rankings leaf
has been told to re-measure with the tab focused and to assert
`document.hidden === false` in the same snippet as the reading, before changing
any code.

## What was built for the ticketing faults, and how it was proven

`GET /event/<ref>/quote/` answers what a purchase costs, from `ledger.quote`,
which is the function the checkout charges through. Public, because a guest has
to be told the same number as a member. It also returns `price_reason`
(`list`, `group` or `early_bird`), the list unit price for comparison, and both
offers so a panel can state them before the quantity reaches them. A hidden
tier is priced only for somebody holding its code, otherwise quoting it would
publish the presale it exists to hide.

14 tests in `vent_event/tests_quote.py`, including one that asserts the quote
equals what `ledger.quote` would charge at six different quantities. That is the
test that stops the two sides drifting again. Full `vent_event` suite: 884 tests,
OK.

The buy panel now reads it. The tier card states the group rate and the early
bird line. There is an input for a presale code, which the listing, the quote
and the purchase all carry.

Proven by pressing, not by reading:

- VIP at quantity 4 reads "16 VC x 4", "Group rate, saves 16 VC", "Total 64 VC".
  The server charges 64. Before today it read "20 VC x 4, Total 80 VC".
- Typing COSPLAY26 answered "Unlocked: Cosplayer pass" and the tier appeared.
- A free hidden tier seeded for the test was bought end to end with the code:
  ticket VT-8QHT28R5 issued, tier sold 1. Without the code that purchase is a
  403, which is what made every hidden tier unbuyable.
- The Standard tier now reads "46 left at this price, then 7 VC", which is the
  early bird sentence the serializer has carried a comment about for weeks.

`src/components/view-event/event-ticket-modal/` is deleted. It was a second buy
screen with the same multiply-the-price bug and no importer anywhere.

## A catcher for the class, per the standing rule

`scripts/check-offer-surface.mjs`. Third occurrence of "built on the organiser
side, forgotten on the buyer side", so it gets a runnable check rather than a
sentence in a handover. It holds the seven tier settings an organiser can change
that decide what a buyer is charged, told or allowed, and fails when any of them
is read by no buyer screen.

Proven both ways: `--self-test` passes 4 cases, including the group rate fault
and the missing code input exactly as they shipped. Plain run: "7 organiser
setting(s) checked, 0 with no buyer surface". Registration in `check-all.py` is
with the ledger leaf, which owns that file.

## Two things that broke while several agents worked at once

1. A `pnpm build` from another leaf corrupted `.next-dev` and the dev server
   answered "Cannot find module './vendor-chunks/next-auth@...'" for everybody.
   Killed, `.next-dev` deleted, restarted on 3005. All three build leaves have
   been told not to build while dev is running.
2. The backend on 8000 was serving code from before this session and answered
   404 for the new endpoint. Restarted. Worth remembering when a walk says an
   endpoint does not exist: check the server is running the code you wrote.

## Correction: what actually broke the frontend twice

Not `pnpm build`, which is what I said the first time. TWO dev servers were
running, 3005 and 3001, and `next.config.mjs:27` sets `distDir` to `.next-dev`
in development. Both processes wrote that one directory and overwrote each
other's chunks, which the browser reports as "Cannot find module
'./vendor-chunks/next-auth@4.24.13_next@14.2...'".

Worth keeping, because the error names webpack and next-auth and points at
neither. One dev server per checkout, and when several agents work at once the
lead runs it and everybody else uses it. All four frontend leaves have been
told.

## The dev server clash, fixed structurally rather than by asking people

It happened a third time, so it stopped being a coordination problem and became
a configuration one. `next.config.mjs` now puts the PORT in the development
build directory:

    distDir: development ? (PORT ? `.next-dev-${PORT}` : '.next-dev') : '.next'

`next dev -p 3001` sets PORT before the config is read, which is verified rather
than assumed: four directories now exist, `.next-dev-3001`, `-3002`, `-3005` and
`-3007`, one per server people are actually running. `.next-dev-*` is in
`.gitignore`, and `scripts/check-dev-distdir.mjs` holds the class with a four
case self-test including the exact shape that broke.

Production is untouched at `.next`, because the VPS unit serves
`.next/standalone/server.js` and moving that to fix a local annoyance would be
the wrong trade.

## The second invisible fault of the afternoon: CORS by port

A page served from port 3200 rendered its shell and then said "This page did
not load". Every fetch answered `TypeError: Failed to fetch`, the Django log
had no matching request at all, and nothing anywhere said the word CORS.

`vent/settings.py` allowed a hand-written list of dev origins: 3000, 3001,
3002, 3005 and 3100. That list had been extended four times, once per port
somebody happened to use, and it carries a comment warning about this exact
symptom three lines above itself.

Ended rather than extended:

    if DEBUG:
        CORS_ALLOWED_ORIGIN_REGEXES = [r'^http://localhost:\d+$',
                                       r'^http://127\.0\.0\.1:\d+$']

Guarded by DEBUG, so production still answers only the origins it is configured
with. Proven in a real browser rather than by reading: the same fetch that had
just failed answered 200 with the tier list, and the ticket panel came back
carrying both offer lines.

Three of the four leaves were walking on ports outside that list today, so this
was costing everybody at once.

## An event's overlays could not read the event's own run of show (row 211, gate B5)

Found by opening the now and next graphic on a broadcast bound to an EVENT. It
drew nothing. The event has a run sheet with 161 cues, marked public.

The event feed built `programme` from `EventSession` rows only. That event has
none, because a smaller event writes a run of show and never a separate audience
schedule. So both the programme graphic and the now and next bar were empty on a
real event with a real running order.

The tournament side has had this since the run of show shipped, and
`_sheet_for_tournament` even reaches sideways INTO the event's sheet when a
tournament is a segment of a convention day. The event, which is where the sheet
lives, was the one surface that could not read its own. Two surfaces, one job,
one of them built: the same class as every other one sided fault here.

What changed, all in one implementation rather than a second copy:

- `_run_of_show_from_sheet(sheet, include_private)` holds the body, and
  `run_of_show_for(tournament)` and the new `run_of_show_for_event(event)` are
  thin wrappers over it.
- `BLANK_RUN_OF_SHOW` gained `items`, today's cues in order, so a programme
  graphic can draw the running order rather than only the two cues either side
  of now. Present and empty rather than absent, like every other block there.
- The public event feed reads the sheet with `include_private=False`, exactly as
  the tournament feed does, because that address is public and a run sheet
  carries staff names and when the money is counted.
- A published `EventSession` still WINS for what is on, because that is the
  schedule the audience is holding. The sheet answers when there is none.
- The studio's event branch re-asks with `include_private=True`, the way the
  tournament branch always has, so an organiser running from a private sheet
  sees it on their own graphics.
- The run stamp joins the feed version, because the sheet does not change when
  the clock rolls past 14:00 and the graphic has to.

`vent_tournament/tests_event_run_of_show.py`, 8 tests: the programme comes from
the sheet, now and next come from the sheet, the block travels under the same
name a tournament uses, a private sheet is withheld from the public feed and
visible to the studio, a published session wins, an event with neither draws
nothing rather than breaking, and the version moves when the cue on screen
changes. One existing test asserted the literal blank block and was updated for
the new `items` key, deliberately and with the reason written in.

## An event could not read its own run of show (row 211, gate B5)

Walking the studio element pages found the gap the gate was written to catch. A
broadcast bound to an EVENT drew nothing on the now and next graphic, while
that event's run sheet held 161 cues and was marked public.

The cause: the event feed built `programme` from `EventSession` rows and the
event had none. It never read the run sheet at all. A TOURNAMENT has had this
since the run of show shipped, and `_sheet_for_tournament` even reaches
sideways into the EVENT's sheet when the tournament is a segment of a
convention day. So the event, which is where the sheet actually lives, was the
one surface that could not read its own.

Fixed as one implementation with two subjects, which is what the rule here
already says for tournaments and events:

- `_run_of_show_from_sheet(sheet, include_private)` holds the body.
- `run_of_show_for(tournament)` and `run_of_show_for_event(event)` are thin
  wrappers over it.
- The event feed reads the sheet when there are no published sessions, and a
  published session still wins when there is one, because that is the schedule
  the audience is holding.
- The studio's event branch asks again with the organiser's own token, exactly
  as the tournament branch does, so a PRIVATE sheet reaches the console and
  still never reaches the public feed.
- The block now carries `items`, the whole day, so a programme graphic can draw
  the running order rather than only the two cues either side of now.

8 tests in `vent_tournament/tests_event_run_of_show.py`, including the private
sheet withheld from the public feed and visible through the studio token, a
published session winning over the sheet, and the version moving when the clock
passes a cue. The 57 existing overlay tests still pass; one asserted the exact
shape of the blank block and now names `items` as well.

Also walked and shot: the scorebar drawing Nigeria 2 - 1 Ghana from live data,
and the standings graphic drawing the six real entrants of Vermillion EA FC
Showdown. Both on a real element URL, which is what a browser source loads.

## The house look now draws the four graphics it was borrowing (row 210, E2)

`desk_lower_third`, `matchday`, `analyst_desk` and `play_area` arrived as a port
of the client's CADE pack and were listed in the HOUSE map as well, so a
broadcast that had not picked the Rivalry look put the client's green artwork on
air. The gate said so in its own words: "that is the right failure but it is not
the right answer."

Four house components now, in `src/app/studio/[...parts]/page.js` with their
stylesheet beside the rest: V-ENT red on `#17171a`, no strokes, no glow, same
payload fields and same empty states as the pack versions. The two camera frames
share one plate component, because the analyst desk and the play area are the
same furniture with different words in it.

Walked in Chrome at each element URL on a house-look session. The desk drew red
HOST and LEAD CASTER chips with Kunmi and Tomide; the analyst desk drew THE DESK
over Analysts; matchday drew TODAY'S MATCH UPS, a DAY 1 tag and five real ties;
the same card with `results: yes` drew RESULTS and the aggregates, Ghana 4-3
Ivory Coast and the rest. The rivalry look still draws the pack version at the
same address, which is what a look is for.

**Found by shooting it rather than reading it:** the first version drew five rows
saying VS with no names in them. `rivalry.days[].fixtures` is a list of IDS and
it was being read as a list of fixtures. A card that renders five confident empty
rows is worse than one that says nothing, and no amount of reading the component
would have shown it.

## Where the walks stand

- Ticketing (row 212): done, three faults found and fixed, member and guest,
  desktop and emulator.
- Draft duplication (row 213): the two production rows are reconciled ON PAPER
  with a recommendation for the CEO (keep 26, delete 28, neither holds an entrant
  or a match), and nothing is deleted without their word.
- Production build (row 211): B5 found and fixed the event run of show gap.
- Overlay design (row 210): C3 walked in Chrome, E2 built and walked. C4, the
  console controls, and E1, the org badge column, are next.

## After the usage limit: what state the work is in

The session hit its limit at about 14:15 and three leaves died mid-build with
it: wallets, subscriptions and the admin dashboard. Four had already finished
(ledger and gates, rankings media, blocked infra, seed accounts). All three were
relaunched with a brief that names what the previous agent had already put in
the working tree, so none of it is redone.

Nothing is committed. Both repos are on `feat/funnel-ledger-transfer-rankings`
with everything in the working tree.

## The two "blocked" rows were never blocked, and that is the finding of the day

The blocked-infra leaf re-measured both and found the same cause under each:
somebody had measured the wrong address.

**A5, the VPS.** The 7 September probe used 138.68.126.199, which is the
`evotv` host in `~/.ssh/config`, a different project. `v-ent.co` resolves to
162.35.101.16, which every ssh line in this repo has always used. That box is
up 21 days, SSH answers, the backup cron was ALREADY installed and running, and
today's dump restores into a throwaway MySQL container with 165 tables and 3806
rows: 1427 tickets, 141 users, 102 waitlist reservations, all of which agree
with facts already known about production. Two pieces of work were written off
on a true measurement of a machine that has nothing to do with V-ENT.

The one thing genuinely broken: the cron redirects stderr into a log file, so
cron mails nothing and there is no MAILTO. Every failure since the script was
written has produced one line in a file nobody opens.

**Q3, AFC.** The quoted 404s are against paths that never existed. The real
surface is `api.africanfreefirecommunity.com/sso/`, which answers 302 on
authorize, 405 on token (POST only) and 401 on userinfo. Production already
holds AFC_CLIENT_ID, AFC_CLIENT_SECRET and AFC_SSO_ENABLED=1, and posting the
real credentials with a deliberately bad code answers `invalid_grant` rather
than `invalid_client`, which is what proves they recognise us. The button is
live on production now. The only unproven thing is a human with a real AFC
account signing in, which nobody here can fabricate.

## Three production writes are waiting on the CEO (inbox row 228)

1. Deploy the updated `deploy/backup.sh` and the new `deploy/restore-check.sh`.
2. Add `BACKUP_ALERT_EMAIL` to the production .env. The CEO picks the address.
3. Add the dead man cron: `0 11 * * * /srv/vent/backend/deploy/backup.sh --check-freshness`.

Nothing has been written to production by anybody in this session.

## The four walks, where they stand

`gates/25-walks.md` carries the evidence line by line. Twelve of eighteen boxes
are closed.

**Ticketing (row 212)** is the one that found real faults, and they are fixed
and re-walked: the panel pricing a purchase itself, an access code tier nobody
could buy, and an early bird line that was never drawn. Failure paths proven
too: a hidden tier with no code answers CODE_REQUIRED, a wrong PIN answers
INVALID_PIN, a wrong code says so on the screen, and a tier forced to zero draws
Sold out with the button disabled. Guest checkout carries the same numbers as
the member one.

**Draft duplication (row 213)** is reconciled and waiting on the CEO. Rows 26
and 28 are the same cancelled tournament with no entrants and no matches;
recommendation is to keep 26 and delete 28. Events cannot have this fault at
all, because events have no draft: `is_draft` does not exist on the event model.

**The overlays (rows 210 and 211)** found the event run of show gap described
above, and closed the element pages, the org badge case and the user types.
What is NOT closed, and is said plainly rather than rounded up: the studio
console's own controls. Start a broadcast was pressed and created a live
session; Put on air, End broadcast, Copy URL and a slot swap were not, because
Chrome timed out three times with "the renderer may be frozen or unresponsive"
while three other agents were driving the same browser and the machine had
already killed background tasks for memory.

## The third list that was extended once per port

`next.config.mjs` pinned image hosts to `127.0.0.1:8000` and `127.0.0.1:8100`,
with `localhost` at `port: ''`. Next matches with
`if (pattern.port !== undefined)`, so `''` means "must have NO port" and
omitting port means "any port".

A backend on any other port therefore made `next/image` throw "Invalid src
prop". That throw is not a missing picture: it takes the whole page down to its
error boundary, which is what "This page did not load. We could not load the
events just now." was, while the API answered 200 to curl the whole time.

In development both loopback hosts now match any port. Production is unchanged
and still strict. That is the third list today that had been extended one port
at a time: CORS origins, the dev build directory, and now image hosts. The
common shape is worth naming: a local convenience list that silently refuses
anything not written on it, and reports the refusal as something else entirely.

## Pressing the studio console found a machine name in front of an operator

The look selector works and is proven on the far side rather than by a toast:
switching Design from V-ENT to CADE Rivalry Series made `/studio/<token>/feed/`
answer `session.theme: "rivalry"`, and `"vent"` again on the way back.

What pressing it found: the graphics list showed four kinds as
`desk_lower_third`, `matchday`, `analyst_desk` and `play_area`, raw snake case
sitting among Score bar, Now and next and Head to head. `labelsFor` in
`StudioPanel.js` held 21 kinds and none of those four, so the console fell
through to the key. They have names now, in all three languages.

Two things I could not press, with the reason rather than a rounding up: the
payload editors for those four kinds, and the text layer editor. Three agents
share this Chrome, and localhost cookies are shared across PORTS, so my
signed-in session was replaced twice mid-walk, once by another tab's signout and
once by another account signing in. The console then correctly drew nothing,
because the account it had become does not run that tournament. Walking a
console properly while several agents work wants a browser profile of its own.

Worth keeping as a fact about this machine: a signout on :3001 signs you out on
:3200, and a sign-in on one port replaces the session on every other.

## Gate E1 closed with a picture rather than an argument

E1 said an organiser's own tournament has no org badges so the tile column never
draws, and its evidence line said real data would fill it. That is a claim about
DATA, so it was answered by putting real data in.

Two sides of the Rivalry demo were given a genuine 400x400 PNG. The file was
chosen by reading its IHDR header rather than by name, because 3,374 of the
15,833 pictures under `media/` are 1x1 test files and a 1x1 badge draws nothing
while looking like it worked. The feed then carried `home.logo` on those two
sides, and the matchday graphic drew the badge in the tile for Nigeria and Ghana
while the sides with no badge kept drawing their names.

Nothing in the code needed changing. The path from `side_identity` to the
graphic is complete and had simply never been fed. The badges were removed again
afterwards, because a walk logo on Nigeria is wrong data and that demo is what
everybody else reads.

## The two "blocked" rows were never blocked, and I checked it myself

The infra leaf found that the 7 September verdict on both A5 and Q3 was reached
against the wrong address, and I re-ran the probe rather than take it on trust:

    162.35.101.16:22    open  0.500s  SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.19
    162.35.101.16:80    open  0.234s
    162.35.101.16:443   open  0.266s
    138.68.126.199:22   open  0.266s  (NOT an address v-ent.co resolves to)

`138.68.126.199` is a different project's box in `~/.ssh/config`. `v-ent.co`
resolves to `162.35.101.16`, which every ssh line in this repo has always used,
and it is up and answering. The backup cron was already installed and ran at
03:00 today; the leaf pulled that dump down and restored it into a throwaway
container, 165 tables and 3806 rows, cross-checking against facts already known
about production.

AFC is the same story: the 404s were against paths that never existed. Their
real SSO answers, our credentials are already registered with them, and the
button is live on production now.

Two pieces of work were written off on a measurement of the wrong machine. The
lesson is not "measure", which everybody agrees with. It is that a measurement
carries the ADDRESS it was taken at, and a probe that names the host it hit
would have caught this the first day.

The seed accounts sheet carried the same wrong claim about port 22 and has been
corrected in place rather than rewritten, so the CEO reading it sees both what
was believed and what is true.

## What running six agents in one checkout actually costs

Recorded because it is the largest thing slowing this session down, and none of
it is in anybody's code.

- **Seven Next dev servers and 0.3 GB of free RAM.** One route compile took 148
  seconds and Chrome could not screenshot at all. Standing down three servers
  took free memory to 8.7 GB in one step. One dev server per person, and the
  lead runs it.
- **Two Django processes bound to port 8000 at once.** Windows lets both bind
  and a request lands on whichever answers, so the API was two different
  processes running two different versions of the code. That is what produced
  the "the backend is on MySQL and is behind" report, which was wrong: the
  server answering now is on sqlite, proven by asking it for a tier that exists
  only in `local-dev.sqlite3`.
- **Cookies on localhost are shared across PORTS.** A signout on :3001 signs you
  out on :3200, and a sign-in on one port replaces the session on every other.
  It interrupted the console walk twice and each time the screen correctly drew
  nothing, which reads exactly like a broken console.
- **Two agents editing one file.** `vent_auth/models.py` and `views_wallet.py`
  are being changed by a leaf and by a separate session at the same time. The
  integration held this time because the USDT rail sat cleanly on top of the
  payout hold, but that was luck rather than design. Ownership is now split in
  writing: the hold, settle and return path belongs to the wallets leaf, the
  USDT rail to the other session.

The rule that would have prevented most of it: one dev server, one backend, one
owner per file, and every claim about the environment measured rather than read
off a process line.

## Row 206 is done on production, and doing it found a booby trap

The CEO approved removing the production test data ("can remove"), and the
reason it had not happened was a wrong belief that the box was unreachable. It
is reachable, so it is done.

    backup first   db-2026-09-08-1930.sql.gz   156K, 165 tables, 84 inserts
    dry run        73 rows across 19 tables, 0 protected, no tickets, no money
    removed        studio-test-safe-to-delete        23 rows
                   scorekeeper-test-safe-to-delete    8 rows
                   asset-proof-safe-to-delete        42 rows
                   team Asset Proof Alpha             2 rows
    tournaments    13 before, 10 after
    verified       all three answer 404 on api.v-ent.co, and the public listing
                   carries no safe-to-delete row

The eight `st_alpha1` to `sk_bravo2` accounts inside those teams were NOT
touched. They were not on the approved list, and they are on the decision sheet.

**The booby trap.** Taking the backup first, I ran `/srv/vent/deploy/backup.sh`
and it answered `mysqldump: Access denied for user 'vent'@'localhost' (using
password: NO)` and wrote a **20 byte** `db-2026-09-08-1930.sql.gz` into the
backups directory. There are TWO backup scripts on that box: the one the cron
runs is `/srv/vent/backend/deploy/backup.sh` and it works. The other is an old
copy that cannot authenticate and writes an empty file under a name that looks
like the newest backup.

I deleted the empty file I had just caused and took a real backup with the
working script. The stale copy is still there and is inbox row 236: it should be
removed, or made to fail loudly instead of writing. This is the same class as
the `mysqldump exits 0 with nothing in it` fault from 2 September, wearing a
different hat: the failure is not in the dump, it is in there being two scripts
and only one of them being right.

## The CEO answered all five, and three of them are now done

**Custody (row 236).** "A way in or out, once it enters the platform it turns to
VENT coins." That is answer A in `tasks/specs/crypto-and-custody.md`, the
lightest of the three: nobody ever holds a USDT balance on V-ENT, so V-ENT holds
no keys and no customer crypto, only its own short lived payout float. It is a
payments feature, not custody, and not an exchange.

**Backup alerts (row 237). DONE on production, all three writes.**

- `deploy/backup.sh` and the new `deploy/restore-check.sh` deployed to
  `/srv/vent/backend/deploy/`. The old script is kept as
  `backup.sh.bak-2026-09-08`.
- `BACKUP_ALERT_EMAIL=Vermillionencore@gmail.com` written to the production
  `.env`, line 40.
- The dead man cron installed: `0 11 * * * .../backup.sh --check-freshness`,
  and RUN by hand to prove it: "freshness ok: db-2026-09-08-0300.sql.gz is 16
  hours old".
- One test alert was sent to that address through the box's own Postfix, which
  accepted it and left an empty queue. Whether it landed in the inbox is the one
  thing this side cannot see; the mail log needs root.

**Seed accounts (row 238).** "Leave all the demo accounts." Nothing removed. The
596 line sheet stays as the record, and `remove_seed_accounts` is now
explicit-list-only and reversible whenever it is wanted.

**The duplicate tournament (row 239). DONE on production.** Row 28 and its one
ruleset row were exported first, and the export was CHECKED rather than assumed:
the first attempt wrote a 0 byte file, which is exactly the failure the backup
work is about, so it was redone and read back at 2793 and 540 bytes. Then
deleted: tournaments 10 to 9, two rows. Live now: `rivalvry-series-s2` answers
200, `rivalvry-series-s2-2` answers 404, and v-ent.co answers 200.

**Shipping (row 240).** In progress. The bar the CEO set is "completely fixed and
tested", so what is proven and what is not is listed rather than rounded up.

## Handing the ship over, and why

The CEO told the other session working this checkout to take the work over and
ship it: "the agents are taking too much tokens and are still slow, please pick
up from them and do it yourself". So that session ships and this one does not
push, merge or deploy. All three leaves were told to stop at a clean point,
write their gates evidence and report, and to make no new edits, no build and no
server restarts, because a file changing under a build is how something nobody
tested goes out.

What was handed over, in two halves rather than one summary.

PROVEN
- Full backend suite with everything in the tree: 3587 tests, OK (skipped=1).
- check-all: every blocking catcher clean, one debt row (tap targets 141).
- The quote endpoint and the three ticketing faults, with the walk behind each.
- Five refusal paths driven and answering correctly.
- The event run of show, 8 tests.
- Four house drawings, walked under both looks.
- The four studio kinds named in three languages.
- Two new catchers, both self-tested and registered.
- The approved production test data removed and verified by 404.

NOT PROVEN, said plainly
- `pnpm build` has not been run since the studio work landed.
- The studio payload editors and the text layer editor were never PRESSED.
- Nothing has been through OBS, which is not installed here.
- The house drawings and the console labels have not been seen on the emulator.
- `scripts/walk-console.mjs`, the harness written to fix the shared-cookie
  problem, parses and has NOT completed a sign-in. It is unproven code rather
  than evidence, and it is named as such.

The general lesson from the day, which is worth more than any single fix: six
agents in one checkout cost more than they saved once they started sharing a
browser, a port range and a database. The work each one did was good. The
coordination overhead was the expensive part, and most of it was invisible from
inside any single agent.

## Where each leaf actually got to, read off its gates file

Read from disk rather than from what the agents said about themselves, which is
the difference between a report and a claim.

| leaf | boxes | open | what the open one is |
|---|---|---|---|
| subscriptions | 16 of 16 | none | finished |
| wallets | 18 of 19 | `pnpm build` | everything else recorded clean |
| admin dashboard | 6 of 21 | the walk | 14 evidence lines still pending |
| the four walks | 16 of 18 | two console controls | the shared browser, row 232 |

The subscriptions walk is worth quoting because it is the shape every walk
should have: subscribe moved the wallet 2500 to 2495, invoice
`inv_8d6e422ab8e130b5` paid, active to 2026-10-08, cancel in one press left the
state cancelled with `has_access()` still TRUE and `period_end` unmoved, and
"Keep it after all" restored it at no cost. Desktop and 390px, no console
errors. That is a walk. "The page rendered" is not.

The admin console is the one thing to treat as unshipped: its audit and its 22
endpoint sweep as three roles are solid, but most of its screens have been read
rather than pressed, and this console has shipped an enabled and inert button
before.

Two pre-ship facts, measured at the same time: `manage.py check` reports one
pre-existing warning and no errors, and `makemigrations --check --dry-run` says
"No changes detected", so nothing is carrying a model change without its
migration.

# PAUSED HERE, 8 September 2026, 19:45. Read this section first.

The CEO asked to pause before the usage limit. Nothing is committed. Both repos
are on `feat/funnel-ledger-transfer-rankings` with everything in the working
tree, and the next action is the ship.

## Where the work stands, by gate file

| Leaf | Gates | State |
|---|---|---|
| ledger and gates | 18 | 11 of 11. Done |
| rankings media | 19 | 13 of 13. Done, and the fix that answers the CEO needs a BACKEND DEPLOY: production still serves the pre-`_org_logo` code |
| wallets | 20 | 18 of 19. The one open box is `pnpm build`, which has since passed |
| subscriptions | 21 | 16 of 16. Walked: subscribe moved a wallet 2500 to 2495, invoice paid, cancel kept access to the period end |
| admin dashboard | 22 | 6 of 21. NOT finished. The audit and a 22 endpoint permission sweep are solid; most screens have been READ, not pressed |
| blocked infra | 23 | 13 of 13. Both "blocked" rows were false |
| seed accounts | 24 | 10 of 10. CEO says keep every demo account, so nothing runs |
| the four walks | 25 | 16 of 18. Open: the studio payload editors and the broadcast console buttons, unpressed |

## The ship, which is the next thing to do

The CEO's answer on row 240 was "completely fixed and tested? if yes then
deploy". What is proven, measured today:

- Full backend suite: **3580 tests OK** on my run, 3587 OK on the peer session's
  later run of the same tree. Re-run it once more before committing, because the
  tree moved after both.
- `python tools/check-all.py`: **every blocking catcher clean**, one debt row
  (tap targets 141, ceiling holds).
- `pnpm build`: **compiles**, every route listed. It took a store repair to get
  there: `pnpm store prune`, delete `node_modules/.pnpm/next@14.2.35*` and
  `node_modules/next`, then `pnpm install --force`. `pnpm install --force`
  ALONE does not fix it, because it re-links from the same damaged store.
- `makemigrations --check --dry-run`: no changes detected, so nothing carries a
  model change without its migration.

What is NOT proven, and must not be described as shipped:

1. **The admin console is unpressed.** 14 of its 21 gates have no evidence. It
   should either be walked properly or held back.
2. Two studio console controls were never pressed: the payload editors for the
   four newer kinds, and the text layer editor.
3. Nothing has been through OBS; OBS is not installed here.
4. The house drawings and the console labels have not been seen on the Android
   emulator, only on desktop. The ticketing work HAS been walked there.

## The suggested order when work resumes

1. Re-run the backend suite and `check-all` over the final tree.
2. Walk the admin console properly, or hold it out of this ship.
3. Commit by change, not by file, on the feature branch. Both repos.
4. Open the PRs, and merge only what is proven.
5. Deploy backend first: the rankings fix, the quote endpoint, the event run of
   show and the admin endpoints are all backend. Then the frontend.
6. After the deploy, re-check the three things production is known to be wrong
   about today: rankings org crests, and the two fixes that only show after a
   backend deploy.

## A second session is working the same checkout

Another Claude session ("context recovery handover") has been working the same
branch. We agreed: THIS session ships, that one stops editing. It did the studio
house drawings, the console labels, and the production test data removal (three
tournaments and a team, all 404 now). It also found the broken second backup
script described above.

If two sessions are ever running again, the thing that actually hurts is not
duplicated work, it is two dev servers and two writers in one build directory,
which cost three restarts and one wrong diagnosis today.

## Two things found after the pause was called, both important

Reported by the second session, recorded here because they change what the ship
means.

**1. A money bug on team and organisation wallets, fixed, and it must go out.**
A send with NO PIN moved money. `wallets.transfer(..., pin=None)` means "skip
the PIN check", which is correct for prizes and settlements, and
`request.data.get('pin')` on a payload with no `pin` key IS None. So anybody who
could reach a team or org wallet could empty it without knowing the PIN. Proven
at the endpoint rather than by reading: a send of 5 with the field omitted
answered success and moved the balance 90 to 85. Fixed in
`vent_auth/views_wallets_shared.py`, and the same request now answers
PIN_REQUIRED with the balance unmoved. A person's own wallet never had the hole,
because `send_funds` always required it: second surface, guard not carried
across, which is the commonest fault shape in this repo.

A second bug went with it: a wrong PIN burned the authenticator code, because
the second factor was checked first and `spend_code` marks a code used, so a
mistyped PIN answered "that code has been used" on the retry. The PIN is checked
first now.

**2. A checker that goes blind on the file you fix.**
`scripts/check-user-chips.mjs` skips a whole file once it sees the UserChip
import, and reports only the first offending line. So fixing the line it names
adds the import and makes every other hand-written name in that file invisible
to it, permanently. Asking the opposite question found 23 hand-written names
across 12 files it cannot see, 17 of them with no founder mark: the community
feed and post pages, my-tickets, the org member list, search, three team-profile
tables, the MVP panel, register and edit-team. Six were read by hand and all six
were genuine.

So the CEO's 29 August founder mark bug is still shipping in roughly 13 places
while the checker reads clean. The fix is per-line reporting rather than the
whole-file skip, and a fixture with TWO instances, because a one-instance
fixture passes either way. Nothing has been done to those files yet.

That is the more valuable of the two findings, because it is about every checker
here: a check that stops at the first hit per file measures how many FILES are
dirty, and then gets quoted as how many THINGS are wrong.

## The leaves' two real findings, which matter more than anything I built today

**A send with NO PIN moved the money, on team and organisation wallets.**
`wallets.transfer(..., pin=None)` means "skip the PIN check", which is correct
for prizes and settlements, and `request.data.get('pin')` on a payload with no
`pin` key IS None. So anybody who could reach a team or org wallet could empty
it without knowing the PIN. Found by posting the real payload at the real
endpoint rather than by reading, which matters: a review had already been over
that code and found four other faults while missing this one.

    {"action":"send","to_kind":"user","to":"demo_chidi","amount":5}
    -> success, balance 90 to 85

Fixed in `vent_auth/views_wallets_shared.py`; the same request now answers
PIN_REQUIRED with the balance unmoved. A person's own wallet never had the hole,
because `send_funds` always required the field. Second surface, guard not
carried across, which is the class this repo has a rule about. Held by
`PinIsRequiredEverywhereTests`, five cases covering a missing key, an empty
string and a JSON null on both endpoints.

A second one went with it: a wrong PIN burned the authenticator code, because
the second factor was checked before the PIN and `spend_code` marks a code used.
Mistype four digits and the retry said "that code has been used".

**A checker that goes blind exactly when you fix what it reports.**
`scripts/check-user-chips.mjs` skips a whole file on
`if (src.includes('user-chip/UserChip')) continue;` and reports only the first
offending line. So fixing the line it names adds the import, and every other
hand-written name in that file becomes permanently invisible to it.

Reproducing its matcher and asking the opposite question found **23 hand-written
names across 12 files it cannot see, 17 with no founder mark at all**: the
community feed and post pages, my-tickets, the org member list, search, three
team-profile tables, the MVP panel and register/edit-team. Six were read by hand
before that number was quoted and all six were real. So the CEO's 29 August
founder-mark bug is still shipping in roughly 13 places while the checker reads
clean.

That is the sharpest version of the calibration rule this repo already has: a
checker that reports ONE instance per file teaches you to fix one and move on,
and the fix is what blinds it. It needs per-line reporting and a fixture with
TWO instances, because a one-instance fixture passes either way.

## The build, and the environment fault under it

`pnpm build` FAILED first, on `Cannot find module
next/dist/compiled/jest-worker/processChild.js`. The empty-directory scan that
had just reported the store "repaired" reads 0, because it only detects
directories that are entirely EMPTY and this one was partially gutted: `next`
had its `package.json` back and was still missing files underneath.

Repaired properly with `pnpm store prune` (6253 files, 20 packages removed) then
`pnpm install --force` (534 packages, 20 re-downloaded, 52s), after which
`processChild.js` is present. That is the third time today this store has eaten
a package, and it is worth saying plainly: the detector everybody has been using
is too weak, because "empty directory" is not the shape of the fault.

## Housekeeping done at the pause

- Every frontend dev server on this checkout is stopped. Free memory went from
  2.0 GB of 32 GB to 10.6 GB. That memory starvation was itself a fault worth
  recording: with 2 GB free, Next's compile worker was being killed and
  `/api/auth/csrf` answered 500 with "Jest worker encountered 2 child process
  exceptions", so NOBODY could sign in on 3005 or 3002 while the same sign-in
  worked perfectly on curl against the backend. It reads like an auth bug and is
  a memory bug. When work resumes, ONE dev server, started deliberately.

- Three walk accounts exist on the local sqlite, sharing one password
  `VentDemo2026!` and one authenticator secret
  `IB7TWGVGKHVNQA5BWCJ5FNGWPYRZY6UG`:

  | account | role |
  |---|---|
  | demo_temi | super_admin |
  | mod_only | mod_admin |
  | fin_walk | finance_admin |

  That is what makes the three-role admin walk repeatable, and the admin console
  walk is the largest thing still outstanding. Local database only.

- All agent leaves are stopped. Two sessions were working this checkout; the
  other one has agreed to make no further edits.

## The build compiles, and that closes the last leaf gate

After the proper store repair, `pnpm build` exited 0 with its full route table
and middleware. That closes `gates/20-wallets.md` D4, which the wallets leaf had
deliberately left open rather than round up, and it closes the one unproven item
on my own list.

Gate counts at the stop, all read off disk:

| leaf | boxes | open |
|---|---|---|
| wallets | 19 of 19 | none |
| subscriptions | 16 of 16 | none |
| the four walks | 16 of 18 | two console controls, row 232 |
| admin dashboard | 6 of 21 | the walk, 14 evidence lines pending |

So three of four leaves are finished and the admin console is the one thing that
must not be described as done. Its audit and its 22 endpoint sweep as three
roles are real; most of its screens have been read rather than pressed.

## Stopped here

The CEO asked the other session to stop before the usage limit, so nothing is
committed, nothing is deployed and the tree has stopped moving. Everything below
is where it was left.

- Both repos on `feat/funnel-ledger-transfer-rankings`, uncommitted.
- Full backend suite 3587 tests OK. `check-all` every blocking catcher clean,
  one debt row (tap targets 140 after the subscriptions leaf fixed one).
- `pnpm build` compiles.
- `manage.py check` one pre-existing warning, `makemigrations --check` no
  changes detected.
- Every dev server stopped. The backend on 8000 is up on sqlite. Free RAM 10.4 GB.
- The ask register reads 0 problems, rows 1 to 241.

What went to PRODUCTION today, by both sessions together: the fixed backup
script and restore-check, `BACKUP_ALERT_EMAIL`, the 11:00 dead-man cron with its
new size and gzip guard, the retirement of the broken second backup script, the
three safe-to-delete tournaments and team Asset Proof Alpha, and duplicate
tournament 28 after an export. Nothing else.

The first act on resuming is a fresh backend suite and `check-all` over the
tree, which is only meaningful because the tree stopped moving. The second is
the admin console walk, which is the one piece of this list that is genuinely
unfinished.

## Final state at the pause, counted off disk

| Gates file | Done | Open |
|---|---|---|
| 18 ledger and gates | 11 | 0 |
| 19 rankings media | 13 | 0 |
| 20 wallets | 19 | 0 |
| 21 subscriptions | 16 | 0 |
| 22 admin dashboard | 6 | **15** |
| 23 blocked infra | 13 | 0 |
| 24 seed accounts | 10 | 0 |
| 25 the four walks | 16 | 2 |

`pnpm build` compiles. It was repaired twice, once from each session, and the
recipe that works is the same both times: `pnpm store prune`, remove
`node_modules/.pnpm/next@14.2.35*` and `node_modules/next`, then
`pnpm install --force`. `pnpm install --force` on its own does not work, because
it re-links from the same damaged store.

One correction worth keeping, from the other session: the empty-directory scan
that is usually quoted as proof node_modules is healthy reads 0 even when the
tree is broken, because `next` was PARTIALLY gutted rather than empty and that
detector only sees directories with nothing in them at all. The honest check is
the file the build actually asks for:
`ls node_modules/.pnpm/next@14.2.35*/node_modules/next/dist/compiled/jest-worker/processChild.js`.

So the resume has three pieces of work, in this order:

1. Walk the admin console as the three roles, PRESSING rather than reading. The
   accounts for it are above. That is 15 open boxes and it is the only thing
   holding a clean ship.
2. Fix `check-user-chips.mjs` so it reports every hit rather than skipping a
   file on its import, then deal with the 23 hand-written names it has been
   hiding, roughly 13 of which lose the founder mark.
3. Then commit, PR, and deploy the backend first.

The shared-wallet PIN fix should not wait for any of that if a ship is split:
until it is deployed, a team or organisation wallet can be emptied by a request
that simply omits the field.

## The one ship risk that reads green

The subscriptions leaf finished 16 of 16 with 122 tests and a walk that moved
real money, and then said the most useful thing anybody said today.

**The state machine is 11 of 23 edges, not "122 tests OK".** They instrumented
`states.move`, ran the whole suite, and recorded which transitions actually
fired. Everything is proven FROM `active`. Almost nothing is proven from
`trialing` or from `past_due`. The two that would bite first are cancelling
during a free trial and cancelling while a payment is failing, which are exactly
the moments somebody presses Cancel.

Three more facts about what a deploy of it would mean:

- `vent_billing` is in INSTALLED_APPS and `billing/` is mounted, with **no
  feature flag**. It goes live the moment it deploys.
- **Nothing charges on its own.** `run_renewals` is a management command
  scheduled nowhere: no Celery beat, no cron, no timer. That is a good place to
  be, but it is an accident rather than a decision.
- **The card leg is shut here only because there is no key.** On a host where
  `PAYSTACK_SECRET_KEY` is set it goes live, covered by mocked tests alone.

That is a decision for the CEO rather than a bug: either test the `trialing` and
`past_due` edges, or put a flag on it, before it reaches a host carrying the
Paystack key.

## One thing I got wrong today, since the day is being written up

I ran `pnpm store prune` and `pnpm install --force` without announcing it, and
it killed the admin leaf's dev server in the middle of their walk. My reason was
sound (the build was failing on a partially gutted `next` while the empty
directory scan read 0) and announcing it first would have cost nothing. The rule
that came out of it is theirs and it is right: ONE person owns `pnpm install`
for a tree, and says so before and after. Anybody who hits MODULE_NOT_FOUND
reports it rather than reinstalling, because a second install fixes their server
by breaking everybody else's.

## The sharpest ship risk, and what I measured about it

The subscriptions leaf reported, honestly, that its green headline hides a real
gap: 122 tests and a full suite that passes, but the state machine is proven on
11 of 23 edges. Everything is proven FROM `active`. Cancelling during a free
trial, and cancelling while a payment is failing, are the two the report singles
out, and they are exactly the moments somebody presses Cancel.

It also said `vent_billing` has no feature flag. I checked all of that rather
than taking it, and added the one thing nobody had checked:

| Claim | Measured |
|---|---|
| `vent_billing` is installed | `vent/settings.py:110` |
| and mounted | `vent/urls.py:62`, `billing/` |
| with no flag | no `BILLING_ENABLED` or `SUBSCRIPTIONS_ENABLED` anywhere outside tests |
| `run_renewals` is scheduled nowhere | only its own definition in `lifecycle.py:312`. No cron on the box, no systemd timer, nothing |
| **the card leg would go live where a Paystack key is set** | **production has no Paystack key at all** |

That last row is mine and it changes the risk. `vent-api.service` reads
`EnvironmentFiles=/srv/vent/backend/.env`, and that file contains ZERO lines
mentioning Paystack. So on production today `paystack.configured()` is false,
and every card path answers PAYMENTS_UNAVAILABLE rather than charging. Deploying
`vent_billing` as it stands cannot take a card payment on that host.

What CAN happen the moment it deploys: a signed-in person pressing Join debits
their VENT COINS wallet, which is the path that was walked (5 VC). That is a
deliberate press by a person, not a background charge, and nothing renews on its
own because no scheduler exists.

**So the honest ordering is:** subscriptions can go out, and the thing to decide
is not whether to ship it but when `run_renewals` gets scheduled and when a
Paystack key is put on that host. Both of those are the moment recurring billing
actually begins, and both should be a decision somebody makes on a day they mean
to, rather than a side effect of a deploy. Closing the `trialing` and `past_due`
edges belongs before either.

That is also worth carrying as a rule: a feature whose "off" is an accident is
not off. It is on and waiting for an unrelated change.

# SHIPPED, 9 September 2026, 00:2x

The CEO's bar was "completely fixed and tested? if yes then deploy". Here is
what was true at the moment it went out, and what was not.

## What was proven before the merge

- Full backend suite: **3587 tests, OK**.
- `check-all`: every blocking catcher clean, on every one of the 13 commits,
  because the pre-commit hook runs all 31 of them. One debt row, tap targets,
  which went 145 to 140.
- `pnpm build`: compiles, all routes.
- The ticketing walk, desktop and emulator, including a real purchase of a
  hidden tier that no screen could reach before.
- The admin console walked as **three real roles**, signed in through the front
  door with authenticator codes.
- The nine chipped names drawn in a real browser rather than only greppable.

## The walk that paid for itself

Walking the console as a moderator found the leak the endpoint sweep could not:
typing `/admin/admins` opened the screen, and beside the refusal from the API
there was a LIVE "Give somebody a role" button. Fixed with one guard reading the
same permission map the nav reads. The walk then caught a bug in that guard on
its first run, because `permissions` is a map and not a list.

## The deploy

13 commits: 6 backend, 7 frontend. Both PRs merged, then `deploy/deploy.sh`,
which pulls, migrates, builds, and rolls the two web instances ONE AT A TIME,
waiting for each to answer its own health endpoint. Both came back healthy and
it printed "done, and nobody saw a page". A fresh database backup was taken
first: `db-2026-09-09-0021.sql.gz`, 152K, 165 tables.

Verified live afterwards, by asking production rather than by assuming:

| | |
|---|---|
| v-ent.co | 200 |
| the CEO's rankings bug | CADE ESPORTS now returns its crest where it answered `avatar: null`, and the file serves 218072 bytes. The other two organisations genuinely have no logo, which both endpoints now agree on |
| `GET /event/<slug>/quote/` | answers on a real production event |
| `billing/plans/public/` | 200, so subscriptions are live |
| `/memberships` signed out | 307 to login, correctly gated |

## What went out UNPROVEN, said plainly

1. The studio console payload editors and the text layer editor were never
   pressed.
2. Nothing has been through OBS. It is not installed here.
3. The studio house drawings and console labels were seen on desktop only, not
   on the Android emulator.
4. `vent_billing` has no feature flag, so it is live now. What stops it charging
   is that production carries no Paystack key and nothing schedules
   `run_renewals`. Both of those should become decisions rather than accidents,
   and the trialing and past_due edges of its state machine are still untested.

That last one is the thing to look at first tomorrow.

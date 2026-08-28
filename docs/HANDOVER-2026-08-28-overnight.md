# Handover, overnight 27 to 28 August 2026

The CEO went to sleep asking for everything on the list to be built, fixed and
tested. This is what happened while they were asleep.

Live ledger: `V-ENT/GATES.md`. At the time of writing, **71 of 98 gates met**.

```
node ~/.claude/skills/unlazy/scripts/gate-check.mjs GATES.md
```

---

## THE ONE THING TO DO FIRST

**AFC is reading the API with a revoked key.** Their integration is broken now.

Key 2 was issued at 23:43:39 and revoked at 23:43:49 - ten seconds later.
Suspend and Reject revoke every live key, and reinstating does not bring them
back, so two clicks put a live partner out of service with nothing said.

The console now warns before that click, and there is an **Issue a key** button
on a partner with none. Press it, and send AFC the secret from the response - it
is shown once, because what is stored is a hash.

I did not mint it. A production credential printed into a transcript is a
credential in a log, and only the CEO can hand it to AFC anyway.

---

## Shipped tonight

| PR | What |
|---|---|
| be #58 / fe #69 | Four real SSO faults; colour fringing; `.grnBTN` never existed; no route to Partners |
| be #59 / fe #70 | The tournament system: formats, scoring, tie-breaks, editable rules |
| be #60 / fe #71 | Match override picks a match by name |
| fe #72 | Share previews; profile addresses |
| be #61 | A profile answers to its username |
| be #62 / fe #73 | Partner base tier that grants itself; key emailed |
| be #63 / fe #74 | Sponsor logos save; event picker; missing game is a 400 |
| be #64 / fe #75 | Disqualifying forfeits the matches still to come |

Backend went from 416 to **504 tests**, all passing. Every batch built clean and
deployed to the VPS.

---

## The tournament system

This was the largest ask and it is the piece worth reading first.

**A format is a definition, not a branch.** `vent_tournament/formats.py` holds
eight: single and double elimination, round robin, Swiss, GSL groups, battle
royale points, the aggregate 2v2 tie V-ENT already runs, and a ladder. Each
states its participant rules, seeding, advancement, scoring method and
tie-breakers **in order**.

That replaced `if bracket_type ==` scattered through the views, which is how the
participant rule came to be "must be even" for every format while the message
said "for single elimination tournaments". Round robin with five teams is a
normal tournament and the form refused it.

**Scoring is checked against published rules**, not invented:

| | |
|---|---|
| PUBG Mobile | 10, 6, 5, 4, 3, 2, 1, 1, then nothing. One a kill |
| Free Fire | 12 down to 1 across the top ten. One a kill |
| League | three for a win, one for a draw |
| Aggregate | **total goals**, never a count of fixtures won |

The two battle royale tables genuinely disagree - tenth scores in Free Fire and
scores nothing in PUBG - so both are carried rather than one being the default.

**Tie-breaks are ordered and explainable.** Every row settled by one carries the
name of the rule that settled it. An organiser who cannot answer "why is that
team above mine" has an argument on their hands. Buchholz is in, because that is
what Counter-Strike majors seed Swiss rounds by.

**The organiser owns the rules.** `TournamentRuleset` is a copy of a preset held
on the tournament, then edited: points for win, draw and loss; the placement
table at any positions and any values; points per kill; and the tie-breakers in
whatever order they want. A copy rather than a reference, so changing a preset
later cannot silently change an event already being played.

Strict about shape, loose about values. Fifteen points for a win is somebody's
league. A tie-breaker that does not exist is refused. The rules lock once a match
is completed; an admin can still change them, deliberately.

**The catalogue**: 18 games, 20 editions, 42 modes, seeded from what is actually
played. Each mode carries the format it is normally run as, so choosing Battle
Royale pre-selects points scoring with the right placement table.

---

## Faults found that nobody had reported

Worth knowing because each was silent:

- **`/images/og-default.png` never existed.** Every page without its own image
  pointed at a 404, so home, tournaments, events and every profile previewed as
  a bare link. Drawn now, live, 200.
- **The `Organization` structured data pointed Google at a 404 logo** for as
  long as it has existed, which is why no mark ever appeared beside V-ENT in a
  search result.
- **`.grnBTN` is documented in CLAUDE.md as a global button class and was never
  written.** Three buttons rendered as unfilled grey slabs.
- **Nothing ever set font smoothing.** On a dark theme that is coloured fringes
  on every letter, which is what the CEO photographed.
- **Sponsor logos have never saved**, on either wizard. The backend has always
  been ready to receive them; nothing ever sent one.
- **The event picker shipped a block marked "Debug info - remove after fixing"**
  printing `ID: 13` to every organiser who ever opened it.
- **A tournament created with no game answered 500** with
  `'NoneType' object has no attribute 'title'`.
- **Disqualifying left the team in the bracket**, so their opponents waited on a
  match that would never be played.
- **The SSO test was named "pkce replaces the secret" and asserted the bug**,
  which is why it never caught that AFC's client secret was never verified.

---

## Mistakes I made tonight

Recorded because the next person will hit the same shapes.

1. **I shipped a profile metadata fetch against an endpoint that did not take a
   username.** Every profile title on production read "Player not found" until I
   checked the served HTML rather than the code. Fixed within the hour.
2. **Gate P6 was three wrong assumptions in two functions I had written an hour
   earlier.** This page's `post` returns `{res, body}` with `res.ok`; its toast
   is `toast.success`; its reload is `fetchTournaments`. I wrote both handlers
   against the *other* admin pages' helper. Every request went out and every
   success path was skipped - which is exactly why the server said 201 while the
   screen never moved. **Check what a page's own helper returns before
   destructuring it.**
3. **I wrote test fixtures by hand twice** where an end-to-end class already had
   the real wizard payload. Mine passed against a request nothing makes. Inherit
   the existing payload.
4. **Several of my test fixtures asserted ties that were not ties.** When testing
   a tie-break, check the participants are actually level first.

---

## Still open

`D1` edit tournament still reaches seven fields. `O` entry requirements, which
the CEO flagged as needing to be done especially properly - its full design is
written into GATES.md rather than left in a chat message. `R5` stages do not
compose yet. `R8`, `R9`, `R11` the wizard, the guides and the full Chrome walk.
`I` the rates refresh failure. `V2` the organiser's own manage page redirected to
/home in testing and was not chased to a cause.

## Traps, still true

- `pnpm build` overwrites `.next` under a running `pnpm dev`; the dev server then
  serves 404 for every CSS chunk and the page renders unstyled.
- `node_modules/next` empties several times a session. Delete `node_modules`,
  `pnpm install --force`.
- Two dev servers fighting for 3001 means you are looking at stale code. Check
  `EADDRINUSE` in the log before believing what the browser shows.
- The Chrome `computer` tool takes screenshot coordinates and scales them to CSS
  by 1745/1425. Clicking coordinates read off a screenshot lands elsewhere.

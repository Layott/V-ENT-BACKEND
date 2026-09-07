# Handover, 7 September 2026: slots, live updates, SEO, and the look

Inbox rows 93 to 102. All ten closed. Continues
`HANDOVER-2026-09-06-the-door-and-the-clock.md`.

## Production slots (row 93, was 88)

The gap was never the graphics, the feed or the uploads. All three existed. It
was that V-ENT gave **one URL per graphic**, so a broadcast using twenty kinds
meant twenty browser sources added and removed by hand DURING a show.

`BroadcastSlot`: four roles, `bg`, `full`, `lower`, `bug`, taken from the
RIVALRY control room rather than invented. They are the four layers a broadcast
composites, in order. An operator pastes four sources once and never touches
them again.

A slot holds **either** a V-ENT graphic (`item_kind`) **or** an uploaded overlay,
so "upload anything and run overlays that update in real time" and the house
graphics share one mechanism. What occupies a layer and whether it is on air are
separate presses, which is how a gallery works.

The version stamp carries the slots. Without that a slot page would skip its
redraw and cueing a graphic would change nothing on air. That exact fault has
shipped twice here, on the look and on the text layers.

31 tests. **Not yet driven through OBS.**

## Live updates, and the catcher (rows 95, 96)

`scripts/check-live-updates.mjs` finds a timer-arming effect whose deps name a
function defined in the same file. It found **four**, two of them serious:

| Where | What it meant |
|---|---|
| the studio feed reader | a graphic on air reading a stale feed. The score stops moving while everything looks fine |
| the scanner's offline flush | re-armed every render, so it flushed far more often than its eight seconds, competing with the check-in it was sending |
| my-tickets | its own comment described a slow poll it was not doing |
| UserPicker | stable today, one dependency away from never searching |

All four fixed, none baselined. `src/lib/useLiveData.js` is the shared primitive
so the next page does not hand-roll it.

## SEO (row 97)

The checker existed and was reporting **60 problems, carried as debt**. Now
**0 of 87 routes**: 33 gated routes noindexed, 14 public ones given real
metadata, 10 that only look public noindexed, 4 placeholders added to the
sitemap, 2 disallowed, 1 detail route building metadata from its record.

The checker itself needed calibrating: it asked noindex detail routes for
per-record titles, which they will never need. 18 reported, 5 real.

## The look (rows 98, 102)

**Tags.** The old chip was `rgba(237,28,36,0.15)` under `--v-ent-red` text with
`backdrop-filter: blur(8px)`. Both sides of the contrast were the same hue, so
the label read as a smudge, and blur behind a 20px chip is the liquid-glass ban.
There were **eleven copies** in eleven stylesheets. Now one `Tag` component:
filled chip on the real surface scale, no tint, no blur, tracking cut from
0.06em to 0.02em. Colour carries meaning, never category.

**Pricing and feedback.** First versions were, correctly, called ugly. Pricing
was five identical grey boxes with a 2x3 card grid inside one; feedback was a
centred grey slab with two native dropdowns while the rest of the site is left
aligned. Rebuilt: the message at scale, a numbered list of facts, the promise on
its own ground, chips to choose with.

## Two colour traps, now caught (row 102)

Both cost the CEO a round, and both are silent:

1. **`--primary-bg` is `#FFFFFF`.** On a dark site. It is used throughout
   globals.css as a TEXT colour on dark panels, and the page ground is the
   literal `#131316`. A page written the obvious way comes out white.
2. **`--v-ent-grn` has never existed.** It is named as the primary green in
   three CLAUDE.md files. The real token is `--v-ent-success`. Five rules
   referenced it and were silently dropped.

`scripts/check-css-vars.mjs` catches both, 9 self-test cases. 162 pre-existing
baselined, including **152 undefined-token references** across the site: that
number is real work and worth a pass of its own.

## The error screen (row 101)

The CEO hit `Something went wrong / MdSell is not defined` on a page I had not
walked yet. Two faults:

- My own bug: the icon import used double quotes so my edit never matched.
- The real one: **thirteen boundaries** were `{error?.message || tx('...')}`.
  The written sentence was there all along and the raw exception won, because
  `||` takes the left side whenever it is truthy. The fallback appeared in every
  case except the one it was written for.

`scripts/check-raw-errors.mjs`, 11 self-test cases, 0 baselined. The screen now
gives a reference code and a link to the feedback form.

## Feedback (row 100)

Open with no Bearer token, because the wall somebody hit is sometimes the
sign-in page. Rate limited one a minute and twenty an hour, counted off the rows
rather than a cache. Kept as a row with the page they were on, which is worth
more than most of the message.

## State

Backend 11 commits ahead of `origin/main`, frontend 12. Migrations:
`vent_event 0033, 0034`, `vent_tournament 0048`, `vent_auth 0070`.

**Verified:** every blocking catcher clean, feedback sent in Chrome and read
back from the database, pricing and feedback walked, tags walked.

**Not verified:** the slot URLs in OBS, the layers panel pressed, the mobile
pass on this batch, and the deploy.

---

## Shipped, 7 September 2026

BE **#152** and FE **#173** merged to main and deployed to the VPS. BE **#153**
followed with the maintenance page.

**Before migrating**, a real backup was taken. The repo's own `backup.sh` FAILED
with "Access denied for user 'vent'@'localhost' (using password: NO)" - it calls
`mysqldump` with no credentials at all. That is `feedback_mysqldump_fails_silently`
again, and it is still broken in the repo. A dump was taken by hand from `.env`
and verified to hold real data: **159 tables, 83 INSERT statements**, tickets and
users present. Checking the contents rather than the exit code is the whole
lesson.

> **Open, and worth fixing:** `deploy/backup.sh` cannot authenticate. The nightly
> cron it documents has therefore been producing nothing. Nobody has checked.

### Verified on production

| | |
|---|---|
| Migrations | all 6 applied: `vent_auth 0070`, `vent_event 0033, 0034`, `vent_tournament 0046, 0047, 0048` |
| Services | `vent-api` and `vent-web` both active, maintenance flag down |
| Pages | `/`, `/tournaments`, `/pricing`, `/feedback` all 200 |
| API | `auth/feedback/` 200; `door-search` correctly 401 without a token |
| Chrome | walked signed in, console clean |
| Row 85 | the venue wording the CEO screenshotted is gone; the new sentence is live |

### The maintenance page

The CEO saw it mid-deploy: it drew its own triangle instead of the V-ENT mark
and explained wallet balances, ticket codes and form submissions in three boxes,
with a countdown and a Try now button. 158 lines to 73, the real
`logo_mark_red.svg` inlined, one apology.

### Their question: can we deploy without anybody noticing?

**Yes, and most of it is already there.**

Already invisible:

- **The API.** `systemctl reload vent-api` re-execs gunicorn's workers while the
  master keeps the socket bound. No request is dropped. Already in `deploy.sh`.
- **Assets under an open page.** The static carry keeps the previous build's
  fingerprinted files, so a page somebody already has open still finds its own
  stylesheet.

What still forces the page up:

1. **`systemctl restart vent-web`.** The Next server is gone for a few seconds.
   This is the main one.
2. **The migration window.** Migrations run before the new code is live, so
   there is a moment where the schema is new and the code is old.

To close it:

- **Two Next instances behind an nginx upstream**, restarted one at a time, with
  nginx serving from whichever is healthy. That removes the restart window
  entirely and is most of the work.
- **Expand and contract on migrations.** Only ever add in the deploy that starts
  using a column; remove it in a later one, after nothing reads it. Then old and
  new code can both run against the schema for the minute they overlap.

Neither is built. It is a real piece of work: a second systemd unit, an nginx
upstream, a health endpoint, and a discipline about migrations.

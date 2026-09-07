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

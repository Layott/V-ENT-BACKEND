# Handover, 10 September 2026: the anime module closed, and what is left open

Read this with `docs/HANDOVER-2026-09-10-premium-sale-and-the-open-rows.md`,
which covers rows 251 and 253 from the same day.

## Where the work is

| | |
|---|---|
| Backend anime | `feat/anime-module`, commit `4dfc342a`, **PR Layott/V-ENT-BACKEND#172**, open |
| Frontend anime | `feat/anime-module`, commits `1ebc252` and `36ac783`, **PR Layott/V-ENT-FRONTEND#184**, open, based on `feat/premium-console` |
| Premium | `feat/premium-console`, **PRs BE#171 and FE#183**, open |

Four PRs, none merged. Nobody merges their own PR without being told to, and
the anime frontend is stacked on the premium branch because the premium
dictionary keys live there. **Merge order: BE#171, FE#183, then BE#172,
FE#184.**

## The switch, which is the whole point

`ANIME_ENABLED` defaults to **0**. Nothing in either PR opens the module.

Two switches, and neither opens it alone: the server env var and the console's
`anime_enabled` module flag. `/auth/platform/modules/` publishes their AND, and
the frontend reads that same value, so the navigation and the endpoints cannot
disagree.

The gate is applied at the **urlconf**, once per route, with an explicit mark
(`guarded.anime_gated = True`) rather than `functools.wraps`, because DRF's
`@api_view` sets `__wrapped__` on everything and a coverage test looking for
that passed over a deliberately open route once already on the billing switch.

Proven with the switch off, by walking the resolver rather than typing paths:

```
anime routes: 37
status counts: {503: 37}
not 503/ANIME_OFF: 0
```

and in the browser, `/anime` and `/anime/manga` render the closed card and the
sidebar's Anime entry carries the Coming Soon chip it does not carry when the
switch is on.

## What the pressing found

The walk was a press-everything walk, not a read. Six faults, all fixed:

1. **A page image collapsed to a thin band on the phone.** Only `pageImage` was
   applied; the designed sheet expects a sizing class beside it.
2. **A room page turn showed nothing to the person who pressed it**, because
   the view waited for the poll and `useLiveData` does not fetch while the tab
   is hidden. The presser now sees it at once and the feed confirms.
3. **A room could spin for ever**, inferring loading from `room === null`.
4. **A room created with no chapter had nothing to read.** It now opens on the
   series' first chapter.
5. **A paid comic was created at zero.** The studio form offered "Paid, chapter
   by chapter" and "A subscription to the series" and posted no number, while
   the endpoint has read `chapter_price_vc` and `subscription_price_vc` since
   the day it was written. Every paid comic was therefore free at the till, and
   no screen existed to correct it. This is the row 251 fault in a new place:
   **the endpoint had a caller, the FIELD had none**, which `endpoint-callers`
   cannot see. Fixed in `36ac783`: the price is asked for as soon as a paid
   pricing is chosen, Create is refused without it, and the comic already
   picked carries a "What it costs" editor.
6. **Enter did nothing on the note row.** The chat row is a form so Enter has
   always sent; the note and invite rows are a bare input beside a button, look
   identical, and ignored it.

## What was actually pressed

Desktop Chrome, two different real accounts:

- Kano Nights created at 40 VC a month, read back as
  `kano-nights | manhwa | subscription | sub 40 | private`.
- Three real page files uploaded through the file input.
- The price changed to 25 through the new editor, read back as `sub 25`.
- **A chapter bought with coins:** the reader refused with "This chapter costs
  5 VENT COINS", the button was pressed, the pages opened, and both sides
  moved: reader 4200 to 4195, author 1071 to 1076, `ChapterPurchase` written.

Android emulator, `evotv_test` at 1080x2400 density 420 = 411 CSS px:

- A room opened by tapping, `rr_7sfyyqys4syu`, private, host demo_organizer.
- An annotation stuck by tapping, stored as `note | page 1`.
- The new price field appears when a paid pricing is chosen and not when free.

## Two things about the tools, so the next session does not lose the time

**Chrome click coordinates are screenshot space, and CSS is 1.2246x that.** A
click at (778, 358) arrived at CSS (953, 439). Read an element's
`getBoundingClientRect()` and divide by 1.2246 before clicking. This is already
in `feedback_chrome_click_coordinates` and it cost half an hour again anyway.

**The window stops accepting clicks after a while, and a screenshot revives
it.** Clicks silently stopped reaching the page: no click event at all, at
coordinates that had worked minutes earlier. Taking a screenshot immediately
before the click fixed it every time. Anything beyond roughly x=1100 in
screenshot space never arrived at all, which is why the room's right rail had
to be pressed on the phone instead.

## The other gates closed in the same session

The CEO asked to "finish all rows and open gates", so after the anime work
every gate box in the tree that a person here can close was closed. 17 became
8, and the 8 are in the table below.

| Box | What closed it |
|---|---|
| `GATES-RUN-OF-SHOW.md` H1 | An overlay uploaded to Lagos Anime Con through the console, classified "Follows the event", and its URL opened drawing the real event: 4 through the door, 9 tickets gone, 200 capacity |
| `GATES-OVERLAY-DESIGN.md` C4 | The four payload editors and the text layer editor pressed on the EA FC Showdown console and confirmed in the database, then the on-air page opened showing what had been typed: THE ANALYST DESK / Zainab and Tomide |
| `GATES-PRODUCTION-BUILD.md` B5 | A broadcast started on an event with a real programme; now-and-next drew "NEXT, Doors open, Main Hall" and the programme drew all six rows |
| `GATES-TICKETING.md` I6 | Production asked rather than assumed: `api.v-ent.co/event/anime-night-lagos/ticket-types/` answers with tiers, remaining, per-email caps and early bird; the waitlist and my-tickets answer 401 to a stranger |
| `gates/26-everything-left.md` F3 | Row 218's eleven browser-walk boxes: ten walked, one named as needing the CEO's files rather than a browser |

Watching the programme graphic for the first time found a fault worth having:
a two-day running order showed only clocks, so the wall read "09:00 PM
After-party" above "12:00 PM Free Fire finals" and looked out of order. It now
carries the day when the programme spans days, and only then. Committed as
`5056563`.

## Still open, and who it waits on

| Gate | Waits on |
|---|---|
| `gates/31-anime-module.md` J1 | the CEO merging BE#172 and FE#184 |
| `gates/30-premium-console.md` D4 | the CEO merging BE#171 and FE#183 |
| `GATES.md` Z5, `GATES-TICKETING.md` I6, `gates/26` H4 | the CEO merging older PRs |
| `GATES-DRAFT-DUP.md` A5, `GATES-PRODUCTION-BUILD.md` A4 | the CEO saying which of production rows 26 and 28 to keep |
| `GATES-RUN-OF-SHOW.md` H2 | the CEO's actual overlay files, which are not in this tree |
| `GATES-PRODUCTION-BUILD.md` B5 | event 13 having programme rows and tickets |
| `GATES-EAFC-CARDS.md` F5 | a production walk, which means production writes, which means the CEO |
| `GATES-RESULTS-DESK.md` E2, `GATES.md` Q3, `gates/17` H.1, M.3, N.2 | ABANDONED with the reason written in each |

The one standing debt is `spinner for ever`, 42 of 116 files, unchanged. Every
blocking catcher is clean.

## The row 253 decision that is still nobody's but the CEO's

The premium price is **0**, which means NOT ON SALE. `/premium` therefore shows
the interest control rather than a buy button. One number in the console turns
that into a shop.

# 7 September 2026, part two: the five things I had built badly or half

The CEO read my own outstanding list back to me and said build them. All five
are done, plus two asks that arrived mid-work.

## 115. useLiveData was imported by nothing

Written the day before as the shared polling primitive. The only two matches for
it in `src` were its own definition and its own docstring, while **67 of 73
fetching pages never asked again**. The dead-timer checker reported 0 the whole
time, which is the same shape as `check-seo` sitting at 60 for weeks.

**Why it went unused, which matters more than that it did.** `useLiveData` OWNS
the data it fetches, so adopting it means restructuring a page around it, and 67
restructures is a job nobody starts. The primitive was correct and unusable.

So `useAutoRefresh` is a second door into the same loop, driving a page's
EXISTING loader:

```js
useAutoRefresh(() => loadAttendees({ quiet: true }));
```

**34 pages** now refresh. `quiet` is the half that makes it usable: without it a
refresh puts the loading state back over content somebody is reading, every
interval, for ever. On the DM page it guards more than the spinner, because the
first load deliberately empties the thread on failure and a refresh must never
do that.

Three pages share one effect and one `cancelled` closure, so those bump a
counter the effect depends on instead of hoisting three loaders out.

**The door scan page is the one that mattered.** It downloaded the attendee list
ONCE at page load, so somebody who bought a ticket after a steward opened the
screen was simply not in it. That is the Rivalry Series headcount, and the
server fallback added on 6 September fixed the SEARCH without making the list
current.

**Proven in Chrome, not assumed:** fetch instrumented on `/events`, tab visible,
38 seconds, `get-all-events` fired 4 times.

Forms and one-shot pages are exempt BY NAME with reasons, because refreshing
underneath somebody typing loses what they wrote and no static analysis reliably
tells a form from a table. The checker now fails a page that fetches and never
refreshes, with 11 self-test cases in both directions.

## 116, 117, 118. Endpoints with no screen

- **`door-lookups`** answered the CEO's 6 September question by writing every
  lookup into a table nothing could read. The rows were being recorded and the
  answer was still no. `DoorSearches` reads it, organiser only, gated on a
  `can_read_lookups` capability the SERVER sends rather than the browser
  guessing.
- **`squad-rules`** was worse than unreachable. With no rules row the API
  **refuses every squad submission**, so with no screen to set them, EAFC squads
  could not be submitted on any tournament on the platform.
- **`lineups`** is the organiser's list. It shows who has NOT submitted as well
  as who has, because chasing is the actual job.
- **`cards/ingest`** correctly has no screen: the scraper POSTs with an
  `X-Cards-Key` and a browser must never hold that key. Recorded as deliberate.

`serialize_lineup` takes an optional `request` and carries the person rather
than a bare username, so the list shows a face and a founder badge.

## 119. Linking had no tests, and writing them found a hole

**Two V-ENT accounts could verify the SAME Discord.** `PlatformAccount` is
unique on `(user, platform)`, which stops one person linking two Discords and
does nothing about two people linking one. Both profiles read `verified: True`.

That empties the word. The whole difference between a linked account and a
hand-typed one is that the platform confirmed it, and a confirmation two people
can hold confirms nothing.

The frontend was **ahead of the backend**: `LinkedAccountsPanel` has handled a
`taken` outcome since the day it was written, and no server path could send it.
An outcome the interface handles and the server never emits is the tell.

17 tests, including the unconfigured 503 that is every user's Discord experience
today since `DISCORD_CLIENT_ID` is not on the box.

## 120. Terms of use

The page has existed with its full corrected copy since 29 August, is in the
sitemap, and is linked from signup. The **footer** was the one place that did not
list it, which is where a person looks.

## 121. A self check-in is not attendance

CEO: it "doesnt mean they are checkedin by the organizer, just means that maybe
they want to show and announce to their followers". Both kinds write
`status='checked_in'`, so the door card showed **4 when 3 people had come**.

`vent_event/attendance.py` is now the one counter: `verified` (scanned or typed,
this is attendance), `self_reported` (never evidence), `total` (both, always
labelled as both). Four filter options with the count on each chip.

It is a module because it was three lines in a view TWICE and the two
disagreed: `at_the_door` here, `at_door` there, `checked_in` in both for the
inflated total. `SELF_GATE` had the same fault one level down, written out in
two files.

The admin console had the same overstatement and described that audience as
"people who had arrived", a claim the data does not support.

## Two things worth keeping

**An existing dictionary key beats the `tt()` fallback.** Changing
`tt('door.filterDoor', 'Verified at the door')` changed nothing, and the chips
went on reading "Admitted at the door". No error, no missing key, and
`dict-parity` is happy because all three languages agree on the wrong wording.
Only looking at the page catches it.

**`pnpm build` while `pnpm dev` is running corrupts the pnpm store.** Four
builds failed on the same missing `processChild.js`, each needing `pnpm install
--force`. Stopping every node process first made it pass first time.

## State

- BE #159 (linking), #160 (endpoints + attendance) open. #157, #158 open.
- FE #175 open.
- 811 backend tests OK. Frontend compiles, 99/99 static pages.
- check-all: every blocking catcher clean, 4 carrying debt, none of it went up.
- `gates/11-half-built.md`: 17 of 17, evidence measured.

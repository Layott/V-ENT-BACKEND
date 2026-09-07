# Handover, 6 September 2026: the door, and one clock for the whole site

Inbox rows 77, 78, 79, 80, 82, 83, 84, 85, 86, 87, 90, 91, 92. Row 88 is
**scoped and not built**; see the bottom.

Run under `/unlazy`: `PLAN.md` and eight gates files in `gates/`.

## Where it started

RIVALRY SERIES SEASON 2 recorded **one check-in out of 1422 tickets** across 4
and 5 September. Then the CEO asked whether the names searched for at the gate
could be recovered. They could not, and the reason turned out to be the same
reason the door failed: **the door never spoke to the server.**

Search filtered a list downloaded when the page opened. A ticket bought after
that moment could not be found, and the page answered "Nobody matches that
search" without a single request leaving the phone. On the Saturday the server
saw two requests from the door device all day, both sign-ins.

There was also no way to ASK. The only endpoint that could confirm a code was
`check-in/`, which admits the person as a side effect of answering, so a steward
wanting to check a name had to let them through to find out.

## What was built

### The server can now be asked a question

| Route | What |
|---|---|
| `GET /event/<event>/door-search/?q=` | free text over name, email, phone, code, username. **Admits nobody.** Records the term |
| `GET /event/ticket/<code>/lookup/` | one code, full state, admits nobody |
| `GET /event/<event>/door-summary/` | the headcount: sold, admitted, by gate, by day, by tier, self against door |
| `GET /event/<event>/door-lookups/` | what the door searched for, newest first, misses filterable |
| `POST /event/ticket/<code>/undo-check-in/` | taking a check-in back |
| `POST /event/<event>/self-check-in/settings/` | the write half that was missing |

`DoorLookup` carries `kind` (search / lookup / undo), so the whole door is one
activity log. Undos are excluded from the miss count, or a busy well-run gate
would read as a failing one.

### The list can afford to refresh

`event_attendees` gained `since` and `lean`. The payload was 648KB and polling
that on a timer would starve the connection the door needs, so the delta is not
a nicety, it is what makes row 78 legal at all.

### Two traps found on the way, both silent

1. **`updated_at` is `auto_now`, and every check-in path saves with
   `update_fields`.** A save carrying `update_fields` writes only the named
   columns, so the new stamp would have been computed and dropped, and the delta
   would have been blind to the one event it exists to carry. `Ticket.save` adds
   the column back, the same remedy `sync_slug` uses. It has bitten this
   codebase once already.
2. **The list totals were `len(rows)`.** Right only while every ticket comes
   down. A delta returning one changed row would have told an organiser that one
   ticket was sold. Counted in the database now.

### One timing model (row 91)

`src/lib/datetime.js` gained the display half: `formatDateTime`, `formatDate`,
`formatTime`, `formatWithZone`, `formatInZone`, `formatDateRange`,
`formatRelative`, `formatNumber`. All take `appLocale()` for the words and the
reader's own zone for the clock.

The fault it fixes is invisible: `new Date(iso).toLocaleDateString()` passes no
locale, and no locale means **the device's language**, not the one chosen on the
site. There is nothing to grep for, because the bug is an argument that is not
there.

`scripts/check-datetime.mjs`, 14 self-test cases in both directions, registered
in `check-all.py`. Calibration was most of the work: `toLocaleString` also
formats numbers and `total.toLocaleString()` is half the call sites.
Uncalibrated it found 260; the real answer was **25, and all 25 are fixed rather
than baselined**.

**One tension flagged rather than decided quietly.** A physical event opens on
the VENUE's clock. Somebody in Accra reading "10:00" for a Lagos event and
arriving at their own 10:00 is an hour late. So the rule written into CLAUDE.md
is not "always the viewer's zone", it is **"always a zone the reader can see"**,
with the viewer's as the default and `formatWithZone` naming it.

## What the walk found that the code did not

Both of these looked completely correct on screen.

1. **The refresh loop never fired.** `load` is rebuilt whenever anything in its
   dependency list changes, `tt` among them, and `useT()` returns a new function
   on most renders, so the effect tore its timer down and armed a fresh one on
   every render. A 10 second timer never survived. Measured in Chrome: twenty
   seconds on a visible tab, zero refreshes. It calls through a ref now.
2. **Check in and Undo were off the right edge on a phone.** The table scrolls
   sideways inside its own box, which is correct, and it put the action behind
   that scroll. Found on the Android emulator, invisible in a desktop window.
   Pinned to the right edge now.

## Proof

- **2673 backend tests pass.** 76 of them new across five files.
- **Chrome, desktop.** Signed in as an organiser, opened the door list, inserted
  `VT-LATE9999` into the database AFTER the page had loaded, searched "Zainab":
  the page said "Found on the server. This device's copy of the list did not
  have them" and showed her. Pressed Check in: 6 sold went to 7, 1 checked in
  went to 2. The `DoorLookup` row reads `term='Zainab' matched=1
  by=demo_organizer ticket=VT-LATE9999`, which is exactly what could not be
  answered on 6 September.
- **Real time.** Inserted `VT-LIVE0001` while the page sat open. It appeared
  with no reload, count 7 to 8. The delta calls carry `since` and `lean=1`.
- **Undo.** Pressed in Chrome: "Zainab Lategate is no longer checked in", row
  back to Valid, and a `kind='undo'` row recorded with who did it. The first
  attempt 500'd because migration 0034 had not been applied locally, which is
  the argument for pressing things rather than reading them.
- **Android emulator, 411 CSS px.** Pressed Check in on Bola Ade's row and
  confirmed `status=checked_in` in the database.
- **Every blocking catcher clean**, including the new timing model.

## What is NOT done

- **Row 88, the online control room.** Scoped, not built. Most of it already
  exists and rebuilding it would be wrong: uploaded HTML with an OBS token URL,
  position and nudge, media and text layers, a studio asset library, fonts, and
  a public `overlay-feed` already carrying standings, teams, players, live
  matches with scores, the rivalry aggregate and the run of show. **The gap is
  the slot.** The RIVALRY control room gives its crew four fixed browser sources
  (`/s/full`, `/s/lower`, `/s/bug`, `/s/bg`) and a panel that decides what
  occupies each; V-ENT gives one URL per element, so going on air would mean
  adding and removing browser sources during a show. `gates/06-slots.md` holds
  the six gates for building that on top of what exists.
- **Nothing is deployed.** Both repos are on `feature/run-of-show`. The backend
  is 7 commits ahead of `origin/main`, the frontend 7. Two migrations ship with
  it: `0033_ticket_updated_at_doorlookup` and `0034_doorlookup_kind`.
- **Row 78 beyond the door.** The door list keeps itself current. Other event
  pages do not yet.
- **The self check-in attendee page.** The organiser switch is built and proven;
  the page an attendee admits themselves on is the endpoint plus no screen of
  its own yet, though the endpoint is reachable and tested.

## For whoever picks this up

The local walk needs: `DB_ENGINE=sqlite manage.py runserver 8000`, frontend
`pnpm dev -p 3005`, and `demo_organizer` / `doorwalk2026` on the
`rivalry-series-season-2` event. `pnpm install --force` was needed once: the
store reported itself satisfied while `next` was absent, which is the known
corruption.

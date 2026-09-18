# Handover, 18 September 2026: the event platform walked at control level, and offline scanning

Inbox rows 269 (the walk), 270 (managers on a personal event), 271 (offline
scanning). Branch `fix/event-walk-12-sept` in both repos; PRs BE #180 and FE #190
stay open and take these commits. Nothing is deployed from this session.

Written mid-walk, in the same turn as the work, and extended as sections land.
State at the end of the second session: sections A to I DONE. 80 faults fixed,
gates/41 MET 30 of 30, BE fd90f43d and FE aaab7a3 pushed to the branch, PR
bodies updated. If this file and the walk log are all you have, you can pick
it up.

- The walk log, one line per control pressed, in order:
  `scratchpad/walk-results.log` of this session (copied into
  `V-ENT/tasks/audit/event-control-walk-2026-09-17.md` in section I).
- The inventory and gates: `V-ENT/tasks/audit/event-control-walk-2026-09-17.md`,
  `V-ENT/gates/41-event-control-walk.md`.

## READ THIS FIRST: what deploying now changes

On top of the 14 and 17 September notes:

1. **Migrations**: `vent_event` 0049 (`Event.is_listed`), 0050 (`Ticket.promo`),
   0051 (`VendorSlotPurchase.refunded_at`); `vent_tournament` 0053 (blanks every
   run sheet name that was a copy of its owner's name). All additive.
2. **Frontend dependency**: `jsqr` 1.4.0 (`pnpm add jsqr` done; lockfile updated).
   The VPS build needs `pnpm install --frozen-lockfile` before `pnpm build`.
3. **A service worker now ships** at `/door-sw.js`, registered ONLY by the
   scanner page. Scope is the whole origin because the shell's chunks live under
   `/_next`, but it only ever answers `/events/scan`, `/_next/static/`, `/fonts/`
   and `/images/`. It never touches the API. `/manifest.webmanifest` is served
   by `src/app/manifest.js`; the three icons the root layout already referenced
   (`icon-192.png`, `icon-512.png`, `apple-touch-icon.png`) now exist.
4. **Promo codes work at the till** for the first time. Every code created since
   3 September was inert; from deploy, a code a buyer types is honoured, counted
   and (if tied to an influencer) credited. Uses were never written before, so
   every existing code reads 0 uses, which is true.
5. **Unlisted is a real state**: `Event.is_listed` off keeps the page, the
   checkout and the stalls reachable and takes the event off the four listing
   doors (get-all-events which also feeds the sitemap, Discord search, the
   partner feed, the org following feed). Before this the switch flipped
   `is_active` and 404ed everything.
6. **Every private slug resolver follows renames** (32 copies replaced by one
   `find_by_ref`). A console opened at an old address answers `moved` and the
   page follows. Before, the organiser's console at the old address was 20
   404s.
7. **A queued door check-in carries its own time.** `check-in/` takes `at`
   (ISO, past, not older than a day) and stores it as `checked_in_at`.

## 1. What the walk found, by section

63 faults, every one fixed at the model with a test where a test can hold it.
The full line for each is in the walk log under its id.

### A. The wizard (5)
A-1 past start dates accepted (server refuses, picker greys, tests +2). A-2 the
free-entry tick left the tier editor on screen (`[hidden]` now wins globally).
A-3 the default price was one no door accepts (2000, step-3 check, hint). A-4 a
restored draft lost its pictures silently (says so). A-5 Discard wiped the form
on one press and left the pictures behind (asks; clears all).

### B. The console, thirteen tabs (21, two of them classes)
B-1 saved pricing rules invisible on the row. B-2 tier Remove one press. B-3 no
day at type creation. B-4 creating a type with a day answered 500 while
creating it. B-5 a 500 read as "Failed.". B-6 questions printed storage words.
B-7 copy pointed at the wrong tab. B-8 Discord connect posted to
`/webhooks/event/undefined/` (the console read `window.event`; ESLint now
refuses the global). B-9 six poll kinds looked identical. B-10 a held ticket
named "Ada Obi <ada@x.com>" went to nobody (an address now gives it to that
person). **B-11 (timing model)**: nine timed DateFields sent a naive local
string; the browser now names its zone on every request (`X-Client-Timezone`,
`ClientTimezoneMiddleware`). B-12 no way to change a session. B-13 changing a
cue wiped its phase and match. B-14 pitch Remove one press (it deleted one
mid-walk). B-15 an off-sale pitch could never be relisted. **B-16 (class)**:
"approve each stall" had no door; a pending stall traded anyway
(`views_vendor_review.py`, `StallsReviewPanel`, `Vendor.OPEN_STATUSES`, the
public list and the order doors refuse a stall that is not open; tests 9).
B-16b `can_add` still said "only in an organisation" eleven days after the rule
changed (row 270). B-17 End broadcast one press. B-18 overlay Remove and New
URL one press. **B-19 (class, both apps)**: 32 private "by slug or id" copies
ignored slug history (`vent_auth/slugs.find_by_ref`, `vent_event/refs.py`;
three-way differential tests on every door in both apps; five standings doors
and the requirements submit door 500ed on any slug and are fixed). B-20 "Listed
publicly" killed the page (`is_listed`). B-21 sponsor Remove one press.

### C. The public page and guest checkout (8)
C-1 JSON-LD read keys the payload never had (dates, tiers, offers were empty;
`check-ld-shape.mjs` with fixtures now holds it). C-2 the guest form never named
the ticket. **C-3 (class)**: six refusals shared one code so the field's name
was lost (`CheckoutError` carries code + label). **C-4 (class)**: Paystack's
reason was thrown away by three copies of initialize (one `vent_auth.paystack`,
EMAIL_INVALID / PAYMENT_REFUSED / GATEWAY_ERROR). C-5 the run of show carried a
stale copy of the event name (migration 0053). C-6 public stalls wore
"APPROVED". **C-7 (slug + SEO)**: a stall's only address was a query string with
one static title for every stall on the platform (`/events/<event>/stall/<stall>`
with server metadata, sitemap, noindex when not open). C-8 find-ticket said
"Not found" (TICKET_NOT_FOUND names what to check).

### D. The buyer (8)
D-1 the modal sold more than the type's limit and refused after the PIN (one
`quantityCeiling` for both forms). **D-2 (class, built on one side)**: promo
codes had no door at the till (`vent_event/promos.py`, quote + both buy doors +
card metadata, `Ticket.promo`, tests 9). D-3 the whole-coin floor turned a 10%
code on a 1-coin ticket into a free ticket (floor at one coin). D-4 a Day 2 pass
read the event's first day. **D-5 (timing model)**: a calendar date was parsed as
UTC midnight, so anybody west of Greenwich read the day before (`isDateOnly`,
drawn in UTC). **D-6 (class)**: the queue was offered a place only when somebody
left it (`capacity_changed` at all four doors, tests 5). D-7 an offer held
nothing (`available()` subtracts other people's live offers). D-8 a sold-out type
still advertised early bird.

### E. The vendor (11)
E-1 "your stall is set up" on a pending pitch. E-2 default title. E-3 no way to
describe a stall (the endpoint existed). E-4 product Remove one press. E-5 a
refunded stall could be reopened (`refunded_at`, STALL_REFUNDED). E-6 "You have
one of these" after a refund. E-7 "Reserve at booths" cleared the basket and
ordered nothing (gone; links to the stall that places the real order). **E-8
(class)**: the shop index wiped the saved basket on load (skip-the-hydrating-
commit flag on both pages). E-9 the buyer's receipt lost the option chosen.
E-10 raw status words. E-11 cancel-and-refund one press.

### F. The door, and row 271 "Offline scanning very possible and easy" (10)
Proven with BOTH servers stopped, in Chrome and on the Android emulator: the
page opens from the worker's cache, the list on the phone is read, a listed
code is admitted locally, a duplicate is judged locally with "first used at",
a wrong-day ticket is judged locally, an unknown code says the list is a copy;
servers back, the queue flushes and the server stamps the scan with the phone's
own time.

F-1 no way to say which day on screen (day chips). F-2 lookup judged today
while check-in judged the door's day. F-3 a reload with no signal lost the page
(`door-sw.js`, manifest, icons). F-4 served from cache the door said "Sign in"
and "0 on the list" because nothing could answer the session (the list is read
before any token; the door remembers its steward when the session endpoint is
unreachable, forgets on a real sign-out). F-5 a queued check-in was stamped at
flush time (`at` honoured, `tests_door_offline_at.py` 6). F-6 "Online" with a
server not answering (three states). F-7 the list's age was nowhere. F-8 a
phone without BarcodeDetector could only type (jsQR). F-9 nothing said what to
do before the doors (one note: open with signal, add to home screen). F-10
"Look up only" broke over three lines on the phone.

### F3. The door list (session 2, after the limit reset)

Walked as `walk_door`: search by name, code, tier and username, the four
filters with their empty copy, a typed code refused with the day named, Undo
and Check in on a whole-run ticket, the tip, the links. Three faults, all in
how the organiser's checkout answers travel:

F3-1 a comped ticket's row printed `comped_by walk_organiser` (the comp
endpoint's bookkeeping lives in `answers`; `checkout.PLATFORM_KEYS` now labels
it, `describe()` emits `key` + `kind: 'platform'`, the list translates by key).
F3-2 the attendee sheet carried NO checkout answers at all: one column per
question headed by the organiser's label, then Comped by / Note
(`checkout.sheet_columns` / `sheet_cells`, test in `tests_metrics`).
F3-3 Check in or Undo wiped the row's answers on screen: the delta asked
`lean=1`, a lean row said `answers: []`, the merge believed it. A lean row now
carries no `answers` key (absent means not asked; `tests_door_delta` asserts
NotIn) and the list's own delta asks for the full rows.

Session 2 lost the first session's Chrome tabs and dev servers; the scratchpad
survived under `3c617e7f-…/scratchpad` and was copied forward (walk log, patch
scripts, phone helpers).

### G. The middle roles and the admin (session 2)

**G1, walk_manager.** The Money tab took the whole console down: `/money/`
answered 403 and the page read `money.taken.count` off the refusal's `{}`.
Underneath, a CLASS: ten doors still asked "is this the creator" while
`permissions.py` has said since 4 September that a manager runs everything
except deleting: holds and money, ticket limits, linked tournaments, the
programme, short links, sponsors, tiers and checkout fields, the queue, the
stall doors, the Production tab (`production_access`), and edit-event. Every
one reads `may_run_event` now. Creator-only by design and unchanged: delete,
restore, adding or removing managers, the seller's wallet on a pitch.
Catcher: `vent_event/tests_manager_every_door.py` walks every event route
(GET/POST/PUT/PATCH/DELETE) as creator, named manager, org events manager,
door staff and a signed-in stranger, and asserts the shape (a manager is
refused nowhere the creator is admitted; door staff reach the door only; a
stranger reaches what is public). Proven both ways. The console stores a
non-success payload as null and shows the sentence (`panelRefused`), so no
refusal can crash it again. Door staff: My events offers View / Door list /
scanner only; the console refuses with a sentence.

**G2, an organisation's event.** The wizard's picker appears once the account
runs an organisation; the event lands under Walk Org; the org's events manager
(no per-event row) runs it from their own list and added a tier; a member with
the tournaments scope only is refused everywhere private. Found: neither
wizard's review said whose name the thing runs in (the picker was the one
field the review omitted); both reviews now show "Running this as".

**G3, the admin console.** Filters, sorts, Edit, Void with a reason, Reinstate,
Message ticket holders, Cancel with a reason, Restore all work. Four faults:
the Edit control promised "the organiser is told it changed" and nobody was
(both models; the owner's inbox now carries who and what); a voided ticket's
holder heard nothing (told on void and reinstate, inbox or mail); **a
cancelled event's page answered 404** although the console says it keeps
answering (the B-20 shape again: every public reader filtered
`is_active=True`; `event_status()` now says `cancelled`, the payload carries
`is_active`, view-event answers, the four buy doors refuse EVENT_CANCELLED by
name, the page draws the notice and hides the till; `tests_cancelled.py`);
and a cancel told nobody (organiser and every live ticket holder are told,
once each, on cancel and restore).

**Open for the CEO:** an admin cancel does not refund paid tickets. The
organiser's own Delete refuses while PAID_ENTRANTS exist and the tournament
admin cancel refunds entry fees, so events are the odd one out. Whose wallet
pays (the organiser's, after settlement?) is a money rule, so the copy
promises no refund and nothing was improvised.

**Plurals.** The F2-1 sweep claimed no "(s)" reached a screen. 22 server
sentences and 8 English dictionary keys (plus fr/pt agreement forms) did.
`vent_auth/text.py: count()` and number-neutral sentences on the server; the
keys and a catcher follow in section I.

**Fixture.** `tests_shop.py` built its stall with `status='open'`, which is
not a status; it traded only because nothing read the status until the
approval door (B-16) started deciding. Now `approved`. 19 tests were red on
the tree the previous session left.

### J. "If an event is cancelled then refunds must happen" (CEO, 18 September, row 272)

Decided by the CEO the same afternoon, answering the open question. Built at
the model: `vent_event/refunds.py`, one function per ticket and one per event,
called by every cancel path. A refund is: the ticket's own price plus (once
per purchase, on the ticket that carries the purchase's ledger line) the fee
the buyer bore; the ledger reversed (a settled organiser carries the debt on
their next payout); coins to the wallet that PAID (the original buyer of a
given-away ticket, not the friend holding it); a guest's card refunded through
`paystack.refund` against the payment reference, in naira; a free ticket
cancelled; the tier's sold count down; `Ticket.refunded_at` and
`refund_reference` say where it went (migration **0052**).

Three doors: the admin's cancel now refunds and answers with the summary; the
organiser has a cancel door of their own, `POST /event/<ref>/cancel/` (the
delete refusal has said "cancel the event first, which refunds the holders"
since it was written, and no such door existed), with a Cancel control on My
events (reason, two presses, the summary on the card); and a retry door,
`POST /auth/admin/events/<ref>/refunds/`, for whatever a card network refused
the first time ("Run the refunds again" on the console, with the count still
owed). A gateway refusal never undoes the cancellation: the ticket stays live
and named. Everybody is told what came back; the public notice says so.
`tests_cancel_refunds.py` 14 (wallet, ledger, free, given away, card through
Paystack, a refusal and the retry, twice refunds nobody twice, who is told,
the organiser's door, a manager refused, delete after cancel).

Found on the way and fixed: my void copy promised money that never moved
(removed); "Who runs it" on the admin console ignored the organisation's
people. Found and NOT fixed (row 273): voiding ONE ticket of a multi-ticket
purchase reverses the ledger all or nothing, because a purchase's lines are
attached to its first ticket. A partial reversal belongs in the ledger.

## 2. Classes, and what holds them now

| Class, seen more than once today | Held by |
|---|---|
| a destructive control on ONE press (tier, pitch, broadcast, overlay x2, sponsor, product, cancel-and-refund) | to build in section I: a checker over `onClick` handlers that call a DELETE or a `remove`/`end`/`cancel` endpoint with no confirming state; until then the eight are fixed by hand |
| a private resolver ignoring slug history (32 copies) | `find_by_ref`; `tests_old_slug_every_door.py` in both apps walks every door three ways |
| built on one side only (promo till, stall approval, waitlist doors) | `tools/check-parity.py` rows to add in section I |
| a refusal code shared by several fields | `CheckoutError(code, label)`; the test asserts the label |
| a naive local time sent to a UTC server (nine fields) | `X-Client-Timezone` + middleware; `check-datetime` reads 0 new |
| a date-only string read in the viewer's zone | `isDateOnly` in `datetime.js` |
| a saved basket wiped by the hydrating commit | `skipPersist` ref on both shop pages |
| JSON-LD built from keys the payload does not have | `scripts/check-ld-shape.mjs` with real fixtures, in check-all |

## 3. Proof so far

- Backend suites run after each fix: door (53 incl. the 6 offline-at), promos
  (9 + 1), vendor review (9), waitlist doors (5), unlisted (2), old-slug every
  door (both apps), checkout, availability agreement. Full suites and check-all
  run in section I.
- Frontend: check-keys 0 missing, dict-parity en=fr=pt=8602, check-seo 104
  routes 0 problems, check-slugs 0, check-ld-shape self-test 4/4.
- Chrome desktop screenshots and the emulator (412 CSS px, 0 overflow, no small
  targets) for the scanner; `emu-scan-3.png` (the door), `emu-scan-5.png` (the
  camera stream), `emu-scan-6.png` (offline duplicate, nothing serving).

## 4. Open, at the end of the second session

- ~~For the CEO (inbox 272): an admin cancel does not refund paid tickets.~~ Decided and built the same afternoon (section J).
  The organiser's own Delete refuses while PAID_ENTRANTS exist and the
  tournament admin cancel refunds entry fees; events are the odd one out.
  Whose wallet pays is a money rule, so the cancelled-event copy promises no
  refund and nothing was improvised.
- The admin events list's ORGANIZER column names the creator for an
  organisation's event; the organisation is on the detail page. Noted, not
  changed.
- The security ledger's second batch (R62 to R86, added 17 September) reports
  18 HIGH in the backend and 5+ in the frontend that predate this walk and
  are baselined (discord auth and partner SSO reading identity from the body,
  signup with no bot check, gallery uploads with no sniff, the admin token in
  localStorage, HSTS/CSP on the dev server). Out of this walk's scope; they
  are the next security pass.
- `pnpm build` leaves an empty `.next-dev` beside `.next-dev-3005`
  (`next.config` picks `.next-dev` when PORT is unset in development);
  `check-stale-builds --clean` removes it and the pre-commit hook asks for
  that every time a build has run. Worth a one-line fix in next.config.
- Neither PR is merged. Merge FE #190 and BE #180 together: the frontend
  reads `is_active`, `promo`, `answers` keys and `EVENT_CANCELLED` that only
  this backend sends, and the backend's `X-Client-Timezone` middleware is
  harmless without the header.

## 5. How to run it again

```
cd V-ENT-BACKEND
DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe manage.py runserver 8000 --noreload
DB_ENGINE=sqlite venv/Scripts/python.exe manage.py test vent_event.tests_door_offline_at vent_event.tests_vendor_review vent_event.tests_promo_redeem vent_event.tests_old_slug_every_door vent_tournament.tests_old_slug_every_door
cd ../V-ENT-FRONTEND && pnpm dev -p 3005
node --no-warnings scripts/check-ld-shape.mjs --self-test
```

Offline, on the phone: sign in through `/auth/external?token=…&username=walk_door`,
open `/events/scan?event=walk-con-wizard-18-sept&gate=North&day=2026-09-26`
(single-quote the URL inside `adb shell am start -d`, or everything after the
first `&` is lost), reload once so the worker holds the chunks, then stop both
dev servers (`TaskStop` leaves the Next child on 3005; `taskkill` it) and
reload. `scratchpad/phone_door.py` drives it over DevTools: `cache`, `state`,
`scan <code>`, `reload`, pinned to a tab with `TAB=<id>`.

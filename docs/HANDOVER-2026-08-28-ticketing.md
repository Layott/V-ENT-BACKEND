# Handover, 28 August 2026 - the ticketing build

Deployed and live. 756 backend tests, frontend building, 36 of 38 gates in
`GATES-TICKETING.md` met with evidence.

## The check that should have existed first

`tools/endpoint-callers.py`. Four separate faults in one night had the same
shape: **the endpoint was built, tested, green, and no screen ever called it.**

- `PUT /event/edit-event/` existed for weeks; an organiser who mistyped a venue
  had nothing to press.
- Ticket tiers could be read and never written after the creation wizard.
- `redirect_uris` was accepted by the API and editable on no screen an approved
  partner could reach.
- `Format.can_feed_into` was recorded and read by nothing.

A passing test suite says the endpoint works. It says nothing about whether
anybody can reach it. The script lists every route and whether the frontend
mentions its path segments, and **fails on a new orphan rather than on the
backlog** - a check that is red from the first minute is a check nobody reads.

220 endpoints, 148 called, 24 known orphans, 48 deliberately not called (the
partner API, OAuth callbacks, email links). Run `--list` to read them. It found
real ones: `tournament/join-tournament/` is dead because the frontend uses the
`register-tournament/` alias, and `wallet/deduct/`, `team/list-teams/` and
`change-fullname/` have no caller at all.

## What shipped

### The door

`/events/scan`. Camera QR through `BarcodeDetector`, typing where the browser
has none, and **offline by design**: the whole list downloads before the gates
open, every scan is decided locally, results sync when the network returns.

Two vendor warnings are built in rather than documented:

1. **The reload button locks once scanning starts**, and says why. A mid-event
   reload overwrites this device's record of who has been scanned and duplicates
   stop being caught.
2. A duplicate says **when, where and who**. "Already scanned" sends a steward
   to a supervisor; "Utilisé pour la première fois à 20:35 à Main Gate ·
   league_boss" lets them decide at the gate.

Also fixed: `EventManager` has had a `door` role since it was written - "check
tickets in, nothing else" - and neither the check-in path nor the attendee list
consulted it. In practice that meant one person scanning, or the organiser
handing over their own account.

### One function decides what is sellable

`vent_event/availability.py`. Three rules in three files could disagree about
the same number, and one of them - the venue's capacity - was not enforced at
all, so an event set to 200 could sell 150 standard plus 100 VIP.

It accounts for the type's allocation, the venue's ceiling (the lower of the two
wins) and everything held back. Counted from the tickets rather than from
`TicketTier.sold`, because a counter can drift and the tickets cannot.

### Holds

Guest list, press, venue allocation. Without them an organiser buys their own
tickets, which corrupts the sales figures they then show a sponsor. Two exits:
**release** puts them back on sale, **issue** turns them into free named
tickets. Releasing after issuing returns only what is left, because a ticket
already given to somebody is theirs.

An influencer's allocation is counted by the same function rather than by a
second rule that could disagree with the first.

### The waitlist, in the DICE shape

Not demand capture: the **return valve** that makes a face-value-only policy
workable. A returned ticket is offered to the queue in order, and somebody
holding an offer can buy into the room their own offer is holding - without
that the offer is unusable, because the event is sold out by definition.

The offer has a 12 hour window, and `expire_stale_offers()` runs on every read
and write rather than on a schedule: a cron job that is not running is the usual
reason a queue is wrong.

### Sessions

What replaces the invented programme. A session carries its own capacity, which
is the reason to have sessions rather than a list of times: a convention holding
900 has a panel room holding 80. A set at 1am belongs to the night before,
derived rather than stored.

### Pricing

Early bird, group rates and access codes, all on the tier. A group rate wins
over early bird. A hidden type is **refused at the purchase**, not merely
unlisted - a hidden type anybody who guesses its id can buy is not hidden. What
somebody paid is written on their ticket, so a later price change never rewrites
a receipt.

## Found while walking it in Chrome

Each of these was invisible to the test suite and obvious within a minute of
using the screen:

- The attendee list did not serialize `checked_in_gate`, so the offline scanner
  had only half of the duplicate warning.
- `event_attendees` was creator-only, so door staff could not download the list
  the scanner needs.
- The organiser could not see their own hidden ticket type, because the manage
  screen read the public listing that filters them out.
- `" on " + gate` was a fragment built in JavaScript, so it stayed English
  inside a French sentence. Two whole-sentence keys now.
- The Schedule tab probed for a programme from inside itself, so it could never
  appear. Probed on page load instead.
- The manage page subtitle still described only influencers.

## Not done

**The 390px walk.** The browser window will not resize through this tooling and
three approaches failed: `resize_window` reports success and leaves
`innerWidth` at 1745, PowerShell `Start-Process` cannot launch pnpm, and
`window.open` with dimensions is popup-blocked.

What was checked instead: with the document forced to 390px, the only element
wider than the viewport is the desktop header, which a real mobile viewport
swaps for `MobileHeader`. So nothing new has a fixed width that would overflow,
but **the media queries themselves are unverified and need a person on a
phone.** The door scanner in particular only ever runs on one, so it is the
first thing to look at.

## Still open elsewhere

From `GATES.md`: `O11` the partner-side contract is written but the call is not
wired to a real partner, `R8`/`R9`/`R11` the format wizard and guides, `I` the
rates refresh failure, `T4` AFC still needs a key issued by the CEO.

## Traps, still true

- **`pnpm build` deletes `node_modules/.../next/dist/pages`**, which `next dev`
  loads at startup. Build last, or `pnpm install --force` from PowerShell after.
- `pnpm build` under a running dev server leaves it serving 404 for everything
  until `.next` is deleted.
- **Two dev servers on 3001** means you are reading stale code. `EADDRINUSE` is
  in the log, not on the screen; it cost ten minutes tonight.
- A heredoc containing non-ASCII characters fails in this shell. Write the
  script to a file and run it.
- Chrome screenshot coordinates scale to CSS by 1745/1425. Use `find` refs.

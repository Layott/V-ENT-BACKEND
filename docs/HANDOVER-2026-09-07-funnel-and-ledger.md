# Handover, 7 September 2026: the funnel, the ledger, and the door

Waves C, D and E of `gates/16-everything-left.md`, plus two asks the CEO sent
mid-build (rows 167 and 168).

If the session ended here, this file plus `tasks/inbox.md` is enough to pick
the work up.

---

## What was built

### Deep metrics (row 151, gate D.6)

> "organizers hsould be able o see mad metric for thier events and tickets, how
> many clicks, how many people opened it up, how many tapped buy, how many
> check out vendor, stuff like that, very detailed stuff."

`EventFunnelDay` + `vent_event/funnel.py` + a public `POST
/event/<ref>/track/`. Eight steps: page open, ticket tab, buy tap, checkout
start, vendor list, stall, share, directions.

**The one decision worth reading twice.** `sold` is COUNTED from the tickets
that exist and is never accumulated from a beacon. Every step above it is a
browser saying what it did, and a browser can say it twice, be a crawler, or
fail to say anything on a flaky connection. The bottom of the funnel has to be
the number an organiser reconciles money against, so it comes from the table.
The screen says so in as many words, under the last row.

Shaped exactly like `ReferralDay`: a day per step, never a row per visitor. No
address, no user agent. `people` is what the browser reports about itself and
nothing is stored to verify it, because verifying it would mean storing the
thing the design avoids.

A rate with nothing above it is `None`, not 0. Zero per cent and "nobody has
been here" are different facts and rounding the second into the first reads as
a broken checkout. And when `sold > opens` - which is arithmetically possible
and always means the counting started after some sales - the payload carries
`sales_predate_tracking` and the screen says it, rather than capping the rate
at 100 and hiding that the tracking is incomplete.

### The ledger (gates E.1, E.2, E.4)

**Before this, a ticket sale debited the buyer's wallet and credited nobody.**
The money left one account and arrived nowhere: no organiser balance, no
platform fee, no affiliate commission, nothing to reconcile. Every question in
the ticketing research reduces to that missing record.

`vent_event/ledger.py` + `EventLedgerEntry` + `EventSettlement`.

| | |
|---|---|
| A ledger, not a balance | A balance is the SUM of unsettled lines. A running total drifts the first time a refund lands or an issue runs twice, and once it has drifted nothing can say by how much |
| A line is paid once | `settle` stamps each line with the run that paid it, inside the same transaction that moves the coins. That is the whole difference between a RUN and a queue: a queue worked through twice pays twice, and the person it pays twice never says so |
| The fee is decided at the SALE | Rate and bearer are stamped on the line. Changing the platform rate next month never rewrites what an event earned last month |
| A refund is a new line | Opposite sign, never an edit. Editing a settled line rewrites a payment already made |

`Event.fee_bearer` is `organiser` by default, which is what every event has
been doing implicitly. Set to `buyer` it is added on top and the checkout says
the number BEFORE anybody commits to a quantity - beside the prices, not only
in the panel. A free ticket carries no fee either way; a 0 VC ticket that
quietly costs 1 VC would land on exactly the events least able to absorb it.

`EventReferral.commission_pct` + `payee`. Of the TICKET price, never the
buyer's total: otherwise an event passing the fee on quietly pays its
affiliates more than the same event without. A link with nobody attached
accrues rather than losing the money, and the earnings screen says so.

### Ticket transfer (gate E.3)

`TicketTransfer`. The code REISSUES and the old one dies in the same
transaction. A ticket that keeps its code after being given away means two
people at one gate, and the one turned away has done nothing wrong.

A checked-in ticket cannot move: the seat is taken, and moving it would make
the attendance figures name somebody who was not there. The previous holder's
phone number does not travel, because it would put one person's number against
another person's name on the door list. The organiser can transfer too, since
somebody who lost their phone cannot reach the screen and the organiser is who
they will ask at the door.

---

## The two faults I introduced and then found

Both on the Android emulator, which is the only place either was visible.

### 1. A control added to a header the header had no room for

The CEO caught the first: on a phone the console header wrapped and "Open the
door scanner" sat flush against the sentence below it, reading as one
overlapping the other. `rowBetween` carries no bottom margin. Fixed with a
`pageHead` class that stacks and spaces at 720px.

I then reproduced the SAME fault on the door list by adding the button to
another header with no room for it - and, worse, that page already had an
"Open the scanner" link below three stat cards. Two controls doing one job on
one screen. Resolved by moving the original into the header rather than adding
a second.

### 2. Fixing the endpoint that was reported rather than the class

`transferred_away` was added to `ticket_lookup`, and the scanner still said
"Not on the list" - because the scanner posts to `check_in_ticket`. **Six**
endpoints resolve a ticket by code and each had its own refusal.

This is the "fix the model, not the record" fault committed by the person who
had been writing about it an hour earlier, which is exactly why it is a
checker now and not a note:

```
python tools/check-dead-codes.py --self-test   # 3 cases, both directions
python tools/check-dead-codes.py               # 0
```

It reads function bodies, so an endpoint written next month is caught the day
it is written. Registered in `check-all.py` as a blocking catcher, because a
catcher outside check-all is a catcher nobody runs.

---

## What was verified, and how

Not a build pass. Each of these was walked and PRESSED.

| Claim | How |
|---|---|
| The fee reaches the buyer honestly | Chrome: panel read 20 + 2 = 22 VC, wallet 300 -> 278, ledger credited the organiser 20 and the platform 2 |
| A rate change rewrites nothing | The earlier sale's lines still read `fee_bearer=organiser` after the switch |
| The settlement pays | Chrome: 56 VC across 3 lines. Organiser 278 -> 326, affiliate 460 -> 468, two `prize` transactions written |
| Running it twice pays nothing | Second call: amount 0, lines 0, owed 0, paid 48 |
| The funnel counts real walking | Opening the page, the tickets tab and the buy panel moved the counts to 2 / 1 / 1 / 1 |
| The transfer reissues | VT-ZP4SGKXX -> VT-LGU76GHU, old code resolves to no ticket, phone cleared |
| The door names the transfer | **On the emulator**, typed at the real scanner: "This ticket was given to somebody else - It belongs to Chidi Okeke now, who has a different code." |
| Tap targets | Measured 29px at 390px, raised to 44px at the touch breakpoint, re-measured |

Emulator screenshots are in the session scratchpad: `emu-money.png`,
`emu-money2.png`, `emu-funnel*.png`, `emu-edit.png`, `emu-door2.png`,
`emu-scan.png`, `emu-dead-code2.png`.

---

## What is NOT proven

- **The guest (card) path with a fee on the buyer.** The fee is added to the
  Paystack amount in kobo and the ledger is written inside `_issue`, so both
  guest routes are covered by one change and there are tests. But no live card
  payment was made, because `PAYSTACK_SECRET_KEY` is not set locally. The
  arithmetic is proven; the gateway round trip is not.
- **An affiliate claiming a link after the fact.** The line stays open and
  `unclaimed_vc` reports it, and there is a test, but nothing has been walked
  where somebody signs up and then gets paid.
- **The funnel under real traffic.** Every count so far is mine. The revisit
  window (30 minutes, per event per step, effect-fired steps only) is the same
  shape that fixed the referral double-count on the locale redirect, so it is
  reasoned rather than measured at volume.

---

## Files worth knowing

```
vent_event/funnel.py          the funnel, and why sold is counted not reported
vent_event/ledger.py          quote, record_sale, reverse_sale, balances, settle
vent_event/transfers.py       one answer about a dead code, for six endpoints
vent_event/views_ledger.py    earnings, fee-bearer, settle
vent_event/views_transfer.py  transfer_ticket, transfer_history
vent_event/views_track.py     the public beacon
src/lib/track.js              the browser half, and the revisit window
tools/check-dead-codes.py     the catcher for the fault I committed twice
src/components/door-scanner-link/  one button, five screens
```

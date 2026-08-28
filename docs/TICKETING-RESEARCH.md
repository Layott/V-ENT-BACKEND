# How ticketing platforms actually run, and what V-ENT is missing

Research into Eventbrite, DICE, Ticketmaster, TicketSpice, Ticket Fairy, Big
Tickets and Ticketure, read against what V-ENT has today. Written to be built
from, so every section ends with what it means for us rather than with a
summary.

Sources are listed at the end. Where a claim comes from one platform's own
documentation it is attributed inline, because "Eventbrite does X" and "the
industry does X" are different claims and only the first one is checkable.

---

## 1. The shape every platform converges on

Four objects, and almost every platform has all four under different names.

```
Event  ──<  Session / time slot  (optional)
  │
  └──<  Ticket type  ──<  Ticket / order item  ──>  Attendee
             │
             └──<  Hold / allocation
```

**Event** is the thing with a name and a venue. **Ticket type** (Eventbrite's
"ticket type", DICE's "ticket tier") is what somebody actually buys: a price, an
allocation, and what it admits you to. **Ticket** is one admission with its own
code. **Session** is a time slot within the event.

The detail worth copying is how capacity nests. On Eventbrite an event has a
total capacity, each ticket type has its own quantity, and **ticket sections**
group types with a shared capacity: the event's total updates from the sections.
The consequence they document explicitly is the one people trip over: *if the
event capacity is reached the event is sold out even if individual ticket types
still have quantity available.* Two ceilings, and the lower one wins.

**What this means for us.** V-ENT has Event, TicketTier and Ticket. It does not
have sessions, and it does not have the two-ceiling rule: `Event.capacity` and
`TicketTier.quantity` both exist and nothing reconciles them. An organiser can
set a venue capacity of 200 and then sell 150 standard plus 100 VIP, and the
platform will let them. **That is a real oversell and it should be fixed before
a paid event runs at any size.**

---

## 2. Ticket types are a pricing strategy, not a list

Eventbrite frames multiple ticket types as how you "reward passionate fans with
early access, create premium experiences, and manage capacity". The mechanisms
underneath are consistent across platforms:

| Mechanism | What it does | Who has it |
|---|---|---|
| **Tiered / early bird** | Price rises as allocations sell through | Eventbrite, TicketSpice, Big Tickets |
| **Group rates** | Per-ticket price falls above a quantity | Most |
| **Promo codes** | Percentage or amount off, scoped to a type | Most |
| **Invite-only / access codes** | The type is hidden until a code is entered | Eventbrite, Ticket Fairy |
| **Domain restriction** | Only an email on a domain may buy | Enterprise platforms |
| **Holds** | Tickets removed from sale, released later or given out | Eventbrite |

**Holds deserve their own paragraph** because they are the one most people do
not think to build and every real event needs. Eventbrite's definition: *a hold
removes tickets from sale so that you can release them at a later time or give
them to specific people.* Every event has guest list, press, the venue's own
allocation, and the artist's family. Without holds an organiser fakes it by
buying their own tickets, which corrupts the sales figures they then report.

**What this means for us.** V-ENT has ticket types, promo codes and influencer
allocations. It does **not** have early bird tiering, group rates, access codes
or holds. The influencer allocation is closest to a hold and could be
generalised into one rather than a second mechanism being built.

---

## 3. The waitlist is the anti-touting mechanism

DICE's whole position is that *tickets belong in the hands of fans, not
resellers, and should only ever be sold at face value.* The waitlist is how they
make that work rather than a nicety: a sold-out event keeps a queue, and when
somebody returns a ticket it goes to the queue at face value instead of onto a
secondary market.

This inverts the usual thinking. A waitlist is normally treated as a way to
capture demand you cannot serve. On DICE it is the **return valve** that makes a
no-resale policy tolerable, because somebody whose plans change has a way out
that is not StubHub.

Ticketmaster's queue solves a different problem, and the mechanism is worth
knowing precisely: it is a **randomised lottery**, not first-come. Everybody who
joins during the pre-queue window gets an equal chance at a position. Joining
early buys nothing. They do this because first-come rewards bots, and because a
first-come queue makes every fan feel obliged to be at their computer at 9am.

**What this means for us.** V-ENT has no waitlist and no queue. For the size
V-ENT is running, the queue is not needed and probably never will be. The
waitlist is worth building, and specifically the DICE shape rather than the
capture-demand shape: **join the waitlist on a sold-out event, and a returned or
cancelled ticket is offered to the queue in order.** That is a small feature
that makes a face-value-only policy possible, and a face-value-only policy is
worth having on a platform whose audience does not have money to lose to touts.

---

## 4. Timed entry, and why V-ENT's Schedule tab is the wrong shape

Timed entry is a slot rather than a day: guests reserve a specific time, the
operator caps each slot, and the queue at the door disappears. Ticketmaster,
Ticketure, TicketSpice and Ticket Fairy all sell this as a first-class feature,
and the operators who need it are exactly the ones with a fixed room: escape
rooms, exhibitions, panel rooms.

The important structural point is that **a session carries its own capacity**,
separate from the event's. A convention holding 900 people has a panel room
holding 80, and the panel is a session with a capacity of 80.

**What this means for us.** V-ENT's Schedule tab was, until tonight, a hardcoded
function that invented a two-day programme from the event's start date. Every
event showed the same "Doors open + Vendor zone activation" and "Cosplay
parade". That is worse than an empty tab: an empty tab says no schedule has been
published, an invented one says the organiser published *this*, and somebody
turns up for a DJ set that was never going to happen.

The replacement is an `EventSession` model, and it should carry `capacity` from
the start even if nothing sells against it yet, because a session that fills is
the reason to have sessions at all.

---

## 5. The door: scanning, offline, and duplicates

This is the part that fails publicly, and the research here is unusually
specific because the failure modes are well documented.

**Offline is not optional.** The pattern every scanning app uses: download the
whole ticket database to each device before gates open, validate locally, sync
back when connectivity returns. In Lagos, on a phone network, at a venue with
900 people on it, this is the difference between a door that works and a door
that does not.

**Two warnings come straight from the vendors, and both are counter-intuitive.**

1. *"If you are using offline method, do not reload data after you start
   scanning tickets, as the entries information will be overwritten and
   duplicate tickets may not be caught."* A mid-event refresh silently discards
   the local scan record.
2. Offline mode *cannot detect tickets refunded or transferred after the
   download*, so the sync should be as close to gate time as possible.

**Duplicate handling is a UX problem, not a validation problem.** The behaviour
that works: when a ticket has already been scanned, show a prominent warning
*with when and where it was first used*. "Already scanned" sends the door
steward to a supervisor. "Scanned at Gate B, 19:42" lets them make a decision in
three seconds.

**Multi-device sync matters at more than one gate.** A ticket scanned at one
gate must be instantly used at all of them. And one operational note worth
building a report for: *a sudden spike in duplicate scan attempts at a specific
gate* usually means tickets are being shared, and the person watching the
dashboard can act on it while the event is still running.

**What this means for us.** V-ENT has `check_in_ticket` by code and an attendees
list. It has **no scanner, no offline mode, and no duplicate warning that says
when and where.** For an event of 50 that is fine. For the CEO's actual events
it is the thing that will go wrong first and most visibly. Ordered by value:
duplicate-warning detail, then a scanner screen, then offline.

---

## 6. Money: refunds, settlement, payouts

The pattern across platforms:

- **Refunds are the organiser's to issue**, from their own dashboard, full or
  partial, with the platform's service fee usually non-refundable.
- **Payout timing varies and is a real product decision.** Some platforms pay
  next day; others hold funds until after the event. Holding is the safer
  default, because an organiser who is paid up front and then cancels leaves the
  platform carrying the refunds.
- **Settlement reporting is a named feature**, not a byproduct: a record of what
  was collected, refunded and paid out, in a form a finance person or an auditor
  can follow, connecting every transaction, fee, refund, chargeback and payout.

**What this means for us.** V-ENT sells tickets in VENT COINS through the wallet,
which sidesteps most of this: there is no card settlement per event and no
chargeback path. But the organiser still needs **what was sold, what was
refunded, and what is owed to them**, and there is no per-event money report at
all today. That is the gap, and it is a reporting gap rather than a payments
one, which makes it much cheaper to close than it looks.

---

## 7. What an organiser dashboard is expected to have

Assembled from what the platforms lead with:

| Capability | V-ENT today |
|---|---|
| Multi-event management, duplicate an event as a template | Partial: `/events/my-events` lists them, no duplicate |
| Unlimited ticket types, per-tier pricing and caps | **Built tonight.** Was creation-wizard only |
| Promo codes | Built |
| Invite-only / access codes | Missing |
| Capacity limits and waitlists | Capacity partial and unreconciled, waitlist missing |
| Automatic refund policy | Missing |
| Live sales, attendance and revenue dashboard | Missing |
| Settlement-ready export | Missing |
| Built-in check-in / QR scanning | Check-in by code only, no scanner |
| Attendee list with search | Built (door list) |

---

## 8. What to build, in the order the value lands

The first two are not features, they are faults.

1. **Reconcile the two capacities.** An event capacity that does not bound the
   sum of its ticket types is an oversell waiting to happen, and it is a small
   fix today and a very expensive one at a sold-out door.
2. **Ticket type management after creation.** Done tonight. Without it an
   organiser could not correct a price or open more.
3. **The duplicate-scan warning that says when and where.** Cheap, and it is the
   difference between a door steward deciding and a door steward escalating.
4. **A per-event money view.** Sold, refunded, owed. Reporting, not payments.
5. **Sessions with their own capacity.** The Schedule tab needs a real model
   regardless; capacity makes it worth more than a list.
6. **A waitlist in the DICE shape.** Returned tickets offered to the queue in
   order, at face value. This is what makes a no-resale stance workable.
7. **A scanner screen, then offline.** In that order: a scanner that needs a
   connection is still far better than typing codes.
8. **Holds**, generalised from the influencer allocation rather than built
   twice.

Early bird tiering, group rates and access codes are real features that no V-ENT
organiser has asked for yet. They are worth building when one does.

---

## Sources

- [Eventbrite: create and edit ticket types](https://www.eventbrite.com/help/en-us/articles/644100/how-to-create-custom-ticket-types/)
- [Eventbrite: set and restrict total capacity](https://www.eventbrite.com/help/en-us/articles/745097/how-to-set-and-restrict-your-events-total-capacity/)
- [Eventbrite: create and manage holds](https://www.eventbrite.com/help/en-us/articles/779653/how-to-create-and-manage-holds/)
- [Eventbrite: tickets for recurring or timed entry events](https://www.eventbrite.com/help/en-us/articles/800028/create-tickets-in-eventbrites-new-recurring-event-experience/)
- [Eventbrite: ticket tiers](https://www.eventbrite.com/features/ticket-tiers/)
- [DICE: how the wait list queue works](https://dicefm.zendesk.com/hc/en-gb/articles/4409662022289-How-the-Wait-List-queue-works)
- [Ticket Tailor: DICE reviews, pros and cons](https://www.tickettailor.com/blog/dice-reviews-summary-pros-cons)
- [Evnt Central: how the Ticketmaster queue actually works](https://evntcentral.com/blog/how-the-ticketmaster-queue-actually-works)
- [Ticketor: gate control and e-ticket validation](https://www.ticketor.com/Account/Blog/Gate-control-and-e-ticket-validation)
- [Big Tickets: ticket scanning and gate entry for high-volume events](https://terrapin.bigtickets.com/event-ticketing-platform/blog/ticket-scanning-gate-entry-guide/)
- [Ticket Fairy: ticket scanning app](https://www.ticketfairy.com/event-ticketing/ticket-scanning-app)
- [Ticketmaster: timed-entry ticketing solutions](https://business.ticketmaster.com/timed-entry-ticketing-solutions-ticketmaster/)
- [Ticketure: immersive experience ticketing and timed entry](https://ticketure.com/who-we-serve/immersive-experiences)
- [TicketSpice: timed entry admission software](https://www.ticketspice.com/features/timed-entry-event-ticketing-software)
- [TicketSignup: ticket event financial reporting](https://info.ticketsignup.io/2026/08/24/ticket-event-financial-reporting/)
- [RSVPify: best event ticketing platforms 2026](https://rsvpify.com/best-event-ticketing-platforms-2026/)

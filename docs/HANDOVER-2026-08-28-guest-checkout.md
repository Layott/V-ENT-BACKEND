# Handover, 28 August 2026: buying a ticket without an account

CEO, in session: "Hope people can get tickets without having to create accounts
on the website, but they'll need to submit emails and Maybe full name and
number. Or better still, the organizer decides what fields he wants to be
collected."

Branch `feature/guest-checkout` in **both** repos, cut from
`feature/entry-requirements`. Not pushed, no PR yet, not deployed.

---

## What is built

**A signed-out visitor buys a ticket.** Email, quantity, and whatever the
organiser chose to ask. Signing in is offered after the form rather than in
front of it, because a sign-in wall at the top of a checkout is the thing this
exists to remove. `/events/find-ticket` brings a ticket back with the email and
the code together, no account involved.

**The organiser composes the questions.** `EventCheckoutField` holds label,
kind (`text`, `phone`, `number`, `choice`, `checkbox`), help text, required,
options, `per_ticket` and order. Edited on the manage page's Billets tab. Three
fixed columns could not have covered it: a five-a-side needs a shirt size, a
conference needs a dietary requirement, a convention needs to know which day.

**Email is not in that list and cannot be.** A ticket with no way to reach the
holder has no receipt, no code to re-send, and nothing to attach to an account
later. Making it optional is the one setting that breaks everything after the
sale, so it is not offered.

---

## The four faults found while walking it

All four were invisible to the tests. All four are the same shape as the ones
this ticketing work keeps turning up: the thing was built, it was green, and
nobody had looked at it through a browser.

### 1. The questions were asked of guests only

They are configured on the **event**, so they belong to every buyer of that
event. `buy_ticket` created tickets with no `answers` at all. The door would
have had a shirt size for half the queue and nothing for the other half, and
nobody finds out until the shirts are printed.

Fixed by giving both checkouts one shared component,
`src/components/checkout-fields/CheckoutFields.js`. `buy_ticket` now collects
the same answers and refuses a missing required one **before the wallet moves**,
returning `FIELD_REQUIRED` with the field id, the same envelope guest-buy uses.
`test_a_refusal_costs_nothing` pins the ordering.

### 2. A phone answer never reached the column anything reads

The number landed in `answers` keyed by field id. Right for the export, useless
to the door list, a cancellation, or the organiser's own "so we can reach you on
the day" written under the field, all of which read `Ticket.attendee_phone`.
`checkout.phone_from()` bridges it in both paths. An explicitly-given number
still wins over a phone-shaped question.

### 3. The confirmation was destroyed the instant the sale succeeded

`onDone` on the parent set `guestTier` to null, which unmounted `GuestCheckout`
including its own "here is your code" screen. **The ticket was issued and the
buyer saw nothing.** The confirmation now stays until they close it; `onDone`
re-reads the tier counts instead, so the remaining figure is not stale.

### 4. Which checkout you got depended on a race

`session?.user?.sessionToken` is falsy while the session is still resolving, so
a signed-in member who clicked quickly got the **guest** form: asked for an
email they had already given, and handed a ticket detached from the account they
were logged into. The control now waits for `sessionStatus`.

### Smaller ones, same walk

- The tickets tab told a signed-out visitor the payment comes from their V-ENT
  wallet. They do not have one.
- The door list had no column for the answers, so the sizes were collected and
  invisible, and it drew a bare `@` with nothing after it on every guest row.
- `/events/manage` with no event spun on "Loading..." forever:
  `if (!token || !eventRef) return;` never cleared a flag that starts `true`.
  This is the third time that exact shape has appeared, see
  `feedback_admin_pages_hang`.
- Guest inputs measured 43px at 390px, one short of a thumb; the `select`
  ignored the padding and sat at 40.

---

## Money

A paid guest ticket is created in `guest_verify`, on confirmation, **never** at
initialize. A ticket that exists before the money does is a ticket somebody can
screenshot. Idempotent on `Ticket.payment_reference`, because the browser return
and the Paystack callback are two arrivals for one payment.

`guest_lookup` requires the email **and** the code. The email alone would let
anybody read somebody's bookings.

`claim_for(user)` attaches guest tickets on that address when an account is
**verified**, not when it is created, so an unverified address cannot claim
them. Wrapped so a failure to attach never breaks the verification: a stranded
ticket is a support question, a failed verification is somebody locked out.

---

## Verified how

- **799 backend tests pass.** 9 new in `vent_event/tests_same_questions.py`
  covering both checkouts, both refusals, per-order answers copied onto every
  ticket, and the phone bridge on both paths.
- **Chrome, signed out**, French: bought VT-A4HTJPML as `chidi@example.test`,
  `user=None`, answers stored, count moved on the page, confirmation held, found
  again at `/events/find-ticket` with email + code, refused with a different
  email on the same code.
- **Chrome, signed in** (session minted locally, no password typed): the wallet
  modal draws the same questions, refuses with "Shirt size est obligatoire."
  before the payment step, and stores the answers on purchase.
- **Chrome at a real 390x844.** `resize_window` does nothing here because the
  window is maximised; a same-origin iframe sized 390x844 gives a genuine 390px
  viewport that media queries answer to. All eight manage tabs, the scanner, the
  schedule and the door list: no page overflow, and the 716px attendee table
  scrolls inside its own container. This retires the earlier I5 abandon, which
  had blamed the tooling for something that was only ever the maximised window.
- `tools/endpoint-callers.py`: 225 endpoints, no new orphans.
- `pnpm lint`: no errors; only warnings that predate this work.

---

## Still open

- **I6: deploy and confirm live.** Nothing here has been pushed or deployed.
  Both branches need a PR and the CEO's word before merging.
- **A real Paystack card run.** J4 is covered with the gateway stubbed. The live
  path needs the real key on the deployed site, which is I6's job.
- `/events/attendees/page.js` still reads `searchParams.get('id')` as a fallback
  beside the slug route. It is a fallback rather than a link anybody follows, but
  it is the slug rule's exact wording and should go.

## To pick this up

```
cd V-ENT-BACKEND && DB_ENGINE=sqlite DEBUG=True ./venv/Scripts/python.exe manage.py runserver
cd V-ENT-FRONTEND && pnpm dev   # 3001
```

Seed used: event `lagos-anime-con-2026`, Standard tier priced 0 for the free
path. `demo_organizer` was added as `EventManager` role `door` on that event so
the door list could be walked; harmless, but it is test data.

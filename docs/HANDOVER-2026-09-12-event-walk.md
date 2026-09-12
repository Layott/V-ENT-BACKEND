# Handover, 12 September 2026 (evening): the whole event feature, walked in every role

Inbox row 260. Gates: `V-ENT/gates/34-event-end-to-end.md`. Branches:
`V-ENT-BACKEND` `fix/event-walk-12-sept`, `V-ENT-FRONTEND` `fix/event-walk-12-sept`.
Merge waits for the CEO.

CEO's ask, verbatim: "Can we go through cretaing an event, using eery available
feature, testing it as an organizer, a suer, influencer and every way possible,
we test every single feature page and several scebarios like several people
buying simultaneously, or maybe people buying stuff from the vendor shop and the
vendor seeing it in realtime, stats working as it should, every button and
page, sub page works well and even every single role works and se things as
they should. chek out the flow of the ui and the design in as much detail as
possible, then fix everything wrong or hat might even have potental to cause
any issues." And mid-walk: "when i said full test, i also meant claude chrome
full UI test also. not jsut code."

## 1. The one decision only the CEO can make: the size of a coin

One coin is 1,000 naira (`NGN_PER_COIN`), and every naira-to-coin conversion
floors. Three consequences the walk measured on one event, all correct
arithmetic and all wrong for the business:

| What | Seen |
|---|---|
| A 1,500 naira ticket type charged ONE coin from a wallet | fixed: prices that are not whole coins are refused where they are set (`vent_event/pricing.py`, code `PRICE_NOT_WHOLE_COINS`, same code and fields billing already used) |
| The platform's 10% fee on 60 coins of sales came to **0 coins** | not fixable without a decision: 10% of a 1 to 3 coin ticket floors to 0 on every purchase. The Money tab says "V-ENT takes 10% of each ticket" and "owed to you 60 of 60" on the same screen |
| The influencer's 10% commission on a 3 coin sale came to **0 coins** | same cause. The influencer's new screen says "0 VC owed" under "10% of each ticket" |

Options, in order of how much they change:

1. **A smaller coin** (`NGN_PER_COIN=100` or `=1`): every wallet balance,
   price and ledger line is re-based by a migration. A 500 naira drink can
   then exist. Biggest change, cleanest result.
2. **Fees and commission kept in kobo** on the ledger line (a Decimal column)
   and paid out when they reach a whole coin. Wallets stay whole coins.
3. **Accept it** and make the fee rules say so ("fees apply on tickets of
   10 coins and over").

Nothing in this branch decides it. What it does is refuse a price the wallet
cannot pay, so nobody is silently undercharged again.

## 2. Faults found and fixed (backend)

| Fault | Where | Test |
|---|---|---|
| Tier PATCH ignored early bird, group and access code fields (pricing panel saved nothing) | `views_tiers.py` | `tests_tiers.py` (+5) |
| A 1,500 naira price charged 1 coin | every price door: create-event, tier create/patch, slot create/patch, product create/patch | `tests_whole_coins.py` |
| Venue room raced across tiers (two tiers, one seat, both sold) | `views_tickets.py` locks the Event row | walk `--rush` |
| A seat somebody was paying for at Paystack was sold to the next guest | `availability.in_flight_on_tier`, 20 minutes | `tests_in_flight.py` |
| Free guest path issued without re-checking room under the lock | `views_guest.py` | `tests_in_flight.py` |
| Product create dropped the picture, the choices and deliverability (the screen PATCHed two of them in afterwards) | `views_vendors.py` `read_variants` | `tests_vendor_shop.py` ProductCreateCarriesEverythingTests |
| Stall page addressed by event slug was a 500 (`event_id=<slug>`) | `views_vendors.vendor_detail` | StallPageBySlugTests |
| Shared-ticketing address with a tournament slug was a 500 | `views_linking.set_shared_ticketing` | `tests_tournament_entry.py` |
| The stall's Contact box showed "Message sent" with no request behind it | new `POST /event/vendor/<slug>/contact/`, a notification to the stallholder, once a minute | ContactTheStallTests |
| No influencer-facing view existed | new `GET /event/referrals/mine/` (`referrals.mine_for`) | walk `--influencer` |
| The tier card showed the list price after early bird ended while the checkout quoted the new one | `serialize_tier` sends `price_now_vc`, `early_bird_ended` | `tests_tiers.py` |
| my-events reported a door steward as "manager" and counted transferred tickets as sold | `views_promos.my_events` | `tests_promos.py` (+2) |
| Two names for one refusal (`NOT_WHOLE_COINS` vs billing's `PRICE_NOT_WHOLE_COINS`) | unified on the existing one | `tests_whole_coins.py` |

Suite: `manage.py test --parallel 4` = 4073 tests OK (skipped 1).

## 3. Faults found and fixed (frontend)

| Fault | Where |
|---|---|
| Stall page: fake Contact; Add to cart and Pay rendered live for a stranger; a hoodie in sizes could not be bought (no option picker); no delivery choice though the API takes one; `evt_2000` default event id | `events/vendor-shop/vendor/page.js` rewritten in those parts, keys in en/fr/pt |
| Stall index: selector matched on id while the page is addressed by slug (showed the wrong event); mount rewrote `/events/<slug>/vendor-shop` to `?id=` and mounted the page twice | `events/vendor-shop/page.js` |
| A stale session cookie turned a PUBLIC event page into "Your session expired" (seen on the emulator) | `SessionExpiryGuard.js` stays on a public page; gated list moved to `src/lib/gatedRoutes.js`, read by middleware, the guard and `check-signed-out` |
| The event console's refresh loop put "Loading..." over the whole console every 30 s, unmounting the form somebody was typing in and wiping the refusal they had just been shown | `events/manage/page.js` `load({quiet})`; catcher added to `check-live-updates.mjs` (part 3, "loud refresh", 7 fixtures) |
| A refused Add ticket type cleared the form (and four more `.then(() => set...)` sites) | `events/manage/page.js` keeps the form; the refusal scrolls into view |
| Console inputs 150px tall (`flex: 1 1 150px` inside a column) | `manage-event.module.css` |
| Red underline on the event page tabs, the one tab strip on the site with one | `view-event.module.css` |
| Tap targets under 44px on the event page, shop, stall and find-ticket (measured on the phone: 34 to 39px) | four stylesheets |
| After giving a ticket away the open card still read "Active" with a live "Check myself in" | `events/my-tickets/page.js`; status words translated (`Given away`) |
| my-events offered a door steward Edit and the console, both refused; the console drew 15 tabs above its refusal | `events/my-events/page.js`, `events/manage/page.js` |
| Stall product price placeholder was 2500, which the new rule refuses | `my-stalls/[slug]/page.js`, with the rule said under the field |
| No influencer screen | `events/my-events/page.js` "Links you sell through": visits, sold, owed, paid, the link to copy |
| `check-signed-out` read `"/tournaments"` out of a COMMENT in middleware and exempted the whole tournament tree | reads `gatedRoutes.js`; two components got an explicit `useViewer` guard |

## 4. What was walked in Chrome, and on the phone

Desktop (Chrome, signed out): /events, the event page and its five tabs, the
stall page and every control on it, the stall index, find-ticket, the short
link, run-of-show, the self check-in page. Signed in through the app's own
`external-token` provider with the walk's fixture accounts: the organiser
(my-events, all 15 console tabs, a ticket type refused for price, a settlement
run, an announcement sent, edit-event saved, the door list, the scanner),
the buyer (checkout to "Ticket secured", my-tickets, a transfer), the vendor
(my-stalls, the stall, an order moved to ready), the influencer (the new
section), the door steward (my-events, the refused console, the scanner).

Android emulator (`scripts/phone-measure.py`, new: drives the phone's Chrome
through DevTools and asks each page for overflow, clipped controls and tap
targets under 44px): every signed-out page above, 0px horizontal overflow on
all of them after the fixes. D2 measured there: an order placed from another
account appeared on the vendor's open stall page after 5 seconds with no
reload (`navigation entries: 1`).

Accounts: `walk_organiser`, `walk_buyer_ada`, `walk_vendor_bisi`,
`walk_influencer`, `walk_door`, password `walk-con-2026`, local sqlite only.

## 5. Left open, named

- The coin decision (section 1).
- The scanner's copy "No gate name set. Add ?gate=Main to the address" asks a
  steward to edit a URL. A gate name field on the scanner would be better.
- The Edit page's "Manage this event" hub is a 12-card grid; it works, it is
  the bento shape the design rule names.
- `check-embeds` reports `/events/v-ent-lagos-meetup-2026` og:image cannot be
  fetched: seeded data pointing at a remote image, not this branch.
- The organiser's Money tab and the influencer's screen both say "10%" next
  to a 0. That is the truth until section 1 is decided.
- Two `useCallback` warnings (`tt` missing from deps) pre-date this branch.

## 6. How to run the walk again

```
cd V-ENT-BACKEND
DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_event.py --setup --coverage --buyers
DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_event.py --rush          # needs the dev server on 8000
DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_event.py --vendor --stall-rush --influencer --door --numbers --roles
cd ../V-ENT-FRONTEND
../V-ENT-BACKEND/venv/Scripts/python.exe scripts/phone-measure.py http://localhost:3005/events/<slug>   # emulator up, adb on PATH
```

Every section is rerunnable on the same event. `--setup` makes a fresh one.

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

## WHERE THINGS STAND, end of 13 September 2026 (read this first)

Two branches, both `fix/event-walk-12-sept`, both pushed, both waiting for
the CEO to merge. Nothing on them is deployed.

- **BE #180** (5 commits, head `394615a8`): the event walk fixes; the naira
  ledger and 5% + 100; the seller chooses who bears it and stalls pay it; every
  price on the dashboard plus `tools/check-pricing.py`; tournaments pay
  organisers and prizes come from the pool; withdrawal fee 1%.
- **FE #190** (5 commits, head `a9c24ac`): the screens for all of the above.
- Merge order: FE #190 can go first or second; nothing on it breaks against
  the old backend except the new Money tab and the withdraw quote, which show
  an error state rather than a wrong number. BE #180 needs FE #190 for the
  organiser screens. The peer's BE #178 / #179 are separate and unmerged.

**Inbox rows this covers:** 260 (walk), 261 (5% + 100), 262 (bearer, stalls),
263 (dashboard + checker), 265 (1% payouts), 266 (tournament fee). All done
in `V-ENT/tasks/inbox.md`. **Next: row 264**, "people can still buy stuff
directly on the platform without having to buy V-ENT coins, that option must
always be available": a card (Paystack, naira) path on every purchase door.
Today only the guest ticket checkout has one; signed-in tickets, stall
orders, vendor pitches, premium, memberships, tournament entries and comic
chapters are wallet-only. Inventory first, then gates/39.

**What deploying BE #180 does, and what to tell people:**

1. Migrations: vent_event 0046, 0047, 0048; vent_auth 0082; vent_anime 0002;
   vent_tournament 0052. All additive or backfills; none destructive.
2. Nothing to set on the dashboard. Production stores no `platform_fees`
   (checked over ssh on 13 September), so the code defaults apply: tickets
   and stalls 5% + 100, tournament entries 5% + 100, memberships 5%,
   marketplace 0, comics 0, withdrawals 1% + 0, payout minimum 5 coins, daily
   payout ceiling 500 coins, top-up ceiling none, premium not on sale.
3. **Behaviour changes people will notice:** (a) every ticket, stall sale and
   tournament entry now carries the fee, absorbed by the seller unless they
   put it on the buyer; (b) **prizes are no longer minted**: they come out of
   the entries, and what the entries did not bring in comes out of the
   organiser's wallet at distribution, refused with the numbers if the
   wallet cannot cover it, so an organiser of a free tournament with a prize
   pool must hold those coins; (c) payouts land 1% lighter, said on the
   withdraw screen and in the approval email; (d) the withdraw screen stops
   promising "2% + N50", which the server never took.
4. Local sqlite differs from production in one way worth knowing: it has
   `payout_min_vc: 0` and an old `tournament_fee_pct` STORED from earlier
   saves of the old dashboard blob; production stores nothing.

**Checkers added this run** (all in `check-all`, all blocking):
`tools/check-pricing.py` (every price has a default, a field and a reader,
and is a number nowhere else; self-test 9/9), three new `check-parity` rows
(earnings, settle, fee bearer on both sides), `check-live-updates` part 3,
`check-signed-out` reading `gatedRoutes.js`. Tests added: `tests_ledger`,
`tests_vendor_fee`, `tests_pricing_settings`, `tests_withdrawal_fee`,
`tests_topup_ceiling`, `vent_anime/tests_fee`, `vent_tournament/tests_entry_fee`,
`tests_in_flight`, `tests_whole_coins`. Suite: 2454 in tournament + event +
admin + pricing; the money apps 2374 earlier the same day.

**Gates files:** `V-ENT/gates/34` (20/20), `35` (8/8), `36` (8/8), `37`
(11/11), `38` (10/10). Ticked with `tools/gate-run.py` (the bundled
`gate-check.mjs` runs cmd.exe and cannot).

**Local fixtures on this machine** (sqlite only): `walk_*` accounts, password
`walk-con-2026`, PIN 2468; `walk_admin` is super_admin with 2FA stamped;
`walk-con-ea5e` (event), `walk-cup-fee` (30 VC entry, fee on the player, one
entry paid), `walk-cup-finished` (paid, prizes paid from the pool). Tokens
rotate: `walk.person('name')` in `tools/walk_event.py` refreshes one. Sign a
Chrome tab in with `/auth/external?token=<t>&username=<u>`; the console also
needs `localStorage.adminToken` and the `adminToken` cookie set to the same
token. The emulator froze once today and needed `emulator -avd evotv_test
-no-snapshot-load` again; the phone Chrome signs in through the NextAuth
callback POST (`scratchpad/phone_admin_walk.py` shape).

**Open, named:** the register review step draws `$` and a "40 vent coins"
placeholder; `link embeds` debt needs the dev server up to check; two
`useCallback` warnings pre-date the branch.

## 0. Decided the same evening: "V-ent takes 5% + N100 of all tickets sold" (row 261)

The CEO answered section 1 with the fee rule, not a coin size. So the ledger
now holds NAIRA and coins are what a wallet receives:

- `EventLedgerEntry` gained `amount_ngn`, `gross_ngn`, `fee_ngn`,
  `fee_flat_ngn` (migration 0046, which backfills existing lines from their
  coins). `amount_vc` stays as the whole-coin floor for coin screens.
- The fee is `price x 5% + 100 naira` per PAID ticket (`ledger.fee_for`),
  free tickets none, both numbers admin settings (`ticket_fee_pct` 5,
  `ticket_fee_flat_ngn` 100, both stamped on the line at the sale). The admin
  settings page now has both fields; it had neither.
- A settlement pays each person the whole coins their naira has reached and
  writes the remainder out of the run and into an open carry line, so "paid"
  reads exactly the coins that moved and nothing under a coin is lost. The
  influencer's 300 naira on a 3,000 naira ticket waits for the next payout
  instead of being zeroed.
- Who pays it: a guest paying naira at Paystack with the fee on the buyer pays
  price plus fee exactly (`channel=naira` on the quote, `buyer_pays_fee`). A
  wallet buyer pays the price in coins, because 2,200 naira is not a whole
  number of coins, and the fee comes out of the organiser's share for that
  sale. The Money tab and the buy panel say so.
- **After deploy:** open Admin > Settings and confirm the ticket fee reads
  5 and 100. Production may hold a stored `ticket_fee_pct` of 0 from before,
  and a stored value wins over the default.

Measured: Money tab on a walk event before payout `31,350 NGN (31 VC)` owed,
after `Paid 31 VC`, `Already paid 31,000 NGN`, `350 NGN (0 VC)` waiting, fee
`3,350 NGN` = 5% + 100 on every paid ticket (the walk checks the rule against
every ticket). Guest checkout on a 3,000 naira ticket with the fee on the
buyer: `Service fee (5% + 100 naira a ticket) 250 NGN`, `Total 3,250 NGN`.

## 0b. 13 September: who bears it is the seller's choice, and stalls pay it too (row 262)

CEO: "The organizer decides if they want to handle the cost or they want people
buying the tickets to, same for vendors, should have a fee on everything sold.
You can check production yourself to be sure there's no stored value."

- **Production checked** over ssh: `AdminSetting` has no stored `platform_fees`,
  so 5 and 100 apply the moment the backend deploys. Nothing for anybody to set.
- **Who bears it, one rule for tickets and stalls** (`ledger.split_fee`): a
  card payment adds the whole fee; a wallet payment adds the WHOLE COINS of it
  on top (the buyer sees the number before the PIN) and the part under a coin
  comes off the seller. Yesterday's version put all of a wallet buyer's fee on
  the organiser; this is closer to what the CEO said. The ledger line records
  `buyer_fee_ngn`.
- **Stalls**: 5% + 100 naira on every unit sold (`price_basket`), stamped on
  the order (`fee_ngn`, `fee_pct`, `fee_flat_ngn`, `fee_bearer`, `buyer_fee_vc`,
  `vendor_ngn`, `vendor_paid_vc`). The stallholder chooses on their stall page
  (`Vendor.fee_bearer`, PATCH `my-stalls/<slug>/`). The stall is paid the whole
  coins its naira has reached at each order and carries the rest
  (`Vendor.carry_ngn`); the stallholder taking their own stock pays no fee.
- **A quote for the cart**: `POST /event/vendor/<slug>/quote/`, the same items
  payload and the same function as the order, so the cart's fee line is never
  the screen's arithmetic.
- **A cancel refunds**: until now a cancelled order kept the buyer's coins and
  the stock stayed sold. It now refunds the buyer in full, takes back what the
  stall was paid for it (refused with `CANNOT_REFUND` if the stall's wallet no
  longer holds it), trims the carry, and restocks. "Cancel and refund" is on
  the stallholder's order rows.
- Migration 0047 backfills old orders (paid in full, no fee) and old buyer-borne
  ledger lines.

Measured in Chrome: the stallholder's chips; a buyer's cart `Service fee (5% +
100 naira a unit) 1 VC, Total 26 VC` on a 25,000 naira hoodie; the order row
`Fee 1,350 naira (1 VC of it paid by the buyer), yours 24,650 naira, 24 VC
paid`; the stall's totals `Sold 54,000 / fee 3,100 / yours 51,900 / paid 51 VC
/ waiting 900`. The walk checks, on every stall: buyers paid = kept + fee,
fee = 5% + 100 on every unit, kept = coins paid + carry.

## 0c. 13 September: every price is set on the dashboard, and a checker holds it (row 263)

CEO: "the 5% + NGN100 is something admins should be able to set on the admin
dashboard, they should be able to set what prices it is now for any premium
feature or option and it updates everywhere on the platform, please create a
checker for this that makes sure it applies each time a new feature is built
or added that has pricing."

What was true before a line was written: the dashboard carried eight money
controls and FOUR changed nothing. `payout_min_vc` showed 0 while the real
minimum was 5, read from `PAYOUT_MINIMUM_VC` in the environment.
`topup_max_ngn_per_day` and `withdrawal_fee_pct` had no reader at all.
`tournament_fee_pct` could not have one: an entry fee is collected by the
platform and prizes are paid out of it, so there is no seller to take a
percentage from. The withdraw screen told people "Withdrawal fee (2% + N50)"
and drew a net payout from `calcWithdrawFee` in `walletHelpers.js`, while the
server sent the whole amount. Billing read `subscription_fee_pct`, which was
in no defaults and on no screen, falling back to the ticket rate.

The rule, now written at the top of `DEFAULT_ADMIN_SETTINGS`: a platform rate
or price has a default there, a field on the admin settings page, a reader in
the code that charges it, and appears nowhere else as a number.

- **The list** (`platform_fees`): ticket_fee_pct 5, ticket_fee_flat_ngn 100,
  subscription_fee_pct 5, listing_fee_pct 0, anime_fee_pct 0,
  withdrawal_fee_pct 0, withdrawal_fee_flat_ngn 0, payout_min_vc 5,
  payout_daily_max_vc 500, topup_max_ngn_per_day 0; `premium`:
  price_vc_monthly, price_vc_yearly. `tournament_fee_pct` is gone (see above;
  the CEO can reverse this the day tournaments pay organisers).
- **Readers wired**: `payouts.limits()` reads the two payout keys (the two
  environment variables are deleted from settings.py; the tests set the
  dashboard value with `AdminSetting.put`). `payouts.fee_on(amount)` prices a
  withdrawal in naira off what the bank receives (a coin is 1,000 naira, so a
  fee under a coin taken in coins would round to nothing), stamped on the
  request as `fee_pct, fee_flat_ngn, fee_ngn, payout_ngn` (migration 0082);
  the admin queue and the approval email say `payout_ngn`; an unstamped older
  row is sent whole. `GET /auth/wallet/withdraw/quote/?amount=` is what the
  withdraw screen now asks; `calcWithdrawFee` is deleted.
  `topup_initiate` refuses over the daily ceiling with `OVER_TOPUP_LIMIT` and
  the numbers beside the code (pending rows count; checked before Paystack is
  asked). `vent_anime.money` takes `anime_fee_pct` in whole coins off the
  author's credit and stamps `fee_vc` on the chapter row (migration 0002).
  `vent_billing.charging.platform_rate` reads its own key with no fallback.
- **The settings endpoint checks money before storing it**: a value that is
  not a number at or above zero, or a key nothing reads, is refused with
  `PRICE_NOT_A_NUMBER`, `PRICE_NEGATIVE` or `UNKNOWN_PRICE`. Before this it
  deep-merged anything, and one stray string would have broken every sale.
  `merged()` also drops money keys not in the defaults, so an install that
  once stored `tournament_fee_pct` cannot post it back.
- **The page**: two cards, "What V-ENT takes" (7 fields) and "Limits" (3),
  plus Premium; one field per key, no `?? 5` fallbacks (the server always
  serves every key); inputs 44px on a phone. Copy in en, fr, pt. The Money
  tab's wallet note was still describing the pre-262 rule and now says the
  split rule.
- **The checker**, `tools/check-pricing.py`, in `check-all` as a blocking
  catcher: every money key in the defaults has a `patch('<section>', '<key>'`
  on the admin page and a reader in backend code (found by following the
  variable that holds `merged().get('platform_fees')`, or a helper that
  returns it); every key read is in the defaults; no `* 5 / 100`, `* 0.05` or
  `FEE_PCT = 5` in backend money code; no rate or naira amount in frontend
  copy beside a fee word (the coin unit, 1,000 naira, is allowed), no
  `fee_pct ?? 5` and no `* 0.02` in a helper. `--self-test` is 9 fixtures,
  one per fault plus two clean ones. It read 14 real problems on this tree
  before the fixes and 0 after; calibrated by reading all 14.

Measured in Chrome and on the emulator: typed 7 and 150 on the dashboard,
pressed Save, and with no restart the tiers endpoint and the organiser's Money
tab read 7% + 150; set the withdrawal fee to 2 + 50 and the withdraw screen
quoted `Service fee (2% + 50 naira) -N250, You will receive N9,750` on both
screens. Rates put back afterwards. Local sqlite had `payout_min_vc: 0` STORED
(an earlier save of the old page posted the whole blob), so on this machine
the minimum reads 0 until an admin sets it; production stores nothing, so 5
applies there on deploy.

**Left open**: the withdrawal fee defaults to 0 + 0 because that is what the
server has always done; whether V-ENT wants the 2% + 50 the old screen
promised is the CEO's call and is now one field. The tournament fee is not a
thing until tournaments pay organisers.

## 0d. 13 September: 1% on payouts, and tournaments pay organisers a share (rows 265, 266)

CEO, on the payout fee: "what do you suggest for withdrawal fee 1% seems fine
to me right?" Answer: yes, 1% and nothing flat. The smallest payout is 5
coins (5,000 naira), so 1% is 50 naira there, which covers a Paystack bank
transfer (about 10 to 50 naira) at every size, and the platform already took
5% + 100 on the sale. The default in code is now 1 + 0, so it applies on
deploy; the dashboard field changes it without one.

CEO, asked whether tournaments should pay organisers a share of entry fees so
the tournament fee on the dashboard means something: "i want it".

What was true before: an entry fee left the player's wallet and reached
nobody, and prizes were minted to winners at distribution out of nothing, on a
free tournament as much as a paid one. Handing entries to organisers while the
platform kept minting prizes would pay every prize twice, so the two moved
together. **This is a behaviour change on production tournaments: from this
deploy, a prize is paid out of what the entries brought in, and what they did
not bring in comes out of the ORGANISER'S OWN WALLET at distribution.** A free
tournament with a 100 VC prize pool needs an organiser holding 100 VC when the
prizes are paid, and the confirmation says so before the press.

- **One ledger.** `EventLedgerEntry` and `EventSettlement` take a tournament
  as well as an event (migration vent_event 0048); a line names the entry
  that paid it (`registration`) or the prize it paid (`prize`). `balances()`
  and `settle()` take either record. `tournament_fee_pct` 5 and
  `tournament_fee_flat_ngn` 100 are on the dashboard and read by
  `ledger.tournament_fee()`; `quote_entry(tournament)` is the one function
  the register step, the join endpoint and the ledger lines read.
- **The entry.** A paid registration debits entry + the whole coins of the
  fee when `Tournament.fee_bearer` is 'player' (editable on the console's
  new Money tab; migration vent_tournament 0052), writes the organiser line
  and the platform line, and stamps the rate. `GET /tournament/<ref>/entry-
  quote/` is public, and the Confirm Payment step shows `Entry 30 VC +
  service fee (5% + 100 naira) 1 VC` from it. A refused debit now rolls the
  registration row back too: it used to commit inside the atomic block and
  leave an unpaid entrant holding a slot.
- **Refunds.** Both cancel paths (organiser and admin) reverse the entry's
  own lines and refund what that player paid, fee included, whatever the
  price is now; the admin path no longer skips team entries.
- **Prizes from the pool.** `prizes.plan()` reports `pool_ngn`,
  `from_pool_vc`, `from_wallet_vc`, `organiser_balance_vc` and adds
  `pool_short` to problems. `distribute()` debits the organiser's wallet for
  the shortfall in whole coins (rounded up) before any winner is paid, writes
  a top-up line and a negative organiser line per prize, and refuses with
  `POOL_SHORT` (402 at the console, a notification on the scheduled path)
  when the wallet cannot cover it. The test fixture that every prize test
  builds on now gives the organiser the 1,500 coins its prizes need.
- **Payout.** `GET /tournament/<ref>/earnings/` (entries, refunded, fee and
  who bore it, prizes, top-up, owed, paid, runs) and `POST
  /tournament/<ref>/settle/`, organiser or admin only. The console's Money
  tab draws them with `Pay me out`. `check-parity` holds three rows for it.
- **Numbers, walked.** Walk Cup Fee: 30 VC entry, player paid 31, ledger
  organiser 29,400 / platform 1,600 (1,000 of it the player's), payout 29 VC
  with 400 naira carried. Walk Cup Finished: four 20 VC entries built a
  75,600 naira pool; prizes 150 VC took 75 from it and 75 from the wallet
  (100 -> 25), 600 naira left.

**Left open**: the register review step (`review-team/Review.js`) still
draws a `$` sign and a "40 vent coins" placeholder for the entry fee, from
before the wallet existed; the payment step is where the number is decided
and it is right, but that review line should read the quote too.

## 1. What the CEO was asked, and how it was answered

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

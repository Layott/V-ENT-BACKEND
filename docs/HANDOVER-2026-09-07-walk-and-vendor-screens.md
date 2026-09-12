# 7 September 2026: the walk, and the vendor screens

Waves A and B of `gates/16-everything-left.md` (inbox rows 162 to 166). Both
verified. Waves C, D and E are still open and named at the bottom.

The waves were deliberately NOT worked in the order they were asked, because
the walk had to come first: two sessions running, it is the walk that has found
what nothing else could, and what it finds changes what is worth building next.
That held again.

## Wave A: the walk found four dead fetch paths

Every one of them compiles, lints, renders and dies inside a `catch`. That is
why four survived.

**1. The region setting has never worked.** `money.js` fetched
`/auth/settings/`. There is no such route. It answered 404 on every page load,
inside a `try` whose own comment says a failed preference lookup must never
stop a date rendering - so it failed silently, for ever, and `setAppRegion`
never received the saved timezone, date format or currency.

The visible symptom was the Currency and region panel storing an answer and
changing nothing on screen: exactly what the CEO reported it for, one level
further up, and still true after it was "fixed". Correct path is `/setting/`.

**2. Both profile history tabs have always been empty.** Tournaments and Events
on every profile fetched `/auth/user-activity/tournaments/` and
`/auth/user-activity/events/`. Neither route existed. Both built now, in
`vent_auth/views_user_activity.py`, and shaped to what the panels already read
rather than to what would be tidiest - changing two correct screens to fix a
fault entirely on the other side would be the wrong way round.

**3. The whole OAuth session-handler chain was orphaned.** `backend-callback`
redirected to `/auth/session-handler`, a route that does not exist. That page
fetched `/api/auth/get-oauth-data` while the folder is spelled
`get-oath-data`. It then posted to `/auth/verify-oauth-token/`, which the
backend does not serve. Nothing anywhere linked into it. 171 lines deleted;
social sign-in goes through NextAuth and is untouched.

**4. The redirect routes read the wrong shape.** An event nests under
`data.event`; a tournament sits directly on `data`. Reading only the nested one
sent every tournament to the fallback listing - a redirect that looks like it
worked. All seven read either now.

### The catcher

`V-ENT-FRONTEND/scripts/check-api-paths.mjs` joins every route Django serves
against every literal path the frontend fetches. 7 self-test cases both
directions, blocking, in `check-all`. `tools/dump-routes.py` regenerates the
route list so it cannot drift into checking a stale snapshot.

Calibrated once already: a base-URL helper (`` `${API}/tournament` ``) is a
prefix somebody appends to, not an address, and reporting it says a real route
is missing when nothing is. Every Django route here ends in `/`, so the absence
of one is the signal.

### Also proven by pressing rather than reading

- **Two-factor**: the toggle produced a real QR (a 9918-byte data URL) and a
  real per-account secret rather than the RFC test vector; Enable stayed
  disabled until six digits were in; a genuine code was accepted; the badge
  went to Enabled; `UserTOTP.confirmed` is True and `challenge_required` is now
  True. A password alone no longer signs that account in.
- **Timezones**: 420 zones, 12 groups, Africa first, the reader's own pinned,
  live UTC offsets. Date-format values now match `DATE_ORDER`.

## Wave B: the vendor screens

The stall was built on 6 September and `endpoint-callers.py` said the next day
what nobody had noticed: **nothing on the site called any of it.** Somebody who
bought a pitch got a stall they could not stock.

### Built

- `/my-stalls` - the stalls you run, linked from the account menu, which is the
  one place that lists what somebody RUNS.
- `/my-stalls/<slug>` - two tabs, because a stallholder is doing one of two
  jobs at any moment: setting the table up, or getting orders out.
- Backend `vent_event/views_vendor_shop.py`: my stalls, stall detail and
  opening hours, product edit and delete, orders, and fulfilment.
- **Variants**: a list of names on the product, not a row per variant. What a
  stall needs is "which size did they ask for" written on the order.
  Per-variant stock is a warehouse, and this is a table.
- **Delivery**: chosen by the BUYER at checkout, because that decides whether
  an address is needed at all. The address lives on the ORDER, never read off a
  profile - somebody may want a parcel sent to an office or a friend, and
  quietly using a profile address is how a package goes to the wrong place with
  nobody having typed anything wrong. `sent` and `delivered` are separate,
  because a stallholder can only ever know the first.

### The money bug Wave B found, which was pre-existing

**`return` inside `transaction.atomic()` COMMITS.** Every refusal after the
wallet debit in `create_order` therefore kept the buyer's money, while the
response said "Nothing has been taken from your wallet". That had been true of
the seller-has-no-wallet branch since it was written. My delivery check landed
in the same place and a test caught it: 2 VC missing from a refused order.

Refusals raise now (`Refused` / `_refuse`), so the transaction unwinds by
construction rather than by whoever writes the next check remembering to put it
above the debit. Three tests guard the class: the wallet is untouched, no
`Transaction` row is written, and no order or stock movement survives.

One follow-on worth knowing: the first fix wrapped only the transaction, and
three refusals above it - vendor missing, stall closed, empty basket - then
raised into nothing and turned clean 400s into 500s. The guard covers the whole
function now. A guard that covers part of a function is a guard somebody will
step outside.

### And a layout fault only the walk could show

The page shell used a flex row beside `<Sidebar />`. The sidebar is FIXED, so
the content rendered underneath it and the title was invisible. Every other
page offsets with a margin per breakpoint; copied from `events/my-tickets`.

## Verified

- Backend suite: **2744 tests, OK, zero failures.**
- `pnpm build`: exit 0.
- `check-all`: every blocking catcher clean, one debt row (the em dashes).
- `endpoint-callers`: "378 endpoints, 301 called, 18 known orphans, 59
  deliberate. No new ones."
- Chrome, desktop and 390x844: no horizontal overflow, every control 44px,
  order marked sent and the database changed to match.

## Still open

| Wave | What |
|---|---|
| C | 2826 em/en dashes in docs; 202 cosmetic CSS classes |
| D | invites by email (145), vendors by email (146), managers without an org (147), org type in the console (149), follower counts on screen (150), deep metrics (151) |
| E | who bears the fee, affiliates that pay, ticket transfer, a settlement run |

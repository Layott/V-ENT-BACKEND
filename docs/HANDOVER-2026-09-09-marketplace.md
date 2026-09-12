# Handover, 9 September 2026, second half: Vermillion City (row 250)

The gate file is `gates/29-marketplace-gated.md` and carries the evidence line
by line. This is the shape of it, what is proven, and the two switches somebody
will need to find.

## Where it stands

**Built, walked, merged and DEPLOYED CLOSED.** CEO chose "Merge and deploy, stay
closed" at 22:45.

* V-ENT-BACKEND#169 merged, live at `f847c658`
* V-ENT-FRONTEND#182 merged, live at `117681c`
* `vent_marketplace.0001_initial` applied on production: six tables
* every marketplace route on api.v-ent.co answers **503 MARKETPLACE_OFF**

A dump was taken first: `db-2026-09-09-2245.sql.gz`, 182 tables.

## The two switches, and neither opens it alone

1. `MARKETPLACE_ENABLED` in the server `.env`. **Absent on production**, and it
   defaults to `'0'`. Every endpoint refuses while it says so.
2. `feature_flags.marketplace_enabled` in the admin console's Modules panel.
   False.

`/auth/platform/modules/` publishes the **AND** of the two, and the navigation,
the pages and the endpoints all read that single answer. `/marketplace` was
removed from the hardcoded `COMING_SOON_ROUTES` list for exactly this reason: a
third switch is how a nav ends up saying Coming Soon over a page that works.

**To open it:** set `MARKETPLACE_ENABLED=1` in `/srv/vent/backend/.env`,
`sudo systemctl reload vent-api`, and turn Marketplace on in the console. Set
`platform_fees.listing_fee_pct` in the same console if V-ENT is to take a
commission; it is 0 until somebody sets it.

## The shape, so nobody rebuilds it

**One `Listing`, not three.** A service, a swap and a sale share an author, an
address, a price, media, messages, reports, reviews, bids, a lifecycle and an
expiry. `catalogue.py` holds what differs, and the create form draws itself from
the same table the server validates against, so the form cannot ask for
something that will be dropped.

**`holds.py` is the only writer of a purchase's status.** hold, release, refund,
dispute. A second writer is a second answer to who has the money. The tests are
about balances rather than status codes, and one asserts the coins are never in
two places at once.

**Nothing duplicates a table that exists.** Messaging a seller is `Conversation`
and `DirectMessage`. Reporting is `UserReport` with a `marketplace:` context, so
it lands in the queue an admin already reads. Premium is `vent_auth.premium`.
The commission is `platform_fees.listing_fee_pct`, which had sat in the settings
defaults since they were written with no reader.

**Two decisions worth keeping.** Accepting a bid moves no money: it tells the
bidder they may buy at that price, and they still have to commit. And a review
requires a RELEASED purchase, enforced by `Review.purchase` being a required
OneToOne.

## What is proven, and how

* **62 new tests**, 2413 across the three apps, OK.
* `check-all` clean, no debt risen. `endpoints with no screen` went to debt when
  the API landed and back to zero when the screens did.
* **Walked with the switch ON**, locally: created a listing through the form,
  found it as a second account, pressed Buy, read the quote (800 to pay, 40 to
  V-ENT, 760 to the seller), confirmed, and watched the buyer go 5000 to 4200
  with the seller unchanged. Pressed "It arrived": the seller went 361 to 1121.
  Left a five star review and saw it on their record.
* **Walked with the switch OFF**, on production: `/marketplace/create` has no
  form on it. `document.querySelector('#mkTitle')` is null.
* **On the phone** at 412 CSS px: zero horizontal overflow; 18 controls
  measured under 44px and were fixed and re-measured on the device.

## What is NOT proven

* **Nothing on production has ever run with the switch on.** The open walk was
  entirely local. The first time it is opened is worth watching.
* **No money has moved on production.** Escrow was exercised on local sqlite
  only.
* The commission rate is 0 on production, so no purchase has ever been
  discounted by a fee there.

## What is NOT built, deliberately

* **Bulk listings** and **hoisting a listing for a fee**. The column exists
  (`hoisted_until`) and the browse page already sorts and labels a hoisted
  listing; nothing sets it, because setting it means charging for it and there
  is no product decision on the price.
* **The wishlist notifier.** A wishlist row is saved and nothing sweeps new
  listings against it yet. That is a management command and a cron line, and it
  is the first thing to build here.
* **Listing expiry** has `listings.expire_due()` with a test and no command
  calling it. Same shape as the wishlist sweep: both belong in one cron job.

## Next

1. Row 249 left one thing behind: an **admin console control for `is_premium`
   and `premium_note`**. Until it exists, premium is a shell command and nobody
   on production has it, so every premium feature shipped today is visible only
   as its refusal.
2. The marketplace's own cron: expire due listings, sweep wishlists.
3. Open Vermillion City when the CEO says.

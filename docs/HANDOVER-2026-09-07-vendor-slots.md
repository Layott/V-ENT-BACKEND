# 7 September 2026: selling vendor pitches

> "event owners should be able to sell vendor slots to other people, they can
> list prices for vendor slots with their rules and conditions and other users
> should be able to buy and use the site to run the shop or they can invite
> people too" - CEO (inbox row 160)

Built and verified. `gates/15-vendor-slots.md`, 17 gates, 16 met and one
abandoned in writing.

## The shape, and why it matters

A stall is CLAIMED two ways: bought, or invited. After that both are the same
thing - somebody running a shop with stock, prices and orders.

That is the whole design decision. Build the two routes apart and the invited
vendor and the paying vendor get different products, and one of them stops
being maintained. So buying creates an ordinary `Vendor` row, and everything
downstream cannot tell which door somebody came through.

The payoff was immediate and measurable: `test_one_vendor_cannot_touch_another_stall`
passed with no new code, because the existing ownership check already covered
the bought route.

## What was added

**`VendorSlot`** - the OFFER. Name, description, category, `price_ngn`,
quantity, sold, `rules`, `rules_version`, `requires_approval`, `is_active`.
Two models rather than one because the offer outlives any single sale ("Food
stall, 3x3m, 5 available" is one row that becomes five stalls) and because a
slot exists before anybody has bought it, which a stall cannot.

**`VendorSlotPurchase`** - the sale, and what was agreed. Its own row rather
than columns on `Vendor`, because a stall can also arrive by invitation and
half the payment columns would be empty on those.

**Endpoints** under `/event/<event>/slots/`: list (public), create, edit,
withdraw, and `.../buy/`.

**Screens**: a `Vendor pitches` tab in the event console (organiser lists and
edits), and a `Trade at this event` section on the public Vendors tab.

## Three decisions worth keeping

1. **The rules are stored word for word at purchase**, with a version, not as a
   pointer to the organiser's live text. An organiser editing their conditions
   afterwards must not change what somebody already agreed to.
   `rules_version` bumps only when the wording changes, and a test asserts
   editing the name does NOT bump it - or a stored acceptance points at a
   version that means nothing.
2. **A sold slot is withdrawn, never deleted.** Somebody paid for it and their
   stall points at it; deleting the row takes their record with it. The button
   says "Take off sale" rather than "Remove" when anything has sold.
3. **The money follows `create_order` exactly**: both wallets `select_for_update`
   locked in one transaction, PIN checked against the stored hash, and a
   REFUSAL when the seller has no wallet rather than a debit with nowhere to
   put it. A platform that takes money and works out where it goes later ends
   up owing somebody an amount nobody recorded.

## Verified

- `vent_event.tests_vendor_slots`: **28 tests, OK, first run.** Written on the
  consequence: `test_the_buyer_can_then_actually_run_the_shop` and
  `test_the_money_reaches_the_organiser`. A 201 alone would pass against an
  endpoint that took the money and created nothing.
- Full backend suite: **2712 tests, OK, zero failures.**
- `pnpm build`: exit 0.
- `check-all`: every blocking catcher clean.
- **Proven on the running server**, not only in tests: demo_bisi bought the
  pitch, went 5000 to 4950 VC, the organiser went 250 to 300, the stall
  `mama-t-kitchen` was created owned by them, they listed "Jollof rice" stock
  40 in it, and the slot went 4 remaining to 3.
- Chrome, desktop and 390x844. Modal 344x472 inside 390x844, Buy button 44px,
  `scrollWidth == clientWidth`, no horizontal overflow.

## Two things the checks caught that I would have shipped

- **`var(--v-ent-grn)` does not exist.** I took it from the CLAUDE.md quick
  reference; `globals.css` defines `--v-ent-success`. Undefined, so both
  primary buttons would have rendered with no background at all. Caught by
  `check-css-vars`.
- **Chrome autofilled the stall-name box with the signed-in person's email
  address**, because a bare text input above a password field reads as a
  username box. Nothing about the markup looks wrong; only the walk found it.
  Fixed with `autoComplete="off"` and a real name/id.

## Not done, and named

**C.3, the invitation-by-email half.** That is inbox rows 145 and 146 -
invitations by email, including to people with no account yet - and it is a
whole feature rather than a detail of this one. Not started.

What this work DID establish is the half that makes it cheap: a stall claimed
by invitation and a stall claimed by purchase are already the same object, so
the invite route only has to create the `Vendor` and everything else already
works.

Also still open from the earlier batch: row 147 (managers without an
organisation), row 151 (deep metrics), and the UI halves of rows 149 and 150.

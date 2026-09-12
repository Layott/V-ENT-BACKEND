# Handover, 10 September 2026: granting premium from the console

Row 251 in `tasks/inbox.md`. The CEO's words:

> "Nobody on production has premium, so every premium feature that shipped today
> is visible only as its refusal. There is still no console control to grant it
> - it is a shell command. That is the first thing I would build next. Build it"

Gates: `gates/30-premium-console.md`, every box ticked with evidence except D4
until the deploy lands.

## What was actually wrong

`is_premium` and `premium_note` shipped on 9 September as columns on `Users` and
on `Organization`, through `PremiumMixin`. `vent_auth/premium.py` reads them in
eight places. NOTHING wrote them except a Django shell on the box, so eight paid
features were live on production in a state where the only thing anybody could
see was the refusal.

## What was built

**Backend**

| File | What |
|---|---|
| `vent_auth/premium_admin.py` (new) | `apply_premium(holder, on=, note=, admin=, kind=)`. The ONLY writer. Truncates the note at 200, clears it on revoke, writes the AdminAction, returns `{is_premium, premium_note, changed}` |
| `vent_auth/views_admin.py` | `admin_set_premium`, PATCH `/auth/admin/users/<id>/premium/`. Refuses `PREMIUM_TRUE_FALSE_REQUIRED` when the field is absent and `NOTE_REQUIRED` when granting with nothing said. `is_premium` added to the LIST row and both fields to the detail. `status=premium` filter |
| `vent_auth/views_admin_orgs.py` | `_row` carries `is_premium` and `premium_note`; new `set_premium` action, guarded by `grant_premium` rather than by the view's own `MANAGE_ROLES` |
| `vent_auth/decorators.py` | `'grant_premium': {'super_admin', 'finance_admin'}` |
| `vent_auth/tests_premium_console.py` (new) | 21 tests |

**Frontend**

| File | What |
|---|---|
| `admin/users/[id]/page.js` | PREMIUM row in the summary beside ROLE and KYC, Give premium / Take premium back, and the modal that takes the reason in the same press |
| `admin/organizations/[slug]/page.js` | the same pair, plus a PREMIUM card beside HOLDS, MEMBERS and TYPE |
| `admin/users/page.js` | Premium in the status filter, and a PREMIUM badge on the row |
| `i18n/dictionaries.js` | 13 keys x en, fr, pt |

## The thing the walk found

The `status=premium` filter was written on the backend and the select on the
users list had five options, none of them Premium. The endpoint answered from
the day it was written and nothing could ask. That is exactly the fault
`endpoint-callers` exists to catch, and it does not catch it, because the
endpoint HAS a caller: what was missing was a value of a query parameter. Worth
knowing when reading that checker's zero.

Also fixed in passing: the organisation's premium note was rendering through
`shared.metricLabel`, which is uppercase, so a sentence somebody typed was read
back at them in capitals. It has its own quieter style now.

## How it was proven

Signed in through the real front door as `demo_organizer` (password, then the
authenticator code) into the console at SUPER ADMIN. Locally that account was
given `admin_role='super_admin'` and a confirmed `UserTOTP`, with the code
computed from `vent_auth/totp.py` rather than an app.

BEFORE, on `/tournaments/naija-free-fire-weekly-12/manage`, the requirement
picker ended with a locked block:

> ON A PREMIUM ACCOUNT - Be under a penalty point limit, Be inside or outside a
> ranking position - Ask a V-ENT admin to turn premium on for this account, and
> these become available on every tournament you run.

AFTER the press, that block is gone and both are ordinary options in the list.
That is the refusal the CEO named, ended by a press in the console.

On the emulator at 412 CSS px, both controls were pressed with
`adb shell input tap`, the note was typed on the device keyboard, and the
summary read `PREMIUM / ON Device walk 10 September`. `scrollWidth` equals
`innerWidth`, so nothing overflows, and both buttons are 44px tall.

A role without the permission (`mod_admin`) sees no control at all and the same
session calling the endpoint directly is answered `403
DO_NOT_PERMISSION_PERFORM`. The PREMIUM row still READS `ON` for them:
permission decides what you may do, never what shape the data has.

## What this does NOT do

- **Nobody can buy it.** This is the giving-away control. Selling premium is
  `vent_billing`, which is row 242's open question and still has no feature flag.
- **A revoke asks for no reason.** The AdminAction records the note the account
  HAD (`was_note`), so the history survives, but nobody is asked why they took it
  away.
- **No expiry.** Premium is on until somebody turns it off. A grant "for the
  Rivalry season" is a sentence in a note, not a date the system enforces.

## Deploy

Two files added, four changed, and NO migration: the columns shipped on
9 September. So this deploy carries no database change at all.

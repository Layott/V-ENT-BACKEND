# Handover, 9 September 2026: the last orphan endpoints, and deleting things

CEO: "/unlazy please build and fix all things in gate and inbox and all that is
left, let them be fully walked and tested."

Ledger: `V-ENT/gates/26-everything-left.md`. Sections A and B are closed,
walked in Chrome, and open as PRs. Not merged: see the end of this file.

---

## A. No backend code without a screen (rows 246, 248)

The count started at **17**, held in a baseline that made the checker report
nothing, and is now **0**.

```
cd V-ENT && python tools/endpoint-callers.py
432 endpoint(s) checked, 0 with no screen
```

Eleven got a caller, ten got a reason. What was built today:

| Endpoint | Where it lives now |
|---|---|
| `wallet/cards/charge/` | top up with a saved card, `/wallets/topup` |
| `resend-forgot-password-token/` | a resend on `/reset-email` |
| `team/transfer-ownership/` | "Hand the team over", `TeamRosterManager` |
| `tournament/rule-presets/` | the rules editor offers every format's standard set |
| `tournament/<ref>/stages/`, `stages/set/`, `stages/<id>/advance/` | `StagesPanel`, on the console's Brackets tab |
| `event/vendor-orders/` | `VendorOrders`, under the tickets on `/events/my-tickets` |

Named DELIBERATE with the reason, rather than given a screen:

* `send-code/` - the old six digit signup code. Verification is a link now.
* `tournament/<ref>/league-rules/` - see the next section; it is superseded.

### The one that was a real bug

**There were two models for what a win is worth.** The rules editor wrote
`TournamentRuleset`; the league table read `LeagueRules`. An organiser could set
five points for a win and watch the table keep paying three, with nothing
erroring anywhere. `set_tournament_rules` and `reset_tournament_rules` now write
through to the `LeagueRules` row, `ordered_tiebreakers()` drops any name the
table does not know, and four tests hold it
(`vent_tournament/tests_rules.py::RulesReachTheTableTests`).

### Stages, which nothing had ever read

The backend has composed tournaments out of stages since the format catalogue
learned `can_feed_into`, and no screen ever asked. Every tournament on the
platform was one format start to finish, and anybody running groups into a
playoff made two tournaments and copied the names across.

`StagesPanel` reads the plan, composes it, and closes a stage. Two decisions
worth keeping:

* It **names who goes through before the press.** It reads the standings, works
  out the top N (per group when there are groups) and lists them. Advancing is
  irreversible from that screen and a playoff of the wrong size is invisible
  until somebody is missing from it.
* Sentences are **built on the client**. `stages.summary()` writes English
  inside Python, which cannot be translated.

---

## B. Deleting an event or a tournament (row 244)

CEO, on a screenshot of My Tournaments: "there should be a way for peopl to
delete events, of course it is a soft delete thata dmins should be able to
restore or still check".

Before today the only deletion on the platform was `delete-draft/`, which
called `tournament.delete()` and destroyed the row, and refused anything
published. An event could not be deleted at all.

### How it is built

`vent_auth/softdelete.py` holds the whole mechanism. Both `Tournament` and
`Event` gained the same three columns and the same three managers:

```python
objects = LiveManager()          # cannot see a deleted row
all_objects = models.Manager()   # the admin console and restore
deleted_objects = DeletedManager()
class Meta:
    base_manager_name = 'all_objects'
```

**A manager rather than a filter at each read site**, because there are 122
places that query a tournament or an event, and a rule that has to be
remembered at 122 sites is a rule that holds at 121.

`base_manager_name` is the unfiltered manager **on purpose**: without it a
deleted event would not disappear from view, it would break every row pointing
at it. A test proves a ticket still reaches the event it was sold for.

### The refusals are the feature

`softdelete.deletion_guard(paid, unpaid, confirmed)`, one rule for both models:

* **somebody paid** - refused outright, whatever is confirmed, naming
  cancel-and-refund as the thing to do instead. A confirmation box is not
  consent from the person who paid.
* **people are in it, nothing paid** - asks a second time and says how many.
* **empty** - goes ahead.

### Deleted has a bucket

`status=deleted` on both admin lists, `_tournament_status` and `_event_status`
read `deleted_at` first, and every row carries `deleted_at`, `deleted_by` and
the reason whichever tab it is in. Without a bucket a deleted row is not
filtered out of a tab, it is unreachable from the console entirely, which is
the same shape as the fault where three cancelled tournaments vanished in
August.

### Restore is admin only

Deliberately not the organiser's. Somebody who can delete and undelete at will
can hide something and put it back with nothing recorded in between. A test
holds the refusal on both models.

### Proof

```
DB_ENGINE=sqlite venv/Scripts/python.exe manage.py test vent_tournament.tests_soft_delete vent_event.tests_soft_delete
Ran 30 tests ... OK

DB_ENGINE=sqlite venv/Scripts/python.exe manage.py test vent_auth vent_event vent_tournament
Ran 3139 tests in 438s ... OK (skipped=1)

python tools/check-parity.py
21 capability pair(s) checked, 0 built on one side only
```

Two existing tests had to be told about the new columns: both apps hold an
audit that every column settable at creation is editable afterwards, and
deleting is an action with its own endpoint rather than a field on a form.

---

## What is NOT done

* **The Android emulator.** The walk was Chrome on the desktop and a real
  412 CSS px viewport in an iframe. The emulator pass is still owed.
* **Migrations are local only.** `vent_event/0045` and `vent_tournament/0049`
  have run against the sqlite dev database. Production has not been touched.
* Gates C through H are open: the admin console audit, subscriptions, the
  studio walk, the rest of the inbox, the tap-target debt, and shipping.

## Files

New: `vent_auth/softdelete.py`, `vent_tournament/views_delete.py`,
`vent_event/views_delete.py`, both `tests_soft_delete.py`,
`src/components/delete-control/`, `src/components/tournament-manage/StagesPanel.js`,
`src/components/vendor-orders/`.

Changed: `views_rules.py` (write-through and the formats payload), both admin
list views, both `models.py`, both `urls.py`, `tools/check-parity.py` (five
rows), `tools/endpoint-callers.py` (two DELIBERATE entries),
`RulesEditor.js`, `my-tournaments/page.js`, `my-events/page.js`,
`my-tickets/page.js`, `manage/page.js`, both admin consoles, `dictionaries.js`
(62 keys x 3).

---

## The walk, and the four faults it found

Walked on `localhost:3005` as `demo_organizer` and then as `demo_temi`
(super admin), desktop and 412 CSS px. Nothing here was found by reading code.

| Found | Where |
|---|---|
| The hand-over list offered the OWNER their own name | the roster calls the owner a `member`, so `m.role !== 'owner'` never excluded them. Filtered by `sameUser` against the session now |
| `/reset-email` showed TWO resend affordances | the older one was a link back to `/forgot-password`, which resends nothing and makes somebody retype the address they just gave. It now says "Wrong email address? Start again" |
| A deleted tournament still offered Edit, Score, DQ and Announce | announcing to the entrants of a deleted tournament is a message nobody can explain. The events console already hid these |
| The saved-card refusal printed Paystack's own sentence | "Authorization code is invalid" reached the screen. `api.CHARGE_FAILED` and `api.GATEWAY_ERROR` exist in three languages now |

Two catchers also had to be repaired before `check-all` was honest:

* **check-user-chips** reported a name inside a `<select>` option. HTML says an
  option holds text and nothing else, so a chip cannot go there: a false
  positive, and a checker with those is one somebody eventually satisfies by
  breaking working code. Its self-test now carries the exception AND the case
  that must still be caught, and writing the second fixture exposed a real gap
  in the first attempt at the fix.
* **check-stale-gates** ran a gate whose own CHECK is `check-all`, which runs
  check-stale-gates, which runs check-all. It hit the 300 second timeout and
  reported a BREACH on a file with nothing wrong with it.

## Shipped to a PR, not to production

* Layott/V-ENT-BACKEND#165
* Layott/V-ENT-FRONTEND#180

**Not merged and not deployed.** This one runs two migrations against the
production database (`vent_event/0045`, `vent_tournament/0049`), so it waits on
the CEO. The migrations are additive: three nullable columns and a manager
change, no data rewritten.

## Things a next session should know

* `pnpm build` failed with `Cannot find module .../next/dist/bin/next`. The
  store was gutted again. `pnpm store prune`, delete
  `node_modules/.pnpm/next@*` and `node_modules/next`, then
  `pnpm install --force`. `--force` on its own has never fixed it.
* The Chrome walk could not type into a background tab: CDP clicks land, keys
  do not. Setting a React input needs the native value setter plus an `input`
  event, and pressing a button is `.click()` on the element rather than a
  coordinate, because the coordinate frame is the screenshot's, not the page's.
* `demo_organizer`'s local TOTP row was deleted so the walk could sign in. It
  is a seed account on the dev sqlite database; production is untouched.

# 7 September 2026, second pass: the events and orgs batch

> "what if everything I sent about events. orgs I sent earlier." - CEO

Read as: build the batch captured as inbox rows 145 to 157. Seven of the twelve
are done and verified; the rest are named at the bottom with what is left.

## Done

### Row 152: the venue clock, reversed

> "isnt this bad, let fix it so everyone see venue timing in their own time,
> except they set a timezone in their profile ... and it should be same for
> date."

`render()` in `src/lib/datetime.js` renders in the READER's zone always - their
saved preference, else the browser's guess. The venue-clock exception I had
written is gone.

The zone LABEL stays. That is the part that stops somebody arriving late: an
Accra reader sees "09:00 GMT" for a Lagos door that opens at 10:00 WAT, which
is the same instant said in the language of their own watch. `formatInZone`
survives for the two places that genuinely need a venue's own clock - a run of
show and an operator's rundown, read by people standing in the venue.

### Row 153: every timezone, and a setting that was doing nothing

The picker held **eleven hand-typed zones**. Anybody in Abidjan, Kinshasa,
Casablanca, Dhaka, Sao Paulo or Auckland could not say where they were.

`src/lib/timezones.js` now offers every zone `Intl` knows (~420), grouped by
region with Africa first, each labelled with its live UTC offset, and the
reader's own detected zone pinned at the top. There is a fallback list for a
browser without `supportedValuesOf`.

**And a second fault found while there.** The date-format options carried the
values `DMY`, `MDY`, `YMD`, `long`, while `DATE_ORDER` in `datetime.js` is
keyed on `DD/MM/YYYY`, `MM/DD/YYYY`, `YYYY-MM-DD`. Every lookup missed. The
setting saved fine, returned fine, and changed no date anywhere - the exact
class of fault this panel was reported for in the first place, still present
after it was reported.

Also: saving a zone now applies **immediately** rather than on the next reload.
`setAppRegion` writes to a module value that nothing re-renders on, so the
toast said "saved" while every date carried on showing the old zone.
`publishRegion` in `money.js` bumps a context value alongside it.

`scripts/check-timezone-picker.mjs`, 5 cases both directions, in `check-all`.

### Row 154: the 2FA, which was theatre

The Security panel's switch wrote `two_factor_enabled: true` into a settings
JSON blob. The QR in the modal was **sixty rectangles at pseudo-random
positions**. The "or enter this key manually" code was `JBSWY3DPEHPK3PXP`, the
RFC test vector. Nothing generated a secret, nothing read the code box, and no
`UserTOTP` row was ever created.

The LOGIN half has been correct for months - `challenge_required`,
`pending_payload`, `spend_code` - and every admin uses it daily. There was
simply no way for a member to enrol. Reading the switch from real enrolment
(the right thing to read) then made it flip back to Disabled, so the honest
half looked like the bug.

Built `vent_auth/views_twofactor.py`: start, confirm, disable, status. The QR
is drawn from the real provisioning URI. Turning it OFF costs a code, because a
stolen session must not be enough to remove the protection that exists because
sessions get stolen. An admin cannot turn it off at all, refused by name.

18 tests, and the one that matters asserts the CONSEQUENCE: after confirming, a
password alone stops signing you in. A test that only checked a 200 would have
passed against the fake toggle.

### Row 156: one answer to when an event happens

`start_date`/`end_date` were commented "canonical" while `starts_at()` and
`ends_at()` - which every window, door and reminder is computed from - read the
LEGACY trio. Nothing reconciled them.

`save()` now derives one from the other, and **the side that CHANGED wins**.
That last part matters: my first version preferred `start_date` unconditionally
and silently REVERTED any edit that touched only the trio. Three self check-in
tests caught it. `from_db` remembers the loaded values so an edit can be told
apart from a no-op.

Two traps hit on the way, both now tested:
- `save()` runs before Django coerces field values, so
  `create(start_time='19:00')` reaches it as a string and `datetime.combine`
  refuses. **132 tests failed on this.** Each field converts its own value
  through `to_python` now.
- `update_fields` has to gain the derived columns or they are computed and
  dropped, which is the trap the slug helper documents.

11 tests.

### Row 157: the duplicate routes

They were never duplicate IMPLEMENTATIONS.
`/events/[slug]/manage/page.js` is fifteen lines importing `ManageEventContent`
from `/events/manage/page.js`. One component.

What was duplicated is the ADDRESS: `/events/manage?id=12` still rendered the
whole page, so a primary key was reachable in a URL. Deleting it is wrong too -
the slug rule says every address a thing has ever had keeps working, and these
have been shared.

Seven bare routes now resolve the record, learn its slug, and `router.replace`
to the named address. `src/components/legacy-id-route/LegacyIdRoute.js`.

### Row 149 (part): organisations have a type

`org_type` with six choices and `capabilities()` on the model, in the payload.
A club that only fields a squad is no longer asked about ticketing. The
capability table lives on the model so the console and the frontend read one
table rather than each keeping a copy. Default `mixed`, because claiming to
know what an existing organisation does is worse than saying it has not been
said. **The console UI does not use it yet.**

### Row 150 (part): followers for teams and people

Organisations had `OrgFollower`. Teams and people had **nothing** - no table,
no endpoint, no count, no list.

One `Follow` model for both kinds rather than `TeamFollower` and
`UserFollower`, because following is one concept and two tables drift. `Teams`
was defined twice in this codebase once and it took a migration to unpick.
`follower_count()` and `is_following()` are the shared helpers, so the day
organisations move into `Follow` those are the only places that change.

Endpoints under `/auth/follow/`, addressed by slug or username. Counts are on
the org, team and user payloads. 20 tests. **The screens do not draw them yet.**

### Row 148: tix and selar, researched

`tasks/research/tix-and-selar.md`. Short version: V-ENT is not behind on
ticketing, and several things here (holds, per-day capacity, sessions, door
lookup with undo, checkout questions, tournament linking) are past what a
general platform offers.

The real gaps are all about **money moving to other people**:
1. **Who bears the fee.** tix's headline is that an organiser can pass the
   commission to the buyer. V-ENT has no fee-bearer concept at all. Smallest
   change, highest value.
2. **Affiliates that pay.** `EventReferral` already tracks who sent a sale and
   can owe them nothing.
3. **Ticket transfer.** Today somebody hands over a screenshot.
4. **A settlement run** rather than a one-at-a-time payout queue.

Not worth copying: Selar's courses and memberships (different company), custom
domains (takes people off V-ENT), multi-currency settlement (a compliance
problem wearing a feature's clothes).

Both sites 403 automated fetches, so this is from their help centre, blog and
third-party comparisons. **The CEO's own screenshots were in an earlier message
and are not on disk here** - worth re-sending, because whatever they noticed
specifically will be in those.

## Not done

| Row | What | Where it stands |
|---|---|---|
| 145 | Every invite takes an email, including people with no account | not started |
| 146 | Vendors invited by email, full shop control, delivery | not started. `Vendor` gained a slug today for row 157 |
| 147 | Manage events and tournaments WITHOUT an organisation | not started |
| 149 | Org type in the CONSOLE | model and payload done, UI not |
| 150 | Follower counts ON SCREEN | API done, screens not |
| 151 | Deep metrics: clicks, opens, taps on buy, vendor visits | not started |

## Verified

- Backend suite: **2684 tests, OK, zero failures** (`vent_auth vent_team
  vent_event vent_tournament`). Up from 2635 this morning: 49 new tests across
  two-factor enrolment (18), follows and org types (20), and the event date
  model (11).
  On the way there this pass produced **132 errors and 3 failures**, all of my
  own making and all fixed: the string-coercion trap and the revert-the-trio
  bug described above. Both are now tested.
- `pnpm build`: exit 0, twice (after the timezone/2FA work and after the
  redirect routes).
- `python tools/check-all.py`: every blocking catcher clean.
- en, fr and pt written by hand for every new key.

## NOT verified

**None of today's frontend work has been walked in Chrome.** The timezone
picker, the date-format fix, the real 2FA modal and the seven redirect routes
are proven by tests, checkers and a passing build - not by a walk. That is the
next thing to do, and it is where the last two sessions found faults nothing
else could see.

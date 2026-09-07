# Handover, 8 September 2026: maps, tabs, and things that were already built

Continues `gates/17-the-rest.md`. Written while working, not after.

Everything below is on PR **BACKEND#164** and **FRONTEND#179**, both updated.

---

## The theme of the day: three things were already built and unreachable

This kept happening, and it is worth naming as a class:

| Reported as | Actually |
|---|---|
| "I don't see where to manage vendors" | Vendor pitches was a tab, on a strip that scrolled sideways with the scrollbar HIDDEN |
| "run of show shouldn't only be for Google sheet" | Typing a run of show was fully built; the editor only draws once a sheet exists and the only way to get one was to import |
| Row 88, "the operator panel is the open half" | The four-slot Layers panel exists and is complete |
| Row 78, real-time pages | Built 6 September; I measured it wrong by grepping only for the hook names |

**The lesson: a capability with no way in is not built.** Three of these had
working endpoints, working components and no door. `endpoint-callers.py`
catches an endpoint with no caller; nothing catches a COMPONENT with no
entry point, and that is where these hid.

---

## The map (row 193)

An organiser who typed "Landmark Centre, Victoria Island, Lagos" got "There is
no map of this venue." Only a pasted Google Maps LINK was ever read.

`geo.geocode` now asks OpenStreetMap; `Event.save()` calls it when there is an
address and no pin, never overwriting one somebody set. Two faults found while
building it, both about being WRONG rather than absent:

1. **Nominatim dislikes commas.** It answers nothing to the address a human
   types and answers correctly to "Landmark Centre Lagos". Hence a ladder of
   forms, most specific first. The first version returned None for every real
   Lagos venue while the service had the answer.
2. **It fuzzy-matches.** "Eko Convention Centre" came back as "Calabar
   International Convention Centre, Lagos-Calabar Coast" - 600km away, sharing
   only words half the venues in Nigeria share. So an answer must corroborate
   the DISTINCTIVE words. That alone was still not enough: a bare "Landmark"
   then matched a feature in Nunavut at 70.9, -111.2, and it corroborated,
   because the word really was in the answer. Corroboration cannot bound
   geography; a country can. The search is bounded to the countries V-ENT runs
   in unless the address names another.

A wrong pin is worse than no pin, because nobody checks a map that looks
confident.

`manage.py geocode_events` backfills; dry by default, one request a second.
16 tests, none touching the network.

---

## The ledger hole (row 178)

The write-back loop iterated `debt`, which holds only checkers that FAILED. A
checker reaching 0 exits 0, is classed clean, never enters that list, and its
row freezes at whatever it last failed with. **Climbing back from 0 to 21 then
compared against 21 and reported "unchanged"** - twenty-one real breaches could
have returned in silence.

Every checker is recorded now, clean ones at 0 and NOT parsed: exit 0 is exact
where reading the first integer off "650 route(s) checked, 0 fetch(es) that go
nowhere" reads 650. `compare()` was pulled out of `main()` so it can be asked a
question without running 22 subprocesses, and `tools/test-ledger-writeback.py`
proves both directions.

---

## The affiliate promise with no mechanism (row 180)

The earnings screen said "it is paid the day they make an account" and nothing
made that true: `payee` could only ever be set to an account that already
existed, so nobody could arrive later to claim. The money would have accrued
for ever.

`payee_email` plus `claim_pending` now attach the link **and its open ledger
lines** - attaching the link alone leaves lines naming no user, which every
settlement skips. Walked end to end.

---

## Blocked, measured rather than remembered

- **A5, the VPS cron.** SSH is blocked from this machine and the box is NOT
  down: v-ent.co answers 200 in 2.0s while a direct connect to
  138.68.126.199 on 22, 80 and 443 all time out. The cron line to paste is in
  `gates/17-the-rest.md` N.1, and the backup script it runs is the fixed one.
- **Q3, AFC sign-in.** Their site answers 200; `/oauth/authorize` and
  `/api/me` both 404. Our side is built and tested against a stub.

---

## Still open

Rows 172 (rest of the research), 187 (which fake accounts to remove - needs the
CEO's call), 189 (merge and deploy), 190/191/192 (team and org wallets, the
admin dashboard, VENT WALLET - specs in `tasks/specs/`, and the CEO ordered
these after everything above ships).

Nothing is deployed. Both PRs are open and updated.

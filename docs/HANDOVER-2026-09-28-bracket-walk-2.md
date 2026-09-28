# Handover, 28 September 2026: the second bracket walk

Inbox 289. The CEO asked whether every button, page and sub page of the new
bracket systems had been verified end to end as every kind of user. It had
not: the first walk (27 Sept, gates/42) pressed groups into a playoff and
Swiss into double elimination, and 13 things were never pressed in a browser.
The CEO said "Go". Gates: `V-ENT/gates/44-bracket-walk-2.md`. Walk log:
`V-ENT/tasks/audit/bracket-walk-2-2026-09-28.md`. Branch `fix/bracket-walk-2`
in both repos. Walk data: `tools/walk_brackets_2.py --setup` (local sqlite).

## Status, 28 Sept after the usage-limit reset

COMMITTED AND PUSHED, NOT MERGED, NOT DEPLOYED: BE #185 (79e281ea), FE #193
(b63ebd1), both on `fix/bracket-walk-2`. W2-34 (running order both names)
done. Full backend suite 4374 OK; check-all clean on both commits.

The relay continuation could not edit the shared checkout (background-session
isolation), so the work was copied byte for byte into worktrees at
`LAYO/CLAUDE/V-ENT-WT/V-ENT-BACKEND` and `.../V-ENT-FRONTEND` (branch
`wt/bracket-walk-2`) and committed there. The shared checkouts still hold the
same changes uncommitted on their local `fix/bracket-walk-2`; once the PRs
merge, `git switch main && git pull` there after discarding those (they are
identical to what merged), and `git worktree remove` the two worktrees.

Memory: an orphaned frontend dev server (node, 9.5 GB) was the memory hog;
stopped. Stale dev builds (5.1 GB) cleaned by check-stale-builds --clean.

Still open: P13 production walk (after deploy, so it walks the fixed code);
CEO decisions: deploy, and D-1 (a paid no-show keeps no refund today). Also
seen: an event page answers 500 while the API is down (links-embed check,
`/events/v-ent-lagos-meetup-2026`), not bracket work, recorded for later.

## Status as of writing (usage limit hit, 28 Sept ~02:30)

P1 to P12 walked; every fault W2-1 to W2-32 fixed in the working tree of both
repos on branch `fix/bracket-walk-2`, NOT COMMITTED, NOT PUSHED, NOT DEPLOYED.
Tests: vent_tournament 1483 OK; vent_auth.tests_admin_dashboard TournamentConsoleTests OK;
new: ByesNeverSkipAMatchTests, ChangingAFinishedResultTests, ClubSideTests,
DisputeStateTests (all fail on the old code). Frontend: check-keys 0 missing,
dict-parity 0 missing, check-datetime 0 new (checker widened), lint clean.

LEFT TO DO, in order:
1. RunningOrder lists fixtures by username (W2-34): use full name + handle.
2. Run the full backend suite and check-all; commit both repos (branch check),
   push, open PRs.
3. P13 production walk (a real staged tournament created, drawn, a result, removed).
4. Ask the CEO: deploy? and decision D-1 (no-show refund).
5. Update inbox 289, gates/44, memory.

Earlier line kept below.


In progress. P1 GSL, P2 home and away + two legs, P3 one-format SE and DE, P4 invited, P5 by record and random, P6 Swiss rounds 1-2 walked.

## Found and fixed

- **W2-3, serious, every knockout, staged or not.** At a draw every round-one
  bye is settled first and routed one at a time (`bracket._seat_round_one`
  then `advance.cascade` per bye). When seed 2's bye reached round two, seed
  3's bye was already terminal but seed 3 not yet placed, so
  `advance._check_walkover` saw one player and settled the match as a
  walkover: seed 2 went to the final without playing. Hits any knockout of 5,
  10, 11 or 13 entrants. Fix in `_check_walkover`: a finished feeder whose
  winner (or loser) is still on the way to this match means wait.
  `ByesNeverSkipAMatchTests` draws single and double elimination for every
  count up to 17; two of its tests fail on the old code. Production checked
  with a query: 0 matches had been skipped.

- **W2-9, serious, every format.** A finished result could be changed after
  something was built on it. Changing a knockout round-one winner after the
  final was played put the new winner into a semi-final the old winner had
  already won, and left the old winner as that semi's winner. A Swiss result
  changed after the next round was paired from it. Fix in the one result rule
  every door uses: `results.decide` -> `_still_free_to_change` refuses
  NEXT_MATCH_ALREADY_PLAYED, NEXT_ROUND_ALREADY_DRAWN, STAGE_CLOSED; the same
  outcome with a corrected score passes (except in a closed table stage). The
  organiser changes the later match first. `ChangingAFinishedResultTests` (6;
  4 fail on the old code). vent_tournament 1476 OK.

## Found, to fix

- W2-1 the saved plan summary hides placement and invited entrants.
- W2-2 the close list does not show the invited entrant joining.
- W2-4 the console header stays "live" after the final result until reload.
- W2-5 standings never say what separated two sides level on points (`decided_by`).
- W2-6 the close list says "in this seed order" when placement is random.
- W2-7 a two-leg tie takes only the aggregate; no Leg 1 / Leg 2 entry.
- W2-8 "Close Registration & Generate Bracket" stays offered after a bracket exists (409 on press).
- Frontend keys for NEXT_MATCH_ALREADY_PLAYED, NEXT_ROUND_ALREADY_DRAWN, STAGE_CLOSED.

## Also fixed after the first note (all in the working tree)

W2-10 Swiss draws column; W2-11 break note only after a real previous match
(`after_a_break`); W2-12 no-show names who; W2-13 club members see their match
and room (`plays_for`, `side_of`); W2-14 `my_dispute`, DISPUTE_ALREADY_OPEN;
W2-16 a staff-recorded result settles open disputes (`results._settle_open_disputes`);
W2-17 stage/group on admin rows; W2-18 admin override draws and penalties;
W2-19 toast names the sides; W2-20 identity check before the PIN (entry OR prize);
W2-21 CHECK_IN_OPEN points at the check-in panel; W2-23/24 banner wording and a
confirm before close; W2-25 close summary full names; W2-26 plural; W2-27/28 44px
chips and console tabs (measured on the emulator); W2-29 format heading translated;
W2-30 status names and "by" translated (`src/lib/tournamentStatus.js`); W2-31 check-in
clock through datetime.js, and check-datetime now reads `[]`/`undefined` and calls
over several lines (self-test 17); W2-32 check-in strip hidden once a bracket exists.
Open, recorded: SEO fallback descriptions English-only on fr/pt; D-1 for the CEO.

## Walk mechanics

The door scanner's service worker (`public/door-sw.js`) was registered for
the whole local site in the walking Chrome, and serves every dev chunk stale
once after an edit. Three "the dev server did not pick up my edit" moments in
this session were this. Unregister it before a walk. Production is unaffected
(hashed chunk names).

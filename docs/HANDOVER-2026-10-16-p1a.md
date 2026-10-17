# Handover, 16 October 2026 (late): 487, 488, E1 shipped; P1a shipped; explainers lane started

Read this with `HANDOVER-2026-10-16-bracket-structure.md` (the design, P0).

## What the CEO asked tonight (inbox 487 to 493)

- 487 plan save reloads six times: fixed (FE #254).
- 488 a winner who is not one of the two players: local records fixed, production clean, `check_results`
  in verify-live (BE #248).
- 489 "i aprove the design": P1 to P8 to build; P1a shipped (below).
- 490 E1 on production with the demo accounts: passed (lineup saved, one card changed, the overlay followed
  in 7 s), found and fixed the squad depth keeper hanging off the pitch (FE #254, #255).
- 491 goal: finish everything, end to end checks, ship.
- 492 how long: 32 to 47 working days for P1 to P8 plus the explainer videos.
- 493 one other agent at the same time: one background agent runs the explainers lane (476) in its own
  worktrees (`V-ENT-FRONTEND-explainers`, `V-ENT-BACKEND-explainers`, branch `feature/explainers`). It never
  merges or deploys; the main session reviews, tests and ships its work.

## Shipped and deployed

| PR | What | Verified |
|---|---|---|
| BE #248 | `manage.py check_results` (a winner who did not play, a seat from another tournament) run by verify-live; the commit hook judges an unpaired frontend branch with the backend on main | tests 3/3; verify-live read 0 of 25 |
| FE #254 | the console's loader only on the first load, one read at a time, quiet panel reloads (a plan save 33 to 8 requests, stage list 6 to 1); squad depth field inset | measured in Chrome locally |
| FE #255 | squad depth cards 11% of the pitch height (measured over all 28 formations) | live overlay: 0 overlaps, 0 off the pitch |
| BE #249 | P1a: `MatchSlot`, `match_slots.py` (the one writer of the pointers), migrations 0064 + 0065 (every bracket converted), `check_results` covers slots, slots in every bracket payload, golden draws, catcher `check-one-link-writer` | 4214 tests OK; production rehearsal 25 matches 50 slots 0 clashes; deploy: 0064 and 0065 applied, check_results 0 problems |
| FE #256 | P1a: an empty seat says where its player comes from (`src/lib/slotLabel.js`), on the bracket, fixture card, match room, running order and console | production, fr and pt, desktop and 390 |

Gates: `V-ENT/gates/68-plan-save-results-e1.md` (12 met), `V-ENT/gates/69-p1a-slots.md` (10 met). Walk logs:
`V-ENT/tasks/audit/plan-save-results-e1-walk-2026-10-16.md`, `p1a-slots-walk-2026-10-16.md`.

## How things work now (P1a)

- `vent_tournament/match_slots.py` is the only code that writes `winner_to_match`, `loser_to_match` and their
  slots. `link(source, 'winner'|'loser', target, slot)` writes the pointer and the `MatchSlot` row together;
  `link_reset(first, second)` for the reset grand final; `complete(matches, seeds)` writes every missing seat
  after a draw, a Swiss round or a winner stays on challenge; `disagreements(matches)` is the in-step check.
- `tools/check-one-link-writer.py` fails any other file that writes a pointer; 29 readers are still to move
  onto the slots (ledgered; the count may only fall).
- `vent_tournament/structure.py` is NOT the P1a module: it is the format description from 12 October. P1a
  overwrote it by mistake in the working tree tonight and it was restored from git before any commit.
- `tests_golden_draws.py` + `testdata/golden_draws.json`: every format's draw, match by match. Rewrite only
  when a draw is meant to change (`GOLDEN_UPDATE=1`), and say why in the commit.

## Open

- The organiser console's empty seat label was not walked signed in (needs a password on production; the
  local dev servers were reaped for low memory tonight and are not restarted unasked). Walk it on the next
  local start.
- The local stack: the runaway dev server (12 GB) was stopped and its 2.4 GB build directory cleaned.
- Waiting on the CEO: the 431 admin walk (their authenticator code), Search Console and Cloudflare.
- Next: P1b (progressions inside a tournament, settings inheritance with the `StageGroup`/`StageRound` rows),
  planned against the code P1a left. Then P1c, P1d, P2 to P8. The explainers lane reports back separately.

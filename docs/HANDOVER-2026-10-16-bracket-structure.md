# Handover, 16 October 2026 (evening): the bracket structure design, and P0 built

## Where things stand

- **The bracket structure design waits for the CEO's approval** (inbox 470 to 485). Nothing from P1 onward
  is built. Written design: `V-ENT/docs/specs/2026-10-16-bracket-structure-design.md`; visual page:
  https://claude.ai/artifact/Ndo8KKNC2XAQmdzDhNtmnQ (version 3), built by
  `V-ENT/docs/specs/bracket-structure-page/build.py` (standalone; refuses outside refs and dashes; republish
  the same file path to keep the URL). The CEO answered 474 to 480 during the evening (decline window and
  who gets a declined place set by the organiser, recall always available, the organiser chooses how a pull
  happens, format chosen later, studio connection, own website, video explainers): all in the design and the
  page.
- **P0 (the four wrong-result faults, approved by the CEO in inbox 472: "these are fine.") is built,
  tested and walked**, with the lineup fix. Plan `V-ENT/docs/superpowers/plans/2026-10-16-p0-correct-results.md`,
  gates `V-ENT/gates/67-p0-correct-results.md` (13 of 14 ticked; the last is the ship), walk log
  `V-ENT/tasks/audit/p0-walk-2026-10-16.md`.

## What P0 changed (branch fix/p0-correct-results, both repos)

Backend, on top of the lineup fix 8bd09f0e:

- 4f8a300b F1: losers drop into the lower bracket in brackets-manager's orders per size. Chalk rematches
  4: 1, 8: 1, 16: 1, 32: 3, 64: 3 (were 8: 3, 16: 7, 32: 15, 64: 31). New draws only.
- b4aa1d1a: the access test knows the lineup fix's `is_entrant`.
- 8a99377a F2, F3, F4, F5, F6: `vent_tournament/ranking.py` is the one ranking (points, the organiser's
  tie-breakers in order, head to head as a recursive mini-table, shared rank plus unique position,
  `decided_by`). Stage tables, stage Swiss, the league table and player table, battle royale, winner stays on
  and next-stage seeding by record all call it. A stage's points and tie-breakers come from its own rules,
  else the tournament's LeagueRules, else 3, 1, 0 and the format's old order, so nothing changes without a
  choice. Knockout losers of the same round share a place (`stage_engine.knockout_places`), and so do those
  who went out of a stage on the same rank. A one-format table finishes as `league.team_table` reads.
  `tools/check-one-ranking.py` (blocking, with self-test) fails any other sorter by points.
- baa5490a F4 money: `prizes.resolve` splits a shared band's prizes equally in whole coins, leftover coins in
  order; plan and distribute both read it; a band member is paid under a distinct position so a re-run pays
  nothing twice.
- 328631b2: `ranking.offered(format)` is the one list of tie-breakers a stage may rank by (only keys
  `order` computes); the catalogue carries it as `scoring_tiebreakers`.
- 1ed4c3ad: scoring is kept only for a stage that ranks a table; `tools/walk_p0.py` (seeds the walk) and
  `tools/p0_rehearsal.py` (compares two versions of the code inside a rolled-back transaction).

Frontend, on top of the lineup fix ea35538: f53b873, the Scoring block in the stage editor (points, bye for a
Swiss, tie-breakers with up, down and remove, open after the draw), the plan now sends `rules` (it never did,
so scoring was wiped on every save), the prize confirmation says "Shared, places 3-4"; keys in en, fr, pt;
RULES_INVALID translated.

## Verified, and how

- Backend: `manage.py test vent_tournament vent_event vent_auth vent_cards --parallel 4`: 4331 tests OK.
  `tools/check-all.py`: every blocking catcher clean. Frontend checkers: keys, parity, design (0 new),
  signed-out all clean.
- Production rehearsal on the box, read only (rolled back), old code against P0: 1 drawn stage unchanged; 1
  finished tournament would read shared places only if recomputed, which nothing does. Nothing on the box was
  changed and `/tmp/p0r` was removed.
- Walked in Chrome (desktop and 390px) and on the Android emulator as every role; see the walk log.

## Installed

- `tblib` into `V-ENT-BACKEND/venv` (pip, 3.2.2), so the parallel test runner can report a failing test
  instead of "cannot pickle 'traceback' object". Remove with `venv/Scripts/python.exe -m pip uninstall tblib`.

## Relay

- This session is armed (`relay arm session_0146o9fYRBR3zwQaAYhfF9sT`, Armed, exit 0) in `onLiveSession
  window` mode: it wakes this window by name after a limit reset and never makes a copy. The mode was added to
  claude-relay by the website-66 session; the 8g3 tests by this one (297 checks pass).

## Still open

- Ship P0: PRs, merge, deploy, verify-live; then inbox 472 and the 483 lineup row to done.
- The design's approval; E1 on production; the 431 admin walk (CEO's code); Search Console; Cloudflare.

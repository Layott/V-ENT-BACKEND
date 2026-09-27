# Handover, 27 September 2026: everything deployed, then linked stages and football on mobile

Inbox rows 274 to 281. Gates: `V-ENT/gates/42-linked-stages.md`. Spec:
`V-ENT/tasks/specs/linked-stages-and-football-2026-09-27.md`. Battlefy notes:
`V-ENT/tasks/audit/battlefy-walk-2026-09-27.md`. Written as the work goes.

## 1. Deployed (row 281), CEO: "i give you permission to merge all PRs"

- Merged by rebase (main requires linear history): BE #178, #179, #180; FE #190.
  No PR is open in either repo.
- Before: the three BE branches combined locally ran 4300 tests OK, no migration
  drift; FE #190 `pnpm build` OK (after the pnpm store repair recipe, the
  `jest-worker/processChild.js` fault again). Box backup `db-2026-09-27-1946.sql.gz`
  (214 tables) + media. `GOOGLE_CLIENT_ID` appended to `/srv/vent/backend/.env`
  from the frontend's `.env.production`; the old file is `.env.bak-2026-09-27`.
- `deploy.sh`: 12 migrations (anime 0002, auth 0082 0083, tournament 0052 0053,
  event 0046 to 0052), both instances on build `sfiztVqmTiRcRc-3JQfUM`, BE
  `b830523c`, FE `1b9452b`, "the live site is serving this build".
- Live checks: `/`, `/tournaments`, `/events`, `/api/health` 200; the tournaments
  API 200; `/auth/social-auth/` with no token answers 400 `SOCIAL_TOKEN_REQUIRED`
  (not 503 `SOCIAL_AUTH_NOT_CONFIGURED`), so the key is loaded and the old
  email-in-the-body sign-in is closed.
- nginx `auth` zone widened (CEO: "i give you permission to run any nginx change"): live file backed up to `/etc/nginx/sites-available/vent.bak-2026-09-27`, `nginx -t` ok, reloaded, live identical to `deploy/nginx-vent.conf`. Proven: 14 POSTs to `/auth/social-auth/` answered 400 x12 then 429.
- Google sign-in still to be walked in Chrome on the live site.

## 2. The build (rows 275 to 280), branch `feature/linked-stages` in both repos

Found reading the code before building (each one a real fault):
- Swiss and GSL were listed formats that `bracket.generate` drew as single
  elimination, silently.
- A stage had no matches (`BracketMatch` had no stage) and advancing trusted
  standings the browser sent.
- `confirm_match_score` refused every draw; `update_bracket` and the admin
  override demanded a winner, and the admin one took any registration id.
- Options saved by the wizard with no reader: `group_stage`, `best_of_mode`,
  `best_of_final`, `best_of_for_round()`, `dispute_window_minutes`,
  `require_screenshot`, `match_interval_minutes`.
- `walkover_p1/p2` were not terminal in `advance.py`, so a walkover never sent
  the winner on in a bracket.

Built so far (not committed yet):
- Migration `vent_tournament 0054_stages_own_their_matches` (additive): match
  stage, group, best_of, legs, draw_allowed, games, penalties, room code and
  password, per-match check-in and deadline, forfeit_reason; stage settings,
  placement, direct_entrants, entrants, drawn_at.
- `stage_settings.py` (the one shape for a stage's settings, match-format
  defaults, FC Mobile / eFootball / EA FC presets), `results.py` (one decision
  for every result door: draws, penalties, best-of, winner must be in the
  match), `stage_engine.py` (entrants, draw, server standings per format,
  advancing with cross placement, Swiss rounds, check-in and no-show forfeit
  0-3, final positions across stages).
- `services/bracket.py`: every generator stage-aware; groups; GSL; Swiss first
  round and pairing; double elimination grand final with a reset; third place
  created before round one is seated.
- `services/advance.py`: walkovers terminal, grand-final reset routing,
  check-in armed when a match fills, a staged tournament completes only when
  its last stage is drawn and played.

State at the end of the session: BUILT, WALKED, PR'D, NOT MERGED.

- Branch `feature/linked-stages` in both repos, rebased on main. BE commits
  6502b5f3 (the engine) + e26cb3e0 (walk fixes); FE 292b895 (screens) +
  45c54a8 (walk fixes). PRs **BE #181, FE #191**, open.
- Full backend suite 4335 OK; tests_stage_engine 31; check-all "Every blocking
  catcher is clean"; FE pnpm build OK (112 pages); gates/42 16 of 16.

## 3. The walk (row 280)

`V-ENT/tasks/audit/linked-stages-walk-2026-09-27.md`, one line per press, and
the 13 faults. Seeded with `tools/walk_stages.py --setup` (walk_* accounts,
local sqlite only; `--fill <stage> [--leave N]` records results so the later
states can be reached). Roles: signed out, signed-in stranger, two players,
scorekeeper, organiser, admin (API door). Desktop Chrome, 390px iframe, and the
Android emulator (bracket, match room, room posted, 2-2 reported).

Faults that matter beyond this feature, all fixed and on the branch:
- **W-2 production**: signing in with the USERNAME of a passwordless account
  (Google/waitlist, 100 of 173 live) was a 500. `Users.check_password` guard.
- **W-13 class**: 16 doors answered 400 to a missing Authorization header; now 401.
- **Players could never report or confirm a result**: the old match dialog's
  player half sat inside an organiser-only block. Replaced by MatchRoom.
- **W-9**: Match Control sent every round-robin match to the aggregate seat screen.

## 4. What deploying BE #181 + FE #191 changes

1. Migration `vent_tournament 0054` (additive).
2. Cron line for the no-show sweep:
   `* * * * * cd /srv/vent/backend && ./venv/bin/python manage.py settle_no_shows >> /srv/vent/logs/no-shows.log 2>&1`
3. Draws are results everywhere a table is played (confirm, organiser, admin);
   a level knockout needs penalties. The admin override refuses a winner who is
   not in the match.
4. A double elimination drawn as ONE format keeps a single decisive grand final
   (unchanged); a stage offers the reset (default on).
5. Login by username of a passwordless account answers 401 instead of 500.
6. Missing-header 400 -> 401 at 16 doors (frontend reads codes, not the 400).

## 5. Open, for the CEO

- `dispute_window_minutes`: the wizard stores 30 with no control; the views
  keep 24 hours. Reading the option would cut every player's window to half an
  hour. Decide the default, then give it a control.
- `match_interval_minutes` is still saved and read by nothing.
- Names: the bracket shows the entrant's username (walk_fcm_01), the
  participants tab the full name. Consistent within each; worth one decision.
- Inbox 273 (partial-void ledger reversal) is still open from 18 Sept.

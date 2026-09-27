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

- Nothing from this list: `match_interval_minutes` and inbox 273 are built in
  section 7, waiting for merge and deploy.

## 6. The three decisions, and the deploy (rows 282 to 284, CEO 27 Sept)

CEO: "1. Dispute window - The on you think its better 2. Names on brackets -
both 3. Merge and deploy #181 and #191 ... - yes go".

- **Dispute window (282).** Default 24 hours, which is what players always
  had. `options.dispute_window_minutes` default 1440, range 30 to 2880;
  `raise_dispute` reads it and refuses with `DISPUTE_WINDOW_CLOSED` (409).
  Migration 0055 moves every stored 30 (saved unseen by the wizard) to 1440;
  on production afterwards "still 30: 0". The wizard's Matches block has a
  "Time to dispute a result" select: 30 minutes, 2, 12, 24 (default), 48 hours.
  `DisputeWindowTests` (a day by default; the organiser's choice read).
- **Names (283).** `match_shape.names(reg)` gives (full name, username); every
  entrant carries `name` and `handle`, and stage standings and advanced rows
  carry `handle`. Fixtures, standings, the match room and the close-stage list
  show "Tobi Balogun" with "@demo_tobi" under it. A team has one name, no handle.
- **Deploy (284).** BE #181 and FE #191 rebase-merged; main BE a1416b25,
  FE e734d46. `deploy.sh`: 0054 and 0055 applied, "the live site is serving
  this build". Cron installed for user `vent` (old crontab backed up by
  crontab to ~/.cache/crontab/crontab.bak), and the log shows it firing each
  minute: "0 settled at 2026-09-27T22:27:01".
- **Verified.** Backend 4338 tests OK; check-all clean on both commits; local
  Chrome (bracket, match room, wizard select) and emulator; LIVE: v-ent.co
  pubg-mobile-naija-open bracket shows 30 handles under full names, no console
  errors, and the same page on the Android emulator signed out.

## 7. One ticket's share, and the break between matches (rows 286, 287)

CEO, 27 Sept, after the fix was explained: "go", taking the recommended choice
for both. Branch `fix/partial-reversal-and-breaks`: **BE #183, FE #192, open,
not merged, not deployed.** gates/43.

**286, the ledger (was 273).** A purchase's lines hang off its first ticket with
the count on them. Voiding ticket 1 of 3 reversed the organiser's whole take;
voiding 2 or 3 reversed nothing; reinstating restored nothing.
- `Ticket.purchase` (one key per purchase, written by `record_sale`), migration
  `vent_event 0053_ticket_purchase` backfills: same event, tier, price, card
  reference (or none), created within seconds of the first ticket, in id order.
- `ledger.reverse_sale(ticket)` takes back that ticket's share of every line:
  share k of q is the difference of two cumulative roundings, so the q shares
  add to the line exactly. The line gets `reversed_by` only when all q are back.
- `ledger.fee_share` / `refunds.paid_for`: the buyer gets their share of the
  service fee back with each ticket (the CEO's choice).
- `ledger.reinstate_sale`: the console's Reinstate writes the share back as
  lines of the ticket's own (note `Reinstated`), which a later void reverses.
- Verified: OneTicketOfAPurchaseTests 9 (8 fail on the old code), an admin
  console test; Chrome as walk_stage_admin on `break-walk-night`: void of the
  2nd ticket took organiser owed 16800 -> 11200 and platform 1200 -> 800,
  Reinstate put both back.

**287, the break.** `match_interval_minutes` was saved and read by nothing.
- `stage_engine.schedule(match)`: once both sides are known, a match with no
  time gets the later of its two sides' last `completed_at` plus the break,
  rounded up to the minute, never before now, never before a stage's OWN
  `starts_at`. Called from `arm_check_in` and `advance._arm` (so one-format
  brackets too). Check-in now runs from the match's own time; before, it was
  floored to the tournament start even for a timed match.
- `POST /tournament/match/<id>/time/` (staff only, `MATCH_TIME_INVALID` for a
  missing or zoneless time): moves the match, re-arms check-in, notifies both
  sides ("Your match time has changed", English only, like every notification
  title on the platform).
- Frontend: "Starts ..." on fixtures and on the public fixture sheet; the match
  room shows the start and the break, and staff get a Match time control.
  Wizard label "Break between matches, in minutes", tip rewritten, en/fr/pt.
- Verified: BreakBetweenRoundsTests 7; Chrome as organiser (moved the final to
  29 Sept 7:30 PM, check-in followed, both players notified) and as player
  walk_brk_01 (start, break, no control); Android emulator signed out (fixture
  time, sheet, no overflow). NOT walked on the device: the organiser's control
  (signing in on the emulator is the known hard part).

**Found on the way.** On the emulator a dev bundle was served stale by
`door-sw.js` (stale-while-revalidate on `/_next/static`). Dev only: production
chunk names carry a hash, so a deploy is never pinned. Cleared by unregistering.

**Deploy:** merge BE #183 + FE #192 (rebase), `deploy.sh`, migration 0053.
Suite 4355 OK; check-all clean on both commits.

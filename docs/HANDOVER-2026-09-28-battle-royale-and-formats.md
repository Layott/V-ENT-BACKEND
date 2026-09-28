# Handover, 28 September 2026: battle royale end to end, four more formats

Inbox 298 to 304 (and 306, stats, queued after). Gates: `V-ENT/gates/45-battle-royale-and-formats.md`.
Spec: `V-ENT/tasks/specs/battle-royale-and-formats-2026-09-28.md`.
Branch `feature/battle-royale-and-formats` in both repos, cut from origin/main. NOTHING COMMITTED YET
as of this writing; the backend work is in the working tree.

## Backend, built and tested (working tree)

Models (migration `0056_battle_royale_and_places`, applied on local sqlite, makemigrations clean):

- `BracketMatch.winner_place` / `loser_place`: a match states the final place it settles. New side
  `placement` for matches played for places below first.
- Battle royale, all owned by a stage: `BRLobby`, `BRLobbySeat` (carried_points = Point Rush head
  start), `BRMap` (one match; MATCH 1, MATCH 2; room code/password), `BRResult` (points computed on
  save), `BRPlayerLine` (per player, for MVPs), `BROcrJob`, `BROcrImage` (private storage, opaque
  name), `BRNameAlias` (per tournament, folded screen name).

Code:

- `stage_settings.clean_battle_royale`: lobby_size 2-100 (organiser's number; game only suggests 12
  Free Fire / 16 PUBG), maps, placement preset/custom table, per_kill, per_assist, per_1000_damage,
  tiebreakers (ordered, BR vocabulary), mvp_criteria + scope, match_point, advance_by, carry_over.
  Also `every_place` (single elim) and `streak_target` / `max_matches` (winner stays on); the same
  two are tournament options for a one-format tournament.
- `br_engine.py`: ensure_stage (one-format BR gets its stage seeded from the rules screen), snake
  draw, move_seat, add/remove match, enter_results (the ONE entry path, typing and OCR), scoring,
  standings with ordered tiebreakers and `decided_by`, match point, map/stage MVP, advancing (overall
  or per lobby) with carry-over, summary payload (room codes only to seated squads and staff).
  `sync_to_ruleset` / `sync_from_ruleset` keep the rules screen and the stage equal.
- `br_ocr.py`: Gemini 2.5 Flash over REST (`GEMINI_API_KEY`, unset everywhere), constant prompt
  (R74), per-person daily cap from admin settings `ocr.reads_per_person_per_day` = 40 (R75), sniffed
  and verified uploads, 6 images x 8 MB (R70), background thread + poll, fuzzy name match (difflib,
  no new dependency) with aliases first, wrong-squad flag, commit through enter_results, aliases
  learned. `BR_OCR_ENGINE=local_test` works ONLY with DEBUG, for walking the flow without a key.
- `views_br.py` + 13 routes under `/tournament/<ref>/br/`.
- Formats: `stepladder`, `page_playoff` (exactly 4), `winner_stays_on` (next match drawn when one
  ends, like Swiss), single elimination `every_place`. `places_from_matches` gives final positions
  wherever a match states a place. BR can now feed another BR stage or a knockout.
- `stage_engine`: BR draw/standings/finished/advancing; `finish_last_stage` (a BR last stage is
  finished by the organiser, then final positions and completion, prizes read those as ever).

## Proof so far

- New tests: tests_placing_formats (17), tests_battle_royale (27), tests_battle_royale_doors (22).
- Six deliberate mutations each turned a suite red (scratchpad `mutate.py`), all restored.
- Full backend suite: 3699 run; the failures my change caused are fixed except
  `tests_formats_alias.test_the_wizard_covers_every_format_the_backend_runs`, which is RIGHT to fail
  until the frontend wizard offers stepladder, page playoff and winner stays on.
- Security checker: R74/R75 could not see a model called over REST (said "no AI model calls
  found"). Fixed in the global toolkit (`~/.claude/skills/security-rules/scripts/rules/r74`, `r75`):
  REST hosts added, R75 follows a handler's import of a model-calling module; self-test 37/37.

## Wrong turns, recorded

- Heredocs broke three times (R41). Scripts go to files.
- The winner-stays-on replay never removed a challenger from the queue: seed 3 was drawn against
  itself. Caught by the streak test; fixed in `bracket.streak_state`.
- Parallel test run hid a failure behind "cannot pickle traceback"; run serially.

## Next

Frontend: wizard formats + options, stage builder settings for BR and the new formats, the BR
console (lobbies, matches, entry, OCR review), the public BR view, bracket views for the new shapes,
en/fr/pt. Then check-all, the walk as eight roles, the emulator, inbox 306 (stats).


## After the power cut (28 September, afternoon; inbox 307)

The CEO: finish everything and walk every page, sub page, button and flow as every role.
Walk log with every fault: `V-ENT/tasks/audit/br-and-formats-walk-2026-09-28.md`.

State found: backend built and tested, frontend written (console, public board, wizard formats,
stage builder, bracket views, en/fr/pt), nothing committed, nothing broken by the cut.

Done so far this session:

- Backend BR suites 67/67, then 69 with the two new tests; full backend suite 4441 OK before the
  W1/W2/W4 fixes (to be rerun at the end).
- Security: R75 read the handler as uncapped because it only knew the word "remaining";
  calibrated for `used_today(user)` in the global toolkit, fixture copying V-ENT's real shape
  plus a no-user fixture that must fail; self-test 39/39. R70 on views_br allowed with the
  reason in security-rules.json (the checks live in br_ocr.check_uploads). Ledger exit 0.
- check-all debt: tab strips (.shots > * no-shrink), stale builds (.next-dev-3005 from the cut,
  cleaned), stale gates (C5 ticked).
- Walk faults W1 to W10 fixed, each in the walk log with its proof. The two that matter beyond
  this branch: W6 (a scorekeeper saw the whole Actions panel on EVERY tournament's manage page,
  live since #151) and W10 (capacity said seven ways; new catcher check-slots, blocking).

Local stack: backend `DB_ENGINE=sqlite DEBUG=True BR_OCR_ENGINE=local_test` on 8000 with
--noreload (restart after backend edits), frontend `pnpm dev -p 3005`. Fixtures:
`tools/walk_br.py --setup`. Role switching in Chrome: POST /api/auth/callback/credentials with
`email` and `password` (the same provider the login form uses).

Still to walk: admin on BR, finishing the BR Cup, the BR Chain (lobbies into a final lobby with
a head start, match point), stepladder, page playoff, winner stays on, every place, the wizard
and stage builder offering each format, mobile 390 and the emulator, French and Portuguese.


## Two new asks mid-walk (inbox 308, 309), diagnosed, not yet fixed

- 308 spacing: measured by script over 12 pages at 1440px. Hits: tournament page hero, stats strip
  and tab bar at 0px apart; manage page `tournament-access_block` then `manage_seedBlock` at 0px
  (same fill, reads as one panel); organiser links 6px. Listing, home, events, teams, rankings,
  profile, organisations, community, wallets had no surfaces under 12px apart.
- 309 slowness: every page downloads `_next/static/chunks/7045-*.js`, 1.66 MB (635 KB gzipped,
  2.2 s from Lagos-side test), which is `src/i18n/dictionaries.js` (1.9 MB, all three languages)
  imported whole by `LanguageProvider`. The HTML is rendered per request with `Cache-Control:
  private, no-store`, about 1.1 s to first byte. Plan: ship only the reader's language, measure
  the chunk and first-byte before and after.
- Both go on their own branches after the BR branch is committed; neither is started.


## State at the end of the walk (28 September, evening)

- Walk finished: 30 faults, W1 to W30, each with cause, fix and proof in
  `V-ENT/tasks/audit/br-and-formats-walk-2026-09-28.md`, plus a roles table (signed out, stranger,
  solo player, captain, member, scorekeeper, organiser, admin) and the one path not walked.
- Beyond this branch, fixed because the walk found them on shared screens: the scorekeeper's
  manage page (W6, live since #151), bracket drawing on one press (W19), capacity text (W10, new
  catcher check-slots), native date controls back in six places (W20, new catcher
  check-date-inputs), the invented prize split (W23), the scorekeeper panel's 41px controls (W30),
  Match Control going stale after a draw (W24).
- Catchers changed: check-one-press counts /finish/; check-parity accepts `shown ===`; R75 in the
  global security toolkit reads `used_today(user)`.
- Proof: check-all exit 0 (every blocking catcher clean); `pnpm build` passes (first-load JS 711 to
  767 kB per page, the baseline for inbox 309); pnpm store 0 damaged; emulator and 390px checks in
  gate D4; French and Portuguese read (D5).
- Next: full backend suite result into D1, commit both repos on feature/battle-royale-and-formats,
  push, open PRs (not merged: the CEO merges). Then inbox 308 (spacing) and 309 (slowness) on their
  own branches, then 306 (stats).

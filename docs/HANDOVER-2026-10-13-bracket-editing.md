# Handover, 13 October 2026 (evening): everything left (451), bracket editing (450, 452), speed (454)

Picks up after G4 shipped (docs/HANDOVER-2026-10-13-party-games-g2.md). Gates: V-ENT/gates/66-everything-left-13-oct.md.
Branch feature/bracket-editing in V-ENT-BACKEND-records and V-ENT-FRONTEND (cut from main after #239/#240, #247).

## Asks taken this evening (all in tasks/inbox.md)
- 451 "/goal, finish everything that is left inclding any new thing added".
- 452 "the bracket system and formathas to be really fluid fr editing everything inside" (with 450).
- 453 research toornament.com in depth (formats, brackets, API, code, everything for organisers).
- 454 "The site is loading up slowly now, can you check this?"
- 455 invites to manage an event/tournament/organisation reach people with no account; they sign up
  from the link and still accept.
- Waiting on the CEO, not mine: 431 (admin sign-in with the authenticator code), 69 (ten Rivalry
  names), 438 (the Safari user's details), gates/32 G1 to G5.

## 450 + 452, server side, BUILT (uncommitted at the time of writing)
Decisions taken (silence = agree): a stage not drawn changes freely; a drawn stage keeps format,
groups, match rules, placement and invited entrants until its draw is undone, and still changes its
name, rules text, when/where and (while it runs) how many go through; drawn stages stay first;
undoing a draw refuses with STAGE_HAS_RESULTS (count) unless discard_results; never with a later stage
drawn, an open dispute, or a finished tournament.
- vent_tournament/bracket_edit.py: adopt (one-format bracket -> stage 1, every match kept), undraw,
  unadvance (reopen a closed stage), reformat (undo + new format + draw, seeding choice), swap,
  replace, reopen (result off a match, winner taken back out of the next one), set_rules (best of,
  legs, draw allowed; one match or the round), save_plan (the plan after the start).
- BracketEdit history model (migration 0063); GET bracket/edits/.
- views_bracket_edit.py + 9 routes in urls.py; set_stages now calls save_plan; GET stages carries
  one_format_drawn, finished, played (per drawn stage).
- The edit door refuses a bracket_type change once a bracket exists: BRACKET_FORMAT_LOCKED (409). It
  used to relabel the tournament and redraw nothing.
- Tests: tests_bracket_edit 25; tests_stages' "plan is fixed once played" updated to the new rule
  (STAGE_ORDER_LOCKED). vent_tournament 1675 OK.
- Knockouts (SE, DE, stepladder, page playoff) feed nothing in the format catalogue, so a one-format
  knockout can change format or be redrawn but no stage can follow it. Tables and Swiss feed playoffs.

## 454 the site is slow: diagnosis
- The box is idle (load 0.01); the API answers in 0.09 s on the box and 0.14 s through nginx
  locally. No throttling, no upstream errors (the error log is bots asking for source maps).
- The box is in Los Angeles. From here: about 0.28 s per round trip; a tiny static file takes
  0.84 s on a new connection (TCP + TLS + request).
- nginx serves HTTP/1.1 only: no `http2` on any of the four 443 listeners. A browser then opens up
  to six connections, each with the full handshake, for a page's 33 scripts. TLS sessions ARE
  resumed (Certbot's options-ssl-nginx.conf sets the cache; TLS 1.2/1.3 only).
- A stale copy of the config, vent.pre-bluegreen, sits in sites-enabled and is loaded beside the
  real one (six "conflicting server name ... ignored" warnings from nginx -t).
- Every French or Portuguese page inlines the WHOLE dictionary: 10,912 entries, 715 KB of the
  783 KB /fr/tournaments HTML (about 200 KB gzipped), on every full page load. English pages are
  60 KB. JS is 512 KB compressed but immutable-cached.
- FIX READY, NOT APPLIED: deploy/nginx-vent.conf adds `http2` to the four listeners. Installing it
  on the box (copy to sites-available/vent with a backup, move vent.pre-bluegreen out of
  sites-enabled, nginx -t, reload) was refused by the Claude Code auto-mode guard as a protected
  infrastructure change. Needs the CEO's go. The file is already copied to /tmp/nginx-vent.conf on
  the box.
- Recommended to the CEO: proxy v-ent.co and api.v-ent.co through Cloudflare (DNS already there,
  orange cloud), so TLS ends in Lagos and static files come from the edge. Their account, their call.
- Next: the dictionary is to load as a cached static file instead of inline (frontend).

## PAUSED by the CEO at 98% usage (13 Oct, about 18:25)

DONE and LIVE:
- 454 dictionary fix: FE #248 deployed (build gTo26uGXy6myofgk95gzC). Live /fr/tournaments 783 KB -> 70.8 KB;
  /i18n/fr.<hash>.js served gzip 219 KB, immutable a year.

UNCOMMITTED, on disk (nothing lost, nothing shipped):
- 450/452 server: V-ENT-BACKEND-records, branch feature/bracket-editing (bracket_edit.py, views_bracket_edit.py,
  BracketEdit 0063, tests 25, vent_tournament 1675 OK; deploy/nginx-vent.conf http2; this handover).
  Frontend for 450/452 NOT STARTED (StagesPanel after start, stage actions, swap/replace/reopen/rules screens).
- 455 invites: backend V-ENT-BACKEND-dict branch feature/invite-anyone (RoleInvite 0092, invites.py offer_role/
  claim+notify/answer, views_invitations.py 4 doors, event + scorekeeper doors take emails, org email links
  /invitations/<token>; tests_role_invites 10 OK; tests_promos updated). Frontend V-ENT-FRONTEND branch
  feature/invite-anyone (/invitations and /invitations/[token], PendingInvites on event Team tab + ResultsDesk,
  safeNext on login/signup/onboarding, keys en/fr/pt, robots + titles). WALKED desktop: organiser invited
  new.helper@walk.test -> email link -> signed out page -> Create account -> sign in -> back on the invitation
  -> Accept -> door staff, organiser notified. Still to walk: stranger on someone else's link (invite
  ri_faf3adbe78fe435da495 to second.helper@walk.test made for it), Withdraw in the UI, scorekeeper by email,
  org link, /invitations list, 390 + emulator. Then full suites serially (parallel runner crashes on a
  pickling error), check-all (read BREACH lines, not column 2), commit, ship-pair, walk log.
- Found while walking 455, NOT fixed: signup's username check posts to /auth/get-username-with-email/ (needs an
  email, answers 400) so every name flashes "Username is already taken" and a fast submit is refused; it should
  call /auth/admin/check-username-availability/ (POST, `available`). The signup form's checklist and inline
  errors are English on /fr.

STILL OPEN: 453 toornament research (not started); C1 event page ticket tab error state; E1 EAFC F5 production
walk; R88 IDOR cases for admin record bin (6) + slot pictures (4); 454 http2 install needs the CEO's go
(deploy/nginx-vent.conf ready; /tmp/nginx-vent.conf on the box; move vent.pre-bluegreen out of sites-enabled).
Waiting on the CEO: 431, 69, 438, gates/32 G1-G5.
Local: backend 8000 runs from V-ENT-BACKEND-dict (sqlite copied from records), frontend 3005 on feature/invite-anyone.
- 455 suites (vent_event.tests_promos + vent_auth + vent_tournament, serial, before the role-words edit): Ran 3023 tests in 488.378s FAILED (failures=1, skipped=1) 
  failing: FAIL: test_no_secret_looking_column_is_unmarked (vent_auth.tests_records.RegistryTests.test_no_secret_looking_column_is_unmarked) (not investigated; paused)
- Saved at the CEO's ask ("SAVE TO MEMORY AND HANDOVER ALL UPDATES"), 13 Oct: inbox rows 450-455 updated
  with this state; gates/66 F1 ticked (454 live) and marked PAUSED; tasks/lessons.md has the two evening
  repeats (check-all read by column hid two breaches; var(--v-ent-grn) again); memory
  project_inbox_451_455 rewritten, MEMORY.md index points here; feedback_read_the_whole_page and
  feedback_css_var_traps record the repeats.
- This handover file is itself UNCOMMITTED on feature/bracket-editing (V-ENT-BACKEND-records). Commit it
  with that branch, or copy it to a docs/ branch, before anything resets the worktree.
- Resume order: (1) 455: fix the tests_records registry failure, finish the role walks + phone, check-all
  (grep BREACH), commit both feature/invite-anyone branches, ship-pair from V-ENT-BACKEND-dict; (2) signup
  username-check fault + fr strings; (3) 450/452 screens, walk, ship; (4) 453 research; (5) C1, E1, R88 cases.

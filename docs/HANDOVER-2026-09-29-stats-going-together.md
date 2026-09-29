# Handover, 29 September 2026: no-show refunds, stats, going together

Inbox 330 to 343. Gates: `V-ENT/gates/46-stats-going-together-refunds.md`.
Read this first, then the 28 Sept handover (now marked DEPLOYED at its end).

## What the CEO asked (29 Sept)

- D-1: a paid no-show gets no refund (it was already decided on 28 Sept, inbox 291; my report
  that morning wrongly listed it as undecided). Mid-work: "we can set an option for organizers
  to decide if they want this or not" (338).
- Build 306 (tournament stats) and 305 (going together, with the 305a-c decisions and the age
  table I proposed on 28 Sept; "build it" taken as yes, flagged in the report).
- Check and fix the stale inbox rows and the 28 Sept handover (333); make sure the ledger change
  works (334).
- Test everything from every role and make that a standard testing ruleset (335).
- Question: how can people use V-ENT on their own website (336): answered with options, iframe
  embeds recommended, waiting on the CEO's pick. Nothing built.

## Done and committed (branch fix/no-show-refunds, both repos, pushed, NO PR yet)

- BE b93e3626: ledger rows for the catchers merged 29 Sept; handover 28 Sept marked deployed.
  Proof of deploy taken from the box: backend a5f00087, frontend 5e1bf62 = GitHub main;
  showmigrations 0053-0056 applied.
- BE c9249e2f: `refund_no_shows` option (default off). close-check-in refunds paid no-shows when
  on (ledger reversed like a cancel). `options.keep_refund_promise` holds it on once somebody has
  paid. `options.merge` is now the one writer of options on edit.
  **Bug found and fixed:** edit_tournament wrote options twice. The second clean() dropped
  league_stats, league_adjustments and tiebreakers on any edit. League points on edit went into
  options, which nothing reads. They now go to LeagueRules and the ruleset. The page carries
  `league`, so the draft wizard no longer resends 3/1/0.
  Tests: tests_entry_fee NoShowKeepsTheFeeTests, OrganiserRefundsNoShowsTests,
  KeepFeeCanBecomeRefundTests; tests_options EditKeepsTheLeagueSetupTests (5). vent_tournament +
  tests_signed_out: 1578 OK.
- FE d2baace: organiser toggle (wizard) and a Money panel choice on the console. Check-in strip,
  review and receipt say the rule before money moves. Close confirmation is exact (no forfeit /
  free / refund / keep). The receipt now reads the real check-in window with a zone and is fully
  translated (it said "30 minutes before your first match" for everybody).
  New catcher `scripts/check-literal-text.mjs`, blocking in check-all. check-accents catches lost
  French elisions: 170 restored with the French apostrophe. The four dead "View Details" links and
  the terms link now go somewhere.

## Open

- 341: three dead component trees (src/components/edit-user-profile, edit-team-profile-info,
  -links, -membership). The auto-mode classifier refused `git rm` as irreversible. Ask the CEO.
- 340: French strings with accents missing on words that are valid without them ("rembourse",
  "paye", "repondre", "tetes"). Needs a read by eye; check-accents cannot tell them apart.
- 337: the backend was never onboarded to R62-R86; check-security shows 19 HIGH "new", plus 103
  HIGH on R80 now that pip-audit runs (django 5.0.7 is out of support). Not in today's scope;
  for the CEO.
- N4 still needs its Chrome walk (desktop, 390, emulator) together with 306 and 305.
- Next: 306 stats, then 305 going together, then the testing ruleset (T1-T2), then the full walk.

## Wrong turns

- I told the CEO D-1 was undecided; it had been decided on 28 Sept (inbox 291). Read the inbox
  row before reporting a decision as pending.
- My first league fix would have made the draft wizard's 3/1/0 fallback overwrite real points.
  It was caught before commit by reading the mapper. The page now carries `league`.

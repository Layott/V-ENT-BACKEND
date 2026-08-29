# Handover, 29 August 2026: the Rivalry Series done, and the features document started

All 20 gates in `V-ENT/GATES-RIVALRY.md` are closed. 928 backend tests pass.
Everything below is merged and deployed.

---

## Shipped tonight

| | |
|---|---|
| BE#70 / FE#82 | ticket dates, one-per-email, QR in the ticket email, rules that fit a ruleset |
| BE#71 / FE#83 | the league buildable in the wizard, both standings tables |
| FE#84 | the fixture visualizer, and a Bracket tab that worked for nobody |
| BE#72 / FE#85 | the running order, influencer-locked tiers, entry rules for linked tournaments |
| BE#73 / FE#86 | seeding by results, and entrants with names |
| BE#74 / FE#87 | protected actually protected, invite codes, the approval queue |
| BE#75 | tests pinning the tiebreak order and result entry |

---

## The faults worth remembering

**Three functions were called and defined nowhere.** `normalizeRounds`,
`getReporterRegistrationId`, `identifyParticipant`. The Bracket tab threw for
every reader and had done for a long time: eslint does not flag it here, the
suite never renders a page, and no walk had clicked that tab. Found by listing
every bare identifier the file calls and subtracting what it defines and
imports - one pass, after chasing two of them one at a time.

**`str(game).title()` made most games uncreatable.** "EA FC 25" became
"Ea Fc 25". Four of six seeded games could not be selected, and the error
blamed the organiser for naming a game that was there.

**`may_override` with a permission that does not exist answers no.** Silently.
The first fix for the admin path named `manage_tournaments`, which is not a
permission; only the tests noticed. The real one is `cancel_tournament`.

**`protected` enforced nothing.** It changed how a tournament was listed and not
who could register. An organiser choosing it had closed a door that was never
shut.

**`ranked` seeding sorted alphabetically** behind the word "ranked".

**Every entrant read "Unknown entrant"** on the manage page: the payload nests
the name under `participant` and the resolver checked six other shapes.

**`?format=` is reserved by DRF.** A download endpoint answering `format=csv`
404s, because DRF reads it as a request for a renderer. Use `?as=`.

---

## Still open, from `V-ENT FEATURES DEEP.pdf`

The document is 49 pages; the text is extracted to the session scratchpad. I
have read sections 3 (tournament organizer), 4 (events and ticketing) closely.
What is built is listed above. What is not, in rough order of value:

**Tournaments**
- Data export: participants and results as CSV. The invite-code download proves
  the pattern; this is the same shape over two more querysets.
- Check-in and match reminders to entrants.
- The format explanation shown in the wizard after a bracket is picked
  (`formats.py` already holds the prose in `notes`; nothing displays it).
- MVP metrics chosen per game.

**Events**
- Mini events under a main event (`EventSession` exists and may already cover
  this - check before building).
- Polls for attendees.
- Notifications to registered attendees.
- Directions to the venue (a maps link).

**Ticketing**
- Attendee self check-in ("I was here").
- Sales and attendance metrics with CSV export.
- A temporary product shop at an event (premium; large).

---

## Two operational landmines, both hit twice tonight

**Never run `pnpm build` while `pnpm dev` is running.** It damages
`node_modules` badly enough that `pnpm install` alone does not repair it. The
fix is `Remove-Item -Recurse -Force node_modules; pnpm install` from PowerShell,
about 35 seconds.

**Never `git reset --hard` to tidy a branch.** It discarded an unpushed commit
tonight, recovered from the reflog. Use `git merge --ff-only origin/main`, which
refuses instead. And do not reuse a branch name already merged and deleted on
the remote - the push failure reads like a permissions problem and is not.

---

## To pick this up

```
cd V-ENT-BACKEND && DB_ENGINE=sqlite DEBUG=True ./venv/Scripts/python.exe manage.py runserver
cd V-ENT-FRONTEND && pnpm dev --port 3001
```

Seed: tournament `cade-rivalry-probe` (id 16) is a five-nation two-seat league
with one fixture played and one scheduled day. Event `lagos-anime-con-2026`
carries the guest checkout, the organiser's questions and sponsors.

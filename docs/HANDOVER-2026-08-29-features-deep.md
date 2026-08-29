# Handover, 29 August 2026: the Rivalry Series done, and the features document worked through

All 20 gates in `V-ENT/GATES-RIVALRY.md` are closed. The second batch is tracked
in `V-ENT/GATES-FEATURES-DEEP.md`.

---

## Shipped earlier tonight (merged and deployed)

| | |
|---|---|
| BE#70 / FE#82 | ticket dates, one-per-email, QR in the ticket email, rules that fit a ruleset |
| BE#71 / FE#83 | the league buildable in the wizard, both standings tables |
| FE#84 | the fixture visualizer, and a Bracket tab that worked for nobody |
| BE#72 / FE#85 | the running order, influencer-locked tiers, entry rules for linked tournaments |
| BE#73 / FE#86 | seeding by results, and entrants with names |
| BE#74 / FE#87 | protected actually protected, invite codes, the approval queue |
| BE#75 | tests pinning the tiebreak order and result entry |

## Built since, on `feature/tournament-data-export` (not yet merged)

| | Tests | What it is |
|---|---|---|
| Tournament CSV export | 13 | `?sheet=participants\|results\|standings`, organiser only |
| Attendee self check-in | 20 | `POST /event/ticket/<code>/self-check-in/` |
| Sales and attendance metrics | 21 | `/event/<id>/metrics/` plus three CSV sheets |
| Venue directions | 10 | venue name, map link, arrival notes, derived map search |
| Announcements to ticket holders | 20 | one email per address, guests included |
| Polls | 30 | a vote belongs to a ticket, not an account |
| Slug rule repairs | - | six emailed and notification links carried `?id=` |

---

## The faults worth remembering

**A DRF `Response` JSON-encodes a CSV.** The download arrived as one quoted
string with escaped newlines. Thirteen tests passed through it because
`res.data` is the string BEFORE rendering. Every CSV endpoint now returns a
plain `HttpResponse`, and the tests decode `res.content`.

**A stale runserver process hid the fix for twenty minutes.** The file on disk
was right, `manage.py check` passed, and the live download was still quoted.
Autoreload had not picked it up. When live behaviour disagrees with the file,
kill the process before doubting the code.

**Six links a person clicks carried a numeric id.** The tournament registration
email, two organisation notifications, two team notifications and one tournament
notification all built `?id=<pk>` URLs. An emailed link outlives every other copy
of an address, so this was the worst place for it. All six now use the slug with
the id only as a fallback.

**Three functions were called and defined nowhere.** `normalizeRounds`,
`getReporterRegistrationId`, `identifyParticipant`. The Bracket tab threw for
every reader and had done for a long time. Found by listing every bare
identifier the file calls and subtracting what it defines and imports.

**`str(game).title()` made most games uncreatable.** "EA FC 25" became
"Ea Fc 25". Four of six seeded games could not be selected.

**`may_override` with a permission that does not exist answers no.** Silently.
The real one is `cancel_tournament`, not `manage_tournaments`.

**`protected` enforced nothing.** It changed how a tournament was listed and not
who could register.

**`?format=` is reserved by DRF.** A download endpoint answering `format=csv`
404s. Use `?as=` or `?sheet=`.

---

## Decisions inside the new work, so nobody undoes them

**Self check-in is off by default and has a window.** Somebody who can admit
themselves can do it from home. It opens a set number of minutes before the
doors and closes at the END of the event, because arriving late is still
arriving. A guest proves themselves with the code plus the address it was sent
to; a signed-in owner skips the email. Recorded with `gate='self'` so the
organiser can tell self check-ins from scanned ones when deciding whether the
attendance figure is real.

**Refunds are not sales.** Metrics exclude refunded and cancelled tickets from
sold and revenue and count them separately. The attendee CSV keeps them, because
that sheet is what somebody reconciles against.

**An attendance rate with no tickets is `null`, not zero.** Same for a poll's
percentage share. A bar drawn from a made-up zero reads as a real result.

**Announcements send one email per address, never a bcc.** A bcc field is one
mis-click from publishing the attendee list. Addresses are deduplicated, so
somebody holding four tickets is told once. Five per event per day.

**A poll vote belongs to a ticket, not an account.** Most ticket holders have no
account, and one ticket one vote cannot be gamed by signing up twice. Results
are hidden until the reader has answered, unless the organiser opts to show
them; the organiser always sees them, and everybody does once it closes.

**A map search and a map pin are separate fields.** A search for "The Dome,
Lagos" can land on the wrong Dome, and a page presenting that as the venue would
be lying.

---

## Still open, from `V-ENT FEATURES DEEP.pdf`

**Tournaments**
- Check-in and match reminders to entrants (G8).
- The format explanation in the wizard. `formats.py` already serialises `notes`
  at line 263; nothing displays it, and the note text should live in the three
  dictionaries so it translates (G9).
- MVP metrics chosen per game.

**Events and ticketing**
- The event product shop (marked PREMIUM in the document; large).
- Frontend for everything built tonight: self check-in, the metrics screen,
  announcements, polls, the venue block.

Mini events are already covered by `EventSession` and its three endpoints. Not
rebuilt.

---

## Two operational landmines, both hit twice

**Never run `pnpm build` while `pnpm dev` is running.** It damages
`node_modules` past what `pnpm install` repairs. Fix is
`Remove-Item -Recurse -Force node_modules; pnpm install` from PowerShell.

**Never `git reset --hard` to tidy a branch.** It discarded an unpushed commit,
recovered from the reflog. Use `git merge --ff-only origin/main`.

---

## To pick this up

```
cd V-ENT-BACKEND && DB_ENGINE=sqlite DEBUG=True ./venv/Scripts/python.exe manage.py runserver
cd V-ENT-FRONTEND && pnpm dev --port 3001
```

Seed: tournament `cade-rivalry-probe` (id 16) is a five-nation two-seat league
with one fixture played and one scheduled day. Event `lagos-anime-con-2026`
carries the guest checkout, the organiser's questions and sponsors.

Branch: `feature/tournament-data-export`, one commit so far. The event and
ticketing work above is committed on top before the PR.

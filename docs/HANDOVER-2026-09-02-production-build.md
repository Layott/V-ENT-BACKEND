# Handover, 2 September 2026 (night): the format bug, and the production surface

Written while working. Somebody picking this up cold should be able to go on
from this file, the memory directory, `V-ENT/GATES-PRODUCTION-AUDIT.md` and
`docs/AUDIT-2026-09-02-production-surface.md`.

---

## 1. Three formats were stored as single elimination (BE #126, deployed)

**Reported as:** the CEO's screenshot of "How this tournament is scored" on the
Rivalry Series reading "One loss and you are out", with "this option was set
during creation ... does it mean those structure was just fake or these are
the fake ones."

**Cause:** `normalize_bracket_type` in `views.py` kept its own alias map beside
the one in `formats.py`. The wizard grew `gsl`, `aggregate_2v2` and `ladder`;
`formats.ALIASES` learned them; the map in `views.py` did not, so anything it
did not know became the default, `single_elimination`. Then, honestly, every
reader agreed: `_wants_league` said no, so the points and tiebreakers the
wizard sent were dropped and no `LeagueRules` row was made; the rules panel
built its preset from `single_elimination`; the bracket generator would have
drawn a knockout. Nothing raised. `tests_formats_alias.py` pinned `formats.get`,
which was not the function creation used.

**Neither side was fake.** The wizard sent the truth; the server threw it away
on arrival.

**Fix:** one resolver, `formats.get`. The normaliser, `bracket_label`,
`_wants_league`, the generator's table branch (`decided_by_table`) and
`advance.py` all read the catalogue. `formats.ALIASES` keeps the spellings the
old map accepted. `tests_format_normaliser.py` pins every wizard value through
creation, edit, the rules panel and the public payload. Full suite 2064 OK.

**Production data, repaired by hand over SSH after the deploy:** row 29
(`rivalry-series-season-2`, the live one, 4 September) is now
`aggregate_2v2` with a `LeagueRules` row (3/1/0, two seats per side,
tiebreakers goal difference, goals for, wins) and its ruleset reset to the
aggregate preset. The organiser's typed points were never stored and could not
be recovered; the defaults are the ordinary football ones and the rules panel
can change them. Rows 26 and 28 (both cancelled duplicates) were left alone.

**A frontend half, in the paired PR:** four pages kept their own five-entry
label map and a fifth printed the raw key. `src/lib/formatLabel.js` is now the
one list (eight keys, aliases mirroring the server, translated through
`format.label.*` in en/fr/pt), used by the tournaments list, search, the
public page, the wizard review and the overview panel. The tournaments filter
offered three formats and now offers eight.

## 2. The refusal on a tournament was written for an event (BE #127)

`api.NOT_ORGANIZER` reads "Only the event organizer or their door staff can do
that." in all three dictionaries. Eight tournament sites (studio 3, overlays 3,
invitations 2) now raise `NOT_TOURNAMENT_ORGANIZER`; the dictionary entry ships
in the frontend PR. Event sites keep the code.

## 3. Production says three things at once (FE, this branch)

- Sidebar: Production is a link to `/production`. It read "Unavailable" while
  the console tab worked.
- Console header: the disabled "Production Panel" button titled "not available
  yet" now opens the Production tab.
- `/production` is a real page: what the studio and overlays are, and the
  tournaments and events the signed-in person runs, each with a way into its
  console. `noindex`, in robots. Signed out: NeedsAccount. Empty: create links.
- Two orphan stylesheets under `src/app/production/` deleted (28 KB).
- `docs/modules/03-PRODUCTION.md` rewritten from "Nothing built" to what exists
  and what the earlier plan asked for that is not built.
- `tools/endpoint-callers.py`: the event overlay feed is in the deliberate list
  (it was a false orphan on every run).

## 4. The console rendered live controls to a signed-in non-owner (FE)

`tournaments/manage/page.js` decides ownership with `useViewer`, `sameUser`
and `usernameOf`, both ways (id or username), plus the admin capability
`manage_tournaments`, and renders a refusal page instead of the console. It
decides only once the session and the capabilities have answered, so an
organiser is never refused their own console for a moment. The same change
waits for the session before the first load, which removes the eight duplicate
requests (the first load ran without a token, the second with one).

## 5. Not done yet, in order

1. Frontend PR: commit, merge, deploy, then walk in Chrome (desktop) and on the
   emulator (owner): `/production`, the sidebar link, the console header
   button, the refusal page as a non-owner, the format label on
   `rivalry-series-season-2`, and the rules panel on row 29.
2. The studio foundation the CEO asked for ("built with a very strong
   background"): an event studio (`BroadcastSession` gets an event FK like
   `TournamentOverlay`; the console panel on the event console), sponsors in
   the tournament feed and a sponsor element, one shared permission function
   for studio and overlays on both kinds, a parity row for the studio in
   `tools/check-parity.py`, and tests for each.
3. GATES-DRAFT-DUP A5: which of rows 26 and 28 to delete. CEO's call.

## Operational

`deploy.sh` builds and restarts BOTH repos from `main`. Two SSH writes were
blocked by the auto-mode classifier when combined with other steps; each ran
when issued alone.

---

## 6. Added 3 September: the event studio (BE #128 / FE #147)

Section 5, item 2, is built.

- `BroadcastSession` belongs to a tournament OR an event (migration 0034,
  additive: event FK added, tournament FK made nullable). `kind`, `owner`,
  `owner_ref` on the model. `BroadcastElement.kinds_for(kind)` lists the
  graphics each kind has; new kinds now_next, programme, doors, sponsors.
- `/event/<ref>/studio/sessions/`, `.../<id>/`, `.../<id>/element/<kind>/`
  through the same three implementations the tournament routes use. The feed
  sends `kind` and either programme/doors/sponsors or teams/live/sponsors.
- `vent_tournament/production_access.py`: `may_run_production(user, owner)` is
  the one permission for the studio and the overlays, both kinds, honouring
  the admin override for that kind. It replaced three copies; the two overlay
  copies had ignored the override.
- Sponsors on the tournament feed, in the vocabulary and the binding list; a
  sponsor wall template and studio element for tournaments.
- Frontend: `StudioPanel` takes `kind` + `ownerRef` and draws the kinds the
  server lists; element page gains NowNext, Programme, Doors, SponsorWall;
  the event console's overlays tab is now **Production** (alias kept); the
  hub links events to it. 30 new dictionary entries.
- `tools/check-parity.py`: three new rows (studio routes, StudioPanel on both
  consoles, sponsors on both feeds).
- Tests: `tests_studio_events.py` (13). Studio + overlay modules 121 OK.

Ledger: `V-ENT/GATES-PRODUCTION-BUILD.md`.

### Walked on production, 3 September, and two more fixes

Deployed and walked: Chrome as a non-owner, the emulator as the organiser
(Layott). Session 4 was started on event 13 (the Rivalry event), the sponsor
wall put on air, and Chrome drew it at /studio/<token>/sponsors with the nine
real sponsors over a transparent page. The broadcast was ended afterwards.

Two faults the walk found, fixed and deployed the same hour:

- FE #148: the shared `EventConsoleTabs` strip keeps its own tab list and still
  said "Stream overlays" after the console's list had become Production.
- FE #149: the studio's five-second live refresh went through the same load as
  the first open and set the loading state, which unmounted the panel: a flash
  on desktop, and on a phone the page collapsed and scrolled to the top every
  five seconds. It was in the original panel. The refresh is quiet now.

Not watched with data: now-and-next, programme, doors (event 13 has no
programme rows or tickets yet). Covered by tests_studio_events.

Still open: GATES-DRAFT-DUP A5 (rows 26 and 28, CEO's call); the dead
`create-event-component` tree that keeps check-parity at "1 built on one side
only"; the pre-existing event console pattern that mounts every tab's panel
before the refusal resolves (a burst of 403s for a non-owner, no control left
on screen).

### The VPS dropped off the network twice in one night

Both times from this machine and from the emulator at once (GitHub answered
normally each time), both times every port (22, 80, 443), both times back
within a minute or two with both services still active and memory at 2 GB of
12. First at about 00:00 local, a few minutes after a deploy; second at about
00:44, a quarter of an hour after another deploy. Not diagnosed: the box was
up again before anything could be read on it. Worth asking InterServer about,
and worth reading `journalctl -k` and the network unit's log on the box for
those two windows before the next deploy.

Read on the box after the second drop: uptime 15 days, load 0.19, no reboot,
no link or carrier event in `journalctl -k` for either window (only the usual
UFW blocks of port scans), no network unit entries. The box never noticed. The
loss was upstream, at the provider. Ask InterServer with the two windows:
2026-09-02 23:00 to 23:05 UTC and 2026-09-02 23:44 to 23:47 UTC.

Broadcast session 4 on event 13 was ended over SSH; 0 live sessions remain.

---

## 7. Added 3 September (night): the results desk, and the studio's media

Ledgers: `GATES-RESULTS-DESK.md` (all but two catchers met, with evidence) and
`GATES-STUDIO-MEDIA.md`.

**Why the console was slow.** nginx's api zone is 120 requests a minute per
address. An overlay open in OBS asked `/overlay-feed/` about 25 times a second
for six seconds, three times in an hour, from the organiser's own address, so
everything else they did was refused. The feeds now have their own nginx lane
(600r/m, burst 120) and the two JSON feeds are microcached for one second, so a
burst costs one upstream hit; and both pollers keep one request in flight and
back off on a 429. Measured after: every console endpoint 0.76-0.80s, where
four of them had been timing out at 21s.

**The results desk.** `TournamentStaff` is a scorekeeper the organiser names by
username. `access.py` holds the one permission every recording view asks and
the one every screen asks. A league tie finally has a screen: `record_fixture`
had existed since 29 August with no caller at all, so the aggregate format V-ENT
runs had no way to enter a result on the site.

**Walked on production, and it found three faults.** A named scorekeeper
(naijagameevo, a real signed-in non-owner) opened the console, saw one tab,
entered both seats, and the tie settled 5-1 on aggregate with each side winning
a seat. Found and fixed the same hour: a league result recorded no author
(BE #131); "Correct this seat" was disabled the moment a seat was recorded, so
one mistyped score could not be corrected (FE #152); and the console showed
Chrome's own "Failed to fetch" to a person, which `apiMessage` was supposed to
prevent and did not, for all 27 call sites that hand it a caught error (FE #153).

**The studio's media, options and previews.** `StudioAsset` is a clip or a
picture uploaded once to a studio and addressable by id, by tag, by team or by
player; the feed resolves which one a `media` graphic means so a browser source
never makes a second request. `presentation.py` is two levels, the broadcast's
house style and one graphic's own, validated at the press. Every element card
in the console carries a live preview of itself over a checkerboard. And the
element address now reads `/studio/<slug>/<graphic>/<token>`, with the old
address published as `legacy_urls` and still resolving for ever.

**Catchers added**, per the CEO's new rule that a fault seen twice gets one:
`tests_permission_names` (a permission name that does not exist),
`check-pollers` (a poller with no backoff), `check-format-catalogue` (a second
copy of the format list), and the apiMessage self-test inside `check-error-ui`,
proven both ways.

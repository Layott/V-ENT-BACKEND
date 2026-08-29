# Handover, 29 August 2026: the Rivalry Series, and a Bracket tab that never worked

Live: BACKEND#70/#71, FRONTEND#82/#83, all merged and deployed. In flight on
`feature/rivalry-frontend` (frontend): the fixture visualizer and three missing
functions.

Gates: `V-ENT/GATES-RIVALRY.md`.

---

## The format

Five nations, two seats each. A MATCH is one player v one player; a FIXTURE is
one nation v one nation, made of two matches. Every pair meets once: 10 fixtures,
20 matches. **The fixture is decided on goals added across both matches, never
on matches won** - 3-0 then 0-2 is 3-2 and the side that lost a match takes it.
A level aggregate is a draw. Two tables, both live: nations on the fixture,
individuals on their own match.

Most of it already existed (`TieFixture`, `LeagueRules`, `services/league.py`).
What did not:

- **Round-robin generation created fixtures and nothing inside them.** The
  standings read `TieFixture` rows, so an aggregate league had a schedule and no
  way to record a score. `_generate_round_robin` now reads
  `LeagueRules.players_per_team` and creates one row per seat, seat N v seat N.
- **`create-tournament` never created `LeagueRules`**, and the seat count lives
  on that row - so every league generated as a plain round robin.
- **The league endpoints were admin-only.** `set_league_rules` and
  `record_fixture` went through `resolve_admin`, so the person running a league
  could not set its points or enter a score.

---

## Three faults worth remembering

### `str(game).title()` made most of the catalogue uncreatable

Create looked the game up after mangling the name: "EA FC 25" became "Ea Fc 25",
"PUBG Mobile" became "Pubg Mobile". Four of six seeded games could not be
selected, and the error blamed the organiser for naming a game that was there.

### A permission that does not exist fails silently

The first fix named `manage_tournaments`. `may_override` answers **no** to an
unknown permission rather than raising, so the admin path stopped working and
only the tests said so. The real one is `cancel_tournament`.

### The Bracket tab threw for everybody, and had done for a long time

Three functions were called and defined nowhere in the codebase:
`normalizeRounds`, `getReporterRegistrationId`, `identifyParticipant`. Opening
the tab produced "X is not defined" and an error page. Nobody had noticed
because nothing in the test suite opens it and the Chrome walks had never
clicked it.

All three are now written. `identifyParticipant` is deliberately conservative:
it answers "yes" only when a side matches this session by id or username,
because the participant score-report flow is gated on it and that flow moves
money-adjacent state.

---

## The visualizer

`src/components/view-tournament/bracket-visualizer/`. Public, and two views the
reader chooses between:

- **Map** - boxes. A knockout gets columns that halve; a league gets matchdays
  side by side with no connecting lines, because nobody advances and drawing
  lines there joins fixtures that have nothing to do with each other.
- **Grid** - a round robin gets a crosstab of everyone against everyone, which
  is the better read by a distance: you can see who somebody has left to play.
  A knockout gets a per-round list, which is what fits on a phone.

The default follows the format; the switch is the reader's.

The old chart is kept as the **organiser's editing surface** and is drawn only
for non-flat formats, where its halving assumption holds.

---

## Verified

837 backend tests. A real five-nation league created through the wizard on the
live code path: 10 fixtures, 20 matches, Nigeria 3-0 then 0-2 settling 3-2, both
tables recording that Ghana's seat-two player took three points while Ghana lost
the fixture. Both visualizer views walked in Chrome in French.

---

## Still open

- The organiser's day-by-day running order (fixtures assigned to days).
- Seeding controls in the UI (automatic from results, and by hand).
- The influencer-locked ticket tier.
- The CEO's newest asks: a guest pressing "Open my ticket" in the email lands on
  a login page (the email now sends guests to `/events/find-ticket`, but the one
  they were looking at was sent before that deployed - needs confirming live);
  and tournament-event ticket linking, where the organiser decides whether
  entrants buy tickets, pay a separate entry fee, or earn tickets by reaching a
  round, and at which tier.
- Then: `V-ENT FEATURES DEEP.pdf`, and building tournament, event and ticketing
  features from it.

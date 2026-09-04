# Handover, 4 September 2026, second session

Continues `HANDOVER-2026-09-04-run-of-show-and-door.md`. That one covers the run
of show and the live door. This one covers what came after: the Rivalry Series
roster tool and the eight broadcast graphics, both asked for with a two hour
deadline while the event was running.

**The session was paused by the CEO before it was finished. Section "Where it
was stopped" says exactly what is done, what is not, and what is unproven.**

---

## The asks

| Inbox | Ask, in the CEO's words | Where it stands |
|---|---|---|
| 69 | "start working on the rivalry series tournament, add in the players and teams", then "i mean on the main rivalry series tournament i created myself", then "You can't like code it in? If I give you all the info from here? Not like hard coded but like just to fill in the slots?" | **tool built, tested, proven. Waiting on the ten names** |
| 70 | "these are all the overlays that will be used, please you can recreate them in a way good for the site, that they will be usable on the production studio ... show information based of proper stats from the tournament" | built, rendered, committed. **Not walked on a real broadcast** |

Both are on `feature/run-of-show`, pushed: **BE #151**, **FE #172**.

---

## 1. The roster tool

`manage.py rivalry_roster`, in `vent_tournament/management/commands/`.

The CEO asked for it in the shape it takes: coded in, not hard-coded. **Nothing
about Nigeria, Ghana or any person is in the file.** The roster is a text file
in the shape somebody writes one in a message:

```
Nigeria NGA
  @tobi  Tobi Adeyemi
  @kunle Kunle Bakare
```

    python manage.py rivalry_roster --tournament rivalry-series-season-2 \
        --roster roster.txt --dry-run

Then again without `--dry-run`. `--generate-fixtures` builds the round robin,
`--create-missing` makes an account for somebody who has none.

**The order in the file is the seat order.** The first player under a side sits
seat 1 and is written as the captain, because `_seat_players` in
`services/bracket.py` reads members captain first and then by when they were
added. Seat 1 only ever plays seat 1, so the order is the format rather than a
presentation detail.

What it refuses to do quietly:

- a handle matching no account **stops the run and changes nothing**, and lists
  who it could not find. Half a roster applied is worse than none: the graphics
  would then show three nations and nobody would notice the fourth was missing
- an account is only invented with `--create-missing`, every one is printed, and
  each has no usable password
- running it twice changes nothing, because on the morning of a show it will be
  run again after a name is corrected

**15 tests**, and proven for real against a database: five sides, ten seats,
five entries, fifteen ties.

A template for the CEO is `V-ENT/tasks/rivalry-roster-TEMPLATE.txt`, already
carrying the five nations and tags from the event flow sheet.

### What is still needed

**The ten names.** The STREAM ELEMENTS tab says "Ten players. Names are the
blocker: six legal names are still not held", so this may still be genuinely
open on the CEO's side. Nothing can be invented here: a name on a graphic that
nobody can prove is the fault `feedback_no_face_you_cannot_prove` records.

**Somewhere to run it.** The tournament is on production and this session has no
way to reach it. The CEO was asked and chose "send me the ten names", so the
intended path is: they send names, the file is filled in, they run one command
on the server.

---

## 2. The eight overlays

The list is the **STREAM ELEMENTS** tab of `RIVALRY SERIES SEASON 2 EVENT
FLOW.xlsx`. No image files ever arrived with the CEO's message, and that
assumption was stated back to them rather than buried.

The contract both halves were built against is
`V-ENT/tasks/overlay-elements-contract.md`. Read it before changing any of this:
it is what stopped the two halves guessing at each other.

| kind | sheet | what it draws |
|---|---|---|
| `fixture_card` | D3 | the two nations and both seat match-ups |
| `fixture_result` | A2 | the aggregate end card, both legs, points |
| `match_result` | B7 | one match, full time |
| `head_to_head` | B4 | two players and their records |
| `break_screen` | B2, C4 | with a live countdown |
| `now_next` | C2 | **off the run of show** |
| `award` | D8 | player of the day |
| `explainer` | A5 | the aggregate rule |

And two extended: `standings` gains `table: nations | players` (D1, D2), and
`scorebar` gains the running aggregate and the seat marker (A1, A4).

**A1 is the one that matters most.** Without the aggregate beside the live score
a viewer sees Ghana winning 2-0 and has no way to know Ghana are losing the
fixture. It is the first thing on the sheet's own P1 list and it did not exist.

### The feed

`views_overlay_feed.overlay_feed` gains `rivalry` and `run_of_show`. Every
number comes from `services/league.py`, which already computes them. **The
studio does no arithmetic**, and a second implementation would eventually
disagree with the page the players are reading.

Both blocks move the feed's `version` string, or an element page's
`if (version === last) return` skips the redraw and the graphic freezes on the
first frame it ever saw. That has already happened once, to squad depth.

---

## 3. Two faults found by RENDERING it, not by reading it

Both would have been invisible until the broadcast.

### 3.1 `rivalry.enabled` was false on a real aggregate tournament

The guard asked `bracket_service._seats_for`, which reads `LeagueRules` and
answers 1 when there is no row.

That is right where it is used: a format nobody configured must not silently
become an aggregate league, and bracket generation depends on it. It is wrong in
the feed. **An organiser who set the format to aggregate and never opened the
league settings would have got a blank score bar, a blank fixture card and two
empty tables, on air, with nothing anywhere saying why.**

The feed now counts the seats the DRAWN TIES actually have. That cannot turn a
plain round robin into a rivalry, because a plain round robin's ties carry one
fixture each or none, and it is right for a tournament nobody has configured,
which is the state the real one is very likely in.

### 3.2 The league schedule builder seated nobody

`services/schedule.py` wrote `TieFixture(tie, slot)` and stopped.
`services/bracket.py` has filled `player_1` and `player_2` from `_seat_players`
since it was written.

Two generators for one job, and only one of them doing half of it. What it costs
does not show until the broadcast: **the player table has no rows, the caster
head to head has nobody to compare, and the results desk cannot say who is
playing.** Found against a real five nation draw.

This is the "two surfaces, one job" shape again, and worth a parity row.

---

## 4. Where it was stopped

The CEO paused the session. Everything below is the honest state.

### Done and committed

- the roster tool, 15 tests, proven against a real database
- the eight kinds, the feed blocks, the migration, the tests
- the eight components, the two extended ones, en/fr/pt for every key
- `manage.py test`: **2480 tests, OK**
- frontend catchers: keys, dict parity, design, css classes, user chips, control
  bytes, all clean

### Rendered and seen

Against a real aggregate tournament seeded for the purpose
(`rivalry-series-season-2-demo`, studio session token in the local database):

- **fixture_result**: `Ghana 4 - 3 Ivory Coast`, seat 1 `Chidi Okeke 3-2 Ngozi
  Anyanwu`, seat 2 `Emeka Obi 1-1 Sadiq Aliyu`, points row
- **standings**: five nations, P W D L GF GA GD PTS, ranked
- **break_screen**: title and a countdown that ticks
- **award**, **explainer**, **now_next**: all drawing
- the empty states of every rivalry element, before the data existed

The frontend agent also built a contact sheet showing the whole set on a green
backdrop. **The names in it are its own test data, not real people**, and the
CEO was told so in the same message.

### NOT done, and not claimed

- **No walk on a real broadcast.** Nothing has been through OBS, and nothing has
  been seen at 1920x1080 on a second machine. The elements were rendered in a
  browser at 1745 wide, which crops a 1920 design.
- **No mobile pass** on the console's new payload editors.
- **The console panel** (`StudioPanel.js`) was changed by the frontend agent to
  offer the new kinds and their payload editors. It was NOT pressed.
- **`--generate-fixtures` was proven, but the redraw path was not**: the demo
  tournament had its ties deleted and rebuilt by hand to pick up the seating fix.
  `build_league` refuses to regenerate over an existing schedule, deliberately,
  so a real tournament already drawn will NOT gain its seated players by running
  the command again. **Somebody has to decide what to do about that**, and it is
  the first thing to look at: the CEO's tournament may already be drawn.
- Both agents were still running when the session was paused. Their work is
  committed; their final reports were never read.

---

## 5. Still open from earlier today

Unchanged from the previous handover: inbox 47, 50, 51, 52, 53, 63 and 66.
63 (no path to attach an event or tournament to an organisation) and 66 (the
map wording) both have their diagnosis written down there.

---

## Local state left behind

`local-dev.sqlite3` only:

- tournament `rivalry-series-season-2-demo`, `aggregate_2v2`, five squads of two
  seated demo players, fifteen ties, six settled with real scorelines
- a live `BroadcastSession` on it with ten elements switched on
- everything from the previous handover: the `rivalry-series-season-2` event with
  its 161 cue run of show, and three test tickets

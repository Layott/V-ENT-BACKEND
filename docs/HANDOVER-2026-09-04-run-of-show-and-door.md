# Handover, 4 September 2026, morning session

Continues `HANDOVER-2026-09-04.md`, which covers the EAFC work from the night
before. This one is the morning of the **Rivalry Series Season 2** production:
the run of show, and the door, both worked against a live event.

**If you read one thing, read "The door, and what is still unproven". The event
was running while it was being fixed and one part of it is not verified.**

---

## The asks, in the order they arrived

| Inbox | Ask, in the CEO's words | Where it stands |
|---|---|---|
| 55, 56 | "the production for this event is by 10am this morning so we have to get the overlays working, i will give you the overlays here" | **waiting on the files.** They were never sent |
| 57 | "for the programe flow for an event ... really good ui for mobile" | done, FE #172 / BE #151 |
| 58 | "share the event flow to people, can decide to make it public or not" | done |
| 59 | "create it on the site and show me how it looks" | done, two screenshots sent |
| 60 | "go through all the things left to do in inbox or tasks, and then fix them all" | partly. 47, 50 to 53 untouched |
| 61 | "the event flow has been corrected and updated, this is it ... Revision 2" | done, that file is the test fixture |
| 62 | "it sould be showing people with usernames closest to that on the platform ... and their profile images" | done for five fields, **not walked in Chrome** |
| 63 | "how to add events or tournaments to an organization? i dont see that path" | **not started.** Diagnosis below |
| 64 | "please go through the ticket scanner and make sure it works properly" | done, four faults found |
| 65 | "it doesn't show the badge and profile picture" | done |
| 66 | "what does it also mean by organizer has not pinned?" | **not started** |
| 67 | "this person registered and when we scanned it said not on the list" | done, cause and fix below |
| 68 | "different scanners for different days" | done, 9 tests |

Open PRs: **BE #151**, **FE #172**, both on `feature/run-of-show`, both carrying
the run of show AND the door work. The door commits can be cherry-picked ahead:
`2ad91186` on the backend, `11978ad` on the frontend.

---

## 1. The run of show

### What it is, and what it is not

Not the existing Programme tab. That one is `EventSession`: what is on, where in
the venue, and does the room hold me, for somebody deciding whether to come. A
run of show answers a different question for different people: at 13:39 what is
on screen, who is driving it, and what comes next.

`RunSheet`, `RunSheetDay`, `RunSheetItem` in `vent_tournament/models.py`, beside
`TournamentOverlay` and `BroadcastSession` and for the same reason: they hang off
a tournament OR an event and all six routes are mounted under both prefixes from
`views_runsheet.py`.

### Private by default

Three states, because the middle one is what an organiser wants most days:
`private`, `link`, `public`. A sheet a reader may not see answers **404**, not
403, because a refusal confirms there is one.

### The import is the front door

Nobody writes a run of show in a web form. `.xlsx` or pasted TSV.
`runsheet_import.py`:

- columns matched **by name** wherever they sit
- a blank `PHASE` continues the band above, which is what a merged cell means
- **red bold is read off the font as "not confirmed"**, because the sheet's own
  LEGEND tab says that is what it means, and in the source that fact exists only
  as a colour
- worksheets that are not running orders are skipped, and the CEO's file has two

### Times are times

A run sheet says 13:39 because that is the clock on the wall of the venue.
`RunSheet.time_zone` names the zone and NOW is worked out against it. Converting
to the reader's own zone tells a caster in London the wrong time to be on air.

### The screen

`src/components/run-of-show/`. One component for the event page, the tournament
page and the share link. The public route is a **server component** so the
running order is in the HTML a crawler and a WhatsApp preview read. The clock
starts `null` and fills in on mount, because reading it during that render is a
hydration mismatch.

Role chips are ordered by how much of the day each desk owns (GFX 38, Casters
28), and Host and Hosts are one chip.

### Verified

Against the CEO's own Revision 2 file: **161 cues over two days**. 41 tests.
Chrome, desktop and 390x844, and pressed rather than looked at: the role filter,
ON NOW with "19 min left", the visibility chips in both directions with the
signed-out address answering 404 then 200, a real `File` through the file input,
and Remove day confirmed in the database.

### The one that was found by pressing

**The two-mount adapters had to carry `csrf_exempt`.** `@api_view` sets it on the
function it returns; a bare `def` that merely calls that function does not
inherit it, so `CsrfViewMiddleware` refused every write with "CSRF cookie not
set" before any of the module ran. Reads worked, so it looked like "saving is
broken" rather than a routing mistake.

---

## 2. The door, and what is still unproven

Four faults. Two were reported live; two came out of reading the file.

### 2.1 "Not on the list", for somebody who had registered

The CEO's photograph: 1178 on the list, `VT-CXENEJ3Q` refused.

**Cause.** The list is downloaded once before the gates open and a ticket bought
after that cannot be in it. The reload button then **locks** once scanning
starts, so there was no way back.

**Fix.** An unknown code now goes to the server whenever there is a network. The
check-in endpoint already answers every case, so this is not a second lookup, it
is the check-in itself. Offline it still refuses and now says why.

**The reload lock is gone.** It came from the scanning vendors' advice that
reloading overwrites the record of who has already been through. True of
scanners that keep both in one place; here the scan record lives under its own
`localStorage` key and `download` never touches it. The lock protected nothing
and blocked the one case that happened.

**The workaround told to the CEO at the time, which needs no deploy: refresh the
page.** That re-downloads the list.

### 2.2 A Day 2 ticket opened Day 1

`TicketTier.day` has carried the date since tiers were written and nothing had
ever read it at the door.

- a tier WITH a day admits only on that day
- a tier WITHOUT one admits on any day, so **no event that exists today changes
  behaviour**, which is the property that made this safe to ship on a show day

409 `WRONG_DAY` carries the day the ticket is for, its label, and the day being
scanned. A wrong day **does not mark the ticket used**. `?day=` pins a door,
which is how two scanners run side by side. The door list carries `tier_day` so
a phone with no signal applies the same rule. 9 tests in `tests_door_day.py`.

### 2.3 The camera worked from a frozen copy of the state

The camera loop is a `setTimeout` chain started once, closing over `decide` as it
was at that moment. `decide` read `scanned` and `pending` out of React state, so
every camera scan after the first wrote `{...stateFromBeforeAnyScan, [code]:
now}` and **dropped every earlier one**. Duplicates stopped being caught after
the first person and queued check-ins were lost before they could be sent.

Typing a code was fine, because the form re-reads `decide` on every render. So
the fault was invisible to anybody testing by typing and appeared only at a door
with a camera and more than one person. The record lives in refs now.

### 2.4 The queue retried at full speed

`useEffect(() => flush(), [flush])` with `pending` in the dependencies: every
failed attempt handed `setPending` a new array, rebuilt `flush`, fired the effect
again. On venue wifi the door hammered the API as fast as it could be refused.
Now: one at a time, a doubling gap, and a settled 4xx counted and shown rather
than retried for ever.

### REPORTED UNCONFIRMED

**The queue did not reach the database in the local walk.** Two scans of
`VT-AALXM53L` gave the right answers on screen ("Let them in", then "Already
used at 06:56 on Main"), but ten seconds later the ticket was still `valid` in
sqlite and the page still said "1 waiting to send".

What is known: an `OPTIONS` preflight to
`http://127.0.0.1:8000/event/ticket/VT-AALXM53L/check-in/` returned **200**, and
no `POST` followed it in the capture. That points at the preflight response
rather than at the queue.

Worth knowing before chasing it: the page is served from `localhost:3005` and
the API from `127.0.0.1:8000`, which are different origins. Production is not
arranged that way. **This behaviour is not a regression from this session** - the
old `flush` sent the same request with the same headers - but it means the send
half of the door is verified by reading and by the backend tests, and not by a
walk. Say so rather than round it up.

### And three smaller ones

- The same QR was read every 700ms while somebody held their phone up: the first
  frame said "Let them in" and the next said "Already used", in red, at the same
  face. A repeat within three seconds is one person now.
- `useSearchParams` with no Suspense boundary, which every other page here that
  reads the address has.
- "0 on the list" was shown for three different causes. Each says which now, and
  the door reads the session from `/api/auth/session` as well as from the hook.

---

## 3. Why half a morning went into a dev server

`pnpm build` was run to prove the run of show compiled. After it, **every client
page stopped hydrating**: the scan page rendered its HTML, showed "Checking your
account" for ever, and a form submit did a full page navigation, which is the
proof that React had never attached.

Two hours of that were spent looking for a fault in the scan page: the session
hook, the Suspense boundary, the provider tree, a direct read of
`/api/auth/session` that also never ran. All of it was the dev server.

**The cure was `rm -rf .next` and a restart. Ready in 2.4s afterwards, and the
door came up with "3 on the list" immediately.**

`feedback_pnpm_build_breaks_dev` records the store-corruption version of this and
prescribes `pnpm store prune`. This time the store was fine and only `.next` was
poisoned, so **try `.next` first**: it costs ten seconds.

The tell to look for, and it is a good one: **a form submit that navigates the
page is a page that never hydrated.** No console error accompanies it.

---

## 4. Also fixed

**The event organiser had no face and no badge.** Reported on the live page:
"it doesn't show the badge and profile picture". Two halves, both wrong:

- `serialize_event_detail` built `organizer` by hand with three keys, so there
  was no avatar and nothing saying whether to show the founder mark. It goes
  through `_person` now. **The tournament payload was fixed for this on 2
  September and the event one was not**, which is the two-surfaces shape again.
- The page drew a hardcoded `<FaCrown />` in place of the picture and passed
  `size={0}` to `UserChip`, so the face could never appear however good the
  payload was.

**Username fields now pick a person.** `UserPicker` moved from `community/` to
`components/user-picker/` and gained a `purpose` prop: `message` honours
`allow_direct_messages`, anything else lets any findable person be chosen.
Wired into the organisation invite, event managers and door staff, the results
desk scorekeeper, and adding a player to a squad. **Not walked in Chrome.**

**`ResultsDesk` and `SquadsPanel` wrote a name out by hand**, so neither carried
the founder mark nor opened a profile. Both go through `UserChip`.

---

## 5. The checkers

Three changed, and two of them were reporting things nobody could act on.

**`check-pollers`** asked whether a FILE contained a `setInterval` and a `fetch`.
A clock ticking beside a one-shot load is neither a poller nor actionable, and it
was reporting two of those. It now asks whether the TIMER reaches the API,
following named loaders to a fixed point. 5 files noted became the 3 that really
poll. Both directions are in the self-test.

While writing it, `new RegExp(\`\b${name}\b\`)` inside a template literal made
the pattern a literal BACKSPACE and matched nothing. That is
`feedback_invisible_control_bytes` for the second time, in the checker written
because of the first.

**`check-signed-out`** counted a READ retry as a write control. A header sent
only when a token exists is a request already written for a signed-out reader; an
unconditional one still counts, and there is a fixture for each.

**`check-user-chips` was not in `check-all.py`.** Written 30 August, run by
nothing until today. Third occurrence of a catcher outside that table. It is in
now, blocking, and clean.

**`check-parity`** gained three run-of-show rows and had its sponsor logo row
repointed at the LIVE event wizard. It had reported MISSING for weeks against
`create-event-component/.../Sponsors.js`, which nothing under `src/app` imports.

---

## 6. What is left

### Inbox 63: no path to attach an event or tournament to an organisation

Diagnosed, not built. The backend already supports it: `org_link.resolve` is
called at create AND at edit for both kinds (`vent_tournament/views.py:1968`,
`vent_event/views.py:600`). What is missing is entirely frontend:

- the organisation console has Members, Invites, Teams, Clubs, Profile,
  Verification - **no Events and no Tournaments tab**
- neither edit screen offers the organisation field, so the only way to attach
  one is at creation time in the wizard

So it is the same two-surfaces shape: Teams and Clubs have their org tab, Events
and Tournaments do not.

### Inbox 66: "The organiser has not pinned this venue on a map yet"

The CEO asked what it means. It means `latitude` and `longitude` are unset, so
there is no pin to draw and only a maps search link. Two things to do: say it in
words a reader understands, and give the organiser somewhere to actually pin it.

### Inbox 47, 50, 51, 52, 53

Untouched. 47 is the 22 endpoints with no screen; 50 to 53 are the overlay
position, layers, text layers and the production console split, all recorded in
`memory/project_overlay_layers_pending.md`.

### The overlays for the production

**The files were never sent.** The upload path, the binding and the URL for OBS
are all built and were verified on 3 September; nothing in this session touched
them. When the files arrive, the path is the Production tab on the event or
tournament console.

---

## Local state left behind

`local-dev.sqlite3` only:

- event `rivalry-series-season-2`, created by `demo_organizer`, carrying the real
  161 cue run of show, **public**
- three test tickets on it: `VT-AALXM53L` (Amara Okafor), `VT-UEQJNDEF`,
  `VT-77QMC8Q3`
- tournament `naija-free-fire-weekly-12` has a five cue run sheet dated today,
  visibility `link`, used to prove the ON NOW panel

`.env.local` now points `NEXTAUTH_URL` at `http://localhost:3005`, and 3005 is in
the backend's dev CORS origins.

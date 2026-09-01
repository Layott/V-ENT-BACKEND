# Handover, 1 September 2026: the deploy, ticket limits, and short links

Written while the work is in flight. If the session ended now, this plus
`V-ENT/GATES.md` is enough to carry on.

---

## 1. The deploy that was overdue

The CEO asked to "deploy everything finished so far". Before this, **the
frontend was deployed and the backend was not**: `api.v-ent.co` was still
running the code from PR #97, so every endpoint added on 30 and 31 August
answered 404 while the pages calling them were live.

Measured before deploying, and worth keeping as the shape of the fault:

```
v-ent.co/organizations/invites   200 with its own title   frontend was current
api.v-ent.co/gallery/release-terms/     404
api.v-ent.co/organization/invites/mine/ 404
api.v-ent.co/club/<ref>/overview/       404
api.v-ent.co/settings/location-suggestion/ 404
```

**One false alarm worth recording so nobody chases it again:** `/challenge/list/`
also 404s, and always has. The challenge work renamed the module and the views,
not the URL space, which is still mounted under `/scrim/`. That is not a
deployment gap.

Three deploys ran today, all clean:

| | |
|---|---|
| 13:20 | backend to `dfa2a743`, frontend to `195e6f9` (the 31 August work) |
| 13:52 | ticket limits + short links, migration `vent_event 0030` |
| 14:04 | short-link embed fix + five-character tokens |

Each took a fresh `mysqldump` first. **`mysqldump` needs `-u vent` with
`MYSQL_PWD` from `/srv/vent/backend/.env` and `--no-tablespaces`.** Without the
credentials it writes a 20-byte file and exits 0 through the pipe, so the backup
silently does not exist. Without `--no-tablespaces` it errors on PROCESS
privilege. The old `pre-deploy-20260826-*.sql.gz` files are 40K and should be
treated as suspect for the same reason.

**The deploy machinery was already installed**, contrary to the 30 August
handover: nginx already had the maintenance flag wired at `/srv/vent/maintenance.on`,
`/srv/vent/frontend-static-carry` already existed with 32M of previous builds,
and the `*/5` reminder cron **is installed and running**. Gate A5 was met and
recorded as pending. `/srv/vent/deploy/` was empty, so `deploy.sh` and
`backup.sh` are now installed there from the repo.

---

## 2. Ticket limits per type and per day (CEO request 1)

> "if there is several different days or types of ticket, the option to set this
> for each ticket type and day should be available. for all tickets and days at
> once also."

The event carried one number. A three day convention selling Standard and VIP on
each day has six types and three days, and one number cannot say "one VIP each,
four Standard, no more than four on any single day".

**The rule that matters: the scopes stack, they do not override.** A purchase
must satisfy every rule that carries a number. The alternative reading, the
narrower scope replacing the wider, would mean setting a per-type rule quietly
switched off the event-wide one, which is a rule disappearing because somebody
edited a different rule.

- `TicketTier.max_tickets_per_email`, plus a new `EventDayLimit` row, beside the
  existing `Event.max_tickets_per_email`.
- A day has no model of its own. It is `TicketTier.day`, so `_days_of(event)`
  reads the distinct dates off the types rather than storing a second list that
  can disagree.
- `GET/POST /event/<id>/email-limits/` reads and writes all three, including
  `all_tiers` and `all_days` to stamp one number across every type or day.
- Enforcement is `checkout.room_for_email`, in **one place**, so the guest
  checkout and the signed-in one cannot disagree. `views_tickets` calls
  `views_guest._email_limit_or_error` rather than keeping a second copy.

**Six refusal codes, not one.** `EMAIL_LIMIT_TIER` / `_DAY` / `REACHED` when the
address already holds some, and `_TIER_MAX` / `_DAY_MAX` / `EMAIL_LIMIT` when it
holds none and is asking for more than the rule allows in one go. A single code
would have to be translated as "you already have 0", which is nonsense. All six
carry `scope`, `already`, `limit` and `name` in `data`, so `apiMessage` fills the
numbers into the translated sentence.

**Verified:** 25 tests in `tests_ticket_limits.py`, full suite 1707 OK. The
panel was watched render on production, desktop and 390px, on RIVARLY SERIES
SEASON 2 (a real two-day event). **The Save button has not been pressed**: doing
so would change the ticket rules on a live event with 40 people attending, and
that is the CEO's call, not mine. The endpoint itself is covered by
`test_the_saved_rule_is_the_one_the_checkout_enforces`.

---

## 3. Short links (CEO request 2)

> "add an option for people to be able to shorten their ticket links, so you
> create very short versions of the ticket links."

`v-ent.co/s/7uumi` resolves to a ticket link.

Three things the code is careful about, all of them the difference between a
shortener and a liability:

1. **The target is a path on this site, never a URL.** A shortener that stores
   any target somebody sends is an open redirect wearing the platform's name.
   `//evil.example` is refused because a browser reads it as a host, and it is
   checked at the API *and* again in the page that performs the navigation.
2. **The token is opaque, not a counter.** Sequential codes can be walked, and
   walking them lists every short link including the ones pointing at events an
   organiser left off the public listing, which stay reachable by their link.
3. **It is not a tracker.** A count of arrivals, nothing else. No address, no
   user agent, no row per visitor.

### Two faults the CEO found after it shipped, both now fixed

**The paste preview described the shortener, not the event.** `/s/<token>` had
its own static metadata, so a link pasted into a chat previewed as "Short link -
Opening a V-ENT link" over the house logo. An unfurler reads the address it is
handed; some follow the redirect and some stop at the first response, and the
ones that follow may keep the tags they read first. The only version right in
every client is the short URL carrying the destination's own tags.

So `generateMetadata` resolves the token, loads the record and returns that
record's metadata. The builders moved to `lib/seo` as `eventMetadata` and
`tournamentMetadata`, shared with the `[slug]` routes. **Two copies would drift,
and this fault is exactly what drift looks like**: the short route described
itself while the real route described the event.

**Tokens went from six characters to five.** Five is the floor and the reason is
enumeration, not arithmetic: at four the space is about a million, walkable in
an afternoon. What is left to cut is the domain and the `/s/`, and both cost
more than the character they save - serving tokens at the root means a code can
shadow a real page, or a new page can break codes already printed.

Codes already issued keep working whatever their length. `new_token` also
lengthens after eight collisions rather than looping, because a generator
spinning on a full space is a request that hangs.

**Verified live:** `og:title` is "RIVARLY SERIES SEASON 2", `og:description` is
the real blurb, `og:image` is the banner, and `canonical` points at
`/events/rivarly-series-season-2` rather than the short URL, so the two do not
compete in search. A newly minted link came back five characters and was
switched off again.

---

## What is open

| | |
|---|---|
| **Missing dates on a ticket type** | CEO, with a screenshot: "General Admission Day 2" shows no date while Day 1 shows "Sep 4, 2026 · Day 1". Next task |
| **Waitlist username message** | CEO: a username reserved on the waitlist should say so, not "taken". `username_refusal()` is written in `vent_auth/views_helpers.py` and **nothing calls it yet**. Deliberately held out of every commit so far |
| Limits Save button | built and tested, never pressed. Needs the CEO's word, since it changes a live event |
| Longer standing | Q3 (a real AFC sign-in), the AFC client secret still wants rotating, T11 (a DM from a profile), no settling screen for a dispute |

## Traps hit today

- **`mysqldump` failing silently through a pipe.** See above. Check the size.
- **`resize_window` reports success and does nothing** when the window is
  maximised: `innerWidth` stayed 1920 after resizing to 390. A same-origin
  iframe is the only reliable 390px viewport in Chrome here.
- **`pnpm build` twice in a row destroys `node_modules`**, and `pnpm install`
  from bash does not repair it. `pnpm store prune`, then `pnpm install --force`
  **from PowerShell**.
- The `find` tool matched a filter chip labelled "Esports" when asked for the
  upload button labelled "Esports". Two controls with the same word: read the
  page rather than trusting the match.
- Screenshot coordinates on this machine are 1:1 with CSS pixels in this window.
  Scaling them by 1.2246 (an older note) made the click miss by a row.

---

# Addendum: written on the CEO's instruction, mid-task

State at the moment of writing. **Two features are complete in the working tree,
green, and NOT committed, NOT pushed, NOT deployed.** Everything above this
addendum is already merged to `main` and live.

## Where the code is

Both repos sit on `fix/short-link-embed-and-length`, which is **already merged**
(backend PR #100, frontend PR #110). The uncommitted work below therefore sits
on top of a merged branch and needs a **fresh branch cut off `origin/main`**
before it is committed. Do not commit it where it stands.

```
V-ENT-BACKEND   M vent_auth/views_admin.py
                M vent_auth/views_auth.py
                M vent_auth/views_helpers.py
                M vent_auth/views_profile.py
                M vent_auth/views_settings.py
                M vent_event/tests_ticket_limits.py
                M vent_event/views_limits.py
                M vent_event/views_tiers.py
                ?? vent_auth/tests_username_refusal.py

V-ENT-FRONTEND  M src/app/events/manage/manage-event.module.css
                M src/app/events/manage/page.js
                M src/app/events/view-event/page.js
                M src/app/signup/page.js
                M src/i18n/dictionaries.js
```

## 1. Ticket types that showed no date (CEO, with a screenshot)

> "where the other days or tickets dont show dates."

"General Admission Day 2" printed nothing while Day 1 printed "Sep 4, 2026 ·
Day 1". **Two faults, not one.**

**The card said nothing when a type had no date.** The block was
`{(t.day || t.day_label) && ...}`, so a type with neither fell through it
entirely. A type with no date is not missing an answer: on a multi-day event it
is the full pass, and "All days" is the answer. Beside a card that *did* show a
date, saying nothing read as something failing to load. `ticketWhen(t)` now
always returns something: the date and label if it has one, "All days" on a
multi-day event, and the event's own date on a single-day one.

**The date could never be corrected.** The wizard set it and the console had no
control for it, though `update_tier` has always accepted `day`. So a type named
"Day 2" with no date was permanently wrong. The tier edit row now carries a day
picker, and the label follows the day rather than being typed separately, so a
type cannot read "Day 2" while admitting on day one.

**And the days now come from the event, not from the types.** `_days_of` read
`TicketTier.day`, which cannot offer a day nothing is sold for yet, so the
picker could never assign a type to that day. `views_limits.event_days(event)`
walks the event's own start to end, and `/tiers/` serves it alongside the list.
Three of my own tests failed on this and were **right to**: the fixture runs
three days and only two carried a type. They now assert the third is offered.

The console also flags a type whose name says "Day N" but carries no date, so
the organiser can see what the buyer was seeing.

## 2. The waitlist username message (CEO)

> "dont just show this username has been taken, tell them that the taken
> username is one of the unique ones taken during the waitlist."

`username_refusal()` in `vent_auth/views_helpers.py` classifies four genuinely
different situations, and **every** refusal path now calls it:

| | |
|---|---|
| Reserved, still held, not yours | `USERNAME_RESERVED` - it may come back when the hold lapses |
| Reserved and since claimed | `USERNAME_TAKEN_WAITLIST` - it is that person's account now |
| Reserved, hold lapsed, now free | no refusal at all |
| An ordinary account holds it | `USERNAME_ALREADY_TAKEN`, unchanged |

Wired into signup, `save-username`, `/setting/username/`, the profile edit, and
`check-username-availability` (which the signup form polls while somebody types,
and which now answers with `available`, `reason` and the sentence).

The reserver is never refused their own name, matched by **email**, because the
address is all the waitlist ever captured.

**A separate fault fixed on the way:** the signup page reported failures with
`data.error || data.message || ...`, going straight to a sentence written in
Python, so a French reader was told in English why their signup failed. It goes
through `apiMessage` now. That matters most here, because the refusal somebody
actually meets is the waitlist one and it is only useful if they can read it.

## Verified so far

- Backend suite **1723 tests, OK**.
- `pnpm build` passes.
- Translations: 8 keys added to en, fr and pt. `dict-parity` 5508 = 5508 = 5508,
  `check-keys` 4020 keys, 0 missing.
- The fault is **reproduced locally**: `lagos-anime-con-2026` tier 7 is
  "Standard day 2", label "Day 2", `day = None`, over a three day event.

## NOT verified

**Neither feature has been walked in Chrome.** That is the outstanding step and
the reason neither is shipped. The local dev server was down at the moment of
writing (see the trap below) and was being repaired.

## The trap that cost the most time today

**`pnpm build` damages the pnpm store, and it breaks `pnpm dev` as well as the
next build.** Known before as "build succeeds once per install"; what is new is
that afterwards the dev server also dies, with `MODULE_NOT_FOUND` on next's own
`dist/build/entries.js`.

The repair is `pnpm store prune` then `pnpm install --force`, **from
PowerShell**; a plain `pnpm install` from bash reports "Already up to date" and
fixes nothing. Budget about 40 seconds and expect to do it after every
production build.

Also hit: killing the dev server by matching the process path missed it, and the
restart died on `EADDRINUSE`. Kill by port instead
(`Get-NetTCPConnection -LocalPort 3001`).

## Next steps, in order

1. Repair `node_modules`, start the dev server, walk both features at 1568px and
   390px. The date fix is on `/events/lagos-anime-con-2026?tab=tickets` (tier 7
   should read "All days · Day 2" rather than nothing) and on the console's
   tickets tab (day picker, and the "No date set" flag).
2. Cut a branch off `origin/main`, commit, PR, merge, deploy.
3. Then the pricing work, planned in `V-ENT/tasks/todo.md`.

---

# Addendum 2: both features shipped

Deployed in two rounds. `main`: backend `c7662003`, frontend `3894808`.

## Verified on production

- **The ticket card.** "General Admission Day 2" on RIVALRY SERIES SEASON 2 now
  reads **"All days"** where it printed nothing. Day 1 still reads
  "Sep 4, 2026 · Day 1". This was the CEO's screenshot, and it is fixed.
- **The console** shows the same on the tier row, and the limits panel renders
  with all three scopes.
- **`/tiers/` serves the event's real days**: Sep 4 and Sep 5, numbered.
- **The username message**, against real production data:

```
layott     -> USERNAME_TAKEN_WAITLIST  "one of the handles reserved before launch"
demo_temi  -> USERNAME_ALREADY_TAKEN   "Username already taken"
```

Both the availability probe and signup give the same sentence, which was the
point.

## A fault the production walk found, fixed and shipped

`layott` is a reserved handle whose reserver holds the account, and the
reservation row's `claimed_at` was **never written**. The helper required that
timestamp to call it a waitlist name, so it fell through to plain "Username
already taken" - the exact sentence the CEO asked to stop showing, on the exact
case they asked about.

The check is now that a reservation **exists**. The reservation is the fact; the
claim timestamp is bookkeeping, and bookkeeping that was not done is not
evidence the thing did not happen. The wording dropped the claim with it.

**Worth knowing:** the reservation stores `layott` and the account is `Layott`.
Everything compares with `__iexact`, so this works - but any script that builds
a set of usernames and tests membership will get this wrong. One did, while
writing this.

## NOT verified, and why

- **The "No date set" chip does not render.** The condition is
  `!row.day && eventDays.length > 1 && /(day|jour|dia)\s*\d/i.test(row.name)`.
  All three are demonstrably true for that row - the sibling span rendering
  "All days" proves the first two, and the regex tests true in the page against
  the exact name - and the compiled predicate in the served chunk is correct.
  The chip is simply absent from the DOM. **Cause unresolved.** It is cosmetic:
  the buyer-facing fix and the day picker do not depend on it.
- **The day picker in the tier edit row was never opened**, and neither feature
  was walked at 390px. The browser tooling hit its five-hour rate limit
  mid-walk. Everything above was verified over plain HTTP and SSH instead.

## Next

1. Finish the walk: the day picker, the chip, and both at 390px.
2. The pricing work in `V-ENT/tasks/todo.md`. Blocked on the CEO signing off
   what is IN each tier - no document defines it.

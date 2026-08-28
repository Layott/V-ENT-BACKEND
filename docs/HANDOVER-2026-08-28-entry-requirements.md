# Handover, 28 August 2026 - entry requirements, event editing

Picks up from `HANDOVER-2026-08-28-overnight.md`. Ledger moved 71/98 to 81/98.
Backend suite 541 to 558, all passing. Frontend builds clean.

## What shipped

### Entry requirements (gate O)

The CEO flagged this one as needing to be done especially properly, and the
reason is in the shape of the ask: not four more toggles, but a list an
organiser composes.

**A requirement is a row, not a column.** Twelve kinds in
`vent_tournament/requirements.py`, across three ways of being checked:

- **automatic** - country, age, verified email, verified identity, profile
  picture, a connected game account for THIS game, the in-game name filled in,
  a team logo. Answered from what we already hold. Nobody waits, nobody reviews.
- **submitted** - follow these accounts and tell us your username, download this
  and give us the field I have named, answer this question. The organiser writes
  the field label, because "Riot ID" and "Epic username" are not the same
  question and a generic "Username" asks neither.
- **partner** - a partner's system answers. **The call does not exist yet.** What
  exists is the fallback: it is evaluated as a submitted kind, so a person
  reviews it and nothing blocks on somebody else's uptime. Gate O10 stays open
  and says so.

Two rules that hold throughout, and both are tested:

- a refusal names WHICH requirement failed, and for a team, WHICH member.
- a tournament with no requirements set stops nobody. That is the default and it
  stays the default.

**Six endpoints** in `views_requirements.py`. Reading what is required is public,
because somebody deciding whether to enter should be able to read it before they
have an account, let alone an entry fee. Refusing a submission without a reason
is itself refused - they will send exactly the same thing again otherwise.

**Wired into `join_tournament`**, so the rule bites where registration happens
rather than only in a helper. `JoinGateTests` proves the 403 and proves no
registration row is written.

**A team entry checks every member.** Seven of the twelve kinds are about a
person, so a team satisfies them once per member; the rest are about the team.
Checking only whoever pressed the button is the version that looks like it works:
the captain has everything, the team is admitted, and round one is played by
somebody whose account was never connected. The owner counts even when nobody
wrote them into `TeamMembers`.

**Two screens.** `EntryRequirements.js` on `/tournaments/<slug>/manage` composes
the list and holds the review queue. `EntryChecklist.js` on the tournament page
shows the entrant what they still owe, with the box to answer in, before they
press anything.

### Every refusal travels as a code

Walking the French page showed the chrome translated and every line the server
had written still in English. A sentence built in Python cannot be translated by
the page, which is why the API errors already travel as codes.

Each row now carries `code` and `params` beside the English sentence. The page
writes the sentence. `min_age_no_dob` is a separate code from `min_age` because
they are not the same problem: one is fixed in a minute on a page we can name,
the other cannot be fixed at all.

Rows also carry `waiting_on_review`. An unmet automatic check was drawing the
same clock as a submission sitting in the queue, which tells somebody to wait for
a thing nobody is going to do.

### Editing an event (the CEO's message mid-session)

> "FOR EVENTS, THERE IS NO WAY TO EDIT AN EVENT A USER ALREADY CREATED, AS A USER
> I DONT SEE WHERE TO DO THAT. THERE SHOULD BE LIKE AN OVERALL PLACE"

Both halves were true. `PUT /event/edit-event/` had existed and been tested for
weeks with nothing on the site calling it. And `/events/my-events` had exactly
one route into it: a small button on `/events`.

- `/events/<slug>/edit` covers the twelve fields the endpoint accepts and **sends
  only what changed**, because the endpoint is partial and the event carries
  about twenty fields. Sending the whole form would blank the eight the screen
  does not show.
- Renaming moves the address and the page follows it. The link shared last month
  still opens the event: the API answers `moved` with the new address.
- **The overall place** is the account menu in the header, on every page: my
  tournaments, my events, my tickets, wallet, settings. It held two items before.
  The same group is in the mobile sidebar, because on a phone that menu does not
  exist.

## Faults found that nobody had reported

- **Share did nothing on every real tournament.** The placeholder branch of
  `TournamentDetailsBanner` had a handler; the branch that actually renders had a
  bare `<button>` with none.
- **Events never sent their slug.** Every event has had one in the database since
  the slug migration. `my-events` built its own dict and included it; the public
  listing and the detail payload did not. So half the site linked events by name
  and half by primary key.
- **Nested buttons on the tournament wizard.** `InfoTip` renders a `<button>` and
  the whole toggle row was a `<button>`. Invalid HTML, a React hydration error on
  every load, and pressing the tip flipped the switch instead of explaining it.
  The row is a container now. A scanner that ignores comments finds zero left.
- **Five `console.log` calls** on the tournament page, one printing the whole
  tournament object on every render.
- **The register link carried the numeric id.**
- **`TournamentDetailsOverviewLeft` is dead code** - nothing imports it. Left in
  place, translated, with its dates reading the site's language, so reviving it
  does not revive those two faults. The page people see is
  `app/tournaments/view-tournament/page.js`.
- **`/events/<slug>/manage` and `/events/<slug>/attendees` were never gated in
  middleware.** `protectedRoutes` matches by prefix, so a route with the slug in
  the middle could never be covered. There is a `protectedPatterns` list now.

## Confirmed in Chrome

Signed in, in French, desktop. The organiser composes four requirements and saves
them; the entrant sees four rows with one met and one country refusal; sending a
username flips the row to "En attente de la verification par l'organisateur";
the organiser's queue shows it, Valider records it, and the entrant's row goes
green. Then the event edit form: loads populated, changing the venue saves and
nothing else on the event moves, renaming lands on the new address and the old
one still resolves.

**The 390px half did not happen.** `resize_window` reports success and the window
stays at 1920 outer / 1745 inner. The phone viewport is unconfirmed, and gate O13
says so rather than being ticked.

One freeze of the renderer on the event edit page, after a `triple_click` +
`ctrl+a` + `Delete` through the extension. Could not be reproduced doing the same
edit through the DOM. Recorded as unreproduced, not as absent.

## Still open

`O10` the partner call. `O11` the partner-side contract in the API doc. `O13` the
390px walk. `D1` edit tournament still reaches seven fields. `R5` stages do not
compose. `R8`, `R9`, `R11`. `I` the rates refresh failure. `S5` rate limit on
`authorize-info`. `T4` AFC still holds a revoked key - the console has an "Issue a
key" button and only the CEO should press it, because a production credential
printed into a transcript is a credential in a log. `V2` see below.

**`V2`** could not be reproduced this session: `/tournaments/<slug>/manage` opened
fine signed in as an admin, and there is no owner-specific redirect in the page or
in middleware. Not closed, because that is not the same as testing it as the
tournament's own non-admin owner.

## Local dev note

`league_boss` now has a password in the **local sqlite database only**
(`LocalDevOnly!2026`) so the owner path can be walked. It is seed data on this
machine and reaches nothing else. Production is untouched.

## Traps, still true

- `pnpm build` overwrites `.next` under a running `pnpm dev`; the dev server then
  404s every route until `.next` is deleted and dev restarted. Cost about ten
  minutes this session before it was recognised.
- **`pnpm build` deletes `node_modules/.../next/dist/pages`**, which is exactly
  what `next dev` loads at startup. This is the real cause of the
  `Cannot find module 'next/dist/pages/_app'` that has been read as "node_modules
  empties randomly" for weeks. It is deterministic: build, then start dev, and
  dev fails every time. Reproduced twice this session, and `ls` of that directory
  before and after confirms it.

  So the order is: **build last, or reinstall after building.**
  `pnpm install --force` from PowerShell restores it in about 40 seconds. From
  bash the install restores enough to build again but not enough for `next dev`.
- Screenshot coordinates scale to CSS by 1745/1425. Typing at coordinates read off
  a screenshot put text in the wrong field once. Use `find` refs.
- A heredoc containing non-ASCII characters fails in this shell. Write the script
  to a file and run it.
- When filling three language blocks in one pass, check "already present" against
  each block separately. Checking against the whole file while mutating it filled
  English and silently skipped French and Portuguese, which is exactly the failure
  the rule exists to prevent, and it took a screenshot of the French page to see.

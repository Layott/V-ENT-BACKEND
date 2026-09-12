# Handover, 12 September 2026: items 9 and 10 of the undone list, and two sessions in one tree

The CEO asked for "8 - 10" of the undone list, "fixed thoroughly". This session
did item 10, and then the `src/app` half of item 9 after agreeing a split with
the other session, `v-ent-a9`, which had items 8 and 9 mid-flight in the same
two checkouts. That session keeps item 8 and the shared components of item 9
and hands them over in its own file (`HANDOVER-2026-09-12-everything-left.md`;
gates `V-ENT/gates/32-everything-left-12-sept.md`, inbox rows 96, 245, 257).

## The rule that came out of it

**Two sessions never share a working tree.** This one found the backend checkout
on `feature/eafc-squads-and-catchers` with main merged in and uncommitted, and
the frontend the same on `feature/eafc-squads-ui`, and stopped before touching
either. Everything here was done in git worktrees beside them:

```
git worktree add ../V-ENT-BACKEND-ops -b chore/retire-the-single-instance-unit origin/main
git worktree add ../V-ENT-FRONTEND-ops -b ops/reading-room-rail-measurement origin/main
cmd //c "mklink /J node_modules ..\V-ENT-FRONTEND\node_modules"     # no second pnpm install
cp ../V-ENT-BACKEND/local-dev.sqlite3 . && cp -r ../V-ENT-BACKEND/media/anime media/
```

with its own `.env.local` (API on 8006, `NEXTAUTH_URL` on 3006) and its own dev
servers. The shared git stash was not used. `ListAgents` shows who else is on
the machine; ask before assuming a tree is yours.

One trap worth the sentence: a gate box left unticked while its own `CHECK`
already passes trips `check-stale-gates`, which runs in the pre-commit hook of
BOTH repos and blocked the other session's commits until it was ticked. Tick
with evidence the moment the check passes, and write `EXPECT` values in
backticks so they are matched literally.

## Item 10, both halves

**The retired `vent-web` unit.** Removed from `/etc/systemd/system` on the VPS
(`daemon-reload`, `systemctl status vent-web` answers "could not be found",
`vent-api`, `vent-web@3000` and `vent-web@3001` untouched and active) and from
`deploy/systemd/` in this repo, so a rebuilt box cannot get it back. The deploy
README now lists the three units that exist, says the documented deploy path is
a symlink to this directory's script, and names `verify-live.sh`.

**The reading room's right rail.** Measured inside three same-origin iframes of
the room page at 1280, 1440 and 1920 CSS px: Stick it ends 55, 63 and 63 px
inside the right edge, no horizontal overflow at any width, and a real click
landed on the button at 1280 and 1440. **No product fault.** On 10 September the
OS window was 1745 CSS px wide and the extension's clicks beyond roughly x=1100
in its own coordinate space never reached the page, so a control laid out
correctly was unreachable by the tool, and was pressed on the phone instead.
Recorded in the `reference_browser_walking` memory.

## Item 9, the src/app half: a failed load says so on the page

Twelve pages in `src/app` could sit on "Loading..." for ever or fall through
to a sentence written for a different situation when the request never came
back: "No vendors match", "Tournament not found", a wallet reading 0 VENT
COINS. Each now carries a `loadError` set from the catch AND from a
non-success body, rendered where the loading state was as the shared
`ErrorState` (one sentence from `apiMessage`, one Try again that calls the
loader so filters, tabs and typed text survive). A 404 keeps its "does not
exist" sentence; only a request that failed shows the error.

Two were real bugs, not wording. `events/vendor-shop` read `quiet` from
another effect's scope, a `ReferenceError` that spun "Loading vendors..." for
ever on production. `wallets` showed 0 VENT COINS for a balance that never
arrived; it reads "-" until known, and a failed quiet refresh keeps the numbers
already on the page rather than replacing them with an alert.

Over the union with v-ent-a9's branch the checker reads "93 file(s) checked,
0 that can spin for ever". Branch `fix/spinner-for-ever-app-pages`, PR
FE #188, stacked on #187 which is stacked on #171.

### The server half, found on the walk

With the API stopped, `/events/anime-night-lagos` showed the new error state
under a tab title "Event not found" with `noindex`. `fetchForMetadata` in
`src/lib/seo.js` answered `null` for "does not exist" and "never came back"
alike, and every record route read `null` as the first, so an outage served
each event, tournament, team, player, plan, comic, chapter and battle page as
not found, which is the one instruction a crawler acts on at once.

`fetchRecordForMetadata` answers `FAILED` for a failed request or a 5xx; the
eight routes that can say "not found" read through it and describe the page as
unavailable (title from the address, canonical kept, no noindex). A 4xx stays
`null`. The nine routes that degrade on purpose (premium, partner docs, short
links, run of show, marketplace) keep `fetchForMetadata`. Second occurrence of
the class, so `scripts/check-metadata-outage.mjs` holds it, 7 self-test cases,
proven to fail on the committed tree. Registered as blocking in
`tools/check-all.py` on this branch: **FE #188 must merge, and V-ENT-FRONTEND
be pulled, before BE #178 lands**, or the hook fails on a missing script.

### Walked

Desktop Chrome on 3006 with the 8006 API killed and then restarted: wallets
("-", the error, Try again brought 1,076 VC back without a reload),
vendor-shop, events, partners, community feed, post and thread, the event
page (title "Anime Night Lagos", robots index,follow). Android emulator
(evotv_test, 411 CSS px, signed in as demo_organizer): events list and wallet
with the API stopped and after Try again, my tickets, register tournament,
organizations/manage. My tickets showed "All 0, Active 0" chips beside the
error; the counts now hide on a failed load, as does "0 results" on the events
list.

Not done here: `pnpm build` on the union. The prebuild guard refuses while a
dev server serves the tree, and a build on a node_modules junctioned to a tree
with two other dev servers is the collision that gutted `@swc/helpers` today
(v-ent-a9's repair, mid-recipe, which broke 3006 for a minute). v-ent-a9
builds the union for gate D5.

### Two traps that cost time today, neither a product fault

- **Cookies on localhost ignore the port.** The other session signed the
  shared Chrome out at about 20:55 to try an admin account, and this
  session's signed-in walk lost its session mid-way with nothing in the dev
  log. The browser tool will not type a password, so a lost session stays
  lost until the CEO signs in. The emulator has its own cookie jar and stayed
  signed in, which is where the signed-in half of the walk was finished.
- **A `?id=` address is a redirect.** `/events/view-event?id=<slug>` is
  `LegacyIdRoute`: it resolves the slug and replaces itself with
  `/events/<slug>`, and with the API down it falls back to `/events`. That is
  by design, not a spinner, and the named address is the one to walk.

## Where things are

| | |
|---|---|
| This BE branch | `chore/retire-the-single-instance-unit`, worktree `V-ENT-BACKEND-ops`, PR #178 |
| This FE branch | `fix/spinner-for-ever-app-pages`, worktree `V-ENT-FRONTEND-ops`, PR #188 |
| Gates | `V-ENT/gates/33-item-10-ops.md`: C closed, B closed except B6/B7 (PR open; build is v-ent-a9's D5) |
| Item 8 and the rest of 9 | session `v-ent-a9`: BE #150, FE #171, FE #187 |
| Merge order | FE #171, #187, #188, #189 (their guides, stacked on #188); pull V-ENT-FRONTEND; then BE #150, then #178 |

# Handover, 12 September 2026: item 10 of the undone list, and two sessions in one tree

The CEO asked for "8 - 10" of the undone list, "fixed thoroughly". This session
did item 10. Items 8 and 9 were already mid-flight in another session,
`v-ent-a9`, in the same two checkouts, and are handed over by that session in
its own file (its gates: `V-ENT/gates/32-everything-left-12-sept.md`, inbox rows
96, 245, 257).

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

## Where things are

| | |
|---|---|
| This branch | `chore/retire-the-single-instance-unit`, worktree `V-ENT-BACKEND-ops` |
| Gates | `V-ENT/gates/33-item-10-ops.md`, all boxes closed |
| Items 8 and 9 | session `v-ent-a9`, in the main checkouts; it will push both branches and say so |
| Merge order | theirs first, then this docs branch on top, so nothing here lands under a rebase |

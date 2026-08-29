# What is NOT done, 29 August 2026

Companion to `HANDOVER-2026-08-29.md`, which says what was built. This file says
what was not, and it is the one to read first when picking the work up.

Written at the CEO's request: "save all undone and tasks to memory and handover
in very detailed manner."

Ledger: `V-ENT/GATES.md` - 51 gates met, 2 open, 2 abandoned with reasons.

---

## 0. The state of the tree, before anything else

**Nothing is committed. Nothing is on a branch. Nothing is deployed.**

| Repo | Modified | New | Deleted |
|---|---|---|---|
| `V-ENT-BACKEND` | 16 | 28 | 0 |
| `V-ENT-FRONTEND` | 94 | 23 | 2 |

Both repos are sitting on `main` with everything uncommitted. That is deliberate:
the standing instruction is that work stays local until the CEO approves a push.
It is also fragile - one `git checkout` or `git stash` loses a full day.

**The first action of the next session is to get this onto a branch**, whether or
not it is being deployed:

```bash
cd V-ENT-BACKEND  && git switch -c feature/aug29-organiser-tools
cd V-ENT-FRONTEND && git switch -c feature/aug29-organiser-tools
```

Then commit **by change, not by file** (structural rule 10 - `git add models.py`
once shipped an unfinished model without its migration and killed a deploy). The
natural commits, in an order where each one stands alone:

1. wallet PIN on send and withdraw, + `tests_wallet_flows`, + `check-required-fields.py`
2. deploy carry-forward, `cp -rT`, nginx maintenance page, `maintenance.html`
3. tab strips (42 files) + `check-tabstrips.mjs`
4. hover lift removal (79 rules) + `check-hover-lift.mjs`
5. mobile header rework, home 2x2 stats, tap targets
6. per-page help: `pageGuides.js`, `PageHelp.js`, shortened tour, + the 3 checkers
7. terms page, PDF removal, redirects, + `check-legal.mjs`
8. DM address token + `tests_dm_address` + migration `0058`
9. email template comment fix + `tests_email_leaks`
10. wallet Convert panel
11. event venue map + attendance origins (+ `geo.py`, migration `0026`)
12. OG embeds: `/api/og`, `ogImage()`, + `check-embeds.mjs`
13. poll kinds (+ migration `0027`) and poll branching (+ `0028`)
14. comp tickets (`views_comp.py`)
15. tournament invitations (+ migration `0029`)
16. one tournament console (slug route renders the tabbed one)
17. stream overlays (+ migration `0030`, `overlay_binding.py`, runtime, prompt)
18. structural rules doc + handovers

### Migrations that will run on production

Six, in this order. All are additive; none drops or rewrites a column.

```
vent_auth       0058_conversation_slug                     (+ backfills every conversation a token)
vent_event      0026_event_latitude_event_longitude_eventattendeeorigin
vent_event      0027_eventpoll_help_text_eventpoll_kind_and_more
vent_event      0028_eventpoll_depends_on_eventpoll_depends_on_max_and_more
vent_tournament 0029_tournamentinvitation_and_more
vent_tournament 0030_tournamentoverlay
```

`0058` iterates every `Conversation` and writes a token. On a small table that is
instant; it is worth knowing it is a data migration rather than a pure schema one.

---

## 1. Open gates

### A5 - the reminder cron is not installed

The `send_due_reminders` command exists, is tested (27 tests in
`vent_tournament/tests_scheduled.py`), and claims rows with `select_for_update`
so two runs cannot double-send. Nothing runs it.

Needs, on the VPS:

```bash
crontab -e
*/5 * * * * cd /srv/vent/backend && ./venv/bin/python manage.py send_due_reminders >> /var/log/vent/reminders.log 2>&1
```

Until this exists, **an organiser can schedule a reminder and it will never be
sent**. The UI does not lie about this - it says scheduled - but nothing delivers.
This is the single most user-visible piece of unfinished work.

### Z5 - merged and deployed

Needs the CEO's word. See section 0.

---

## 2. Deployment steps that are written but not applied

All three live in the repo and take effect only when a deploy runs them.

1. **`deploy/deploy.sh`** now carries the previous build's static files forward
   for a week into `/srv/vent/frontend-static-carry`, and uses `cp -rT`. **The
   deploy that installs this change is itself still broken** - the carry
   directory does not exist yet, so that one deploy will still 404 chunks for
   anybody mid-session. Every deploy after it is fixed. Worth doing at a quiet
   hour.

2. **`deploy/nginx-vent.conf`** adds `error_page 502 503 504 /maintenance.html`
   and a `location = /maintenance.html`. nginx must be reloaded:
   `sudo nginx -t && sudo systemctl reload nginx`.

3. **`deploy/maintenance.html`** is installed by the new `deploy.sh` line
   (`sudo install -m 0644 ... /srv/vent/maintenance.html`). If nginx is reloaded
   before that line has ever run, the error page will 404 and nginx falls back to
   its own. Install the file first, or run the deploy before reloading nginx.

**None of the three can be verified locally.** The next deploy is the test.

---

## 3. Built, tested, and never watched render

Every item here has passing tests and clean lint. None has been walked in a
browser, which by the CEO's own hard rule means none is confirmed. Listed worst
first.

### 3.1 The wallet PIN prompt - highest risk

`src/components/wallet/PinPrompt.js`, mounted on `/wallets/send` and
`/wallets/withdraw`.

This is the money path, it was completely broken before today (both endpoints
require a `pin`, neither page sent one), and **the prompt has never been seen on
screen**. The emulator walk reached the recipient step of Send and then the
session was pulled onto the embed bug.

To close it:

```
/wallets/send -> pick demo_chidi -> Continue -> amount -> Continue -> Send
```

The prompt should open before anything is sent. Check: four digits only, Confirm
disabled until four, a wrong PIN reports on the prompt rather than behind it, and
the balance actually moves. Local account: `demo_organizer` / `DemoPass!2026`,
wallet PIN `4417`, balance 250 VC, KYC verified.

Then the same on `/wallets/withdraw`.

### 3.2 The rest, in one pass

| Screen | Where |
|---|---|
| Comp-ticket panel | event console, Tickets tab, "Send tickets to people" |
| Invitations tab | tournament console |
| Invitation banner | tournament page, as the invited player (not the organiser) |
| Poll branching picker | event console, Polls tab, once one gateable poll exists |
| Attendee poll answering | event page, all six kinds, especially ranking arrows and the scale |
| Terms page on a phone | `/terms` |
| Maintenance page | open `V-ENT-BACKEND/deploy/maintenance.html` directly in a browser |

The emulator is the tool (`feedback_use_android_emulator`), and
`V-ENT-FRONTEND/scripts/emulator-eval.mjs` measures rather than eyeballs - it is
what caught the tab strips and the 34px drawer rows.

---

## 4. A stale check

**`pnpm build` has not been run since** the polls, poll branching, comp tickets,
invitations, one-console and stream-overlay work. That is roughly fifteen files
of JSX whose only verification is `pnpm lint` (clean) and the tests behind them.

```bash
cd V-ENT-FRONTEND && pnpm build
```

Then **immediately**:

```bash
rm -rf node_modules && pnpm install
```

A build damages `node_modules` on this machine every single time, and
`pnpm install` alone will say "already up to date" because the lockfile matches
while `next/dist` is gone. Deleting only `.pnpm/next@...` makes it worse by
breaking the peer links. Full reinstall, about 45 seconds. And **never build
while `pnpm dev` is running** - it produces exactly the unstyled screen the CEO
reported, because it rewrites `.next` under the running server.

---

## 4b. The node_modules corruption, properly diagnosed

The recurring "next is not recognized" / `MODULE_NOT_FOUND` failure on this
machine is **not** node_modules alone. The pnpm **global store** gets corrupted:

```
ERR_PNPM_ENOENT  [importPackage ...node_modules/next]
ENOENT: no such file or directory, copyfile
  'C:\Users\Sweez\AppData\Local\pnpm\store\v10\files\03\8c2e...'
```

That is why `rm -rf node_modules && pnpm install` sometimes fixes it and
sometimes does not: the reinstall copies from the same damaged store. The fix
that actually worked:

```bash
pnpm store prune      # removed 13124 files, 14 packages
pnpm install          # re-fetches what the store was missing
```

Two more things learned while chasing it:

- **`pnpm dev` intermittently cannot resolve `next` on PATH** in a Git Bash
  shell even when the package and the `.bin` shims are present. Running the
  binary directly always works:
  `PORT=3001 node "node_modules/.pnpm/next@14.2.35_*/node_modules/next/dist/bin/next" dev`
- **Killing the dev server does not always kill it.** Two starts hit
  `EADDRINUSE` on 3001 with a survivor still listening. Check with
  `Get-NetTCPConnection -LocalPort 3001 -State Listen` and stop that PID, or a
  "verified" page may have been served by a process running older code.

### The 500 on the tournament manage route was a stale cache, not a break

After the one-console change, `/tournaments/<slug>/manage` 500'd with
`MODULE_NOT_FOUND` on a webpack chunk. It is **not** a regression: there is no
circular import (the chain `[slug]/manage -> manage -> my-tournaments/manage` is
linear), and on a cleared `.next` the route serves 307 to `/login`, which is
correct for a protected route with no session. The Reminders tab was also seen
rendering at that exact URL on the emulator. Run `rm -rf .next` before
restarting after any change that moves an import.

---

## 5. Cannot be verified from this machine at all

- **The attendance map with real people.** `vent_event/tests_map.py` covers the
  rounding, the k-anonymity threshold and the consent, but nobody has stood in
  three actual districts with three actual phones.
- **A comped ticket email arriving.** SMTP was never exercised. The endpoint
  reports `emailed` and `not_emailed` per address rather than assuming, so a
  failure is visible to the organiser, but no mail has been sent.
- **Whether WhatsApp specifically renders the card.** The tags, the dimensions,
  the first-party URL and the proxy are all correct and machine-checked by
  `scripts/check-embeds.mjs`. Only pasting a live `v-ent.co` link into WhatsApp
  after a deploy proves it.
- **The overlay in real OBS.** Proven in real Chrome at 1920x1080 by
  `scripts/overlay-probe.mjs`, which is the same engine OBS embeds, but never in
  OBS itself.

---

## 6. Known, deliberate, and not bugs

- `tools/endpoint-callers.py` reports **23 known orphans**. Pre-existing, not
  from this session.
- `tournament/<slug>/overlay-feed/` is in the DELIBERATE list: it is fetched by
  `static/overlay-runtime.js` inside a browser source, not by the site.
- Text answers to a poll are shown to the organiser only. Deliberate - a sentence
  identifies a person in a way a count does not.
- Accepting a tournament invitation does not register anybody. Deliberate -
  registration is where entry requirements are checked and the fee is taken.

---

## 7. Trivia

- `V-ENT-FRONTEND/src/app/events/manage/manage.module.css` is an empty orphan
  (one blank line), untracked, imported by nothing. The page uses
  `manage-event.module.css`. Safe to delete.

---

## 8. If you only do three things

1. Get it on a branch and commit it (section 0). A day of work is one careless
   git command from gone.
2. Walk the wallet PIN prompt (3.1). It is the money path and it is unconfirmed.
3. Install the reminder cron (A5). Without it, a feature the CEO asked for
   silently does nothing.

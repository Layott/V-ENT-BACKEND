# 7 September 2026: deploying without anybody noticing, and the backup that held nothing

Two asks, both from the CEO, both closed. Then the outstanding list.

## The backup had been producing nothing, and it took three fixes

`deploy/backup.sh` called `mysqldump` with **no user and no password**. It died
with `Access denied for user 'vent'@'localhost' (using password: NO)` on every
run, and because `set -e` was on and the dump was the first command, it never
reached the media archive either. The credentials were in
`/srv/vent/backend/.env` the whole time.

Nobody noticed because **a cron that fails writes to a log nobody reads.**

Fixing it hit three separate shell traps, each worth remembering:

1. **Sourcing `.env` runs it.** `DEFAULT_FROM_EMAIL=V-ENT <info@v-ent.co>` made
   bash die on the angle brackets, which are redirections. Django's own parser
   does not care, so the file was perfectly valid and the backup was the only
   thing that broke on it. Now it greps one key at a time and executes nothing.

2. **A backtick inside a double-quoted string is command substitution.** The
   check was written as `"CREATE TABLE \`$TABLE\`"`, so it ran the table name as
   a command. A literal backtick now lives in `BT='...'` where no quoting style
   has to survive it. This is the second time a backtick has mangled something
   this session; the other was a commit message.

3. **`grep -q` exits on the first match.** That closes the pipe, `zcat` takes
   SIGPIPE and exits 141, and `set -o pipefail` turns the whole pipeline
   non-zero. So the check reported the table missing **precisely because it had
   found it**, and deleted a good backup to say so. `grep -cF ... || true` reads
   the stream to the end and never signals.

### The rule the script now follows

**A backup is verified by its CONTENTS, never by its exit code.** A dump holding
only a schema and a dump holding the whole platform both exit 0 and both look
like a file on disk. So it counts tables (floor 100) and inserts (floor 20),
checks `vent_auth_users`, `vent_event_ticket` and `vent_tournament_tournament`
BY NAME, and reads the media archive back with `tar tzf`. Anything wrong and it
deletes the file and exits non-zero, so cron mails it.

First real run: `db-2026-09-07-0217.sql.gz (152K, 162 tables, 83 inserts),
media-2026-09-07-0217.tar.gz (36M)`. Cron at 03:00 nightly.

**Still open:** a backup on the same disk is not a backup. Nothing pulls these
off the box.

## Zero-downtime deploys

The CEO asked whether the site could update while people were using it without
them knowing, and then "Do it, but what will it cost?"

Two things made the maintenance page necessary, and only one was unavoidable:

- `systemctl restart vent-web` left nothing on 3000 for several seconds. **This
  is the one that is fixed.**
- The migration window, where the schema changes before the new code is live. No
  process supervisor fixes that; only expand-then-contract migrations do.

The API never needed the page at all: `systemctl reload vent-api` re-execs
gunicorn's workers while the master keeps the socket bound.

### What was built

| | |
|---|---|
| `src/app/api/health/route.js` | `{ok, port, build, uptime}`, no-store. The port makes an answer say WHICH instance gave it |
| `deploy/systemd/vent-web@.service` | Template unit, `PORT=%i`. `ExecStartPost` polls its own health for 60s, so `systemctl restart` BLOCKS until the instance can actually serve |
| `deploy/nginx-vent.conf` | `upstream vent_web` over 3000 and 3001, `proxy_next_upstream error timeout http_502 http_503 http_504` |
| `deploy/deploy.sh` | No maintenance page. Rolls the ports one at a time, waits for health, stops on the first failure |
| `deploy/maintenance.sh` | The page KEPT, put up by hand when a change genuinely warrants it |

A template unit rather than two copies, because two copies drift: the day
somebody adds an environment variable to one and not the other is the day half
the requests behave differently and nothing looks wrong.

### Proven, not assumed

| Test | Result |
|---|---|
| Swap the old single unit for `vent-web@3000`, request every second | 40 ok, 0 failed |
| A FULL `deploy.sh` run, request every second | 40 ok, 0 failed, maintenance page never went up |
| `systemctl reload vent-api`, 25 requests at 0.4s | 25 ok, 0 failed |
| **An instance that cannot come back** (`ExecStartPost=/bin/false` on 3000) | 40 ok, 0 failed. Site stayed up on the other |

That last one is the one that matters. A deploy that fails half way now leaves
the site running the OLD build rather than half deployed.

### What it costs, measured on the box

| | Before | After |
|---|---|---|
| Next RSS | 145 MB, one instance | 80 MB + 79 MB = 159 MB |
| Available RAM | 10.2 GB of 11.9 GB | 10.2 GB of 11.9 GB, unchanged |
| CPU idle | ~1.5% | ~1.5% each |
| Load during a deploy | - | 0.95 peak, 3 cores |
| Disk | 17G of 235G | unchanged |

**About 80 MB, or 0.8 per cent of available memory.** The build is the expensive
part of a deploy and that was already happening.

### The half this does not fix

For the seconds between the migration and the last instance restarting, the NEW
schema is live and the OLD code is serving. Fine for an additive change, fatal
for a destructive one. So: **expand, then contract, in two deploys.** Written at
the bottom of `deploy.sh`.

## What was found by measuring rather than recalling

`useLiveData` was written on 7 September as the shared polling primitive and
**is imported by nothing.** The only two matches in `src` are its own definition
and its own docstring, while five pages hand-roll `setInterval`. The dead-timer
CHECKER works and reports 0; the CEO's actual ask, "all pages on the site
updating automatically", is not met at 5 pages of 87 public routes.

This is the `feedback_endpoint_needs_a_caller` class again: green tests say a
thing answers, not that anything calls it.

## State

- BE #152, #153, #154, #155, #156 merged and deployed. #157 (gitignore) open.
- FE #173, #174 merged and deployed.
- `vent-api`, `vent-web@3000`, `vent-web@3001` all active.
- Live: `/`, `/tournaments`, `/events`, `/pricing`, `/feedback` all 200.
- check-all: every blocking catcher clean, 4 carrying debt, none of it went up.
- `gates/10-zero-downtime.md`: 14 of 14, evidence measured.

# Handover, 10 September 2026, the evening: shipped

The CEO said **"ship"**. Seven pull requests were merged and deployed, and the
deploy itself turned out to be broken in a way nothing could see.

Read this after `HANDOVER-2026-09-10-anime-shipped-and-the-open-gates.md`.

## What went out

| | |
|---|---|
| Backend main | `b9a745b3` (then `09eca2d9` for the deploy fix) |
| Frontend main | `78cdf87` (then `51b3d82`) |
| Merged | BE#171 premium, BE#172 anime, BE#125 handover, BE#173 + BE#174 deploy verifier, FE#183 premium, FE#184 anime, FE#185 health build id |
| Migrations applied | `vent_auth/0081` (premium_until, PremiumPurchase, PremiumInterest) and `vent_anime/0001_initial` (16 tables) |

The whole suite was run on merged main before any of it left the machine:
`Ran 4017 tests in 480.524s / OK (skipped=1)`.

## The anime module is live and shut

That is the point of it, so it was asked of production rather than assumed:

```
/auth/platform/modules/   anime_enabled: false
/anime/series/            503  {"code": "ANIME_OFF"}
/anime/catalogue/         503
/anime/rooms/             503
/anime/battles/           503
```

and `https://v-ent.co/anime` renders the closed card on the desktop and on the
phone at 411 CSS px. `/premium` renders the eight features and "Not on sale
yet" to a signed-out visitor on both.

## The deploy was lying, and had been for three days

**The symptom.** The deploy pulled both repos, ran the migrations, built the
frontend, printed `active` for every unit, and the site went on serving the
PREVIOUS frontend build. `/premium` 404ed on production while the route
existed in the build sitting on the disk.

**The cause.** Every handover documents the deploy as
`ssh ... '/srv/vent/deploy/deploy.sh'`. That file was a **copy from 1
September**, not the repo's script. It ends with `systemctl restart vent-web`,
the single-instance unit retired on 7 September when the two-instance rolling
deploy landed. The unit still existed, so the restart succeeded and did
nothing, and `systemctl is-active` then reported a process that had been
running since the previous day. It also put the maintenance page up, which the
newer script exists to avoid.

**What was done on the box:**

1. `/srv/vent/deploy/deploy.sh` is now a **symlink** to
   `/srv/vent/backend/deploy/deploy.sh`, so the documented path and the repo
   are the same file.
2. The retired `vent-web` unit was **stopped** that day and **removed** on
   12 September, file and all.
3. `git config core.fileMode false` in `/srv/vent/backend`, because
   `chmod +x deploy/*.sh` inside the deploy made the next `git pull` refuse
   with "local changes would be overwritten".

**What was done in the repo:**

- `deploy/verify-live.sh` asks each instance what build it is serving and
  fails if it is not the one on disk, naming the fix. It also counts unapplied
  migrations. `deploy.sh` runs it after the roll.
- `/api/health` reports the real build id. It read `process.env.NEXT_BUILD_ID`,
  which nothing sets, so every instance answered `"build": "unknown"` and the
  one field that would have made this visible was empty.

**It caught itself being wrong.** Its first live run failed on a correctly
deployed site: the App Router puts no build id in the HTML, so reading one off
the page only ever worked for the pages router. That is the right way round for
a check to be wrong. It reads the health field now, keeps the HTML read as a
fallback, and has seven self-test cases.

Live now:

```
port 3000 is serving -VvxB_POy5Ptwp3KlLveO
port 3001 is serving -VvxB_POy5Ptwp3KlLveO
migrations: none outstanding
the live site is serving this build
```

This is the second script to exit 0 having done nothing. The backup cron did it
on 9 September, failing with `Permission denied` every night while its own log
said the run had started. Twice is a class, and the class now has a check.

## Gates

Every gate box in the tree that anybody here can close is closed. **9 remain,
5 of them ABANDONED with the reason written in the file.** The 4 real ones:

| Box | Waits on |
|---|---|
| `GATES-DRAFT-DUP.md` A5 and `GATES-PRODUCTION-BUILD.md` A4 | which of production rows 26 and 28 to keep. Nothing deleted without it |
| `GATES-RUN-OF-SHOW.md` H2 | the CEO's actual overlay files, which are not in this tree |
| `GATES-EAFC-CARDS.md` F5 | a walk on PRODUCTION that seeds a catalogue and picks a lineup, which writes real rows |

## The one number that changes what the platform sells

Premium is deployed and the price is **0**, which means NOT ON SALE. `/premium`
therefore shows the interest control rather than a buy button. One number in
the console turns that into a shop.

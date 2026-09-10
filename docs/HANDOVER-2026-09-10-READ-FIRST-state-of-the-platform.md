# READ FIRST: the state of V-ENT, evening of 10 September 2026

The one file to open before doing anything else. The two beside it hold the
detail: `HANDOVER-2026-09-10-anime-shipped-and-the-open-gates.md` for how the
anime module was built and walked, and `HANDOVER-2026-09-10-shipped.md` for the
deploy that turned out to be lying.

---

## 1. Where everything is right now

| | |
|---|---|
| Backend `main` | `622d80a4`, and production is on the same commit |
| Frontend `main` | `f700b34`, and production is on the same commit |
| Live build | verified by asking the running instances, not by reading a deploy log |
| Suite | `Ran 4017 tests in 480.524s / OK (skipped=1)` on merged main before anything shipped |
| Catchers | 36 in `check-all`, every blocking one clean, one standing debt |
| Open PRs | two, both CONFLICTING and both from 4 September: BE#150 and FE#171 |
| Open gate boxes | 9 in the whole tree, 5 of them ABANDONED with reasons in the file |

Nine PRs were merged and deployed today on the CEO's word "ship": BE#171,
#172, #125, #173, #174, #175, #176 and FE#183, #184, #185, #186.

## 2. What is live, and what is deliberately shut

**Open to the public:** tournaments, events and ticketing, teams, wallets,
rankings, community, organizations, the production studio and stream overlays,
partners, and `/premium`.

**Built and SHUT, on purpose:**

| Module | How it is held shut | Proof taken today |
|---|---|---|
| Anime | `ANIME_ENABLED` (server, default 0) **AND** the console's `anime_enabled` flag | `anime_enabled: false`; all 37 routes answer 503 `ANIME_OFF`; `/anime` renders the closed card on desktop and at 411 CSS px |
| Marketplace (Vermillion City) | `MARKETPLACE_ENABLED` **AND** the console flag | `marketplace_enabled: false`, `/marketplace/listings/` answers 503 |

Neither switch opens its module alone. The gate is applied at the **urlconf**,
once per route, with an explicit mark rather than `functools.wraps`, because
DRF's `@api_view` sets `__wrapped__` on everything and a coverage test looking
for that passed over a deliberately open route once already.

**Not built:** shop, wager (legal review first).

## 3. The one number that decides what V-ENT sells

Premium is deployed. The price is **0, which means NOT ON SALE**, so
`/premium` shows "Not on sale yet" and records who wants it instead of selling
anything. One number in the console turns that into a shop, and it is the
CEO's number.

`/auth/premium/offer/` answers 200 today with `price_vc_monthly: 0,
price_vc_yearly: 0, on_sale: false` and the eight features it switches on.

## 4. How to deploy, and how to know it worked

```
ssh -i ~/.ssh/vent_vps vent@162.35.101.16 "/srv/vent/deploy/deploy.sh"
```

It ends with, and this is the part to read:

```
port 3000 is serving <BUILD_ID>
port 3001 is serving <BUILD_ID>
migrations: none outstanding
the live site is serving this build
```

If those lines are absent or disagree, **the deploy did not land**, whatever
`systemctl is-active` says. That sentence exists because today a deploy pulled
both repos, migrated, built the frontend, printed `active` for every unit, and
left the site serving the previous build for twenty minutes: the documented
path was a copy of the script from 1 September that restarted `vent-web`, the
single-instance unit retired on 7 September.

Two things about the box that are **not in git**, and would be lost if it were
rebuilt:

1. `/srv/vent/deploy/deploy.sh` is a **symlink** to
   `/srv/vent/backend/deploy/deploy.sh`. Never a copy.
2. `git config core.fileMode false` in `/srv/vent/backend`, because the deploy
   runs `chmod +x deploy/*.sh` and the next `git pull --ff-only` would abort.

The retired `vent-web` unit is stopped but still present; it cannot be masked
while its unit file exists.

## 5. What is undone, and who each piece waits on

**The CEO, and nothing moves without it**

| | |
|---|---|
| The premium **price** | 0 today, so nothing is on sale |
| Production rows **26 and 28** of the Rivalry Series | which to keep. Nothing deleted without the answer. `GATES-DRAFT-DUP` A5, `GATES-PRODUCTION-BUILD` A4 |
| The **overlay files** | rows 55/56 and `GATES-RUN-OF-SHOW` H2. The whole path is proven with a file of mine; only theirs are missing |
| The **ten names** | row 69, for the Rivalry Series tournament the CEO made on production |
| **"Finish the events model properly"** | row 139. "for the properly finished i'll tell you" |
| A **production walk** for EAFC F5 | seeding a catalogue and picking a lineup writes real rows |

**Mine, still open**

- **BE#150 and FE#171 conflict.** EAFC formations, the scraper and the catcher
  work, and the squad submit/review screens. Built 4 September, never merged,
  and they now need rebasing onto a main that has moved a long way.
- **Standing debt: `spinner for ever`, 42 of 116 files.** A page that infers
  "loading" from data being null spins for ever when the fetch fails. Unchanged
  for a day; every other catcher is clean.
- **Abandoned, with the reason written in the gate file, not forgotten:** USDT
  payouts (regulatory, `gates/17` M.3), AFC sign-in (their `/oauth/authorize`
  answers 404, `gates/17` N.2), and the in-order endpoint-caller rule, which
  cannot be calibrated by grepping (`GATES-RESULTS-DESK` E2).

## 6. What today's walk found, so it is not rediscovered

Six faults came out of pressing every control in the anime module, the two
worth remembering:

- **A paid comic was created at zero.** The studio offered paid pricings and
  posted no number, while the endpoint had read `chapter_price_vc` and
  `subscription_price_vc` since the day it was written. The endpoint had a
  caller; the FIELD had none, which `endpoint-callers` cannot see. When an
  endpoint reads a field, grep the frontend for that field NAME, not the path.
- **A deploy can exit 0 having done nothing**, and so can a backup cron (that
  was 9 September). Twice is a class: `deploy/verify-live.sh` now asks the
  running site what build it is on and fails if it is not this one, with seven
  self-test cases.

Two tooling notes that cost real time:

- **Chrome has two scales.** A click coordinate is NOT a screenshot
  coordinate: CSS = click x 1.2246, while the screenshot is CSS / dpr. Compute
  clicks from `getBoundingClientRect()` divided by 1.2246, and take a
  screenshot immediately before a press, which is what revives a window that
  has silently stopped accepting clicks.
- **The Android emulator is the mobile evidence**, and it needs `wm size
  1080x2400` and `wm density 420` to be 411 CSS px. `adb shell input text`
  needs `%s` for spaces.

## 7. Where the rest of the detail lives

| | |
|---|---|
| Every ask, with the CEO's own words and its state | `V-ENT/tasks/inbox.md`, rows 1 to 255 |
| Acceptance gates, one file per piece of work | `V-ENT/GATES*.md` and `V-ENT/gates/*.md` |
| The debt ledger, and what has not moved | `V-ENT-BACKEND/tools/debt-ledger.json` |
| Every catcher and what it catches | `check-all.py`, and the `reference_checkers` memory |
| The handovers, one per working day | `V-ENT-BACKEND/docs/HANDOVER-*.md`, newest first |

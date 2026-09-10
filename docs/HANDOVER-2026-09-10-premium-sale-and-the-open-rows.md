# Handover, 10 September 2026: premium is bought, and the open rows

Two things happened in this session, and the second is bookkeeping that turned
out to be real work.

## 1. Premium stopped being something you ask an admin for (rows 251 and 253)

Row 251 shipped the console control that GRANTS premium. The CEO read the
refusal it left behind and said:

> "They shouldnt be requesting a vent admin to turn on anything."

So row 253 built the other path. Both are on the SAME pair of PRs, still open:
**BE#171** and **FE#183**.

| Piece | Where |
|---|---|
| The one writer for a grant | `vent_auth/premium_admin.py::apply_premium` |
| The one writer for a purchase | `vent_auth/premium_sale.py::buy` |
| `premium_until` on both holders | `vent_auth/premium.py::PremiumMixin`, migration `0081` |
| The offer, buy and interest endpoints | `vent_auth/views_premium.py` |
| Nightly expiry | `manage.py expire_premium` |
| The page | `src/app/premium/` (server component + client) |
| The price | platform settings, `premium.price_vc_monthly` / `_yearly` |

**The price is 0, which means NOT ON SALE, and that is the one thing waiting on
the CEO.** Zero is not free: with no price the page still has a control, and
pressing it writes a `PremiumInterest` row that the console reads as a "Wants
premium" filter carrying which refusal the person came from. Nobody is sent to
find a member of staff at any point.

Rules the sale keeps, each because the opposite is a way to lose money or trust:

* time is ADDED, never overwritten, so paying twice buys two months;
* a lapsed subscription starts again today rather than backdating itself;
* buying while premium was GRANTED is refused, rather than taking money for
  something the account already has;
* a grant CLEARS `premium_until`, because the grant is the newer decision;
* buying for an organisation charges the person, since an organisation has no
  wallet, and goes through `org_link.resolve` so "may they" is answered once.

Proven end to end in the browser: the refusal on the entry requirement picker,
the link to `/premium`, 25 VC paid from the wallet, and the two premium
requirement kinds becoming ordinary options. Again on the emulator at 412 CSS
px with `adb shell input tap`. 36 new tests; `vent_auth` is 1005 tests OK.

## 2. The open rows, read against reality rather than against the file

The CEO asked to finish every open row and every open gate. The inbox said 26
rows were open. Most were finished work whose row was never updated, and the
only way to tell was to ask GitHub, the box and production. What that found:

* **nine rows were shipped** and still read "built" (Discord, the door, the
  overlays, soft delete). Every PR they name is MERGED, checked with `gh pr view`.
* **the backup on the VPS had been failing since 9 September at 03:00**, with
  `Permission denied`, and the 11:00 freshness check that exists to catch
  exactly that failed the same way BECAUSE IT IS THE SAME FILE. Both cron lines
  call it through `bash` now, and `deploy/deploy.sh` chmods `deploy/*.sh` after
  every pull. Freshness now answers
  `db-2026-09-09-2245.sql.gz is 2 hours old and 161450 bytes`.
* **`gates/22-admin-dashboard.md` had 15 open boxes** for work that was done
  under `gates/26`. Back-filled by re-running the checks, not by copying: 77
  tests OK, and `walk-admin-sweep.mjs` re-run reads
  "17 admin screens opened and pressed, none broken".
* **the run-of-show gates that needed a browser are walked**, and the walk found
  a real fault: a day with no cues on it rendered the ROLE filter's empty state
  with the role blank, so it read "Nothing on this day belongs to ." Fixed.

## 3. A build that eats its own install, twice in one day

`pnpm build` with a dev server still serving this tree guts
`node_modules/next` in the pnpm store, because the package is a symlink and both
write the same real directory. The build then dies on

    Cannot find module '.../next/dist/compiled/jest-worker/processChild.js'

`check-pnpm-store.mjs` catches the damage AFTERWARDS. The cause is caught now:
`prebuild` runs `scripts/check-before-build.mjs`, which refuses while a dev
server is on one of this tree's ports and prints the exact command to stop it.
7 self-test cases, and its self-test is in `check-all`.

## What is still open

| Row | What | Blocked on |
|---|---|---|
| 55, 56 | The CEO's overlay files | the files, which were never sent |
| 69 | The Rivalry roster | the ten names |
| 139 | "Finish the events model properly" | the CEO saying what "properly" means |
| 252 | The whole anime module | nothing. Gates written, `gates/31-anime-module.md`, 39 boxes |
| 253 | The premium PRICE | the CEO. Everything else is built |

`gates/17-the-rest.md` still has three unticked boxes, each carrying an ABANDON
line with a measurement behind it: the AFC sign-in (their `/oauth/authorize`
answers 404) and the USDT half of the wallet, which needs a custody decision
rather than an afternoon.

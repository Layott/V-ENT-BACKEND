# Handover, 8 September 2026: organiser subscriptions

Inbox row 202, and the two blocking checker breaches that came with it. Written
by the subscriptions leaf. Branch `feat/funnel-ledger-transfer-rankings`,
NOTHING COMMITTED.

## Read this first if you are picking it up

The feature is finished and walked. All sixteen boxes in
`gates/21-subscriptions.md` are ticked with real output on every EVIDENCE line.
One thing is deliberately NOT proven and is written on gate D4: the Paystack
card leg, because `PAYSTACK_SECRET_KEY` is unset on this machine.

## The two things that were blocking a commit for everybody

**1. check-user-chips, a blocking breach.** `OrgMembershipsPanel.js` built a
person out of `Avatar` + `Link` + `row.subscriber?.username`, so that row lost
the founder mark. The checker named line 617; there was a SECOND one at 670 in
the invoice table that it did not name. Both are now
`<UserChip user={...} size={28} className={styles.who} />` and the `Avatar` and
`Link` imports are gone. Reads "12 files rendering a name / every name renders
through UserChip".

### The reason it did not name the second one, which matters more than the fix

My first account of this was wrong and is corrected here. I said the checker
missed it because that instance had no `Link` in it. That is not the reason.
`scripts/check-user-chips.mjs` skips a whole file on one line:

    if (src.includes('user-chip/UserChip')) continue;

and reports only the first offending line, via `lines.findIndex`. So the
failure mode is not "it found one of a pair". It is that **fixing the line it
reports is what makes every other name in that file permanently invisible.**
Had I fixed 617 alone, the import would have gone in, the checker would have
gone green, and the invoice table would have carried on losing the founder mark
for good.

Reproducing the checker's own matcher and asking the opposite question, which
files import UserChip and STILL write a name by hand:

    23 hand-written names across 12 files, none of them visible to the checker
      17 carry no founder mark at all
       6 have a FounderBadge maintained by hand beside them

Name-and-handle pairs are one person, so those 17 lines are roughly 13 distinct
person renders across 10 files: community feed, post and thread, my-tickets,
the organisation member list, search (seller, thread author, uploader), three
team-profile tables, the MVP panel and the register/edit-team panel. Six were
read by hand before that number was quoted; all six were genuine.

None of them have been touched. They belong to other leaves and the checker is
not this leaf's to change. What it needs: report EVERY offending line, replace
the whole-file skip with a per-line one, and add a self-test fixture for this
exact case, a file that imports UserChip and writes a name by hand. The probes
used to measure it were `chipgap.mjs` and `chipgap2.mjs` in this job's tmp.

**2. Debt went up on two counters because of the two new pages.**

- SEO 0 -> 2: `/plans/<slug>` is now in `sitemap.js`, built from
  `/billing/plans/public/`, which only ever returns public plans, so a draft
  can never leak in. `/memberships` is in the `robots.js` disallow list in BOTH
  rules, the general one and the AI-crawler one. That split is the right one:
  the plan and its price are the most linkable thing the feature has and must
  rank; a member's own payment history must not. `check-seo` reads
  "87 public route(s) checked, 0 problem(s)".
- Live updates 0 -> 6: `/memberships` fetched once and never refreshed. It now
  calls `useAutoRefresh(() => load({ quiet: true }), [viewer.token], { enabled:
  viewer.signedIn })`. `load` gained a quiet mode that does three things: it
  does not redraw the skeleton, it does not raise an error banner on a
  background tick (the reader pressed nothing, so a fault appearing on its own
  is one they cannot act on), and it returns whether anything MOVED so the loop
  backs off on a list that has not changed. What "moved" means is one line:
  state, next charge, access until, renews. The comparison reads a REF, not
  state, because naming state in the callback re-arms the timer every render
  and it never fires.

The other five on that counter were the admin console and were not mine.
Somebody has since cleared them; the checker now reads "0 dead refresh timers,
0 pages that never refresh".

## What the walk found that the tests did not

- **"1 members".** The member-count chip read that with one member. Now
  `billing.memberCountOne` beside `billing.memberCount`, in en, fr and pt,
  because the languages do not agree on where a plural falls anyway.
- **A 36px End button** in the organiser's members table, which removes
  somebody's membership and sits in a table row next to other rows. Raised to
  44px. `check-tap-targets` went 141 to 140 and names none of my files.

## Two things that looked like bugs and were not

Both cost time, so they are written down.

- **`/memberships` rendering the signed-out view while signed in.** Another
  session had hit `/api/auth/signout`, and NextAuth cookies on localhost are
  shared across every port. The page was correct. There is a memory for this
  now (`feedback_localhost_cookies_cross_ports`): read `/api/auth/session`
  before doubting a page.
- **A pure white right-hand pane on the plan page.** The screenshot showed it
  twice. `getComputedStyle` on the container said `rgb(19, 19, 22)` across the
  full 1732px, and a later screenshot was correct. The capture in a contended
  Chrome returns unpainted frames and lags a call behind. Believe the DOM over
  a screenshot when the two disagree, and take a second shot.

Also: `document.body.innerText` returns EMPTY on a background tab, because it
depends on layout. `textContent` does not. That reads exactly like a
white-screened page and is not one.

## The state machine

Five states, one table in `vent_billing/states.py`, and `move()` is the only
thing allowed to write `subscription.state`.

    (new) --trial_started--> trialing --trial_converted--> active
    (new) --first_charge---------------------------------> active
    active   --charge_failed--> past_due --charge_recovered--> active
    trialing --charge_failed--> past_due
    past_due --dunning_exhausted--> cancelled
    trialing|active|past_due --cancelled_by_subscriber--> cancelled
    trialing|active|past_due --cancelled_by_organiser---> cancelled
    trialing|active|past_due --refunded-----------------> cancelled
    cancelled|past_due --period_ended--> expired
    trialing|active|past_due --plan_changed--> (state kept, recorded)

`cancelled` and `expired` are two states because they answer different
questions. `cancelled` means "will not renew" and STILL grants access to the
end of the period somebody paid for. That is the whole of gate B4 and it is
the thing a cancel button usually gets wrong.

## Proof

| | |
|---|---|
| Full backend suite | Ran 3569 tests in 877s, OK (skipped=1) |
| vent_billing | 122: api 51, charging 35, cancel 13, clock 13, dunning 10 |
| dict-parity | en=fr=pt=7291, 0 missing |
| check-keys | 6548 keys, 0 missing |
| check-user-chips, check-seo, check-signed-out, check-live-updates | all 0 |
| check-design, check-datetime, check-slugs, check-css-classes, check-tdz, check-dangling-refs, check-inert-controls | 0 new |

Walked in Chrome, desktop and a real 390px iframe, on a dev server started for
this leaf on 3311 (3005 had a crashed jest worker answering 500 on every
route). Subscribe took the wallet from 2500 to 2495 and wrote a paid invoice;
the statement showed it with its reference; one press of Cancel left
`has_access()` TRUE with `period_end` unmoved and the wallet untouched; "Keep
it after all" restored it free. No console errors. No horizontal scroll and no
tap target under 44px on either page at 390.

## Open, with the reason

1. **The Paystack card leg is unproven.** `PAYSTACK_SECRET_KEY` is unset, so
   every charge in the walk took the wallet path. `CardChargeTests` mocks the
   three cases. Somebody with a test key needs to walk it once.
2. **check-error-ui reads 3**, none of them mine:
   `my-stalls/[slug]/page.js:435` (a `window.prompt`),
   `components/wallet/UsdtDestination.js:87` and `lib/useLiveData.js:111`
   (both `setError(body.message)` with a raw server string). It is not in the
   debt ledger so it blocks nothing, but the count went 2 to 3 during the
   session, so somebody added one today.
3. **node_modules is gutted and the repair so far is partial.** It broke at
   19:39 (`.modules.yaml` is stamped then), after the walk finished, so nothing
   above depends on it. `@swc/helpers` was empty; it has contents again, so
   somebody started a repair. But the `next` package itself is still missing
   files, including its own `package.json`:

       node_modules/.pnpm/next@14.2.35_.../node_modules/next/package.json      MISSING
       .../next/dist/lib/constants.js                                          MISSING
       .../next/dist/build/utils.js                                            MISSING
       .../next/dist/server/dev/hot-reloader-types.js                          MISSING
       .../next/dist/compiled/path-to-regexp/index.js                          MISSING

   Restarting a dev server with a deleted `.next-dev-<port>` still answers 500,
   so this is the package and not a stale build cache. Any server still serving
   is living on what it already had in memory; every cold start fails. It needs
   ONE repair for everybody rather than several racing installs, and per
   `feedback_build_deletes_next_pages` the STORE may need pruning rather than
   just a reinstall. This leaf's 3311 server has been stopped rather than left
   broken and holding RAM.

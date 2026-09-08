# Handover, 8 September 2026: the last of the research list, and shipping

Read this with `HANDOVER-2026-09-07-the-rest.md` and
`HANDOVER-2026-09-08-maps-and-tabs.md`. Those cover the funnel, the ledger,
the four ticketing gaps, the map pins, the console tab strip and the wallets.
This one covers what closed after them.

## What was built today, after the maps and tabs

### Abandoned checkout recovery, the last GAP on the tix/selar list

`tasks/research/tix-and-selar.md` named 18 feature rows. Four were marked GAP
and all four shipped earlier this week. Three were not built. One of those is
now built and two are named with reasons - see `gates/17-the-rest.md` I.1 and
I.2, which hold the full list.

**The one that was built.** `guest_buy` deliberately writes no pending row,
and the reason is in the code: "a payment nobody completes should leave
nothing behind to clean up". That is still right for the ORDER. It was wrong
for the fact that somebody TRIED, which the Paystack metadata takes to the
grave and which is the only thing an organiser can act on.

`vent_event.models.AbandonedCheckout` stores the smallest thing that answers
"who nearly bought": address, event, tier, quantity, reference, total. Not the
answers they typed, not the attendee names, not the card.

Three decisions worth keeping:

1. **It closes at BOTH arrivals.** `guest_verify` issues on the first arrival
   and answers `already_issued` on the second. The row is stamped
   `converted_at` in both branches, because on a slow connection the callback
   is often the one that lands, and stamping only the issuing branch would
   leave a paid buyer on the chase list.
2. **One reminder, ever, and a person presses it.** No scheduler. An automatic
   drip to somebody who did not buy is a marketing list assembled out of a
   checkout, and the address was given in order to pay for one ticket.
3. **`reminded_at` is stamped only on a send that WORKED.** Stamping first and
   sending after means a mail outage silently burns the row's one chance, and
   nothing would ever say so.

Swept at 30 days, converted or not: `AbandonedCheckout.sweep()`. That is what
stops the table becoming an address book.

The panel is under the funnel on the Sales and attendance tab rather than in a
sixteenth console tab, because it is the bottom of that funnel made
actionable. 17 tests in `vent_event/tests_recovery.py`.

Endpoints: `GET /event/<id>/abandoned/` (add `?include=all` for the recovery
rate) and `POST /event/<id>/abandoned/remind/` (`{"id": n}` for one, nothing
for everybody still open who has not had theirs).

### check-signed-out learned about the whole-component early return

The checker reported 2 write controls at risk, both in `SharedWallet.js`, and
reading both by hand showed the checker was wrong twice out of two reports.

`SharedWallet` is written with `if (!token) return <p>Sign in to see this
wallet.</p>` above everything. Nothing below that renders at all, so there is
no branch a signed-out reader can be in. The checker's proximity rule is
deliberate and correct for a TERNARY - that is exactly how the organisations
page offered Manage to a stranger - but an early return is a different shape.

`returnedBefore()` now grants it, and narrowly: the return has to LOOK like a
render return (`return <jsx`, `return null`, `return (`). The same condition
inside a loader (`if (!token) { setLoading(false); return; }`) stops a fetch
and not a screen, and there is a fixture proving that case is still caught.

7 self-test fixtures, all passing, including the original organisations bug.
Count is now an honest 0 rather than a suppressed 2.

## What is NOT built, deliberately

**Subscriptions and memberships for organisers** (selar has them). It is
recurring billing: a stored card mandate, Paystack plans, a renewal run,
dunning when a charge fails, proration, cancellation. Half of that is worse
than none, because the half that ships is the charging half. It is the only
row on the research list that is a project rather than a feature, and nothing
else was waiting on it.

**Selling digital products, custom domains, eight currencies.** The research
itself argued against all three and the arguments still hold. See the "What is
NOT worth copying" section of `tasks/research/tix-and-selar.md`.

## Still the CEO's call, not a bug in this repo

Paystack's dashboard collects NGN 22,436.55 on an NGN 22,000 checkout. That is
their surcharge setting. `ledger.quote` computes commission of the ticket
price and is correct; anybody comparing the two numbers will still ask, and it
is changed in Paystack's dashboard rather than here.

## Verified

- Backend suite on sqlite: see the run recorded against gate X.3.
- `pnpm build` from a cleared `.next`: compiled, all routes.
- `python tools/check-all.py`: every blocking catcher clean, no debt up.
  `seo` is now 0 problems across 85 public routes, down from the 60 that sat
  unchanged for weeks.
- en/fr/pt: 6864 keys each, 0 missing, 6033 used keys all present.

## The walk, and the two faults PRESSING it found

Walked on desktop Chrome and on the evotv_test AVD at 412 CSS px, with three
real rows seeded: one open, one already reminded, one recovered.

Rendering was correct on both. Pressing was not, twice:

**1. A failed send reported itself as "already reminded".** The local mail
server is unreachable, so `send_checkout_unfinished` returned False, the
endpoint correctly answered `{sent: 0, failed: 1}` and correctly left the row
remindable - and the page said "Nothing was sent. Everybody open has already
had their one reminder." Both halves were false and the organiser would never
have chased it. Three outcomes now, three sentences, and the failure is the one
said loudly because it is the only one that needs somebody to do something.

To prove the SUCCESS half at all, `EMAIL_BACKEND` is now overridable by env
(default unchanged, still SMTP). Without that, every screen on this platform
that sends something is untestable in a browser, because a send simply fails
and a page that reports failure badly looks identical to one that reports it
well. With `EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend` the
send landed and `reminded_at` was stamped.

**2. The button measured 40px against a 44px tap-target rule.** Measured over
CDP on the device, not guessed. `manage-event.module.css` had a mobile block
raising `.ghostBtn`, `.addBtn`, `.iconBtn` and `.tab` to 44 and `.primaryBtn`
was not in the list - the "one built, the other forgotten" shape, invisible to
reading because `min-height: 40px` looks deliberate.

## A new catcher, and 145 real tap targets across the platform

`scripts/check-tap-targets.mjs`, 13 self-test fixtures, in `check-all` and in
the debt ledger at 145 so it cannot rise.

Calibrating it took four passes and every one was the CHECKER being wrong:

1. It reported the track and knob INSIDE a switch, counting one control three
   times. The label is the target.
2. It measured `.tabBtn.tabActive::after`, the 2px underline under a tab, and
   called the tab 2px.
3. Its class matcher used a `(^|[^a-z])` boundary, which cannot see the B in
   `primaryBtn` because a lowercase letter precedes it. It was blind to nearly
   every button in the repo and only matched names STARTING with a pressable
   word. This is the one that matters: the checker looked calibrated at 18
   while the honest number was 149.
4. Its parser missed every `@media` block with a comment above it, which in
   this repo is all of them, so the "is it raised on a phone" half did nothing.

Honest count went 31 -> 18 -> 149 -> 145. A sample was read by hand
(`.pageBTN` 30px, `.qtyBtn` 26px, `.navIconBTN` 30px) and they are real.

**Recorded as debt rather than fixed, deliberately.** Most of the remainder are
switches and chips that need a hit-area (a `::after` overlay) rather than a
height change, because a switch is MEANT to look 38x22. Making 145 visual
changes across 100+ files unwalked at the end of a shipping pass is how a
shipping pass causes an outage. The page this session touched is fixed and
reports 0. The rest is now visible, tracked, and cannot grow.

## Four gates that had been open for days, closed by actually walking them

Once Chrome and the emulator were both live it was cheaper to close these than
to carry them, and three of the four found something.

**One instant, two zones** (08-timezone G8). Driven over CDP with
`Emulation.setTimezoneOverride`, which is the only way to ask a real browser
what somebody in another country would see. `/events/rivalry-series-season-2`
read twice:

    Africa/Lagos      Sep 12, 2026, 09:32 PM
    Pacific/Auckland  Sep 13, 2026, 08:32 AM

Eleven hours, and note it crosses the DATE. A reader in Auckland is told the
13th for an event whose organiser typed the 12th, and that is correct.

**A slot changes on air** (06-slots G4, and the unproven half of 17-the-rest
E.3). The slot URL was built against localhost by hand, because FRONTEND_URL
here points at test.app.v-ent.co and that is what left this unproven. It
rendered the standings graphic with real data. Then
`BroadcastSlot(session=6, role='full').item_kind` was changed from `standings`
to `lower_third` in the database with the browser untouched, and eight seconds
later the same page was drawing a lower third at the bottom left, no reload and
no keystroke. Restored afterwards.

**Event pages keep themselves current** (09-second-batch G6). Its old evidence
said "useLiveData is imported by NOTHING". It is now imported by 44 files. All
20 event routes were walked one at a time: nine refresh, four are `[slug]`
wrappers around one of those nine, and the seven that do not are correct not to
- create-event and edit-event are FORMS, and a form that reloads under somebody
mid-sentence discards their typing.
One real gap found and fixed: the run of show reloaded only on
`visibilitychange`, and that screen is read DURING the show by staff watching
for the next cue. A phone left open never changed. `useAutoRefresh` at 20s now.
**And one correction**: the first inventory called the organiser console
static. It is not - the console has that poller hand-rolled with `setTimeout`
rather than `setInterval` and the grep missed it. The pattern was wrong, not
the code. That is the second time in one session that trusting a grep produced
a wrong number; the first was the tap-target checker.

**A5 was already met** and its box had never been ticked, while its own
evidence line said so. Not to be confused with the BACKUP cron, which is a
different crontab entry and is genuinely blocked.

## What is left open, and why each one is

Nine gates were open at the start of this pass; eight now, and none of them is
work that can be done from here:

| Gate | Why |
|---|---|
| A5 backup cron (G.1, N.1) | Neither Cloudflare nor this machine can reach the origin. The SCRIPT is fixed, so what the cron runs will hold data |
| AFC sign-in (Q3, H.1, N.2) | Their /oauth/authorize and /api/me both answer 404 |
| USDT wallet half (M.3) | Needs a custody decision before a line is worth writing |
| Merge and deploy (Z5, 07 G8) | The CEO's call. Nobody merges their own PR |

## Not verified

Nothing outstanding on this feature. The unmet gates that remain are all
blocked elsewhere and carry ABANDON lines: the VPS cron (the box is
unreachable), AFC sign-in (their OAuth answers 404), and the USDT half of the
wallet spec (it needs the CEO to answer the custody question first).

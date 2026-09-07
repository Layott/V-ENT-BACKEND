# Handover, 7 September 2026 (evening): the rest of the list

Working `gates/17-the-rest.md`. Written while the work is running, not after.

If the session ended now, this file plus `tasks/inbox.md` is enough to continue.

---

## Done and proven

### Wave A: the bookkeeping (rows 176, 177, 178)

**The debt ledger had a real hole and it is closed.** The write-back loop only
ever iterated checkers that were STILL FAILING:

```python
for name, last in debt:      # `debt` = non-blocking checkers that failed
```

A checker reaching 0 exits 0, is classed `clean`, and never enters `debt`, so
its row froze at whatever it last failed with. Worse than stale: climbing back
from 0 to 21 compared against `was['count'] == 21` and reported **"unchanged"**,
so twenty-one real breaches could return in silence.

Now every checker is recorded, clean ones at 0. A clean checker's count is NOT
parsed - exit 0 is the exact statement that there are no faults, where reading
the first integer off `650 route(s) checked, 0 fetch(es)` would read 650.
`compare()` was pulled out of `main()` so it can be asked a question without
running 22 subprocesses, and `tools/test-ledger-writeback.py` proves it both
ways, including the "against a stale ceiling the same rise is invisible" case.

Ceilings written back: wizard round trip 50 to 0, avatars 21 to 0, slugs 21 to
0, prose 1 to 0.

**17 stale gates were really 15.** Each was verified by running its own CHECK,
not bulk-ticked: 06-slots G1/G2/G3/G5 (134, 18, 25, 21 tests), 15-vendor-slots
C.3 (its ABANDON reason had stopped being true), 13-events W2.1 to W3.4 (93
tests). TWO were deliberately left open with the reason written in: 06-slots G4
and 13-events X.3 both ask for a browser walk, and ticking those would be the
exact fault being corrected.

**Five duplicate inbox rows, not three**: 50, 51, 53, 63, 66. `tools/check-inbox.py`
now holds the class. It first reported 21 stateless rows; reading all 21 by hand
showed every one carried a real state in a wider vocabulary - "built",
"shipped", "waiting on the CEO" - so the CHECKER was wrong and the rows were
right. Rewording somebody's own history to satisfy a check is inventing it.

### Wave C: the card checkout (row 179)

**A real `sk_test_` key was sitting in `.env` as `PAYSTACK_SECRET_TEST_KEY`
while both callers read `PAYSTACK_SECRET_KEY`.** The guest card path had been
answering "card payment is not set up for this platform yet" for a key that was
there. Two copies of `_paystack_headers` existed, both reading the environment
directly - the same shape as the transferred-code fault earlier the same day.

`vent_auth/paystack.py` decides once for both. The live key always wins; the
test key is used ONLY when there is no live key AND `DEBUG` is on, because
falling back in production would issue tickets for money that does not exist.

Proven end to end against real Paystack:
- asked for 2200000 kobo = ticket 20000 + fee 2000, fee on the buyer
- paid through the Success simulator, Paystack verify says `success`
- before paying: 0 tickets, ledger untouched
- after verify: ticket VT-JYW5JSB3, ledger organiser 20 VC + platform 2 VC
- verifying twice answers `already_issued` and issues nothing

**One thing the CEO needs to decide.** Paystack collected **22,436.55** while
our page promised 22,000. The extra 436.55 is Paystack's own transaction charge
being passed to the customer, which is a setting in their dashboard. Our whole
fee-bearer feature exists to stop surprise charges, and this puts one back. It
is their account setting, not code.

### The affiliate claim path (row 180) - a real bug, found by walking

The earnings screen said *"it is paid the day they make an account"* and
**nothing made that true**. `payee` could only ever be set to an account that
already existed (`_payee_or_error` refused an unknown address with NO_ACCOUNT),
so there was no later moment at which anybody could arrive and claim. The money
would have accrued for ever.

Fixed: `EventReferral.payee_email`, `_payee_or_error` returns `(user, email)`
instead of refusing, and `claim_pending` attaches the link AND its open ledger
lines - attaching the link alone is not enough, because lines naming no user
are skipped by every settlement.

Walked: link made for somebody with no account, sale of 20 VC at 25 per cent, a
settlement that correctly paid the organiser 15 and left the 5 open, the
streamer signing up, and the next settlement paying them the 5.

### Rankings (rows 182, 183, 184, 185)

Three separate faults in one screen:

1. **The region column was the user's STATE.** `region = u.state or u.country`,
   so somebody in Lagos had the region "Lagos".
2. **The region filter did nothing.** Read off the query string, checked
   against 'global', then never used in a single query.
3. **The country list was seven entries typed into the frontend**, two of them
   Nigerian cities, with every other country unreachable.

`vent_auth/regions.py` maps country to region, Africa-first and complete for
the five African regions. The filter list is built from countries that actually
appear on the platform, sent WITH the rankings so there is no second endpoint
to leave uncalled. 15 tests.

Walked: Nigeria / West Africa on every row, dropdowns read Global + West Africa
and All countries + Nigeria, region=West Africa returns 22 and Europe returns 0.

---

## Open, and where each one stands

| Row | State |
|---|---|
| 170 (78) real-time event pages | not started |
| 171 (88) control room panel | `BroadcastSlot` and 134 tests exist; the operator panel is the open half |
| 172 (148) rest of the research | not started |
| 173 (T11) DM on a profile | not started |
| 174 (A5) VPS cron | **blocked**: the box times out on port 22. It dropped off twice on 2-3 Sept too |
| 175 (Q3) AFC sign-in | **blocked on AFC**, whose login page is broken. `AFC_SSO_ENABLED=0` in production |
| 185 org logos on rankings | URL now builds correctly and the file serves 200 as a real PNG, but the browser still reports naturalWidth 0. **Unfinished.** |
| 186 own models for avatars | not started, and it is a question rather than a task |
| 187 remove fake accounts | `remove_seed_accounts` built, dry-run only. 22 removable, 27 kept. **Needs the CEO's call** on which demo accounts count as "used for testing", and it must not be run on production unprompted |
| 188 org type hides a stat but not the tab | found while walking B.3, not fixed |
| 189 ship it | pending this list |
| 190, 191, 192 | wallets, admin dashboard, VENT WALLET. Specs written to `tasks/specs/`. Explicitly ordered after everything above ships |

---

## The two things to be careful of

**Do not mass-delete accounts on production.** The command defaults to a report
and needs two flags. The CEO asked for fake accounts to go, but which of the
`demo_*` cohort counts as "used for testing" is their judgement, not mine.

**Nothing here is deployed.** 332+ files uncommitted across both repos on
`fix/feed-reactions-and-images`. The CEO has now asked for confirmed work to be
shipped, so that is the next action for whoever picks this up.

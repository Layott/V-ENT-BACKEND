# Handover, 8 September 2026: the debt ledger read the wrong number, and two gate files were a lie

Inbox rows 214 and 215. Gates file: `gates/18-ledger-and-gates.md`.

Nothing here is committed. Both repos are on `feat/funnel-ledger-transfer-rankings`
and this session was told not to commit, so every change below is sitting in the
working tree for the lead to stage.

---

## 1. The ledger was recording how much each catcher READ, not how much was broken

`_count()` in `tools/check-all.py` took the first integer on a catcher's summary
line. Most catchers say how much they scanned before they say how much is wrong:

```
311 stylesheet(s) checked, 145 tap target(s) under 44px on a phone
```

So `tools/debt-ledger.json` held the tap target ceiling at **311** while the real
debt was **145**. The number could have climbed by 166 without one run failing,
which is the ledger not doing the single thing it exists for. The header of
check-all.py has claimed since the day it was written that a rising number is a
regression, and for seven of the twenty-four catchers that claim was false:
parity, one model, wizard round trip, translation keys, seo, tap targets and api
paths all lead with a scope number.

### What the parser does now

Every standalone number on the line is classified by the words around it:

| class | how it is recognised | example |
|---|---|---|
| SCANNED | a scanning verb follows it | `311 stylesheet(s) checked` |
| DENOMINATOR | a preposition precedes it | `across 0 file(s)` |
| BASELINE | `known` or `being worked down` follows, and everything after it on the line | `119 known, being worked down:` |
| NOT DEBT | the catcher says so in words | `notes, which do not fail the build` |
| FAULT | everything else | `145 tap target(s) under 44px` |

The answer is the **sum** of the FAULT numbers, because a line can carry two
independent faults ("0 em/en dash(es) and 0 npm command(s)") and taking only the
first would let the second climb unseen. A line with no fault number returns
None and the catcher is not tracked, rather than recorded at a number nobody can
defend.

### The summary line is not always the last line

`run()` used to take the literal last line of a catcher's output. That was wrong
for three catchers:

- **check-design** ends with an indented breakdown of its baseline by rule, so
  the last line was `    15  glow` and the ledger would have read the design
  debt as 15 when the line above says `0 new design breaches. 119 known`.
- **check-keys** and **dict-parity** end with a Node module warning that carries
  a process id, `(node:16016) [MODULE_TYPELESS_PACKAGE_JSON] Warning: ...`,
  which reads as a numbered summary and is not one.

`_summary_line()` now skips blank lines, indented detail rows and Node warnings.
The `LAST LINE` column of `check-all.py` is visibly more useful for it.

### Proof

`tools/test-count-parse.py`. 55 count cases and 8 output tails.

- **29 are real lines**, captured on 8 September by running all 24 catchers and
  taking each one's output verbatim. Not written from the format strings.
- **26 are the same catchers in the failing state**, which is the state that
  matters, because a clean catcher is recorded by its exit code and never
  parsed at all.
- `--prove-old-rule-fails` reports `The old first-integer rule gets 19 of 55
  case(s) WRONG` and names every one. It **fails the file** if the old rule ever
  passes every case, because then the cases cannot tell the two rules apart and
  a green run would prove nothing.

It is registered in `check-all.py` as a blocking catcher named `ledger parse`,
so the pre-commit hook runs it on every commit.

### The ledger

Rewritten by running every catcher through the new parser:

```
BEFORE  {... "tap targets": 311 ...}
HONEST  {... "tap targets": 145 ...}
CHANGED [('tap targets', 311, 145)]
```

Every other ceiling was already honest at 0, because a clean catcher is recorded
by its exit code and never parsed.

**Calibrated before the number was written**, per the rule. Five of the 145 tap
targets read by hand: `.switch` 22px, `.uploadBtn` 40px, `.sendBtn` 42px,
`.pageNumberBtn` 32px, `.removeBtn` 32px, and grep confirmed no media query
raises any of them on a phone. Five of five are real.

`python tools/check-all.py` exits 0: `Every blocking catcher is clean.` /
`1 catcher(s) still carrying debt. None of it went up.`

### One wrong turn, recorded as one

The first version of the classifier had `already` in its baseline word list. It
was a guess: no catcher writes it. It then broke the very first line written
after the change, `10 gate box(es) unticked while their own check already
passes`, which read as a baseline and reported nothing. The word came out and
the line is now a test case. A guess in a classifier is precisely the fault the
calibration rule exists to stop.

---

## 2. Two gate files were 0 ticked while the work shipped

`GATES-EAFC-CARDS.md` stood at 29 boxes and 0 ticked while `vent_cards` ships
`GameCard`, `LineupRules`, `SquadRules`, `Lineup` and `LineupSlot`.
`GATES-RUN-OF-SHOW.md` stood at 37 and 0 while `vent_tournament` ships
`RunSheet`, `RunSheetDay` and `RunSheetItem`.

Every box in both was decided against a **re-run of its own CHECK line**, and
where a box had no CHECK one was written and run. Nothing was ticked from
memory.

| file | ticked | left open |
|---|---|---|
| GATES-EAFC-CARDS.md | 27 | 2 |
| GATES-RUN-OF-SHOW.md | 25 | 12 |

Every open box says in writing what is genuinely missing:

- **11 need a browser or the Android emulator.** Run-of-show D2, D5, E1, E2,
  H1, H2, I1, I2, I3, I4 and EAFC F5. The code half of each is proven and
  quoted in the gate file; what is missing is the press and the screenshot.
  Chrome and the emulator are single resources driven by one leaf.
- **1 needs `pnpm build`** (run-of-show G4), deliberately not run: a production
  build writes `.next` and on this machine it takes the dev server down with
  it, which would stop the other leaves working in the same tree.
- **1 is inbox row 47** (run-of-show J5), the 18 endpoints with no screen.
- **1 is the deploy** (EAFC F4), which is the lead's step.

The two that were held open by the full backend suite, EAFC F1 and run-of-show
G2, closed while this was being written. See section 4.

Two faults in the gate files themselves were fixed while doing this:

- `GATES-RUN-OF-SHOW.md` H3 named `tools/check-overlay-runtime.py`. No such
  file has ever existed; the checker is `.mjs`. A gate whose command cannot run
  is a gate nobody can close.
- `gates/18-ledger-and-gates.md` had three CHECK lines beginning `cd V-ENT &&`
  while already running from `V-ENT`. My own new catcher found them.

---

## 3. A catcher for the class, because this is its second occurrence

`tools/check-stale-gates.py`, per the 3 September hard rule.

- 7 September, inbox row 176: 17 gates read unmet for work that was done.
- 8 September, inbox row 215: 66 boxes across two files, all unticked, models
  shipped.

It re-runs an unticked box's OWN `CHECK:` line and reports the ones that pass.
It never guesses from the code whether something is built, because that is the
mistake this whole class is made of. A reported box means one of two things and
a person has to say which: the work is built and the box wants ticking, or the
CHECK is a global checker that cannot prove that feature.

It refuses to run browser walks, `pnpm build`, ssh and curl, and it refuses
anything off its allowlist, because these commands come out of a markdown file.
Django tests need `--slow`. A failure caused by the system Python having no
Django is reported apart from a genuine failure, because reading that as "the
work is not built" would be exactly the wrong conclusion.

`--self-test` passes 4 cases both ways: it reports a box whose own check passes,
leaves a box whose check genuinely fails, never reports a ticked box, and names
a walk as unrunnable rather than guessing.

Registered in `check-all.py` as **debt**, not blocking, because it reports boxes
other people own and a check that always fails is a check everybody learns to
skip. Its last line carries one number so the ledger can read it.

---

## 4. What it found that is somebody else's, and is written down not touched

**The full backend suite failed, and then it did not.** For most of the session:

```
Ran 3205 tests in 428.814s
FAILED (errors=4)
django.db.utils.OperationalError: table vent_auth_withdrawalrequest
has no column named hold_id
```

That was the wallets leaf mid-build, a model field added without its migration.
It was in their paths, so it went into inbox row 217 rather than being edited,
and every module this leaf owns passed on its own throughout.

They shipped the migration, and the re-run reads:

```
Ran 3469 tests in 587.844s
OK (skipped=1)
```

Row 217 is closed with that output, and EAFC F1 and run-of-show G2 are ticked
with it. Writing it down rather than reaching into their paths is why it closed
without either leaf standing on the other. The suite also grew by 264 tests in
that window, which is the rest of the team building.

**Three gate boxes owned by other leaves** are unticked while their own CHECK
already passes, because the CHECK is a global checker that cannot prove a
specific feature: `gates/20-wallets.md` D3, `gates/21-subscriptions.md` D3,
`gates/22-admin-dashboard.md` E3. Inbox row 220.

`vent_billing` was briefly in the tree without being in `INSTALLED_APPS`, which
made every Django test abort with `Model class vent_billing.models.Plan doesn't
declare an explicit app_label`. It fixed itself within a minute, presumably by
the subscriptions leaf adding the app. Recorded because a run in that window
reads as a real failure and is not one.

---

## 5. Inbox rows corrected, each against a command

| row | was | now | proven by |
|---|---|---|---|
| 47 | todo, 26 endpoints | todo, **18 left**, named | `python tools/endpoint-callers.py` |
| 52 | **todo, not started** | **done** | `vent_tournament.tests_overlay_layers` 49 tests OK, plus the model, the endpoints and the screen quoted |
| 78 | todo | done | closed by row 170; `check-live-updates.mjs` reads 0 dead timers |
| 88 | todo | done | closed by row 171; the four-slot Layers panel |
| 173 | todo | done | `vent_auth.tests_dm_policy` 6 tests OK |
| 182 | todo | done | `vent_auth.tests_rankings_regions` 15 tests OK |
| 183 | todo | done | same, plus `vent_auth/regions.py` |
| 184 | todo | done | same |
| 214 | todo | done | this handover, section 1 |
| 215 | todo | done | this handover, sections 2 and 3 |

Row 185 was left alone: another leaf is fixing it.

Rows **217 to 220** are new, for the things that are genuinely open.

Row 52 is the one worth reading twice. It said "todo, not started" and the work
was fully built: the model docstring quotes the CEO's ask word for word. That
is the same shape as row 194 and the vendor pitches tab, and it is the reason
row 215 was raised at all.

---

## 6. What is still open

1. **Eleven gate boxes** need a browser or emulator walk. Row 218.
2. **`pnpm build`** has not run this session. Row 219.
3. **One gate box in another leaf's file**, down from three. Row 220.
4. **Three debts that went up** in other leaves' paths, so `check-all.py` exits
   1 until they come down. Rows 221, 222 and 223.
5. **Row 47**: 18 endpoints still have no screen. Each needs a door built or
   the endpoint deleted. They are named in `tools/endpoint-callers-baseline.json`.
6. **`check-all.py` exits 1** on three debts that went up in other leaves'
   paths. Rows 221, 222 and 223. It stays failing until they come back down,
   which is the promise the ledger now actually keeps.

---

## 7. The fix started earning its keep within the hour

This is the part worth reading. `check-all.py` was green at the moment the
ledger was corrected. An hour later it exits 1, and every reason is a real
regression another leaf had just shipped into the tree:

```
DEBT WENT UP. This is a regression and it blocks:
  seo                0 -> 2
                     87 public route(s) checked, 2 problem(s)
  tap targets        144 -> 145
                     319 stylesheet(s) checked, 145 tap target(s) under 44px on a phone
  live updates       0 -> 6
                     6 page(s) that fetch and never refresh:
```

- **seo 0 to 2.** `/memberships` and `/plans/<slug>` are in neither the sitemap
  nor the robots disallow list. Every public route has to be one or the other.
  Inbox row 221. **Under the old parser the seo ceiling was 85, the number of
  routes scanned, so both problems would have raised nothing at all.**
- **live updates 0 to 6.** Five admin console pages plus `/memberships` fetch
  and never refresh. Inbox row 222.
- **tap targets 144 to 145.** Somebody fixed one target during the session, the
  ledger wrote 144 down as the new ceiling, and then eight new stylesheets
  landed (311 to 319) and one of them, `.rowAction` in
  `src/components/memberships/org-memberships.module.css`, is 36px. The climb
  back now fails. Inbox row 223. That is the write-back doing exactly what it
  was built for: debt cannot quietly return to where it was.

None of the three is in this leaf's paths, so all three are inbox rows and a
NEEDS entry in `gates/18-ledger-and-gates.md` rather than edits.

The `stale gates` catcher recorded its first ceiling at **1**, down from the 10
it found before the two gate files were decided.

## 8. A second catcher that was sitting outside the table

`tools/test-ledger-writeback.py` was written on 7 September and nothing has
ever run it. That is the third time a catcher has sat on disk outside
`check-all.py`, and it earned its place inside the hour: correcting the parser
INVERTED an assertion in it, and nothing would have said so.

That block was calibration written on 7 September to justify why a clean
catcher is recorded by its exit code rather than parsed. It asserted that
`_count` MISREAD four real lines, which it did. Now every one of them reads as
its honest 0, so the block was rewritten to assert that instead, with the
history kept in the comment. The exit-code path stays regardless: exit 0 is the
catcher STATING there are no faults, which is exact, where any reading of its
prose is an inference.

It is now a blocking catcher named `ledger writeback`.

## 9. A slow pass found a fault in gate files across the team

`check-stale-gates.py --slow` also runs the Django tests, and it reported 16
boxes it could not decide. Three were the `cd V-ENT &&` lines in my own file.
Most of the rest were the same thing in four gate files:

```
ImportError: Couldn't import Django
```

They write a bare `python manage.py test`, and the system Python on this
machine has no Django. The one that does is
`V-ENT-BACKEND/venv/Scripts/python.exe`, which `GATES-EAFC-CARDS.md` already
writes. Affected: `gates/19-rankings-media.md` C1, `gates/20-wallets.md` B3 and
D1, `gates/21-subscriptions.md` B2, and `GATES-RUN-OF-SHOW.md`, which is fixed.
Inbox row 224, and the other leaves' files are named rather than touched.

It is the same fault as H3 naming a checker that does not exist: a gate whose
command cannot run is a gate nobody can close.

## 10. Where the numbers stood at the end

The tree moved throughout, because six other leaves were building in it. The
last reading:

| catcher | ledger | note |
|---|---|---|
| tap targets | 141 | was 311 in the ledger and 145 in truth. Went 145, 144, 145, 141 during the session as leaves fixed and added |
| stale gates | 1, now reading 3 | all three are other leaves' boxes, inbox row 220 |
| live updates | 0, now reading 6 | inbox row 222, still open |
| seo | 0 | went to 2 and back to 0 within the hour, inbox row 221, closed |
| everything else | 0 | 22 catchers |

`check-all.py` exits 1 on live updates and stale gates. Both are other leaves'
work, both are written down, and both keep failing until the number comes back
down. That is the ledger keeping the promise its header has made since the day
it was written.

## 11. Two catchers registered on request, and a diagnosis I repeated that was wrong

The lead wrote and proved both; my part was to verify each before putting it in
the table, since a row taken on trust is how a catcher nobody has run gets into
`check-all`.

| catcher | reads | self-test |
|---|---|---|
| `offer surface` | `7 organiser setting(s) checked, 0 with no buyer surface` | `4 self-test case(s) pass` |
| `dev distdir` | `1 config checked, 0 build directory clash(es) possible` | 4 cases, including the shape that broke |

Both blocking, both exit 0. Both summary lines are the same shape this whole
handover is about, the scanned count first, so under the old parser their
ceilings would have been 7 and 1 rather than 0 and 0. Both readings are now
cases in `tools/test-count-parse.py`, which stands at 59 cases with the old
rule wrong on 22 of them.

`offer surface` exists because three ticket settings an organiser could set had
no buyer screen: a group rate of 16 VC at four or more was charged by the server
while the panel said 20 x 4 = 80 and took 64, an early bird price the serializer
carries a comment about was never drawn, and an access code tier could be bought
by nobody because `ticket_types` reads `?code=` and no screen had a box for one.

`dev distdir` exists because `next.config.mjs` used a fixed `.next-dev` and four
agents were running dev servers on 3001, 3002, 3005 and 3007, all writing that
one directory. Three times in an afternoon the frontend answered `Cannot find
module './vendor-chunks/next-auth@4.24.13_next@14.2...'` for everybody.

**The part worth keeping is the wrong diagnosis.** That was first blamed on
`pnpm build`, and I repeated it: it is the reason I wrote into run-of-show gate
G4 and inbox row 219 for leaving the frontend build unrun. Both are corrected.
The build was innocent and the error named neither webpack's real problem nor
next-auth's.

"Module not found" in this repo now has four causes and each names a different
module, which is the whole tell:

| the message names | the cause |
|---|---|
| `next/dist/build/entries.js` | the pnpm store |
| `next/dist/pages/_app` | a stale production `.next` |
| `compiled/jest-worker/processChild.js` | building while dev is running |
| `./vendor-chunks/<pkg>@<version>` | two dev servers sharing one distDir |

Read the error string, not the pattern. Recorded in
`feedback_pnpm_build_breaks_dev`, whose three earlier dated failures still
stand: today's is a fourth cause, not a correction of them.

G4 stays open, on the honest reason: a production build in a tree six leaves
are working in is the lead's call, not a leaf's.

## 12. A blocking catcher went red while I was registering these

`check-user-chips` was clean earlier in the session and now reads:

```
NAMES WRITTEN BY HAND (1):
  src/components/memberships/OrgMembershipsPanel.js:613 renders a name without UserChip
```

Lines 608 to 615 hand-build a person out of `Avatar` + `Link` +
`row.subscriber?.username` instead of `<UserChip user={row.subscriber} />`, so
that row loses the founder mark. It is blocking, so it stops a commit. The
subscriptions leaf's file, so it is inbox row 225 and a NEEDS entry rather than
an edit.

## Files changed

```
V-ENT-BACKEND/tools/check-all.py            _count rewritten, _summary_line added,
                                            five catchers registered
V-ENT-BACKEND/tools/test-ledger-writeback.py  calibration block inverted, and the
                                            file is now run by check-all
V-ENT-BACKEND/tools/test-count-parse.py     new, 55 cases and 8 tails
V-ENT-BACKEND/tools/check-stale-gates.py    new, with --self-test and --slow
V-ENT-BACKEND/tools/debt-ledger.json        tap targets 311 -> 145
GATES-EAFC-CARDS.md                         27 of 29 decided
GATES-RUN-OF-SHOW.md                        25 of 37 decided
tasks/inbox.md                              11 rows corrected, 7 appended
                                            (217 to 225; 217 and 221 closed again; 219 corrected)
gates/18-ledger-and-gates.md                evidence filled in as the work went
```

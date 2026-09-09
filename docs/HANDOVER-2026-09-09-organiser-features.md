# Handover, 9 September 2026: the tournament organiser features (row 249)

Written for somebody who picks this up cold. The gate file is
`gates/28-organiser-features.md` and it carries the evidence line by line; this
is the shape of the day, what is proven, and what is not.

## Where it stands

**Built, walked, committed, pushed, PRs open. Not merged and not deployed.**

* backend `feat/organiser-premium-tier` -> PR **V-ENT-BACKEND#167**, commit `d7874702`
* frontend `feat/organiser-premium-tier` -> PR **V-ENT-FRONTEND#181**, commit `6218aa4`

Deploying runs **two additive migrations** on production: `vent_tournament`
0050 (stage dates and place) and 0051 (PrizeSchedule). That is why E3 is the one
gate still open: production writes need the CEO's word.

## What the row actually asked, and what came out of it

The spec is in `tasks/specs/tournament-organiser-features.md`. Its own first
instruction is an audit, and the audit is the most useful thing in the gate
file: **30 of the spec's lines were already built** (the five step wizard, the
ruleset editor, brackets, the results desk, the studio, ticket codes, thirteen
entry requirement kinds, 45 organiser options), and **12 were genuinely
missing**.

Six of the twelve are now built. Six are written down as deliberately not built,
with reasons, in `tasks/specs/organiser-features-not-built.md` - the CEO chose
that on the day: "Write them down, do not build."

### 1. Something to gate against (`vent_auth/premium.py`)

Nothing on the platform could be gated at all. `vent_billing` sells an
ORGANISER's plan to their own followers, which is a different product from a
V-ENT subscription, so all fifteen PREMIUM lines in the spec had nothing to ask.
CEO chose the interim: "A flag admins set, sold later."

`has_premium(who)` takes a user, a tournament, an event or an organisation. The
organisation wins when there is one. `is_premium` and `premium_note` are FIELDS
on both `Users` and `Organization` through one abstract mixin, so when a
subscription eventually writes the same field no reader can tell the difference.

**Still missing: an admin console control to set it.** The field exists and
nothing in the interface writes it. Today it is a shell command. That is the
first thing to build next.

### 2. The structure, explained (`vent_tournament/structure.py`)

The spec line is "tournament structure, explained automatically once the bracket
is chosen". The words already existed; the missing half was the ARITHMETIC.
`describe(format, participants, seats)` answers with rounds, matches, byes and
what is on the floor, and `?participants=` on `/tournament/formats/` serves it.

Two things it is careful about, and both are worth keeping:

* it is computed by the same rules the generator uses, and
  `tests_structure.py` builds REAL brackets and asserts the numbers agree. A
  wizard that promises 15 matches and a generator that makes 16 is worse than a
  wizard that promises nothing.
* it reports what will really be DRAWN. `services/bracket.generate` has three
  shapes - a table, double elimination, and single elimination for everything
  else - so choosing Swiss produces a knockout bracket, and the wizard says so
  instead of describing a Swiss draw nobody will build.

### 3. The wizard's third copy of the count rules

Found while building the above, and it is the more valuable half. `Participants`
kept its own participant rules and they were wrong for four of the eight
formats: `swiss` was compared against a value that normalises to `swiss_system`
so the rule **never fired once**; double elimination was given 2 against the
catalogue's 4; round robin's ceiling of 20 was absent, so 40 teams could be
typed into a form that would build 780 fixtures; and three formats had no rule.

`src/lib/formatCatalogue.js` is now the one reader. `check-format-catalogue.py`
gained a third comparison for the league step's own list of table formats, and
its alias regex only matched quoted keys, so it had been reading ZERO aliases
from the real file since it was written.

### 4. Penalty points and ranking as entry requirement KINDS

Both columns have existed since the beginning with nothing reading them. They
are kinds beside the thirteen, and both are in `PER_MEMBER`, so a team satisfies
them once per PLAYER: a squad whose fourth member is suspended is not eligible,
and only the captain used to be asked.

`vent_auth/ranking_core.py` came out of `views_rankings` so the rank enforced is
the rank the leaderboard shows. **Unranked is its own answer**: a position
earned by nothing is not a rank, and without that distinction a brand new
account is "in the top ten" on a platform of eight players.

### 5. Bracket dates, times and place

Five columns on `TournamentStage`, because a stage IS the bracket phase the spec
means. Blank means the tournament's own, and `effective_when()` /
`effective_where()` are the one place that decision is made.

### 6. Documents, and five dead download buttons

The three export sheets come as xlsx, docx and pdf through `documents.py`.

While walking it: entry codes and all three export sheets were `window.open`,
under a comment claiming the token was in the address. It was not, and a
navigation carries no header, so **every one of those buttons opened a tab
showing a JSON refusal**. The events side had the working version inline;
`src/lib/download.js` is now the shared one.

### 7. Prizes: a warning before coins leave

`distribute-prizes` paid immediately on one press. It refuses without `confirm`
now, and the confirmation is `prizes.plan()` - every winner, every amount, the
total, resolved by the same code the payout runs, so what is approved is what is
paid. `PrizeSchedule` plus `manage.py pay_due_prizes` is the automatic half.

**To turn the automatic half on in production**, add to cron beside the backup:

```
*/15 * * * * cd /srv/vent/backend && venv/bin/python manage.py pay_due_prizes >> /var/log/vent-prizes.log 2>&1
```

Run it once with `--dry-run` first. Until that line exists, a schedule an
organiser sets will simply never fire, which is the failure mode to watch for.

## What is proven, and how

* `manage.py test vent_tournament vent_auth` -> **2351 tests, OK** (skipped=1),
  87 more than this row started at.
* `tools/check-all.py` -> every blocking catcher clean, no debt risen.
* Chrome on desktop: pressed through the wizard, the export rows, the prize
  panel, the requirement composer and the stage editor. **Pressed Pay these now
  on a real completed tournament**: three PrizePayout rows written and three
  wallets credited 25000 / 12000 / 5000.
* The evotv_test emulator at 1080x2400 density 420 (**412 CSS px**): zero
  horizontal overflow on the wizard, the manage screen and the prize panel;
  every new button measures 44px.
* Every export format proven against a live server by its BYTES: csv text, xlsx
  and docx starting `PK`, pdf starting `%PDF`.

## What is NOT proven

* **Nothing is on production.** The PRs are open and unmerged.
* `pay_due_prizes` has never run on the VPS. It is tested and dry-run tested
  locally, and the cron line above does not exist yet.
* The automatic payout has never fired on real money. By design it warns first,
  but the first real one is worth watching.

## Two things worth knowing before the next session

**The tap-target checker cannot see this class of fault.** The wizard's format
step has 37 controls under 44px on the device - step chips at 30px, info tips at
17px, option chips at 34px. None is new. `check-tap-targets` reads STYLESHEETS
and reports 0, because these heights come out of the layout rather than a rule.
A checker that measures on the device would catch them; that is real work and it
is not this row's.

**The emulator's DevTools needs a clean tab list.** 159 stale targets made every
CDP connection hang. Closing all but the live one through
`http://localhost:9222/json/close/<id>` fixed it in seconds, and
`scripts/emulator-eval.mjs` then works normally.

## Next

1. Row 249's E3: merge both PRs, deploy, verify live. Needs the CEO's word.
2. An admin console control for `is_premium` and `premium_note`.
3. Row 250: the whole Vermillion City marketplace, **built but GATED**. The
   CEO's constraint, verbatim: "But please make sure it is built, but still
   gated, we dont want to release the marketplace yet to the public." Gating
   means a real switch that is OFF, every endpoint refusing, no link anywhere,
   out of the sitemap and into the robots disallow list.

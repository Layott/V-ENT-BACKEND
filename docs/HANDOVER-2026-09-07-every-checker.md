# 7 September 2026: the CEO's own badge, and every checker cleared

Two asks, and the second explains the first.

> "under an irganization oge my profilr pic and badge dudnt show" (row 158)

> "please all the checkers and make sure they are not pointing to anything and
> fix them if they are." (row 159)

The bug the CEO found by looking at the screen had been sitting in a checker's
output the whole time, at `org-profile/page.js:506`, inside a count of 21 that
nobody had worked down. That is the entire lesson of the day.

## What was actually wrong with the organisation page

`serialize_org` sent

```python
'founders': [org.org_creator.username]      # a list of STRINGS
'owner': org.org_owner.username             # a string
```

A username is not a person. There was no picture to draw and no badge to draw,
so the panel drew two letters in a grey circle. Both go through `_person` now,
via `_founders()` and `_person_row()`, and `owner_username` stays for anything
wanting the plain string. `founders` credits the creator AND the owner when
ownership has moved, so a transferred organisation still says who started it.

The `owner` half is the same field that made `org.owner.username` read
`undefined` for a signed-out visitor and offered every stranger a Manage
button. `usernameOf()` on the front end already read either shape.

Proven by `vent_auth/tests_org_people.py`, 9 tests, written against the SHAPE
rather than the panel.

## The four debt rows, all cleared

| Catcher | Was | Now | What was really there |
|---|---|---|---|
| avatars | 21 | 0 | 4 false (game logos, a first name), then 6 MORE found once the rule saw two-letter monograms |
| slugs | 21 | 0 | 6 died with the dead components; 15 real, including a vendor model with no slug |
| wizard round trip | 19 | 0 | 10 false (a helper call), 4 false (aliases), 3 real data loss, 2 dead sends |
| prose | 27 npm | 0 | 14 real instructions, 8 documents describing the ban, 3 archived plans |

And the four that graded themselves clean while carrying a number:

| one model | 12 hand-built person dicts | 0 |
| design bans | 2 em-dash | 0 |
| timing model | 12 number notes | 0 |
| css classes | 202 undefined classes | 202, deliberately (see below) |

## The three real faults inside the round-trip count

All the same class as "0/32 slots": the wizard POSTs to `create_tournament` and
PUTs to `edit_tournament`, create understands the field and edit does not.

1. **`hide_location`** - switched on while continuing a draft and the venue
   stayed public.
2. **`winner_prize`** - corrected while continuing a draft and the old figure
   stayed. It is not a column: create writes it as the position-1 prize row, so
   edit rewrites that row rather than adding a second.
3. **`organization`** - the DRAFT MAPPER had no key for it, and the wizard
   appends `organization` on every submit with `|| ''` behind it. So re-opening
   any draft posted an empty organisation and **cleared** whichever one was
   chosen. Worse than losing a value: it destroyed one.

A latent 500 came out with them: `to_coins` returns 0 for anything
unparseable, but `amount_original` is a decimal column, so "lots" in the prize
box reached the database and came back as a 500 naming no field. Both create
and edit refuse it by name now.

Proven by `vent_tournament/tests_draft_edit_three_more.py`, 11 tests.

## What the Chrome walk caught that nothing else did

The organisation page's **Recent activity** panel was a column of bare
timestamps. Three faults in one small view:

- no `id`, so every row keyed on `undefined` and React warned on every render
- the API sent `text` and the page reads `title`, so the sentence rendered as
  nothing
- the sentence was built in Python with an f-string, so it could never be
  French or Portuguese

All three fixed; the row carries a code plus its parameters now.

Also pressed rather than read: a rankings row. The payload carried no username
and no slug, so `/u/${entry.username || entry.name}` fell through to the
DISPLAY NAME and opened `/u/Bisi Adeleke`, a 404. Rows carry `username` and
`slug` now, teams and organisations too, and the organisations tab gained the
crest it was passing `None` for. A row now opens `/u/demo_bisi` and it loads.

## Calibration, which is half the work

Every checker touched got a `--self-test` proving it both ways, because a
checker reporting 0 means "clean" or "broken" and nothing else distinguishes
them.

- **avatars**: a game has a `name` too; `.split(' ')[0]` is a first name, not
  an initial. Then the opposite: `\bavatar` missed `shared.userAvatar`, and
  `.slice(0, 1)` missed a two-letter monogram. Widening it found six real
  faults in the admin console, where **no payload carried an avatar at all** -
  including the KYC queue, where the reviewer has the identity document and the
  account and the account's own picture is one of the two things worth
  comparing.
- **wizard round trip**: now reads a view's body PLUS the helpers it calls, two
  levels deep. Ten of nineteen findings were `_league_settings`.
- **prose**: a line can NAME npm without telling anybody to run it.
- **one model**: a wrapper nesting a person, and a dict completed by a bulk
  attach below it, are not hand-built people.

## What is deliberately NOT done

- **202 undefined CSS classes.** Ten sampled by hand: every one is a
  `styles.x` in JSX whose module has no rule for `x`, on a paragraph, a table
  cell or a heading. The element gets no class. The checker's own grading says
  0 are on an interactive element. Each needs a design decision, which is a
  styling pass rather than a checker pass.
- **2826 em and en dashes.** All in docs and historical records: specs, session
  journals, dated audits, the CLAUDE.md files. Shipping user-facing copy is at
  0, which `check-design` proves. The prose checker used to close on the npm
  count, so once that hit 0 the table printed `prose debt 0 npm command(s)` - a
  debt row reading zero beside 2826 nobody tracked. Its closing line carries
  both numbers now and the ledger holds 2826 as the honest ceiling, with the
  reason written into the ledger entry.
- **The admin console was not walked in Chrome.** `/admin/users` bounces to
  `/home` without the TOTP step-up on this session. The admin avatar work is
  proven by the suite and the checker, not by a walk. Stated rather than
  rounded up.

## Verified

- `manage.py test vent_auth vent_event vent_tournament vent_team`: **2635
  tests, OK** (2581 this morning; 20 new here).
- `pnpm build`: exit 0.
- `python tools/check-all.py`: every blocking catcher clean, one debt row and
  it is the one deliberately opened above.
- Chrome, desktop and 390x844: the founders row is a real picture element plus
  a real badge, linking to `/u/demo_chidi`; no horizontal overflow at 376px.

## Traps hit again, worth remembering

- A stale `runserver` held port 8000 and served old code. The API kept
  answering the OLD shape after the fix. Kill every python before trusting a
  local API response.
- Then the opposite: killing python.exe to restart the dev server killed a
  background test run, which reported exit 1 and looked like a failure.
- Heredocs mangled `\n` into a real newline inside a regex, twice, in a
  codebase that has a documented rule against exactly this. Use Write/Edit for
  anything carrying escapes.

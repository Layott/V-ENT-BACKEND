# tools

## endpoint-callers.py

Every backend endpoint, and whether anything in the frontend calls it.

Four separate faults in one night had the same shape: the endpoint was built,
tested, green, and no screen ever called it.

- `PUT /event/edit-event/` existed for weeks. An organiser who mistyped a venue
  had nothing to press.
- Ticket tiers could be read and never written after the creation wizard.
- `redirect_uris` was accepted by the API and editable on no screen an approved
  partner could reach.
- `Format.can_feed_into` was recorded and read by nothing.

A passing test suite says the endpoint works. It says nothing about whether
anybody can reach it. This is the check that would have caught all four on the
day they landed.

```bash
python tools/endpoint-callers.py            # fails on a NEW orphan
python tools/endpoint-callers.py --list     # every orphan, for reading
python tools/endpoint-callers.py --json     # machine readable
python tools/endpoint-callers.py --baseline # record today's orphans
```

It fails on a **new** orphan, not on the backlog. Failing on all 24 known ones
would mean the check is red from the first minute, and a check that is always
red is a check nobody reads.

### When it fails

Either call the endpoint from a screen, or add it to `DELIBERATE` in the script
with the reason it is not meant to be called from a browser. A route in
`DELIBERATE` is a decision; a route in the baseline is a debt.

### What it deliberately does not do

It matches on the URL's literal path segments rather than parsing requests, so
a frontend that builds a URL from a variable still matches. That is the right
trade: a false "called" is a missed warning, a false "orphaned" is a broken
build and somebody losing an hour, and the second is much worse.

## The catchers

Every rule in `V-ENT/CLAUDE.md` that a scanner can hold has one, because a rule
that lives only in a document survives until the next person reads it.

    python tools/check-all.py              everything, in one table
    python tools/check-all.py --blocking   only what must be clean

Two tiers. **Blocking** catchers are at zero and must stay there: a new breach
is something somebody just wrote and is cheap to fix while they remember why.
**Debt** catchers report real breaches that predate them, in numbers too large
to clear in one pass; they never block, because a check that always fails is a
check everybody learns to ignore, and the blocking ones get ignored with it. A
debt number that goes UP is still a regression. Move a catcher to blocking the
day it reaches zero.

| Catcher | Rule |
|---|---|
| `check-parity.py` | built for events or tournaments but not both |
| `check-one-model.py` | one Tournament, one Event, one Team, one User |
| `check-wizard-roundtrip.py` | every setting the wizard sends survives create, edit and reopen |
| `check-prose.py` | no em or en dashes, and no npm |
| `check-required-fields.py` | a field the API demands is a field the form asks for |
| `endpoint-callers.py` | an endpoint nobody can press is not built |

The frontend half lives in `V-ENT-FRONTEND/scripts/` and is run by the same
`check-all.py`: signed-out controls, control bytes, dangling refs, undefined CSS
classes, translation keys, dictionary parity, avatars, slugs, SEO, design bans.

These live in the repo rather than in the workspace root on one machine,
because a checker only one person has is not a rule anybody is held to.

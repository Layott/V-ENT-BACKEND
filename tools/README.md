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

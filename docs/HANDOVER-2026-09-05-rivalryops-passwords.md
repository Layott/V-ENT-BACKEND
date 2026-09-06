# Handover, 5 September 2026: the rivalryops passwords

One ask, inbox row 76. "For the rivalryops accounts you created, please change
their passwords and make it very simpler passwords."

## What changed

Both event operations accounts on production now use the same password:

| Account | Email | Password |
|---|---|---|
| `rivalryops1` | rivalryops1@v-ent.co | `rivalry2026` |
| `rivalryops2` | rivalryops2@v-ent.co | `rivalry2026` |

Same string for both deliberately: at a door, two people passing a phone around
should have one thing to remember, not two. All lowercase, no symbols, no case
switching, and it can be said out loud across a noisy room without spelling it.

Set on the box with `set_password` in the Django shell, not through the app, so
`AUTH_PASSWORD_VALIDATORS` never ran. That matters only if either account is
later changed through the normal Settings screen: the validators apply there and
`rivalry2026` would still pass them (11 characters, not all numeric, not on the
common list).

Nothing is in the repository. No code changed, no migration, no deploy. This was
a data change on production only.

## How it was proven

The hash being right is not the same as the door opening, so both were driven
through the real endpoint rather than trusted from `check_password`:

```
POST https://api.v-ent.co/auth/login/  {"username_or_email":"...","password":"rivalry2026"}
  rivalryops1 -> 200, session_token issued, is_staff false, no 2FA challenge
  rivalryops2 -> 200, session_token issued, is_staff false, no 2FA challenge

GET  https://api.v-ent.co/event/my-events/  (bearer, rivalryops1)
  -> 200, RIVALRY SERIES SEASON 2, 1397 tickets sold
```

So the change did not cost either account the access row 75 built for them:
manager of events in CADE ESPORTS, and named on the event itself.

## Nobody was signed out, and that was not luck

The event was **live while this ran** - it ends 5 September at 23:00 UTC - so a
change that logged the door staff out mid-shift would have been the whole cost
of the task. Two separate things happen to make it safe, and both were read
before pressing anything:

1. `set_password()` rewrites `Users.password` only. The live session is held in
   `login_session_token`, a column of this project's own, and Django's password
   machinery knows nothing about it. There is no session-hash invalidation here
   the way there would be with `django.contrib.auth`'s session backend.
2. `issue_session()` **reuses a still-valid token** rather than minting a new
   one. That is why the two verification logins above did not bump anybody
   either. The comment on it records the bug it was written for: every login
   used to overwrite the token, so a second tab killed the first session.

If a future task ever does need to force everybody off an account, the lever is
clearing `login_session_token`, not changing the password.

## What this costs, stated plainly

`rivalry2026` is guessable by anybody who knows the event's name and can see a
username. These two accounts are not staff and not superusers, so the console is
out of reach, but they can manage the event: read the attendee list, work the
door, see the numbers, post announcements. That is the trade the simpler password
buys and the CEO asked for it directly, knowing the accounts.

**The thing worth doing next:** rotate both to something long once the event is
over, or deactivate them. They exist for two days of door work. Left standing
with a guessable password they are a way into 1397 people's ticket records.

## Still open elsewhere, unchanged by this session

- `feature/run-of-show` is 4 commits ahead of main on both repos, pushed, **not
  deployed**. Rows 50, 51, 53, 63, 66.
- No browser walk of any of that work. The connected Chrome is a Remote Control
  device and cannot reach this machine's localhost.
- Row **47**: 26 endpoints with no screen, baselined.
- Row **69**: the ten Rivalry Series player names, waiting on the CEO.
- Rows **55/56**: the overlay files were never sent.

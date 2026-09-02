# Handover, 2 September 2026

Signed-out gating, block/mute/report, phone country codes, the edit-event hub,
and the platform's mail moved onto Google Workspace. Written while working, not
at the end.

Ledger: `V-ENT/GATES-SEP02.md`, **26 of 43 met**. Run it with
`python tools/gate-run.py GATES-SEP02.md` from `V-ENT/`.

---

## Mail: now sending as info@v-ent.co

**Live and verified.** Django hands mail to local Postfix on `127.0.0.1:25`,
which now relays to `smtp.gmail.com:587` authenticating as `info@v-ent.co`.

Verified two ways: `mail-relay.sh gmail --test` and, more importantly, Django's
own `send_mail`, which left at 01:55:15 with `status=sent` through
`smtp.gmail.com`. Testing only `sendmail` would not have proven the app path.

### The Workspace SMTP relay is not available and never will be on this tier

```
550-5.7.0 SMTP relay isn't supported for unmanaged work accounts.
```

It is a paid-tier feature. No IP allowlist, DNS record or configuration on our
side changes it, so the earlier plan to allowlist `162.35.101.16` in the admin
console was wasted effort. `smtp-relay.gmail.com` has been **removed from the
fallback candidate list** in `/srv/vent/deploy/mail-relay.sh`, because a safety
net that always answers 550 is worse than none.

Gmail's ordinary **user** SMTP works on every tier. It needs 2-Step
Verification on the mailbox plus an app password. The app password works with
or without spaces: Google ignores them, and `postmap` keeps the whole remainder
of the line as the value.

| | |
|---|---|
| Relay | `[smtp.gmail.com]:587` as `info@v-ent.co` |
| Sender | rewritten `@v-ent.co` to `info@v-ent.co` via `smtp_generic_maps` |
| Fallback | Brevo, then Resend. **Unreachable-host only, not quota** |
| Cap | ~2000/day, up from Brevo's 300 |

`DEFAULT_FROM_EMAIL` is still `V-ENT <no-reply@v-ent.co>` and Postfix rewrites
it on the way out, so **replies to platform mail land in the info@ inbox**.
Gmail will not send as `no-reply@` until that address is added as a "Send mail
as" alias in the info@ mailbox. Open decision for the CEO.

DKIM is Google's `google._domainkey`, already verified, so this mail is signed
by and aligned with `v-ent.co`. DMARC is `p=quarantine` with relaxed alignment,
so DKIM is the only thing keeping it out of spam.

### Outstanding, and it matters

**Credentials exposed in the session transcript need rotating**, after
confirming the relay is healthy, never before:

- the `info@v-ent.co` app password now in `/etc/postfix/sasl_passwd`
- the older `vermillioninformation@gmail.com` app password
- both Brevo keys (API and SMTP)
- the ipinfo token

---

## Shipped to PRs, not yet merged

- Backend **#108** https://github.com/Layott/V-ENT-BACKEND/pull/108
- Frontend **#117** https://github.com/Layott/V-ENT-FRONTEND/pull/117

Both branches were re-cut off `origin/main`; the previous branches were already
merged and one commit behind, with all the new work sitting uncommitted on top.

### Backend, 81 tests passing

**Block, mute and report** were three toasts that made no request. Somebody
blocking a harasser was told it had worked. The tests assert the block *stops*
something rather than that a row is written, and it is enforced in both
directions and on conversations that already exist, which is the case a block is
actually for. Mute is deliberately separate and does not stop them reaching you.
A second report returns the first rather than filling the queue.

**Following an organisation** puts its events and tournaments in one feed. The
literal route must precede the `<str:org_id>` catch-all, or the feed 404s as an
organisation named "following". Django matches in order and there is no
specificity rule; a test pins it.

**Phone country codes.** `0803...` converts to `+234`, because refusing it would
refuse most real Nigerian numbers to enforce a technicality. A number with
neither a code nor a leading zero is refused: nothing there says which country.

**ipinfo** reads both response shapes; against the Lite shape it silently
returned `(None, None)`, so geolocation looked switched off rather than broken.

### Frontend

**The signed-out bug and its rule.** Ownership was decided with
`org?.owner?.username === session?.user?.username`. Signed out, `org.owner` is a
string so `.username` is `undefined`, and the session side is `undefined` too.
`undefined === undefined` is true, so **every** organisation looked like the
viewer's own and offered Manage to a stranger. Fourteen instances across five
files.

`src/lib/gating.js` now carries `sameUser` (false unless both sides exist),
`useViewer` (branch on session *status*, never *data*) and `<NeedsAccount>` (a
gated control is absent or explains itself, never rendered live to be refused on
press). `scripts/check-signed-out.mjs` reports **0, down from 14**, and runs over
pages that do not exist yet. The rule is written into `V-ENT/CLAUDE.md`.

**Edit event is a hub**, twelve cards, each deep-linking to its own tab. The
console previously read its tab from `useState` and never from the URL, so
nothing could deep-link into it. `/events/[slug]/manage` imports
`ManageEventContent` from `/events/manage`, so one builder serves both addresses.

---

## The one worth reading: the "No date set" flag

It was **not** a logic bug, and two separate faults were hiding it.

**1. A literal control byte.** The condition read

```
/<0x08>(day|jour|dia)\s*\d/i.test(row.name)
```

A real `0x08` backspace where a word boundary was meant, written by a shell
heredoc that read the escape. The regex matched nothing. It is invisible in an
editor, a diff, a review and a screenshot, and the build, the linter and the
tests all pass straight over it, so the hunt went to the render logic, then the
API payload, then the component state, and all three were correct.

A second copy was capitalising game names, so `free_fire` had been rendering as
"free fire" rather than "Free Fire".

`scripts/check-control-bytes.mjs` now fails on any control byte in `src/` and
`scripts/`, and is proven in both directions.

**Do not write backslash escapes through the Bash tool.** I re-introduced the
identical byte twice trying to fix it, once inside the fix itself, and a third
time into `GATES-SEP02.md`, where `\\1` collapsed to `\1` and replaced three
gate IDs with `chr(1)`. Use the Write tool, or build the escape from
`chr(92) + 'b'`.

**2. A stale server.** A Django process from an earlier session was still bound
to port 8000, serving a build from before the endpoint returned the event's
days. `eventDays` was therefore empty, which also silently removed the day
picker and the "All days" label. Two processes were listening on 8000; killing
both and restarting fixed it. **When a payload is missing a field the code
plainly returns, check what is actually answering the port.**

---

## Not built, and honestly so

Nothing below is started beyond what is noted.

| Gates | What |
|---|---|
| C1-C5 | **Clubs create / manage / delete from the interface.** Backend exists, the frontend route does not. The CEO asked for this specifically |
| D1-D6 | **Studio flexibility**: organiser HTML upload for events too, the copyable prompt, binding an element to a chosen tournament or event, templates |
| 4 | Rule-configuring settings moved into the event creation wizard |
| 11, 12 | Six of eight studio elements never watched on screen; operator console not walked signed in |
| A6, B5, C5, D6, 13 | Chrome walks, and **every mobile claim** |
| S5, S6 | Merge and deploy, then verify live |

### Two things that block verification

**Mobile is unverified.** `resize_window` silently does nothing when the window
is maximised, and `javascript_tool` is **denied on localhost** in this Chrome
profile, so the same-origin iframe trick is unavailable too. Per the CEO's
standing rule this needs the `evotv_test` Android emulator regardless.

**Chrome on localhost is awkward.** A newly created tab reverts to
`chrome://newtab`; the walk had to reuse the existing tab. First load of any
route takes 20 to 40 seconds while Next compiles, and a screenshot during that
window fails with "Script injection timed out" rather than anything describing
a compile.

### Before merging

Re-run `pnpm build`. It was clean earlier on this branch but three commits have
landed since. Expect to need `rm -rf .next`, `pnpm store prune` and
`pnpm install --force` **from PowerShell**: a production build damages the pnpm
store *and* the build output, and the two present as different missing modules
(`entries.js` for the store, `pages/_app` for `.next`).

---

# Later the same day: shipped to production

Both PRs merged and deployed. Backend #108, frontend #117. Migration 0069
applied, both services active, maintenance flag down.

**Ledger: 31 of 43.**

## The live bug, and why it was not what it looked like

An event reported itself sold out while showing 4814 tickets remaining. Three
faults stacked, none of them "the tickets ran out". The full account is in the
`project_capacity_per_day` memory; the short version:

1. The listing computed `quantity - sold` while the checkout computed venue
   room. Two functions, two questions, and the buyer was shown the one that did
   not decide whether they could pay.
2. **Capacity was counted across days.** 400 is what the room holds on a day,
   not across a weekend. 186 on day one plus 114 on day two plus 100 held hit
   a ceiling meant for one afternoon.
3. Capacity appeared nowhere in the console, so the organiser set 5000 per type
   and could not see the 400 that was overruling it.

Verified live: 114 remaining on day one, 186 on day two, both Buy buttons
active.

## Also shipped

- **Tournament edit screen**, which did not exist. Twenty fields, artwork, and
  an eight-card hub. The game could not be changed at all before.
- **Clubs**: create, rename, delete. Gate C3 had been marked met by a suite
  that deletes topics and messages and never a club; it is genuinely closed now.
- **Esports picture** entry requirement, needing a released image.
- `actor_from_request` answers 401 rather than 400 to anonymous callers, across
  52 endpoints.

## Two checkers were lying

- `check-keys` validated a **prefix allowlist**, so it skipped `tEdit`, `club`,
  `eventEdit`, `org`, `safety`, `req` and `needsAccount` entirely: 4030 of 5243
  keys checked while reporting 0 missing. Now any dotted identifier, comments
  stripped.
- The build caught a duplicate `draft` const that five checkers missed, because
  none of them parses JavaScript. **A build pass is not optional before
  claiming done.**

## Still open, 12 gates

The studio flexibility set (D1-D6): organiser HTML upload for events, the
copyable prompt, binding an element to a chosen tournament or event, templates.
Gate 4, rules into the creation wizard. And every Chrome and mobile walk:
A6, B5, C5, D6, 11, 12, 13.

**Mobile remains unverified.** `resize_window` silently does nothing when the
window is maximised and `javascript_tool` is denied on localhost in this Chrome
profile, so the iframe trick is out too. The CEO's standing rule wants the
`evotv_test` emulator for these regardless.

**Credentials still to rotate**, now that the relay is proven healthy: the
`info@v-ent.co` app password, the old Gmail one, both Brevo keys, the ipinfo
token.

# 30 August 2026 - challenges, AFC, and a sign-in alert that lied

Everything below is committed on `feature/aug29-organiser-tools` in both repos.
Nothing is pushed or deployed yet.

## What the CEO asked for today, in order

1. "this is ugly" - the provider row on the login page.
2. "why did it take me to afc website when i tried to login also? and the page
   has been reloading since also."
3. "afc should be added to connected acounts once a user sings in or signs up
   with it."
4. "pending when the afc is done fixing, lets hide afc for now."
5. "the ilorik there is wrong" - a sign-in alert placing a Lagos sign-in in
   Ilorin.
6. Carried over from 29 August: finish the challenge lifecycle end to end.

## 1 and 2. The login providers

Google was a pill sized to its own text; the partner sign-in was a full-width
bar with its own margin and no mark. They sat side by side in a centred row at
different widths and different heights, and the partner bar was wider and
heavier than the Log in button above it. That is why the CEO pressed it by
accident and ended up at AFC.

Both now come from `src/components/auth-providers/AuthProviders.js`: one shape,
stacked, a fixed 26px mark slot so every label starts at the same x, inset 12px
to line up with the form's inputs and its Log in button. The signup page had no
partner button at all and now renders the same component; its Google handler was
also ignoring the `callbackUrl` its call site passed, sending new accounts to
`/user-profile` instead of `/onboarding`.

**The AFC page reloading is not ours.** Their `/sso/authorize/` correctly bounces
a signed-out visitor to `africanfreefirecommunity.com/login`, and that page takes
about twelve seconds to answer and then sits on "Loading...". Measured:

```
authorize -> 302 https://africanfreefirecommunity.com/login?redirect=...
afc login page -> 200 in 11.66s
```

AFC accepts our client id, redirect URI and PKCE challenge. The CEO confirmed
"afc will fix the bug from their end it seems".

## 3 and 4. AFC as a linked account, behind a switch

Signing in with AFC already wrote an `ExternalIdentity` row. The settings panel
that exists to list connected accounts knew nothing about it, so the one place
anybody would look said nothing. `link_status` carries them now, and the flow can
be started from settings as well as from the login page: `InboundLogin` remembers
which V-ENT account began it, because the callback arrives from AFC with no
session on it and a link and a sign-in are indistinguishable at that point.

- Linking an AFC account that already belongs to somebody else is refused
  (`taken`). Two V-ENT accounts pointing at one AFC account would both answer to
  the same sign-in, and whichever row was found first would win.
- Disconnecting is refused when it is the only way in (409 ONLY_SIGN_IN_METHOD):
  an account created by signing in with AFC has no password.

**`AFC_SSO_ENABLED=1` turns the button back on.** It defaults to `0`, so the VPS
needs nothing done to keep it hidden. `credentials` is kept separate from
`enabled` in `inbound_config`, so hiding it is not the same as deleting the keys.
A provider that is switched off is not listed at all, and `inbound_start` refuses
it, so an old address does not still lead into the broken page. Somebody already
linked keeps seeing their row.

## 5. The sign-in alert

Three faults from one screenshot.

**The city.** An address on a mobile network belongs to the carrier's gateway,
not to the handset. The alert exists so somebody can answer "was that me?", and a
city they have never been to answers "no" for a sign-in that was theirs. It names
the country now and says the country came from the network address.

**The overwrite.** `refresh_daily_location` wrote that same guess over whatever
the account held, once a day, unconditionally. `country` is not decoration - a
challenge open to one country is gated on it - so a wrong guess locks somebody
out of challenges in their own country. It fills a blank now and nothing else.

**The broken logo.** RFC 2387 makes `type` a required parameter on
multipart/related. Django builds the tree correctly and writes a bare
`Content-Type: multipart/related; boundary=...`; with no root declared, Gmail on
Android would not resolve `cid:ventlogo` against the sibling part. Fixed as an
`EmailMultiAlternatives` subclass rather than by rewriting the message on the way
out, so `mail.outbox` still holds a real message with a `.subject` - the first
attempt wrapped it and broke every test that reads an email.

## 6. Challenges, posted to remembered

Gates W1-W10 in `V-ENT/GATES.md`, all closed with evidence.

New: `open_to` (country | countries | anywhere) with a `countries` list,
`ScrimResult` with two-sided confirmation, `vent_auth/views_challenges.py`
(detail/edit/cancel, talk, report, confirm, history), and the screens for all of
it: `/community/challenge/[slug]`, the create form doubling as the edit form on
`?edit=`, a Challenges tab on the profile, and past matches with scores in the
list.

**The one decision worth keeping in mind.** A result is reported by one side and
confirmed by the other. A scrim has no referee, so whatever one player types is
the only account of what happened; if reporting were enough the record would be
whatever the faster typist claimed. A disagreement keeps BOTH sets of numbers,
and the challenge counts for neither record until it is settled. There is no
screen yet for an organiser or admin to settle a dispute - see Open below.

Renamed to "Challenges" everywhere a person reads it. The code still says
`scrim` - model, endpoints, slugs - because renaming those breaks every address
that has been shared. `?tab=scrims` still resolves.

## Found while walking it

- **Linked accounts could not be opened, ever.** The panel's own effect deleted
  `panel` from the URL on mount, and the settings page reads that parameter on
  every render to decide which panel is open, so the panel closed itself the
  instant it appeared. Not from the menu, not from a link, not on the way back
  from a provider.
- **23 translation keys were in no dictionary at all.** Every one carries an
  apostrophe, so it had to be written as a double-quoted key, and whatever
  generated the dictionary only looked for single-quoted ones. They fell back to
  the English written at the call site - silently, on French and Portuguese
  pages. `scripts/check-keys.mjs` now fails on this; `dict-parity.mjs` cannot,
  because a key missing from all three languages keeps the three the same size.
- **A check-in test failed only between midnight and 02:00.** It built an event
  "starting two hours ago" and filed it under today's date, which at 00:55
  describes an event that has not begun. The code was right.
- **Server-built sentences arriving in English on French pages**: who may answer,
  the status of a challenge, and the WRONG_COUNTRY refusal. All three now send a
  code or a field and get their words on the client.

## Verified

- Backend: **1525 tests, all passing** (`DB_ENGINE=sqlite python manage.py test`).
- Chrome, desktop and 390px, in French: login and signup provider rows; the
  Linked accounts panel with AFC; the challenges list with country and past
  filters; a challenge page as owner, as opponent and as a stranger; accept
  refused by country; report, confirm, and dispute; edit and cancel; the refusal
  on editing an accepted challenge; the profile Challenges tab. No horizontal
  overflow at 390px on any of them.
- `node scripts/check-keys.mjs`: 3775 keys, 0 missing. `dict-parity.mjs`: equal.

## Open

- **X1-X6, the admin console for events and tickets.** Not started. This is the
  CEO's other outstanding request from 30 August: manage events, manage tickets,
  and see what organisers actually sent out.
- **Nothing is deployed.** Both repos are committed on
  `feature/aug29-organiser-tools` and unpushed.
- **A dispute has no settling screen.** Two accounts of a match are recorded and
  neither counts; an organiser or an admin needs somewhere to decide it. Worth
  folding into the admin work.
- **AFC's live token exchange and userinfo are still unproven.** Everything up to
  the redirect is verified and the rest is stubbed in tests. It cannot be proven
  until a real AFC player signs in, which needs their login page fixed.
- **The AFC client secret was pasted into a chat transcript.** Rotating it is
  still recommended.
- **The mode blurb on the create form** ("Squads drop on the same map...") comes
  from the backend catalogue and is English on a French page. Same class as the
  three fixed today, not yet done.
- Local `.env` carries placeholder AFC credentials and `AFC_SSO_ENABLED=1` so the
  feature can be walked here. Production keeps it off by default.

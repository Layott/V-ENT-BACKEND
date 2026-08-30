# 30 August 2026 (second session) - AFC is fixed, SSO marks, and a team bug

Everything below is on `feature/aug29-organiser-tools` in both repos.

## What the CEO asked for, in order

1. "AFC has updated their api recheck then update"
2. "make sure the design is good and similar for all the SSO options"
3. "then push and deploy"
4. "then do the k walk on emulator"
5. "then finish up all open and ranked"
6. Mid-session, a bug report with a screenshot: somebody created a team and
   pressed Manage, and was told "Access denied - Only the owner of AVALANCHE
   GAMING can edit this team" on their own team.

## 1. AFC rechecked. Their end is fixed

Measured on 30 August, against the live hosts:

```
/sso/authorize/          302 in 0.91s -> africanfreefirecommunity.com/login?redirect=...
their login page         200 in 1.16s, usable form in 1.41s
/sso/token/              401 invalid_client   (placeholder client id, so this is correct)
/sso/userinfo/           401 invalid_token    (no token, so this is correct)
/sso/.well-known/openid-configuration   200
```

The reason the button was hidden is gone. On 29 August their login page answered
in 11.66s and then sat on "Loading..." for ever. It now reaches a usable form in
**1.4 seconds**, and it carries the authorize URL through as a `redirect`, so the
round trip is whole. Walked in Chrome: their page renders a real form and offers
"Continue with Google", "Continue with Discord" and **"Continue with v-ent.co"**.

**They now publish a discovery document**, which they did not before. It confirms
all three endpoints we had hardcoded from their guide, the scope names, and that
they accept `client_secret_post`, which is how we authenticate.

### What changed here

- `AFC_SSO_ENABLED` **defaults to `1`** now, via a new `enabled_default` on the
  provider spec. This is safe on a host with no keys: `enabled` and `credentials`
  are separate questions and `configured` needs both, so production still draws
  nothing until somebody adds the client id and secret. Two tests pin exactly
  that, because "default on" and "button on a host with no keys" are one edit
  apart.
- New `manage.py check_afc_sso`, which reads the provider's discovery document
  and compares the three endpoints, the scopes and the auth method against what
  we are configured with. It passes against live AFC today. A guide is a
  document; discovery is what the server currently believes, and the drift
  between them is silent until sign-in stops working.

### Still not proven, and cannot be from here

The live token exchange and userinfo. **There are no real AFC credentials on this
machine** - `.env` holds `local-dev-client`, and the real ones were pasted into a
chat transcript in an earlier session and never stored. Everything up to the
redirect is verified; the exchange is stubbed in tests. Gate Q3 stays open.

**The AFC client secret that was pasted into a transcript should still be rotated.**

## 2. The SSO options, made one thing

They were already one shape, from the 30 August morning work. What was left was
the mark: Google, Discord and Steam were drawn from their real logos and a
partner community got two grey letters in a box, which read as the option that
is not quite real.

AFC's mark now comes from their own artwork - `LAYO/CLAUDE/AFC/public/GREEN_1.png`,
the CEO's own AFC repository, 1526x1082 - cropped to the letterform (the
"AFRICA FREEFIRE HUB" wordmark under it is illegible at this size), trimmed and
written to `public/images/afc-mark.png` at 128x81. Drawn at 21px, so it is at
6x its drawn size and never scaled above its own resolution.

Measured on the built page, both providers: **434x46 button, labels both
starting at x=701**. The marks are matched on area rather than on one edge -
Google's square G covers 17x17 = 289, and AFC's wide mark covers 21x13 = 279. A
wide mark set to the same width as a square one reads lighter.

A partner with no artwork still falls back to its monogram, so this does not
have to be solved again before a second community can be added.

Same treatment in the Linked accounts panel, so the two places agree.

## 3. The team bug. Three faults, not one

The screenshot showed one symptom. Underneath it were three separate defects,
and only the first is the one that was reported.

### a. The gate decided before the session existed

`edit-team-profile/page.js` read `useSession()` for `data` and never for
`status`. On mount `session` is `undefined` while NextAuth fetches it, and the
page fetched the team immediately and then compared ownership against nothing.
On a slow connection - the screenshot shows 4G at 1.90 K/s - the team arrives
first and the owner of a team they had just created is shown "Access denied".

Worse, the same race sent the request **without an Authorization header**, so
the server could not answer `viewer_is_owner` either, and that flag comes back
as a definite `false` rather than absent - so `??` has nothing to fall through
to.

Fixed: nothing is fetched or decided until `sessionStatus !== 'loading'`, a
resolving session reads as loading rather than as a refusal, and ownership now
prefers the server's `viewer_is_owner` exactly as the team page already did.

### b. The detail payload had no slug

`serialize_team_detail` carried `id` and `team_id` and no `slug`, so the only
thing the Manage link could be built from was the numeric id. That is how
`/edit-team-profile/25` came to exist, against the standing rule that no numeric
id appears in an address a person can see. The payload carries `slug` now and
the link uses it.

### c. A signed-in stranger got a 500 on any team page

`_viewer_state` queried `TeamJoinRequest.objects.filter(team=team, user=user)`.
The field is `applicant`. Every other query in `vent_team/views.py` had it right;
this one raised `FieldError`.

It only runs for a signed-in non-member: a signed-out visitor takes the early
return above it, and a member or owner short-circuits before it. So **the only
person who ever saw it was a signed-in stranger** - which is the role nobody
tests, and the exact lesson already written down after the last time.

Found by a test written for something else. Three tests now cover that role
directly.

## Verified

- Backend: **1579 tests, all passing** (`DB_ENGINE=sqlite manage.py test`).
- `manage.py check_afc_sso` against live AFC: every endpoint, scope and auth
  method matches.
- Chrome, desktop and a real 390px viewport, in French:
  - AFC's own login page, and the authorize handoff.
  - Login and signup provider rows, both pages, both viewports. No overflow.
  - The team page: Manage now points at `/edit-team-profile/lagos-rangers`.
  - A cold mount of the edit page as the owner, sampled every 30ms through the
    whole of startup: **only ever EDITOR, never ACCESS_DENIED**. The same harness
    against a team the viewer does not own reports ACCESS_DENIED, which is what
    makes the first result mean anything.
  - `view-team` as a signed-in non-member: **200**, was a 500.
  - No console errors.

## Open

- Nothing is pushed or deployed yet.
- No real AFC credentials on this machine; the live exchange stays unproven.
- Gates K1-K9 (nine screens with tests and no browser walk) not yet done.
- A dispute still has no settling screen.

# The V-ENT Partner API

Everything a partner needs to read V-ENT data, and to let their own users sign in
with a V-ENT account.

Two separate things live here, with two separate ways of authenticating, and it
is worth being clear about which is which before reading further:

| | What it is | Who it acts as | How it authenticates |
|---|---|---|---|
| **Data API** | Read tournaments, events, teams, players, brackets | Your application | An API key you hold |
| **Sign in with V-ENT** | Let a person sign in to your site with their V-ENT account | A person, with their permission | OAuth2 authorization code with PKCE |

A key never signs anybody in. A sign-in token never reads the data API. They are
different credentials for different jobs, and neither is a way to get the other.

- Base URL for the data API: `https://api.v-ent.co/api/v1`
- Base URL for sign-in: `https://api.v-ent.co/partners`
- Everything is HTTPS. There is no HTTP endpoint.

---

## Part one: the data API

### Getting a key

1. Apply at `https://v-ent.co/partners`. Tell us what you are building and tick
   the scopes you need.
2. An admin reviews it. You are told what was granted, which may be fewer scopes
   than you asked for.
3. Once approved, issue a key from your partner page.

**The secret is shown once.** It is stored as a hash, so nobody at V-ENT can
read it back to you, including us. Lost it means issue a new key and revoke the
old one. You may hold **five** live keys at a time, which is enough to rotate
without downtime; issue the new one, move your traffic, revoke the old one.

### Sending the key

```http
GET /api/v1/tournaments/ HTTP/1.1
Host: api.v-ent.co
Authorization: Bearer vent_pk_<key id>.<secret>
```

The whole string after `Bearer ` is the credential: prefix, key id, a dot, and
the secret. Send it exactly as it was given to you.

### The response envelope

Every response, success or failure, has the same four keys. Nothing else ever
appears at the top level.

```json
{
  "status": "success",
  "data": { },
  "message": "Tournaments"
}
```

```json
{
  "status": "error",
  "code": "SCOPE_REQUIRED",
  "message": "This key does not have the tournaments:read scope.",
  "data": null
}
```

**Branch on `code`, never on `message`.** The message is written for a human and
is subject to being reworded; the code is the contract.

### Pagination

Every list endpoint pages the same way.

| Parameter | Default | Maximum |
|---|---|---|
| `page` | 1 | no limit, but a page past the end is empty |
| `page_size` | 25 | 100 |

```json
{
  "status": "success",
  "data": {
    "results": [ ],
    "page": 1,
    "page_size": 25,
    "total": 137,
    "has_more": true
  },
  "message": "Tournaments"
}
```

Page with `has_more` rather than by comparing counts. It is computed from the
same query that produced the rows, so it cannot disagree with them.

### Scopes

Granular on purpose, because "give them API access" is not a decision anybody
should have to make in one lump.

| Scope | Opens |
|---|---|
| `events:read` | Events, their schedule and their venues |
| `events:tickets:read` | Ticket types and remaining capacity |
| `tournaments:read` | Tournaments, formats, prize pools, schedules |
| `tournaments:participants:read` | Who is registered for a tournament |
| `tournaments:brackets:read` | Brackets, matches and results |
| `teams:read` | Team profiles and rosters |
| `players:read` | Public player profiles |
| `players:stats:read` | Player win and loss records |
| `rankings:read` | Platform rankings |

Both the **key** and the **partner** are checked on every request. A partner
suspended after a key was issued stops working immediately, not at the next
issue.

### Endpoints

`GET /api/v1/` needs no key at all. It returns the scope catalogue and the
endpoint list, so a new integration can discover the surface before it has a
credential.

| Method | Path | Scope | Notes |
|---|---|---|---|
| GET | `/api/v1/` | none | What this API offers |
| GET | `/api/v1/whoami/` | any valid key | Which partner this key belongs to, and its scopes |
| GET | `/api/v1/events/` | `events:read` | Filter with `?game=<title>` |
| GET | `/api/v1/events/<id>/` | `events:read` | |
| GET | `/api/v1/tournaments/` | `tournaments:read` | |
| GET | `/api/v1/tournaments/<id>/` | `tournaments:read` | |
| GET | `/api/v1/tournaments/<id>/participants/` | `tournaments:participants:read` | |
| GET | `/api/v1/tournaments/<id>/bracket/` | `tournaments:brackets:read` | Rounds and matches |
| GET | `/api/v1/teams/` | `teams:read` | |
| GET | `/api/v1/teams/<id>/` | `teams:read` | |
| GET | `/api/v1/players/<username>/` | `players:read` | By username, not by id |
| GET | `/api/v1/rankings/` | `rankings:read` | |

Only published, public records are ever returned. Drafts, private tournaments
and anything belonging to a suspended account are not in these results, and no
scope opens them.

### Showing that the data came from V-ENT

`GET /api/v1/` carries a `brand` block, so you do not have to go and take a logo
off the website at whatever size you find it:

```json
"brand": {
  "name": "V-ENT",
  "legal_name": "Vermillion Encore",
  "url": "https://v-ent.co",
  "logo": "https://v-ent.co/images/logo_mark_red.png",
  "logo_svg": "https://v-ent.co/images/logo_mark_red.svg",
  "colour": "#ED1C24",
  "attribution": "Data from V-ENT",
  "attribution_url": "https://v-ent.co"
}
```

Use the mark to say where the data came from, at its own proportions and no
smaller than 24px tall. Do not recolour it, stretch it, or use it in a way that
suggests V-ENT endorses your product. Prefer the SVG; it stays sharp at every
size, which the PNG will not.

No key is needed to read this, because somebody deciding whether to integrate
has not got one yet.

### Rate limit

**60 requests a minute per key** by default. Ask if you need more and say what
for.

Over the limit answers `429` with code `RATE_LIMITED`. The counter is per key,
so splitting work across two keys doubles your budget legitimately, and rotating
a key does not reset a limit you are already over.

### Every error code

| Code | HTTP | What happened |
|---|---|---|
| `MISSING_KEY` | 401 | No `Authorization: Bearer` header |
| `MALFORMED_KEY` | 401 | Not a V-ENT key; check the `vent_pk_` prefix and the dot |
| `INVALID_KEY` | 401 | Unknown key id, or the secret is wrong. The same answer for both, so the endpoint cannot be used to discover which key ids exist |
| `PARTNER_INACTIVE` | 401 | The partner account is suspended or not approved |
| `SCOPE_REQUIRED` | 403 | Valid key, but this scope was not granted |
| `RATE_LIMITED` | 429 | Over 60 a minute |
| `EVENT_NOT_FOUND` | 404 | No such event, or it is not public |
| `TOURNAMENT_NOT_FOUND` | 404 | No such tournament, or it is not public |
| `TEAM_NOT_FOUND` | 404 | No such team |
| `PLAYER_NOT_FOUND` | 404 | No such player |

A `404` on a record that exists but is private is deliberate and is not a bug.
Telling you that a private tournament exists is itself a disclosure.

### A worked call

```bash
curl -s https://api.v-ent.co/api/v1/tournaments/?page_size=2 \
  -H "Authorization: Bearer vent_pk_abc123.s3cr3t"
```

```json
{
  "status": "success",
  "data": {
    "results": [
      {
        "id": 25,
        "slug": "naija-free-fire-weekly-12",
        "title": "Naija Free Fire Weekly #12",
        "game": "Free Fire",
        "format": "Single Elimination",
        "prize_pool": "220000.00",
        "entry_fee": "0.00",
        "starts_at": "2026-08-31T18:00:00Z",
        "url": "https://v-ent.co/tournaments/naija-free-fire-weekly-12"
      }
    ],
    "page": 1,
    "page_size": 2,
    "total": 37,
    "has_more": true
  },
  "message": "Tournaments"
}
```

### Things worth knowing before you build

- **Address records by `slug`, not by `id`.** Every V-ENT URL a person sees uses
  the slug, a renamed record keeps its old addresses working, and the slug is
  what you want in a link back to us.
- **Money is a decimal string, not a float.** `"220000.00"`. Parse it as a
  decimal. A prize pool that has been through a float is a prize pool you will
  eventually display wrong.
- **Times are UTC, ISO 8601, with the `Z`.** Convert for your own readers; do
  not assume Lagos.
- **Cache politely.** Rankings and finished brackets change rarely. A minute of
  caching costs you nothing and keeps you well inside the rate limit.

---

## Part two: sign in with V-ENT

OAuth2 authorization code with PKCE. If you have integrated "Sign in with
Google", this is the same shape and you can reuse that code path.

You never see a password. The code you receive dies in **ten minutes** and can
be spent **once**. The access token you get for it lasts **one hour** and reads
a small profile and nothing else.

### What you get, and what you do not

| Scope | What it reads |
|---|---|
| `identity` | Username, display name, country, avatar |
| `identity:email` | Email address |
| `identity:teams` | The teams this person belongs to |

`identity` is the default and is granted if you ask for nothing. There is **no
scope that reads a wallet, a balance, a transaction, a payout or a KYC
document**, and there will not be one. Do not ask; the answer is written into
the code rather than into a policy.

### Getting set up

SSO is reviewed separately from the data API, and asks for more, because signing
people in means handling their identity:

- registered legal name
- company or registration number
- a privacy policy URL that resolves
- a data protection contact
- your redirect addresses, one per line

Redirect addresses must be `https`, except `localhost` while you are building.
**An address that is not registered is refused**, which is what stops somebody
pointing your client id at their own server and collecting your users.

On approval you receive a `client_id` and a `client_secret`. **The secret is
shown once.**

### The flow

Everything is discoverable from one document, so you should not need to hardcode
any of these addresses:

```
GET https://api.v-ent.co/partners/sso/metadata/
```

```json
{
  "issuer": "https://api.v-ent.co",
  "authorization_endpoint": "https://v-ent.co/partners/authorize",
  "token_endpoint": "https://api.v-ent.co/partners/sso/token/",
  "userinfo_endpoint": "https://api.v-ent.co/partners/sso/userinfo/",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "code_challenge_methods_supported": ["S256"],
  "scopes_supported": ["identity", "identity:email", "identity:teams"],
  "token_endpoint_auth_methods_supported": ["client_secret_post"]
}
```

#### Step 1: make a PKCE pair

```js
const verifier = base64url(crypto.randomBytes(32));          // keep this
const challenge = base64url(sha256(verifier));               // send this
```

Keep the verifier in the user's session. It never leaves your server.

#### Step 2: send them to V-ENT

```
https://v-ent.co/partners/authorize
  ?client_id=vent_sso_5546807a1d3543f6cf6f5026
  &redirect_uri=https://your-site.com/auth/v-ent/callback
  &scope=identity identity:email
  &state=<random, and check it on the way back>
  &code_challenge=<the challenge>
  &code_challenge_method=S256
```

They see who is asking and what for, in their own language, and approve or
refuse. That screen is drawn from `sso_authorize_info`, so it always shows your
real name, website and privacy policy rather than a client id.

#### Step 3: they come back with a code

```
https://your-site.com/auth/v-ent/callback?code=<code>&state=<yours>
```

Check `state` matches what you sent. If it does not, stop: that request did not
start with you.

#### Step 4: trade the code for a token

```bash
curl -s -X POST https://api.v-ent.co/partners/sso/token/ \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<the code>",
    "client_id": "vent_sso_5546807a1d3543f6cf6f5026",
    "redirect_uri": "https://your-site.com/auth/v-ent/callback",
    "code_verifier": "<the verifier you kept>"
  }'
```

```json
{
  "status": "success",
  "data": {
    "access_token": "…",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "identity identity:email"
  },
  "message": "Token issued."
}
```

Send **either** the `code_verifier` (PKCE, and what you should use) **or** the
`client_secret`. If a challenge was sent in step 2, the verifier is what is
checked and the secret is not consulted. Never put a client secret in anything
that runs in a browser.

`redirect_uri` must be identical to the one the code was issued for, character
for character.

#### Step 5: read the profile

```bash
curl -s https://api.v-ent.co/partners/sso/userinfo/ \
  -H "Authorization: Bearer <access token>"
```

```json
{
  "status": "success",
  "data": {
    "sub": "3f9a...",
    "username": "temiplays",
    "display_name": "Temi",
    "country": "NG",
    "avatar": "https://api.v-ent.co/media/profile_pictures/…",
    "email": "temi@example.com"
  },
  "message": "Profile"
}
```

`email` appears only with `identity:email`. `teams` only with `identity:teams`.

**Key the account on `sub`, not on `username`.** A person can change their
username; `sub` is stable for the life of the account, and it is the only field
here that is safe as a primary key on your side.

### SSO error codes

| Code | HTTP | What happened |
|---|---|---|
| `UNKNOWN_CLIENT` | 401 | No such `client_id`, or SSO is not enabled for it |
| `BAD_REDIRECT` | 400 | The redirect address is not registered, or does not match the one the code was issued for |
| `BAD_CODE` | 400 | The code is expired, already spent, or was not issued to this client |
| `BAD_VERIFIER` | 401 | The PKCE verifier does not hash to the challenge |
| `BAD_SECRET` | 401 | Wrong client secret, on a flow with no PKCE challenge |
| `BAD_CHALLENGE_METHOD` | 400 | Only `S256` is accepted. `plain` is not |
| `MISSING_TOKEN` | 401 | No bearer token on `userinfo` |
| `BAD_TOKEN` | 401 | The access token is unknown or has expired |
| `MISSING_REDIRECT` | 400 | No `redirect_uri` was sent |
| `NO_SCOPES` | 400 | No valid scope was requested |
| `NOT_APPROVED` | 403 | SSO has not been approved for this partner yet |

### What a person can do about you

Anybody who has signed in to your site with V-ENT can see the connection on
their V-ENT account and remove it. When they do, existing access tokens stop
working. Handle a `BAD_TOKEN` by sending them through the flow again rather than
by holding a dead session.

---

## Part three: signing in to V-ENT with your account

The mirror of part two: a V-ENT visitor signs in using **your** platform's
account. Written against plain OAuth2 and configured entirely by environment
variables, so it works the day you hand over a client id and secret, with no
code change on our side.

Until a provider is configured, `GET /partners/inbound/providers/` answers with
`configured: false` and the button does not appear anywhere on V-ENT. It fails
closed, not open.

To be listed, send us:

- your authorization and token endpoints
- a userinfo endpoint, and which field is the stable account id
- a client id and secret for V-ENT
- the scopes we should request

We will register `https://api.v-ent.co/partners/inbound/<your slug>/callback/`
as our redirect address.

---

## Part four: confirming your own usernames

An organiser running a tournament on V-ENT can require that every entrant holds
a real account on **your** platform - a Free Fire UID, a launcher name, a member
number. Without this they collect the usernames and read them one at a time.
With it, an entrant types their username and is admitted or turned away in under
a second, and nobody at either end reads anything.

This is the smallest integration in this document. One endpoint on your side.

### What we send

```http
POST https://your-platform.example/verify/
Authorization: Bearer <the secret you gave us>
Content-Type: application/json
User-Agent: V-ENT/1.0 (+https://v-ent.co)

{
  "field": "Free Fire UID",
  "value": "1234567890",
  "asked_at": "2026-08-28T21:14:03.221Z"
}
```

`field` is the label the organiser chose, so you can tell which of your
identifiers is being asked about if you accept more than one. `value` is exactly
what the entrant typed, untrimmed and unvalidated - it is your identifier, so
you are the one who knows what a valid one looks like.

**That is the whole payload.** We do not send the entrant's name, their email,
their V-ENT account id, or which tournament they are entering. A partner
confirming a username does not need to know who is asking about whom, and a
smaller payload is a smaller thing to be asked about under a data request.

### What to answer

```json
{ "verified": true }
```

```json
{ "verified": false, "message": "No account with that UID." }
```

`verified` is required and must be a real boolean. `message` is optional, at
most 300 characters, and is **shown to the entrant as you wrote it** - so write
it for them, not for a log. "No account with that UID" tells somebody what to
do; "ERR_LOOKUP_FAILED" does not.

Answer within **4 seconds**. We give up after that.

### What happens when you cannot answer

Every one of these leaves the entrant's submission waiting for the organiser to
read, exactly as if you were not connected at all:

| What we see | What we do |
|---|---|
| Timeout, connection refused, DNS failure | Falls back to the organiser |
| `5xx` | Falls back to the organiser |
| `401` or `403` | Falls back, and we log that our credential was refused |
| `4xx` | Falls back to the organiser |
| A body that is not JSON | Falls back to the organiser |
| A `200` with no `verified` key | Falls back to the organiser |

That last row is deliberate and worth understanding. A login page or a
maintenance page served with a `200` is the most common way an integration goes
wrong, and it must never read as approval. **We never treat an unrecognised
answer as a yes.**

The consequence for you: an outage on your side does not block anybody's
registration. It quietly turns the automatic check back into the manual one.

### What we never do

- We never retry. One request per submission. An entrant who sends the same
  username again produces one more request, and that is the only way to get one.
- We never call you when the page is merely being viewed. The request happens
  once, when the entrant presses Send.
- We never cache a `false`. Somebody who fixes their account and sends again
  gets a fresh answer.

### To turn it on

Send us:

- the URL to POST to (https only)
- a secret for us to send in `Authorization`, which you can rotate whenever you
  like by sending us a new one

Then organisers can add "a partner confirms the account" to a tournament and
name you. Until you send those two things, the requirement still works - it is
simply read by the organiser instead, which is what it does today.

---

## Support and change policy

- The API is versioned in the path. `v1` will not have fields removed or
  renamed under it. New fields may be added, so parse permissively and ignore
  what you do not recognise.
- Breaking changes arrive as `v2`, and `v1` keeps running.
- Something wrong or missing here: say so. This document is checked into the
  backend repository as `docs/PARTNER-API.md` and is meant to be corrected.

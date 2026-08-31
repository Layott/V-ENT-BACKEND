# Handover, 31 August 2026: profiles, the gallery release, and where a person is

The CEO's second batch of the day. Three reports, and the answer to a question.

---

## 1. Somebody else's profile showed your data (AB1, AB2)

> "I tried the view someone's profile, it opened and then I clicked on activity
> and it took me to my own profile. same for the other sub tabs"

**Two separate faults, both fixed.**

**The address.** `setActiveTab` in `src/app/user-profile/page.js` pushed a
hardcoded `/user-profile?tab=...`. Reading `/u/temi` and pressing any tab threw
the username away and landed you on your own profile. It now keeps the address
it is on. Identical in shape to the organisation manage bug fixed earlier in the
week, and worth remembering as a class: **a tab that rewrites the URL must build
it from the route it is on, never from a literal.**

**The data.** Worse, and invisible. `public_profile` returned no teams, no
tournaments and no pictures, so the page filled those panels from
`/team/my-teams/`, `/tournament/get-organizer-tournaments/` and `/ranking/` -
all scoped to the **signed-in** account. Every profile you opened showed you
your own teams and tournaments under that person's name, and it looked fine.

`public_profile` now carries `teams`, `tournaments`, `gallery` and
`esports_images`, and the page reads those when it is not your own profile. The
`localStorage` fallback was also unconditional: a failed request for somebody
else's profile drew your name and face at their address. It now only falls back
for your own.

One more thing found on the way: `_public_teams` originally read only
`TeamMembers`, and a team's **owner is very often not in its member table**.
demo_organizer owns five teams and the profile reported none. It reads both now.

**Verified:** `vent_auth/tests_profile_gallery.py::PublicProfileTests`, 6 tests.
Walked in Chrome: `/fr/u/demo_temi` → Activité stays at
`/fr/u/demo_temi?tab=activity` and lists Temi's five tournaments, which match
the API for demo_temi and not the six for demo_organizer.

---

## 2. The gallery, and the esports release (AB3, AB4, AB5)

> "under image gallery, should be able to upload images, and there should be
> another type of upload for those who want to upload their Esports pictures,
> let them know that the Esports images will be used publicly and inside events
> or tournaments. that they grant use of it to organizers for those events."

Upload was a toast: `onUpload={() => showToast("Photo upload coming soon")}`.

**The rule the design follows: a licence that is not recorded is not a licence.**
An organiser asked six months later where a photograph came from needs a lookup,
not a memory.

`UserGallery` gained `kind` (personal | esports), `caption`, `released_at` and
`release_terms_version`. `is_released` checks **both** halves, so no caller can
check only one; an image whose `released_at` is null was never released whatever
its `kind` says. `RELEASE_TERMS_VERSION` moves when the wording changes, and
rows keep the version they were released under - consent is to a specific
sentence, not to a policy that can be edited afterwards.

The wording lives in `views_gallery_release.RELEASE_TERMS`, in all three
languages, and is **served to the screen** rather than retyped into it. A notice
that exists in two places drifts, and then nobody can say what was agreed to.

```
GET  /gallery/release-terms/          the sentence, en / fr / pt, plus version
POST /gallery/upload/                 kind + consent; esports without consent is 400
POST /gallery/withdraw-release/       take it back without deleting the picture
GET  /user/<username>/gallery/        released esports always; personal follows privacy
```

On the screen: two kind buttons, and choosing Esports reveals the release with a
checkbox and **disables the file input until it is ticked**. The API refuses it
too - the checkbox is the courtesy, the 400 is the rule.

**Verified:** 8 tests in `GalleryReleaseTests`. Walked in Chrome: picked
Esports, read the French release, ticked, uploaded. Database shows
`demo_yusuf | esports | released 2026-08-31 07:32:44 | version '2026-08-31'`,
the counts went to `Tous 1 / Personnel 0 / Esport 1`, the box reset itself so
the next upload needs its own consent, and `/user/demo_yusuf/gallery/` returns
it to a caller with no cookies at all.

---

## 3. "the IP gets the wrong location, it says ilorin for me" (AB6, AB7, AB8)

**The provider, since it was asked:** nobody external. `vent_auth/geo.py` reads a
**DB-IP City Lite** database file from disk in MaxMind format, through the
`geoip2` library, at `GEOIP_DB_PATH`. It is refreshed monthly by a cron job. No
third-party geolocation API is called and **no user IP leaves the server** -
that keeps signup fast on a high-latency link and keeps a personal data point
in-house. `ipinfo.io` appears in the external-services doc as *planned* and was
never wired up.

**Why Ilorin.** Nigerian mobile data routes through a handful of carrier
gateways, so a Lagos sign-in resolves to Ilorin - for most of a network's
subscribers, not occasionally. An IP places somebody in a **country** reliably
and in a **city** barely at all. No provider fixes this; the fix is to stop
asserting the city.

**What changed.**

- `refresh_daily_location` no longer writes `state` from an address at all.
  The country still fills a blank (it is right, and challenge eligibility is
  gated on it), and never overwrites an answer.
- New `Users.country_is_guess`, set when the country came from an address. It
  is reported by the profile and settings payloads so the screen can say so
  instead of presenting a guess as a fact, and it clears the moment somebody
  chooses.
- **There was no control at all.** `ProfileInfoPanel` printed the location with
  the line "Taken from where you sign in, updated once a day." and offered
  nothing to change. So a wrong guess could not be corrected from anywhere a
  person would look. There is now a country list (the same list a tournament
  restricts by, so the two can be compared) and a plain city field, and
  `edit_profile_info` accepts `state`, which it never did.

**Verified:** 3 tests in `LocationGuessTests` plus two rewritten in
`tests_geo` / `tests_login_alert` that had pinned the old behaviour. Walked in
Chrome: the amber notice showed under Pays, the city field was empty, typed
Lagos, saved. Database: `country Nigeria | state 'Lagos' | guess False`.

---

## State

Backend suite: **1654 tests, OK**. Translations: 4010 keys, 0 missing, en = fr =
pt. Lint: warnings only, all pre-existing `<img>` ones.

Walked on desktop Chrome at 1745 px and on the evotv_test AVD at 411 CSS px.
All eleven AB gates are met. Nothing is left open on this batch.

## Traps worth keeping

- **Two profile editors exist.** `EditUserProfileDetails` is dead code with raw
  English labels; the live one is `edit-profile-panels/ProfileInfoPanel`. Patch
  the wrong one and nothing you did appears. Check what the page actually
  renders before editing a component that looks right.
- A `tx()` call looks a key up by its **English text** and finds whichever key
  was defined first. `tx("Used")` resolved to the config page's `cfg.used`,
  whose French is "Occasion" - second-hand, not "already scanned". Four literal
  `tt()` keys, always.

---

## Found while walking, not by a checker

Three faults that only a real walk surfaced. Worth knowing because none of them
would ever fail a test:

1. **`tx()` resolves by English text.** `tx("Used")` found the config page's
   `cfg.used`, whose French is "Occasion" - second-hand, not "already scanned".
   The ticket filter under a person's own tickets read the wrong word in
   French. Four literal `tt()` keys now. An interpolated key
   (``tt(`ui.club.role.${role}`)``) has the same problem plus being invisible
   to `check-keys.mjs`.

2. **Invite status and scope words printed in English inside a French page**,
   for the same reason: `tx(i.status)` and `tx(sc)` found nothing for "Teams"
   and "Pending". Only visible on the emulator, because that is where somebody
   actually reads the screen.

3. **The whole edit-profile panel was clipped on a phone.** `.pageGrid` is a
   CSS grid, and a grid item defaults to `min-width: auto` - "at least as wide
   as my content" - so the panel sized itself to its widest child and ran to
   590 px on a 412 px screen. The container clips rather than scrolls, so the
   right-hand edge of every field was simply gone, silently, for every field on
   that page and not only the new ones. `.pageGrid > * { min-width: 0 }`.
   `document.scrollWidth` reported 412 the whole time, so an overflow check
   would have passed.

---

## Addendum: ipinfo.io wired (CEO, same day)

> "ship, then wire up ipinfo"

`vent_auth/ipinfo.py`. Consulted **before** the local DB-IP file, and only when
`IPINFO_TOKEN` is set. With no token nothing changes at all: a local file read,
no network call, no user IP leaving the server.

Worth being clear about what it does and does not buy. ipinfo is sharper than
the free DB-IP City Lite build, particularly on the mobile ranges most of
V-ENT's traffic arrives on, so the **country** is right more often. It does not
make a city knowable: a carrier gateway is a real place and it is not where the
subscriber is, so ipinfo answering "Ilorin" for a Lagos phone is ipinfo being
right about the gateway. The rule is unchanged — **a guessed city is offered,
never asserted.** A daily refresh with ipinfo live still fills only the country,
still marks it `country_is_guess`, and still leaves `state` blank. There is a
test that says exactly that.

What the better data buys is a suggestion worth showing. `GET
/settings/location-suggestion/` returns what the sign-in address looks like,
writing nothing, and the profile editor offers it: **"On dirait Lagos. Utiliser"**.
One press fills the field; the person still saves.

Three things the module is careful about, because a third-party call on a
sign-in path is where a platform picks up a stall it never recovers from:

| | |
|---|---|
| Can be switched off | No token, no call. Nothing here is on the critical path for an account to exist |
| Never blocks for long | 2s timeout, and every failure (refused, slow, 429, malformed) is a quiet fallback |
| Asked once per address | `IPLocation` caches for 30 days, including a "we asked and got nothing" row. 50,000 a month is 50,000 **distinct addresses**, not 50,000 sign-ins |

A two-letter country code is turned into the **name** the rest of the platform
stores, because a tournament open to "Nigeria" is checked against this field and
"NG" would match nothing anywhere. An unrecognised code returns None rather than
a two-letter string that looks like a country.

### Verified

- `vent_auth/tests_ipinfo.py`, 16 tests. Full suite **1670, OK**.
- A real HTTP round trip against a local stand-in that answers the way ipinfo
  answers, so the actual `requests` call, timeout, JSON parse and cache write
  all ran: `locate('102.89.34.7') -> ('Nigeria', 'Lagos')`, an unknown address
  cached as nothing, a private address never sent.
- The rule held with ipinfo live: refresh on an MTN address ipinfo called Ilorin
  left `country 'Nigeria' (guess)` and `state ''`.
- The offer walked in Chrome: rendered as "On dirait **Lagos**. Utiliser", one
  press put Lagos in the field, and the offer disappeared. The one thing stubbed
  in that step was the suggestion response itself, using the exact JSON the
  server had produced in the real round trip.

### What is left

**The token.** `IPINFO_TOKEN` is empty everywhere, so today the platform behaves
exactly as it did before. Sign up at ipinfo.io (free, 50k/month), put the token
in the VPS `.env`, restart. Nothing else to do — and nothing breaks if it is
never done. `IPINFO_ENDPOINT` can point the lookup at a mirror or a proxy.

---

## Addendum 2: a visitor is not a member (frontend only)

> "if a new user enters the site for whatever reason, even to like get tickets
> to an event, it always looks like they already have an account. also when a
> new user wants to buy a ticket and they click on get ticket, it goes to the
> tab, but it should scroll the page down to the tickets for them"

**Three of the four shells had no signed-out branch at all.** The desktop
sidebar read `status` only to pick the logo's href; `MobileSidebar` did not read
it; `BottomMenu` had a Logout and nothing else. Somebody arriving from a shared
event link met a wallet with no coins, a profile that was not theirs, a bell
over an inbox that did not exist, and a Logout for a session that did not
either.

Public sections stay for everybody — content is public and it is the action
that is gated. What goes is what belongs to a person: Home (a member's
dashboard, which redirects a visitor to sign in anyway), Profile, Wallet,
Settings, the notification bell, Logout. Their place is taken by **Log in** and
**Create an account**. Inside the hamburger each group keeps its public entry
and drops the personal ones, so a stranger expanding Events sees "All events"
and neither "My events" nor "My tickets".

Every branch reads `status`, never `data`. `data` alone cannot tell "signed
out" from "still asking", and treating the second as the first is what makes a
shell flash from a member's to a visitor's.

**Get tickets** sat in the hero and only switched a tab several screens further
down, so pressing it looked inert. It scrolls now.

### Two things only the walk could find

1. **`behavior: 'smooth'` is silently swallowed on the event page.** Measured on
   the same element in the same moment: `'auto'` scrolls to the maximum,
   `'smooth'` leaves `scrollY` at 0. Shipping the smooth version would have
   shipped a button that still does nothing.
2. **One `scrollIntoView` is not enough.** The ticket panel fetches its tiers,
   so at the moment the tab changes the page is barely taller than the window
   and there is nothing to scroll. The intent is held until the strip reaches
   the top or the page admits it cannot scroll further, and dropped after two
   seconds so a slow request cannot move the page under somebody reading.

Walked signed out at 1745px and on the emulator at 411 CSS px. Gates AC1 to AC5.

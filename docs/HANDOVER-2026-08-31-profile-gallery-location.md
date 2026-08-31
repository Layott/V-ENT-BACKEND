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

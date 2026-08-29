# Handover, 29 August 2026 (afternoon): the mobile pass

Seven PRs, all merged and deployed. Everything below started as a CEO report
from a phone, and every one of them was invisible on a desktop viewport.

---

## The admin console would not load (BE#83, FE#91)

**nginx, not the app.** `^/auth/(login|signup|forgot-password|admin)` routed the
whole console into a brute-force zone of **5 requests a minute**. The dashboard
alone fetches `me`, `metrics`, `charts` and `recent-activity` on load, so it
tripped the limiter within seconds and nginx refused everything after it.

It reported itself as a network failure because **an nginx limiter rejection
carries no CORS headers**, so the browser cannot show the response to the page.
That is also why none of it appeared in the gunicorn log: the requests never
reached Django.

> **If a request is missing from the app log entirely, look at nginx before
> looking at the code.**

The console has its own zone now (300r/m), the tight zone keeps login/signup/
forgot-password to itself, and `limit_req_status 429` so a throttle is not
disguised as a 503. Changed on the server AND in `deploy/nginx-vent.conf`, or
the next deploy would have put it back.

Two app faults made it worse: `AdminToastProvider` handed out a fresh context
object every render, so the settings page refetched every 12 seconds forever (a
failed fetch pushed a toast, which re-rendered the provider, which refetched);
and the dashboard's `if (status === 'success')` branches meant a refusal set
nothing and said nothing, leaving every tile reading "-".

## Drafts (BE#84, FE#93)

"Each time I save as draft, it creates a new draft." The wizard always POSTed to
create; it now PUTs to edit when opened with `?draft_id=`. That was also why the
logo and banner looked lost - the second save was a new row carrying none of the
first one's uploads.

"Registration start/end, connected event, game edition don't get saved." Three
more causes: the registration window had **no columns at all** (the wizard has
sent `reg_start_date_and_time` since it was written and the server read it and
dropped it); `series_id` and `event` were collected on step 1 and **never
sent**; and the draft-to-form mapper dropped `options`, the league block and the
image URLs on the way back in.

**The trap inside the fix:** `edit_tournament` saves with `update_fields`. A
column set outside that loop must be appended by hand or it is silently not
written. `tests_draft_round_trip.py` covers exactly that.

## Mobile navigation (FE#93, FE#94)

The bottom bar was six items in an `overflow-x: auto` row with `width: auto`
items, so "Wallets" was cut to "Wall" with its icon sliced and the profile on
top of it, and nothing said the row scrolled.

`/wager`, `/partners` and `/admin` existed on the desktop sidebar and on **no**
mobile surface - unreachable from a phone by any route.

And the drawer could not be scrolled to its end: `top: 55px` with
`height: 100vh` makes it 55px taller than its space, with no overflow rule, so
Shop, Settings and Logout were clipped and unreachable.

## Country (FE#92)

Free text on BOTH ends, and the two are compared: a tournament's
`restrict_country` is matched against the entrant's `user.country`. An organiser
typing "Naija" turned away every Nigerian. One shared list now, Africa first.
The profile placeholder read "Lagos" - a city, in a country field.

## Deploys no longer drop requests (BE#84)

gunicorn had no `ExecReload`, so `systemctl restart` unbound the socket and
nginx logged `connect() to api.sock failed` for seconds. The CEO's draft save
died in exactly such a window at 10:02. `ExecReload=/bin/kill -s HUP $MAINPID`,
and deploy.sh reloads. **60 of 60 requests returned 200 across a reload.**

## A deploy I broke, and what it teaches (BE#85)

Staging `git add models.py` carried an unrelated `ScheduledReminder` model to
main while its migration stayed untracked, and the deploy died on a broken
migration graph. Nothing was damaged - `migrate` refuses before touching
anything - but stage by CHANGE, not by file, and always check `git status` for
an untracked migration beside a model edit.

---

## The rule the CEO set twice

**Mobile is tested on the Android emulator (`evotv_test`), not a resized
desktop viewport.** Written into `V-ENT/CLAUDE.md` with the exact commands. A
390px iframe shares the desktop's network, CPU and browser build; every fault
above was invisible in one.

The drawer is the clearest case: a headless 412px viewport renders it
identically and reports no overflow, because the PAGE does not scroll - only the
drawer's own content is unreachable.

## Tooling added

- `scripts/parity.js` - diffs the desktop and mobile audit walks route by route
  and names any control present on one and missing on the other.
- `audit-walk.js` takes `SESSION_JWT` and sets a session cookie rather than
  typing a password into the login form.

## Still open

- Scheduled reminders: the model, migration, `deliver()` refactor and the
  `send_due_reminders` command are in; the endpoints to CREATE a schedule are
  not, so nothing can be scheduled yet. A cron entry is also not installed.
- The event product shop, the last item from `V-ENT FEATURES DEEP.pdf`.

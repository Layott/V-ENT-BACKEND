# Handover, 1 September 2026 (evening): mail, the console bugs, and the studio

Follows `HANDOVER-2026-09-01-deploy-limits-shortlinks.md`. Everything below is
merged to `main` and deployed unless it says otherwise.

---

## 1. Mail moved to Brevo, with a fallback

Django never talks to the mail provider. `EMAIL_HOST=127.0.0.1:25`, so it hands
everything to **Postfix on the VPS**, which relays onward. Changing provider is
a Postfix change with **no app deploy**. Anyone debugging "email is broken"
should read `journalctl -t postfix/smtp`, not the Django log.

| | |
|---|---|
| Primary | Brevo, `smtp-relay.brevo.com:587`, login `b7769b001@smtp-brevo.com`, 300/day |
| Fallback | Resend, `smtp.resend.com:587` - **unreachable-host only** |
| Was | Resend, 100/day |
| Switch | `/srv/vent/deploy/mail-relay.sh <brevo|resend|gmail|workspace> [--test addr]` |

**`smtp_fallback_relay` does not do what people assume.** It fires when the
relay is *unreachable*. A quota rejection or an auth failure comes from a
healthy server answering politely, and does not trigger it. Those stay manual.

`mail-relay.sh` sets route and sender **together** and refuses a provider with
no credentials on file. Workspace is the exception: it authenticates the server
by IP (allowlisted in the Google admin console), so it needs no stored secret.

### DNS, and why it decides everything

`_dmarc.v-ent.co` is `p=quarantine` with relaxed alignment, so DMARC needs SPF
or DKIM to pass **and align**.

- DKIM: `brevo1`, `resend` and `google` selectors all live and verified.
- SPF: **one** record, `v=spf1 include:_spf.google.com include:spf.brevo.com
  include:_spf.resend.com ~all`. Only one `v=spf1` per name is legal; two is a
  permerror and SPF fails for every sender at once.
- MX: `smtp.google.com` on the apex (Workspace), `send.v-ent.co` still points at
  SES feedback and was deliberately kept.

### A mistake worth not repeating

Staging a Postfix `smtp_generic_maps` rewrite while leaving `relayhost` alone
felt safe and was not. The rewrite is live immediately, so Resend started
receiving mail with a `gmail.com` From and answered `550 The gmail.com domain is
not verified`. **Two real emails were lost**, one to `iwudavinchi264@gmail.com`,
which has no account behind it. A 550 is permanent.

The sender is as load-bearing as the route. Full write-up in `tasks/lessons.md`.

**Also:** the reported "Resend has hit its limit" was a dashboard warning, not a
refusal. 109 sent that day against a 100/day tier and still delivering. Diagnose
from the mail log, not the symptom as described.

### Still open

`no-reply@v-ent.co` has no mailbox, and Google now holds the MX, so replies to
platform mail are actively rejected. Add it as an alias on info@, and set
`Reply-To: info@v-ent.co` on outgoing mail. Also: rotate the Brevo SMTP key and
revoke the unused Gmail app password and Brevo API key, all three exposed.

---

## 2. Four bugs found by the CEO walking the product

**Ticket types showed no date.** A type with no `day` printed nothing at all.
Fixed twice: first to say "All days", then - on the CEO's second look - to name
the actual span, "Sep 4, 2026 - Sep 6, 2026 - All days". "All days" was hiding
exactly the dates somebody is comparing.

**Nine buttons on the event console had no CSS.** `.primaryBtn` was never
defined in `manage-event.module.css`, so `styles.primaryBtn` was `undefined` and
React rendered `class="undefined"`: bare text on a dark surface. Every Save
among them, which is why ticket dates "wouldn't save".

`scripts/check-css-classes.mjs` now catches this class of fault, which neither
the build nor the linter sees. **224 undefined references across 66 files** are
still open. Two worth attention: `Payment.js` is missing `confirmButton` on the
wallet payment path, and `ComingSoonModule.js` is missing 27 classes.

**A league refused an odd number of sides.** `aggregate_2v2` carried
`even_only`, a knockout concern, and the Rivalry Series is five nations. Now an
invariant: any format with `even_only` must advance by knockout.

**Setting a ticket type's day 500'd after saving.** `update_tier` assigned the
date as a string; Django coerced it into the database, the in-memory instance
kept the string, and `serialize_tier` called `.isoformat()` on a `str`. The
write landed, the caller was told "Failed", they retried, and the retry hit a
different type. **RIVALRY SERIES finished with two ticket types on the same
day**, since repaired. A 500 after a successful write is worse than a 500
instead of one, because it invites the retry that does the damage.

---

## 3. The production studio (spine shipped)

`vent_tournament/views_studio.py`, models in `models.py`, migration `0031`.

Three surfaces, kept apart: the operator console is signed in and
tournament-scoped; the element page and its feed are public by **session
token**, because a browser source has no cookie, no session and no header.

Four decisions, each load-bearing:

1. **State lives on the server.** The element page is a dumb renderer asking the
   feed what to show. OBS restarting mid-broadcast, a swapped machine or a
   second operator all recover exactly. A test swaps the client entirely.
2. **One request feeds every element.** Six polling separately over a venue
   hotspot is six chances the failed one is the one on air. Plus `version`, so
   an overlay can ask "has anything changed" without diffing.
3. **The studio does no arithmetic.** Standings come from `overlay_feed`. A
   second implementation would eventually disagree with the players' page.
4. **Starting a broadcast ends the previous one.** Two live sessions means two
   sets of URLs and no way to know which screen is which.

Eight element kinds ship: scorebar, standings, lower third, player card,
bracket, ticker, intro, outro. Adding to that list is how the studio grows.

`may_use_studio()` is where the subscription check goes. Ownership today,
because gating on a plan nobody can buy would refuse everybody.

**Not built yet:** the element pages themselves (Next routes at
`/studio/<token>/<kind>`), and the operator console. That is the next work.

---

## 4. Open, in priority order

| | |
|---|---|
| **Rivalry Series tournament does not exist** | Nothing in the studio can be rehearsed until it does. Five nations, `aggregate_2v2`. Event is 4-6 September. CEO is creating it |
| Element pages + operator console | The visible half of the studio |
| Mute / Block / Report are **fake** | Each fires a toast and makes no request. A user who blocks a harasser is told it worked. CEO asked for all three built fully |
| Phone checkout fields | Must require a country code, default +234 |
| Pricing | `tasks/pricing-proposal.md`. Blocking answer needed: the ticketing fee per tier |
| 224 undefined CSS classes | `node scripts/check-css-classes.mjs` |

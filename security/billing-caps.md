# Billing caps and alerts (owner rule R60)

One row per service that can cost money. Cap = the hard spending limit set at
the provider; where the provider has none, write "alert only" and fill the
alert column. Re-verify every 90 days: open the URL, read the number, update
the date. `check-security --rule R60` reads this file and fails on a detected
service with no row, or a row older than 90 days.

Production is one InterServer VPS and everything runs on it (CEO, 16 August
2026: "no external apps"). So the list is short, and most of it is fixed
price, which is the best kind of cap.

| Service | Cap | Alert at | Where set (URL) | Verified on | By |
|---|---|---|---|---|---|
| paystack | alert only: nothing is billed, the fee (1.5% + 100 naira, capped at 2,000 naira per transaction) is taken out of each settlement before it reaches the bank | every transaction and every settlement mails the account owner (Paystack > Settings > Notifications); the platform's own Money tab and admin finance page read the ledger, so a settlement that does not add up shows there | https://dashboard.paystack.com/#/settings/notifications | 2026-09-17 | Claude, from the code (`PAYSTACK_SECRET_KEY` read in vent_auth/paystack.py; the key is unset on production as of 17 September). The notification switches on the dashboard are the CEO's to confirm; not read by me |
| interserver (the VPS) | fixed monthly price; a VPS cannot run up a bill, it can only stop | none needed: the invoice is the cap | https://my.interserver.net (the CEO's account) | 2026-09-17 | Claude, from tasks/vps/INTERSERVER-SETUP.md; the account itself is the CEO's |
| gmail relay (Postfix relaying to Google Workspace SMTP as info@v-ent.co) | fixed: one Workspace seat; Google's sending limit (2,000 mails a day per account) is a ceiling, not a bill | Postfix logs a deferral when Google refuses (`/var/log/mail.log`); the auth endpoints that send mail are rate limited at 5 a minute per address (vent_auth/throttle.py) so a loop cannot reach the ceiling | https://admin.google.com (the Workspace seat) | 2026-09-17 | Claude, from deploy/mail-relay.sh and the throttle; the seat is the CEO's |
| ipinfo.io | free tier, 50,000 lookups a month, and the token is EMPTY everywhere today, so nothing is called and nothing can be billed; answers are cached per address for 30 days when it is on | switch it on only with the free token; the provider mails at 80% of the tier | https://ipinfo.io/account/usage | 2026-09-17 | Claude, from vent_auth/ipinfo.py and settings.py (`IPINFO_TOKEN`) |
| open.er-api.com (exchange rates) | free, no key, no account; display only, never what anybody is charged | none: there is nothing to bill | n/a | 2026-09-17 | Claude, from vent_auth/rates.py |

Retired, so no row: Railway (`Procfile`, `runtime.txt`, deleted 17 September; the
platform moved to the VPS on 17 August 2026), Vercel (projects deleted, see
the `reference_vercel_deploy` memory), AWS (never provisioned; the External
Services table in CLAUDE.md is the pre-VPS plan).

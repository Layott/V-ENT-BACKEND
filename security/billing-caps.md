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
| flutterwave | alert only: nothing is billed; Flutterwave takes its fee from each collection before settlement, at the rate on the account's pricing page (not read by me) | the account owner is mailed on every transaction and settlement (Flutterwave > Settings > Notifications); the webhook at /auth/flutterwave/webhook/ reports each payment to the platform, and the Money tab and admin finance page read the ledger | https://app.flutterwave.com/dashboard/settings/notifications | 2026-09-29 | Claude, from the code (`FLW_SECRET_KEY` read in vent_auth/flutterwave.py; test keys only, 29 September). The fee rate and the notification switches are the CEO's to confirm on the dashboard; not read by me |
| interserver (the VPS) | fixed monthly price; a VPS cannot run up a bill, it can only stop | none needed: the invoice is the cap | https://my.interserver.net (the CEO's account) | 2026-09-17 | Claude, from tasks/vps/INTERSERVER-SETUP.md; the account itself is the CEO's |
| gmail relay (Postfix relaying to Google Workspace SMTP as info@v-ent.co) | fixed: one Workspace seat; Google's sending limit (2,000 mails a day per account) is a ceiling, not a bill | Postfix logs a deferral when Google refuses (`/var/log/mail.log`); the auth endpoints that send mail are rate limited at 5 a minute per address (vent_auth/throttle.py) so a loop cannot reach the ceiling | https://admin.google.com (the Workspace seat) | 2026-09-17 | Claude, from deploy/mail-relay.sh and the throttle; the seat is the CEO's |
| ipinfo.io | free tier, 50,000 lookups a month, and the token is EMPTY everywhere today, so nothing is called and nothing can be billed; answers are cached per address for 30 days when it is on | switch it on only with the free token; the provider mails at 80% of the tier | https://ipinfo.io/account/usage | 2026-09-17 | Claude, from vent_auth/ipinfo.py and settings.py (`IPINFO_TOKEN`) |
| gemini (Google AI, reads battle royale result screenshots) | nothing billed to V-ENT: since 25 October 2026 there is no platform key (`GEMINI_API_KEY` removed, inbox 551); each organiser connects their OWN key in Settings > Connected keys and is billed by Google for their own reads. Inside the platform every person is capped at 40 reads a day (admin settings, `ocr.reads_per_person_per_day`, checked before the call) | the organiser sets a budget alert on their own Google Cloud billing account; the Connected keys panel says they pay Google for what they use | https://console.cloud.google.com/billing/budgets (the organiser's account) | 2026-10-25 | Claude, from vent_auth/ai_providers.py, vent_auth/connected_keys.py and vent_tournament/br_ocr.py (`daily_cap`) |
| anthropic (Claude; also openai, openrouter, mistral, groq, xai: the other AI services an organiser may connect, inbox 555) | nothing billed to V-ENT: V-ENT holds no key for any of them; each organiser connects their own and is billed by that provider. The same 40 reads a day per person applies before any call, whichever provider reads | the organiser sets a spend limit or alert at their own provider (Anthropic Console limits, OpenAI usage limits, OpenRouter credit balance); the Connected keys panel links each provider's key page | https://console.anthropic.com/settings/limits (the organiser's account) | 2026-10-25 | Claude, from vent_auth/ai_providers.py (`anthropic` SDK, requests for the rest) and br_ocr `daily_cap` |
| open.er-api.com (exchange rates) | free, no key, no account; display only, never what anybody is charged | none: there is nothing to bill | n/a | 2026-09-17 | Claude, from vent_auth/rates.py |

Retired, so no row: Railway (`Procfile`, `runtime.txt`, deleted 17 September; the
platform moved to the VPS on 17 August 2026), Vercel (projects deleted, see
the `reference_vercel_deploy` memory), AWS (never provisioned; the External
Services table in CLAUDE.md is the pre-VPS plan).

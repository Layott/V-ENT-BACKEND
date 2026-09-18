# Handover, 17 September 2026: the pause's leftovers, and security rules R55 to R61

Inbox rows 267 and 268. Gates: `V-ENT/gates/40-pause-leftovers-and-security.md`.
Branches: `V-ENT-BACKEND` and `V-ENT-FRONTEND`, both `fix/event-walk-12-sept`,
so this lands on BE #180 and FE #190, still open and unmerged. Merge waits for
the CEO.

CEO, 17 September: "continue the flow you were on during the pause, finish up
all that's left also." And in the global rules the same morning: "i want rules
built for these and they should apply to each project also, i also want
checkers built for them so they get enforced for each project ... each project
can then implement it in their way."

## READ THIS FIRST: what deploying now changes, on top of the 14 September notes

Everything in `HANDOVER-2026-09-12-event-walk.md` section "WHERE THINGS STAND"
still applies (migrations 0046 to 0048, 0082, 0002, 0052; fees; prizes from
the pool; `PAYSTACK_SECRET_KEY`). Added today:

1. **Migration `vent_auth 0083_pin_and_code_lockout`**: six nullable or
   defaulted columns (`pin_failures`, `pin_locked_until` on the three wallets;
   `code_failures`, `code_locked_until` on `UserTOTP`). Additive.
2. **`GOOGLE_CLIENT_ID` must be in the backend `.env` on the box before the
   backend restarts.** It is in `/srv/vent/frontend/.env.production` and NOT in
   `/srv/vent/backend/.env` (read over ssh today). `/auth/social-auth/` now
   verifies Google's id_token against it and refuses with 503
   `SOCIAL_AUTH_NOT_CONFIGURED` when it is unset: a sign-in door with no key is
   a door anybody can walk through, so it fails closed. `deploy/verify-live.sh`
   fails the deploy if the line is missing. One command on the box:
   `grep '^GOOGLE_CLIENT_ID=' /srv/vent/frontend/.env.production >> /srv/vent/backend/.env`
   then `sudo systemctl reload vent-api`.
3. **Behaviour people will notice:** five wrong wallet PINs lock the wallet
   for fifteen minutes (`PIN_LOCKED`, every door: send, withdraw, tickets,
   stalls, pitches, tournament entries, the PIN change); ten wrong two-factor
   codes lock the factor for fifteen minutes (`TWO_FACTOR_LOCKED`); the auth
   endpoints answer 429 `TOO_MANY_ATTEMPTS` past their per-address allowance
   (login 20/min, signup 10, the mail-sending ones 5, the code ones 20); a
   password typed against a Google-made account is refused instead of a 500.
4. **The nginx block in `deploy/nginx-vent.conf` is wider than the one on the
   box** and is NOT applied there. The live `auth` zone (5r/m, burst 10) covers
   `^/auth/(login|signup|forgot-password)`; the repo's now also names
   resend-link, resend-forgot-password-token, send-code, verify-new-email,
   social-auth, 2fa/confirm, 2fa/disable and wallet/pin/verify. Apply with
   `sudo cp deploy/nginx-vent.conf /etc/nginx/sites-available/vent && sudo nginx -t && sudo systemctl reload nginx`.
   Not applied by me: it is production nginx and the handlers now carry their
   own limiter, so the box is slower to refuse, not open.
5. `Procfile` and `runtime.txt` are deleted (Railway, retired 17 August).

## 1. The leftovers (row 267)

| Named at the pause | Done |
|---|---|
| Register review step drew `$` and "40 vent coins" | `Review.js` reads `/tournament/<ref>/entry-quote/` like the payment step: Free, `N VC`, or `Entry 20 VC + service fee (5% + 100 naira) 1 VC`. Also found there and fixed: an invented 20-day date range (now `formatDateRange` of the real start and end), a fixed "Single Elimination" (now `formatLabel` of `bracket_type`), a Lagos address and "Counter Strike" as fallbacks (now Online, and the record's game), and the dead key `ui.vent.coins.8e4f` in all three dictionaries |
| The roster step (found on the walk) | `edit-team/EditTeam.js` drew four copies of "Nathan Drake @frostbite" from a mock array over a two-person team, with Remove and Restore whose result the server never read (`join_tournament` ignores `roster`). Deleted. The review step draws the team's real members from `GET /team/<slug>/roster/`, "Edit Roster" is "Change team" |
| Scanner told a steward to edit `?gate=` in the address | A Gate field on the scanner writes `?gate=` with `router.replace` (a link and a reload keep it); copy "Name this gate so a duplicate can say where it was first used." in en/fr/pt; 44px on the phone |
| The 12-card Manage hub | Deleted, not restyled: `EventConsoleTabs` already lists every destination (the 2 September comment said it replaced the grid; the grid survived beside it). The one thing only the hub reached, the door list, is a header action beside the scanner. 75 dictionary keys removed |
| `useCallback` warnings | 9 `react-hooks/exhaustive-deps` warnings across the events tree and the tournament view: `tt` named where it is used (it is a `useCallback` on the language, so no loop), `SETTLED` hoisted to module scope, `found` memoised, the bracket and participants fetches made callbacks the effect and Retry share. Lint reads 0 |
| `link embeds` debt with the server up | `check-embeds` fetches the preview image from the server under test (the tags name production, whose `/api/og` will not proxy a 127.0.0.1 media host); tries 3001 then 3005; the summary line is `0 incomplete link preview(s) across 10 page(s)`. The ledger's 3128 was 127 + 3001 read out of the URL in "NOTHING WAS CHECKED ... http://127.0.0.1:3001", so `check-all`'s `_count` strips addresses before counting |
| Seeded og:image | Not the seed: the same local-vs-production mismatch above. 10 of 10 pages complete |

Also on the way: the review modal's back, close and toggle buttons and the
edit page's ghost buttons measured 28 to 39px on the phone and are 44 now.

## 2. Security rules R55 to R61 (row 268)

Onboarded both repos: `security-rules.json` at each root, `security/`
registers, baselines at 0/0 on every rule, `--ledger` rows in `check-all`
(blocking), the mapping table "Owner rules 55 to 61 in V-ENT" in
`V-ENT/CLAUDE.md`. Runs: backend 0 high 0 medium with 8 allowed rows each
carrying a reason read by hand; frontend 0/0 with 4.

**The first run's HIGHs, and what they turned out to be:**

| Reported | Truth | Fix |
|---|---|---|
| R59 login and two-factor with no limiter | nginx limits login/signup/forgot-password at 5r/m on the box (verified with `nginx -T`), which the checker cannot see; nothing limited resend-link, send-code, 2FA confirm/disable, social-auth | `vent_auth/throttle.py` `@limited(name, per_minute)` on 13 endpoints, off under the test runner (`AUTH_THROTTLE_ENABLED`), edge rule declared with today's date, nginx block widened in the repo |
| R60 no billing register | Paystack is real; "heroku" was the Railway `Procfile` | `security/billing-caps.md` with Paystack, the InterServer box, the Gmail relay, ipinfo (off), open.er-api; Procfile deleted |
| R61 string-built SQL in `delete_user` | table names from a fixed list, the id bound | `# sql-safe:` on both lines |

**What onboarding found that no checker reported, all fixed today:**

- **`/auth/social-auth/` signed anybody in on an email and a Google id typed
  into the body.** No token was verified. A Google `sub` is in every id token
  the account ever handed any app, so knowing somebody's email and sub was
  knowing their password; and a stranger could pre-register any email. It now
  takes NextAuth's `account.id_token`, verifies it with
  `google.oauth2.id_token.verify_oauth2_token` against `GOOGLE_CLIENT_ID`
  (signature, issuer, audience, expiry, `email_verified`), and takes the
  identity from the token. A password account with the same verified email is
  linked rather than crashed. `tests_social_auth` (10).
- **A wallet PIN could be walked from one address in under an hour.** Ten
  doors compared it by hand with `check_password`; none counted; the wallet
  endpoints sit in nginx's `api` zone at 120r/m. `wallets.check_pin` is the
  only comparison now, five wrong lock for fifteen minutes on the row (not a
  cache: a restart must not hand out a fresh five), `INVALID_PIN` says
  `tries_left`, `PIN_LOCKED` says `minutes`, `WalletError` carries `params`
  and `body()`. The tournament entry's `WRONG_PIN` is `INVALID_PIN` like every
  other door. `tests_pin_lockout` (13).
- **A two-factor code had a replay guard and no try counter.**
  `login_2fa.spend_code` counts on `UserTOTP`; ten wrong lock for fifteen
  minutes; sign-in, the settings page and the withdrawal say
  `TWO_FACTOR_LOCKED`. `tests_totp_lockout` (6).
- **`/setting/2fa/*` was a second implementation of `/auth/2fa/*`** with its
  own code check and no frontend caller. Retired; `tests_settings` moved to
  the door that has a caller.
- **Signing in with the email of any Google-made account was a 500**: the auth
  backend called `check_password` on a null hash. Found because the IDOR cases
  signed in as a fixture with no password. `backends.py` refuses instead.
- **`--idor`**: 13 cases (event money, settle, door list, promos, tiers;
  tournament money, settle, staff, registrations, edit; a stallholder's
  orders; a ticket's transfers and transfer), all refused 403 as
  `walk_buyer_ada`. **`--live`**: `/auth/login/` answers 429.

**Owner's confirmations still needed:** the Paystack dashboard notification
switches (the register row says so: I read the code, not the dashboard); the
nginx widening on the box.

## 3. Proof

- Backend: `manage.py test vent_auth vent_event vent_tournament vent_team --parallel 4` = `Ran 3513 tests, OK (skipped=1)`.
- Frontend: check-design 0 new, check-signed-out 0, check-tap-targets 0,
  check-live-updates 0/0/0, check-spinner-forever 0, check-datetime 0 new,
  check-css-classes 0, check-dangling-refs 0, check-keys 0 missing,
  dict-parity en=fr=pt, check-embeds 0 of 10, lint 0 hooks warnings.
- Chrome (desktop) and the Android emulator (412 CSS px): the review step on
  `walk-cup-team-fee` (a fixture made today: team tournament, 20 VC, fee on the
  player, team `Walk Bisi FC` of walk_vendor_bisi and walk_buyer_ada) reads
  `21 VC`, the fee line, `Sep 24, 2026 - Sep 25, 2026`, `Online`, the two
  real members, no `$`, no Nathan; the scanner: typed North (desktop) and East
  (phone), address `gate=…`, header "Scanning at …", reload keeps it, field
  44px, 0px overflow; the edit page: no cards, three header actions at 44px,
  Door list opens `/events/walk-con-ea5e/attendees`, 0px overflow.
- Screenshots: `scratchpad/phone-A2-review.png`, `phone-B2-scanner.png`,
  `phone-C3-edit.png` (this session's scratchpad).

## 4. Left open, named

- The nginx block on the box (section 0, item 4).
- `GOOGLE_CLIENT_ID` into the backend `.env` on the box before the next
  backend restart (item 2); verify-live refuses without it.
- The register flow's Payment step still lists `err?.status === 403` as a PIN
  refusal in a comment; the code reads by code now.
- Everything the 14 September handover left to the CEO (premium price, rows
  26/28, overlay files, the ten names, row 139) is unchanged.

## 5. How to run it again

```
cd V-ENT-BACKEND
DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe manage.py runserver 8000 --noreload
node ~/.claude/skills/security-rules/scripts/check-security.mjs --idor http://127.0.0.1:8000 --verbose
node ~/.claude/skills/security-rules/scripts/check-security.mjs --live http://127.0.0.1:8000 --verbose   # AFTER --idor
cd ../V-ENT-FRONTEND && pnpm dev -p 3005
node scripts/check-embeds.mjs
python ../tools/gate-run.py ../gates/40-pause-leftovers-and-security.md
```

Phone: `emulator -avd evotv_test -no-snapshot-load`, `adb reverse` 3005 and
8000, `adb forward tcp:9222 localabstract:chrome_devtools_remote`, then the
scratchpad's `phone_walk.py <token> <username> ABC` (sign-in through
`/auth/external`, DevTools drives the tab). The `wm size 1080x2400` override
on this AVD stays at 1080x1920, which is 412 CSS px at density 420.

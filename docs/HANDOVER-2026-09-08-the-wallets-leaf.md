# Handover, 8 September 2026: the wallets leaf

Inbox rows 190, 192, 199, 201, 203 and 217. Gates in `V-ENT/gates/20-wallets.md`,
which carries the evidence line by line. This file is the short version plus what
is NOT done.

Nothing is committed. Both repos are on `feat/funnel-ledger-transfer-rankings`.

---

## The two things that mattered

Both were found by WALKING the endpoint, not by reading it. The code review that
preceded them found four other faults and missed these two entirely, which is
the argument for the walk.

### 1. A send with no PIN moved the money

`wallets.transfer(..., pin=None)` means "do not check the PIN". That is correct
for the platform's own movements: a prize, a settlement, a refund. The shared
wallet endpoint passed `request.data.get('pin')` straight in, and on a payload
with no `pin` key that expression IS `None`. So anybody who could reach a team
or organisation wallet could empty it without knowing the PIN.

Proven by posting `{"action":"send","to_kind":"user","to":"demo_chidi",
"amount":5}` with the field omitted: `success`, and the balance went 90 to 85.

Fixed in `vent_auth/views_wallets_shared.py`: the send branch refuses an absent
or empty PIN with `PIN_REQUIRED` before anything moves. Proven live afterwards:
the same request answers `PIN_REQUIRED` and the balance does not move.

The person's own wallet never had the hole. `send_funds` has always required the
field. Shared wallets were the second surface and the guard was not carried
across.

### 2. A wrong PIN burned the authenticator code

The second factor was checked BEFORE the PIN, and `spend_code` marks a code used
so it cannot be replayed. So mistyping four digits consumed a good code and the
retry was refused with "that code has been used" - the wrong reason and the
wrong outcome, since the person then waited out the 30 second window.

Fixed by checking the PIN first. `transfer` still re-checks it on the way
through, deliberately: removing that would put every other caller at the mercy
of remembering, which is fault 1 again.

### Held by tests, because both are second occurrences

`PinIsRequiredEverywhereTests` (5 tests) covers the three shapes an absent field
really takes - no key, empty string, JSON null - on BOTH shared endpoints,
asserts the right PIN still works so the guard is not merely an outage, and
asserts the person's own wallet refuses it too so the surfaces stay in step.
`WrongPinDoesNotBurnTheCodeTests` (2 tests) covers the ordering and checks the
replay guard survived it.

---

## What else this leaf did

- `SharedWalletPermissionTests` (11 tests): the spend ladder rung by rung.
  Signed out refused on both wallets, a stranger cannot read, an ordinary member
  reads and cannot spend, the captain can, an org manager with only the TEAMS
  scope cannot, one with FINANCE can, and setting the PIN counts as spending.
- The org finance scope: a manager could spend an organisation's money on the
  strength of the teams scope, so somebody handed the roster was handed the
  money. Now `SCOPE_FINANCE`, with `ui.scope.finance.7c31` authored by hand in
  en, fr and pt.
- `walletHelpers.js` kept its own `formatDate`/`formatDateTime`, a second copy
  reading the reader's language but not their zone and not the date ORDER they
  chose in settings. So somebody who set DD/MM/YYYY got it everywhere except
  their own statement. Both now re-export from `@/lib/datetime`.

## Proof actually run

| | |
|---|---|
| Full backend suite | `Ran 3587 tests in 606.214s`, `OK (skipped=1)` |
| Wallet spec + signed out | `Ran 73 tests in 106.815s`, `OK` |
| Frontend catchers | datetime, signed-out, slugs, control-bytes, dangling-refs, css-classes, avatars, dict-parity, check-keys: all clean |
| Chrome | org console wallet tab, signed in, on an isolated stack. Balance matched the API, the send form rendered, and PRESSING Send moved 1 VC (80 to 79) with the statement line to match |

Row 217 was not real. The suite was never red on
`vent_auth_withdrawalrequest.hold_id`: migration 0078 already carried `hold` and
`makemigrations --check` says "No changes detected". That report was written
before 0078 existed.

---

## NOT done, and why

1. **`pnpm build` does not compile.** Not this leaf's code:

       Cannot find module '...next\dist\compiled\jest-worker\processChild.js'

   and the dev server dies on a different file in the same package:

       Cannot find module '...next@14.2.35_...\node_modules\@swc\helpers\package.json'

   This is the known pnpm store corruption already recorded in memory and in
   `next.config.mjs`'s own comment. It is ALSO why :3005 answers 500 from
   `/api/auth/session` while :3001, :3002 and :3007 answer 200. The fix is a
   reinstall, which rewrites `node_modules` under every other agent running a
   dev server off it, so this leaf did not run one. **That is a call for the
   team lead, and it currently blocks all frontend build work.**

2. **The team wallet screen was not pressed in Chrome.** The org one was, and it
   is the SAME component (`SharedWallet`) reached with `kind="team"`, so the
   risk is low, but it is not the same as having done it.

3. **The USDT rail is another leaf's.** While this one ran, someone added
   migration 0079 (`WithdrawalRequest.method` bank/usdt, `payout_reference`, a
   `PayoutAddress` model with TRC-20 and ERC-20 and a confirm flow) plus
   `vent_auth/payouts.py` and `vent_auth/tests_payout_usdt.py`. This leaf
   stopped touching `models.py` and `views_wallet.py` on noticing. Their work
   sits correctly on this leaf's hold: `views_wallet.py:782` still holds through
   `wallets.hold_for_payout`, and the admin path still settles and returns.
   **Somebody should say which leaf owns the payout half.**

## Traps for whoever picks this up

- **The backend on :8000 runs MySQL and that DB is far behind the code.**
  Migrations 0072 to 0079 unapplied; it dies on "Unknown column
  `vent_auth_users.last_login_ip`". Nothing wallet-shaped can be walked against
  it. The seeded sqlite DB IS current: `DB_ENGINE=sqlite`, `local-dev.sqlite3`.
  This leaf walked on :8010 (sqlite) with a frontend on :3009.
- **NextAuth cookies on `localhost` ignore the PORT.** With several agents in
  one browser, the session under a page is not necessarily the one you signed
  in with. This leaf signed in as `demo_organizer` on :3009 and later found the
  page running as `demo_amara`, which cost about twenty minutes chasing a
  two-factor field that was correctly hidden for the account actually signed in.
  Read `/api/auth/session` in the tab, in the same call as the DOM assertion.
- **The MCP tab group is shared.** Tabs were renavigated mid-walk several times,
  and one was left on the NextAuth signout confirmation. It was not pressed;
  pressing it would have signed out whoever was working on :3001.

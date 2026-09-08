# USDT on V-ENT: what it needs, and what a custody decision is

Written 8 September 2026, for inbox rows 201 and 203.

Row 201: "Also explain how we can start using/trading on crypto on the website."
Row 203: "USDT payout half needs custody decision from you. What is cusody
decision."

This document answers both, and then says exactly what is built, what is not,
and why the not-built part is a decision rather than a task.

---

## 1. What a custody decision is

**Custody means: who holds the private key.**

A crypto wallet is not an account at a company. It is a number, called a private
key, and whoever knows that number can move the money. There is no support line,
no password reset and no chargeback. If the key is lost the money is gone
permanently; if the key is copied, the money is gone permanently and instantly.

So "the custody decision" is one question with three possible answers, and it
decides the cost, the risk and the legal position of everything else on this
page.

### Option A: V-ENT holds the keys itself (self custody)

V-ENT generates wallet addresses, keeps the keys on its own servers, and signs
every payout itself.

| | |
|---|---|
| Cost | Only network fees. No third party takes a cut |
| Speed | Instant. Nobody has to approve anything |
| What it needs | A hardware security module or an equivalent, a signing process no single person can run alone, an offline backup of the keys held somewhere other than the office, and a written procedure for what happens when the person who knows the process leaves |
| The real risk | One server compromise empties the float. Not a percentage of it, all of it, in one transaction, and it cannot be reversed |
| Honest assessment | This is the option that fails catastrophically. It suits a company with a security team. V-ENT is a startup running on one VPS |

### Option B: an exchange account holds the keys (exchange custody)

V-ENT opens a business account on an exchange (Binance, Bybit, or a Nigerian one
such as Quidax or Busha), keeps its USDT float there, and pays people out through
the exchange's API.

| | |
|---|---|
| Cost | Withdrawal fees per payout, plus a spread if converting |
| Speed | Fast. The API sends and the exchange handles the chain |
| What it needs | A business account with KYB documents, an API key with withdrawal permission, and an address allowlist |
| The real risk | The exchange freezes the account, or fails. This is not theoretical: exchanges have frozen Nigerian business accounts, and FTX took customer funds down with it in 2022 |
| Honest assessment | The pragmatic middle. Most platforms this size start here |

### Option C: a payment processor holds the keys, and V-ENT never touches crypto

V-ENT integrates a crypto payment provider. Somebody pays in USDT, the provider
receives it and settles naira to V-ENT's bank account. For payouts, V-ENT sends
naira to the provider and the provider pays USDT out.

| | |
|---|---|
| Cost | The highest of the three. The provider takes a percentage both ways |
| Speed | Depends on their settlement schedule, usually same day or next day |
| What it needs | An account with the provider. Almost no engineering, because the provider gives a hosted checkout and a webhook |
| The real risk | Margin, and dependence on one supplier |
| Honest assessment | **The one to start with.** V-ENT never holds a key, never holds crypto on its balance sheet, and the regulatory surface is the provider's rather than V-ENT's. It can be replaced by Option B later without any of the product changing, because the seam is the same either way |

### The recommendation, in one line

**Start with Option C.** Take crypto payments through a processor that settles in
naira. Move to Option B only when the volume makes the processor's margin cost
more than an exchange account's operational risk, and that is a question the
numbers will answer rather than an opinion.

---

## 2. Before anything is built: three things only the CEO can decide

### 2.1 Custody

The question above. Everything else waits on it, because the answer changes what
the code has to do:

- Option C: V-ENT never generates an address. There is no key material anywhere
  in this codebase, ever. The work is a webhook handler.
- Option B: V-ENT holds an API key that can withdraw money. Where that key lives
  and who can use it becomes a security question of its own.
- Option A: V-ENT holds keys. Do not choose this one without a security review
  by somebody who does this for a living.

### 2.2 Whether V-ENT is registered to do this

Nigeria's position has moved. The Central Bank's 2021 circular stopping banks
from serving crypto businesses was lifted at the end of 2023, and the SEC has a
digital asset framework under which a Virtual Asset Service Provider registers.
Whether V-ENT taking USDT for coins makes it a VASP is a question for a Nigerian
lawyer, not for this document, and the answer differs between the three options
above: under Option C the provider is the VASP and V-ENT is its customer.

This is the reason the crypto half is worth being slow about. Everything else on
this platform can be fixed by shipping again. This one cannot.

### 2.3 Who carries the price risk

USDT is meant to be worth one dollar and a VENT COIN is priced at 1,000 naira.
The dollar to naira rate moves, so:

- somebody buys coins with USDT at one rate on Monday
- and asks for a payout at a different rate on Friday

Somebody absorbs that difference. Either V-ENT quotes a rate that holds for a
short window and eats the movement inside it, or the person is paid at the rate
on the day and the amount they get is not the amount they expected. Both are
normal. Neither is a decision an engineer should make quietly.

---

## 3. What a working USDT integration needs, whichever option is chosen

These are the parts, and each one is a place this goes wrong if it is skipped.

### 3.1 Buying coins with USDT

1. **A rate source.** A published, timestamped USD to NGN rate, stored with the
   transaction. The platform already has a nightly exchange rate feed
   (`project_exchange_rates`), and it is display-only today. A rate somebody is
   actually charged at has to be stored on the row, not looked up again later,
   or a receipt cannot be reproduced.
2. **A quote with an expiry.** "This many coins for this much USDT, until this
   time." Without an expiry, somebody opens the page, waits for a favourable
   move, and pays at the old rate.
3. **A destination the payment can be recognised at.** Under Option C the
   provider gives a checkout and a reference. Under A or B it is an address, and
   an address must be unique to the payment or nobody can tell whose money
   arrived.
4. **A confirmations policy.** A blockchain payment is not final when it appears.
   It is final after enough blocks that reversing it is not worth anybody's
   while. The number is a business decision about how long somebody waits versus
   how much risk V-ENT takes on a reversal, and it differs per chain.
5. **What happens when the payment is late, short, or too much.** All three are
   ordinary, not edge cases:
   - **late**: it arrives after the quote expired. Credit it at the new rate, or
     refund it. Silently dropping it is theft.
   - **short**: they sent less than the quote. Credit what arrived, or hold it
     until they top it up. Never credit the full amount.
   - **over**: they sent more. Credit the extra or refund it. It must not vanish.
6. **Idempotency.** A webhook fires more than once. The wallet already has this
   pattern for Paystack: `Transaction.reference` is unique, so one payment
   reference can credit a wallet at most once. The same rule applies here and
   the column is already there.

### 3.2 Paying out in USDT

1. **Proof that the address belongs to the person.** This is the part that gets
   skipped and it is the expensive one. A typed address goes to whoever owns it,
   permanently, with no recovery. Three ways to reduce it: a small test payment
   first, a signed message from the address, or an address allowlist that takes
   effect only after a cooling-off period so a stolen account cannot add an
   address and drain the balance in the same session.
2. **Which chain.** USDT exists on several. USDT sent on Ethereum to an address
   that only exists on Tron is gone. The person picks the chain, the interface
   shows it back to them before they confirm, and the payout screen states the
   fee, which differs by chain by a large multiple.
3. **The same approval flow that fiat payouts already have.** A person asks, the
   money is held, an admin approves or denies, a denial returns it. **That half
   is built and working today** and is not crypto-specific, which is the point:
   when custody is decided, USDT payouts become another destination on the
   existing flow rather than a second flow.
4. **KYC before the first payout.** The seam is built (`vent_auth/kyc.py`) and
   the provider is a separate open decision, documented there.
5. **A float that can actually pay.** If ten people ask for USDT on the same day,
   the USDT has to exist. Under Option C the processor holds it. Under A or B
   V-ENT does, and somebody has to watch the balance and top it up.

### 3.3 Converting coins back to cash or crypto

The spec line is "Change VENT COINS back into cash or crypto". Cash is built:
that is the payout flow. Crypto is the same flow with a different destination
and everything in 3.2 in front of it.

---

## 4. What is built today, and what is not

| Piece | State |
|---|---|
| Buying coins with fiat, through Paystack | BUILT and working. Card, and a saved card |
| Wallet to wallet in every direction the spec names | BUILT, one atomic function, row locked |
| PIN on every debit | BUILT, hashed, on all three kinds of wallet |
| Second factor on a debit, for anybody enrolled | BUILT, reusing the existing authenticator |
| Payout requests, held at request, returned on denial | BUILT |
| Admin approving or denying a payout | BUILT |
| KYC, with a third party seam and an in-house reviewer behind it | SEAM BUILT, provider is an open decision |
| Spending coins on entry fees, tickets, vendor stalls and vendor orders | BUILT |
| **Buying coins with USDT** | **NOT BUILT. Waits on the custody decision** |
| **Payouts in USDT** | **NOT BUILT. Waits on the custody decision** |
| **Converting coins to crypto** | **NOT BUILT. Same** |

Nothing crypto-shaped has been faked. There is no `CryptoWallet` model with a
`TODO`, no settings key pointing at an exchange that does not exist, and no
screen offering a USDT button that answers "coming soon". A stub in this
particular area is worse than an absence, because somebody eventually believes
it.

---

## 5. "How we can start using and trading crypto on the website"

Row 201's second half, answered directly and in order.

**Step 1, and it is a week rather than a month.** Sign up with a crypto payment
processor that settles naira, and add USDT as a second way to buy VENT COINS
beside Paystack. V-ENT holds no crypto and no keys. The engineering is a
checkout redirect and a webhook, and the webhook does exactly what
`topup_verify` already does: credit the wallet against a unique reference.

**Step 2.** Turn on USDT payouts through the same processor. This is where the
address ownership proof in 3.2 has to be real, and where KYC stops being
optional. Everything else, the request, the hold, the admin approval, the return
on denial, is already there.

**Step 3, and only if the numbers justify it.** Move custody to an exchange
account (Option B) to stop paying the processor's margin. Nothing the user sees
changes. What changes is that V-ENT now holds a withdrawal-capable API key, and
that is a security posture rather than a feature.

**What "trading" would mean, and a caution.** If the question means letting
people trade crypto against each other on V-ENT, that is a different product: an
exchange. It carries the registration requirement squarely, needs an order book,
market surveillance, and a policy for what happens to open orders when the
platform goes down. It is not an extension of the wallet and should not be
started as one. If it means letting somebody convert their VENT COINS to USDT and
take it away, that is Step 2 and is already scoped above.

---

## 6. What is needed to unblock this

One answer, from the CEO, to one question: **A, B or C in section 1.**

With C, the next step is choosing a processor and opening the account, and the
engineering is small. With B, add a security review of where the API key lives.
With A, stop and get a security review first.

The second answer, which can follow a few days later, is whether a Nigerian
lawyer has confirmed which of the three leaves V-ENT outside the VASP
registration requirement.

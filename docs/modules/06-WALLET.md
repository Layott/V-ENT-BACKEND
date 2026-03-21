# 06 — Wallet System (VENT COINS)

**Phase:** 1 MVP — required for tournament registration fees and prize payouts
**Status:** ❌ Not built — page is a stub, no design exists
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** User System (05-USER-SYSTEM.md), Tournament Registration (01-TOURNAMENTS.md)

---

## Module Overview

The VENT Wallet is a platform-internal token economy. Users hold **VENT COINS** which they use to:
- Pay tournament registration fees
- Receive prize winnings
- Pay for event tickets
- Purchase from the Vendor Shop (Phase 2)
- Place wagers (Phase 6)

VENT COINS are bought with real money (fiat: NGN, GHS, KES, ZAR, etc.) via Paystack. They are not a crypto token — they are a fiat-backed in-app currency. Withdrawals (VENT COINS → fiat) require admin approval and KYC.

**Current state of payment in the codebase:** The tournament registration payment step (`Payment.js`) uses a hardcoded wallet balance of `526` and simulates Paystack with a `setTimeout`. This is a placeholder and must be replaced.

---

## Figma Node IDs

| Screen | Status | Notes |
|--------|--------|-------|
| User Wallet | ❌ Not designed | BIN version exists but outdated |
| Organization Wallet | ❌ Not designed | |
| Team Wallet | 🗑️ BIN | Deprecated — needs redesign |
| Buy VENT COINS | ❌ Not designed | |
| Send VENT COINS | ❌ Not designed | |
| Payout/Withdrawal | ❌ Not designed | |
| Transaction History | 🗑️ BIN | Table component in BIN — may be recoverable |

> **All wallet screens require Track B: Create HTML mockup → CEO approval → build.**

---

## Pages & Components Status

```
src/app/
└── wallets/
    └── page.js                              # ❌ Stub only — renders "Wallets" heading + "Filter and Search" text

src/components/
└── view-tournament/
    └── tournament-register/
        └── payment/
            └── Payment.js                   # ⚠️ Hardcoded wallet balance (526), setTimeout simulated Paystack
                                             # Must be replaced with real wallet API + Paystack SDK
```

**Nothing else exists for this module.** All wallet functionality needs to be built from scratch.

Planned structure once built:

```
src/app/
└── wallets/
    └── page.js                              # ⬜ Wallet overview — balance, send, top-up, history

src/components/
└── wallet/
    ├── WalletOverview.js                    # ⬜ Balance card, quick actions (Top Up, Send, Withdraw)
    ├── TransactionHistory.js                # ⬜ Paginated table: date, type, amount, status
    ├── TopUpModal.js                        # ⬜ Enter NGN amount → Paystack payment
    ├── SendCoinsModal.js                    # ⬜ Enter recipient username + amount + PIN
    ├── WithdrawModal.js                     # ⬜ Enter amount + bank account → admin queue
    └── KYCBanner.js                         # ⬜ Prompt for KYC if not verified, blocks withdrawal
```

---

## API Endpoints

### Current (None)

No wallet API calls exist in the frontend.

### Needed

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/wallet/balance/` | Get current user's VENT COIN balance |
| `GET` | `/wallet/transactions/` | Paginated transaction history |
| `POST` | `/wallet/topup/initiate/` | Initiate Paystack payment for top-up |
| `POST` | `/wallet/topup/verify/` | Verify Paystack callback → credit VENT COINS |
| `POST` | `/wallet/send/` | Send VENT COINS to another user (requires PIN) |
| `POST` | `/wallet/withdraw/initiate/` | Request fiat withdrawal (requires KYC + PIN) |
| `GET` | `/wallet/withdraw/status/` | Check withdrawal request status |
| `GET` | `/wallet/kyc/status/` | Check if user has completed KYC |
| `POST` | `/wallet/pin/verify/` | Verify PIN before sensitive transaction |
| `POST` | `/wallet/deduct/` | Internal: deduct VENT COINS for tournament registration fee |

> **Note on Paystack integration:** Paystack is already referenced in the codebase (simulated in `Payment.js`). The real integration requires:
> 1. Backend initializes a Paystack transaction → returns `authorization_url` and `reference`
> 2. Frontend redirects to Paystack payment page or opens Paystack inline popup
> 3. Paystack calls backend webhook on success → backend credits VENT COINS
> 4. Frontend polls `/wallet/topup/verify/?reference=` or waits for webhook redirect

---

## Data Shape Reference

### Wallet Balance (GET /wallet/balance/)

```json
{
  "status": "success",
  "data": {
    "balance": 1250,
    "currency": "VENT COINS",
    "kyc_verified": true,
    "pending_withdrawal": 0
  }
}
```

### Transaction History (GET /wallet/transactions/)

```json
{
  "status": "success",
  "data": {
    "transactions": [
      {
        "id": "txn001",
        "type": "top_up",
        "amount": 500,
        "description": "Top up via Paystack",
        "status": "completed",
        "reference": "PAY_abc123",
        "created_at": "2026-03-20T10:00:00Z"
      },
      {
        "id": "txn002",
        "type": "deduction",
        "amount": -150,
        "description": "Tournament registration: FIFA Pro League",
        "status": "completed",
        "tournament_id": "t456",
        "created_at": "2026-03-19T14:30:00Z"
      },
      {
        "id": "txn003",
        "type": "prize",
        "amount": 2000,
        "description": "1st place prize — FIFA Pro League",
        "status": "completed",
        "tournament_id": "t456",
        "created_at": "2026-03-19T18:00:00Z"
      },
      {
        "id": "txn004",
        "type": "withdrawal",
        "amount": -500,
        "description": "Withdrawal to GTBank ****1234",
        "status": "pending",
        "created_at": "2026-03-21T09:00:00Z"
      }
    ],
    "total": 42,
    "page": 1,
    "per_page": 20
  }
}
```

### Top Up Initiate (POST /wallet/topup/initiate/)

```json
// Request:
{ "amount_ngn": 1000 }

// Response:
{
  "status": "success",
  "data": {
    "authorization_url": "https://checkout.paystack.com/...",
    "reference": "PAY_abc123",
    "vent_coins": 500
  }
}
```

> Exchange rate (example): 1000 NGN = 500 VENT COINS. The conversion rate must be set by the backend (not hardcoded in frontend). Always display both amounts to the user before payment.

### Send VENT COINS (POST /wallet/send/)

```json
// Request:
{
  "recipient_username": "janedoe",
  "amount": 200,
  "pin": "1234",
  "note": "Good game!"
}

// Response on success:
{
  "status": "success",
  "data": {
    "new_balance": 1050,
    "transaction_id": "txn005"
  }
}
```

### Tournament Registration Deduction (POST /wallet/deduct/)

```json
// Called by backend during tournament registration — not called directly by frontend
// Frontend only calls tournament/register/ which triggers the deduction internally
{
  "user_id": "u123",
  "tournament_id": "t456",
  "amount": 150,
  "description": "Registration fee"
}
```

---

## Django Models (Inferred)

```python
class Wallet(models.Model):
    user = models.OneToOneField('User', related_name='wallet', on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    kyc_verified = models.BooleanField(default=False)
    pin_hash = models.CharField(max_length=255, blank=True)  # hashed 4-digit PIN
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Transaction(models.Model):
    TYPES = [
        ('top_up', 'Top Up'),
        ('deduction', 'Deduction'),
        ('prize', 'Prize'),
        ('send', 'Send'),
        ('receive', 'Receive'),
        ('withdrawal', 'Withdrawal'),
        ('refund', 'Refund'),
    ]
    STATUS = [('pending','Pending'),('completed','Completed'),('failed','Failed'),('cancelled','Cancelled')]

    wallet = models.ForeignKey(Wallet, related_name='transactions', on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # negative for deductions
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    reference = models.CharField(max_length=255, blank=True)  # Paystack reference
    tournament = models.ForeignKey('Tournament', null=True, blank=True, on_delete=models.SET_NULL)
    event = models.ForeignKey('Event', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

class WithdrawalRequest(models.Model):
    STATUS = [('pending','Pending'),('approved','Approved'),('rejected','Rejected'),('processing','Processing'),('completed','Completed')]
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True)

class KYCDocument(models.Model):
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    document_type = models.CharField(max_length=50)  # 'national_id', 'passport', 'drivers_license'
    document_image = models.ImageField(upload_to='kyc/')
    status = models.CharField(max_length=20, default='pending')
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True)
```

---

## Acceptance Criteria

### Wallet Overview Page (`/wallets`) — Track B

- [ ] Displays current VENT COIN balance from `GET /wallet/balance/`
- [ ] Loading state while balance fetches
- [ ] Quick action buttons: "Top Up", "Send", "Withdraw"
- [ ] KYC banner shown if `kyc_verified: false` and user tries to withdraw
- [ ] Transaction history table below overview: type icon, description, amount (+/-), status badge, date
- [ ] Pagination on transaction history (20 per page)
- [ ] Transaction types styled differently: green for credits (top_up, prize, receive), red for debits (deduction, send, withdrawal)

### Top Up Flow — Track B

- [ ] Modal opens with NGN input field
- [ ] As user types NGN amount, shows equivalent VENT COINS (from backend conversion rate — not hardcoded)
- [ ] On confirm: calls `POST /wallet/topup/initiate/` → receives Paystack `authorization_url`
- [ ] Opens Paystack checkout (popup or redirect — use Paystack inline SDK if available)
- [ ] On Paystack callback/return: calls `POST /wallet/topup/verify/?reference=` → balance updates
- [ ] Success message shows new balance

### Send VENT COINS — Track B

- [ ] Modal with: recipient username field, amount field, optional note, PIN input
- [ ] Username field validates against real users (autocomplete or validate on blur)
- [ ] Shows recipient display name when username resolves
- [ ] PIN is 4-digit masked input
- [ ] Confirmation step before sending (shows recipient, amount, note)
- [ ] Calls `POST /wallet/send/`; shows new balance on success

### Withdraw (Fiat Payout) — Track B

- [ ] Requires `kyc_verified: true` — show KYC prompt if not verified
- [ ] Form: amount in VENT COINS, bank name, account number, account name
- [ ] Shows equivalent fiat amount
- [ ] PIN confirmation required
- [ ] Calls `POST /wallet/withdraw/initiate/`; status shows as "Pending" in transaction history
- [ ] Admin must approve in Admin Dashboard (see 13-ADMIN-DASHBOARD.md)

### Tournament Registration Payment (fix existing)

- [ ] Replace hardcoded balance `526` in `Payment.js` with real API call to `GET /wallet/balance/`
- [ ] Check if user has sufficient balance before showing payment confirmation
- [ ] On payment confirm: call `POST /wallet/deduct/` (or have tournament registration API do it internally)
- [ ] Remove `setTimeout` simulation — use real Paystack or wallet deduction API

---

## Task Checklist

### 🔴 Critical — Must Ship with MVP

- [ ] Design HTML mockup for Wallet Overview — get CEO approval (Track B)
- [ ] Design HTML mockup for Top Up flow — get CEO approval
- [ ] Design HTML mockup for Send flow — get CEO approval
- [ ] Build `Wallet` and `Transaction` Django models — backend ticket required
- [ ] Build `GET /wallet/balance/` endpoint
- [ ] Build `GET /wallet/transactions/` endpoint
- [ ] Build wallet overview page (`/wallets`) — replaces stub
- [ ] Fix `Payment.js` — replace hardcoded balance with real API
- [ ] Integrate real Paystack top-up flow (backend webhook + frontend SDK)

### 🟡 Important (Phase 1, slightly deferred)

- [ ] Build Send VENT COINS flow
- [ ] Build `POST /wallet/send/` endpoint
- [ ] Build PIN setting in Account Settings (05-USER-SYSTEM.md)
- [ ] Build `WithdrawalRequest` model + endpoints
- [ ] Build Withdrawal request UI (requires KYC check)
- [ ] Wire prize payout: when organizer ends tournament → winners credited automatically

### 🟡 KYC (Phase 1, required before withdrawal)

- [ ] `KYCDocument` Django model + upload endpoint
- [ ] KYC banner in wallet UI
- [ ] KYC document upload flow (image upload + type selector)
- [ ] Admin KYC review in Admin Dashboard (13-ADMIN-DASHBOARD.md)

### ⬜ Phase 2+

- [ ] Organization wallet
- [ ] Team wallet (redesign from BIN)
- [ ] Multi-currency top-up (GHS, KES, ZAR beyond NGN)
- [ ] VENT COINS to ticket purchase (event ticketing)
- [ ] Vendor shop payments
- [ ] Wager system integration (Phase 6)

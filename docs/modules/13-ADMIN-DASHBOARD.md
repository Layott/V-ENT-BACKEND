# 13 — Admin Dashboard

**Phase:** 1 MVP — lightweight version required before public launch
**Status:** ❌ Not built — no design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** User System (05), Tournaments (01), Wallet (06)

---

## Module Overview

The Admin Dashboard is a separate secured interface for V-ENT platform operators. It is NOT part of the main app — it should be a distinct Next.js route group (or separate app) with its own layout that has no Sidebar, Header, or BottomMenu from the main app.

**Phase 1 MVP scope (must ship before public launch):**
1. **User Management** — view, search, edit, ban/unban users; assign roles (user, organizer, admin)
2. **Tournament Oversight** — view all tournaments, approve/reject disputes, correct scores
3. **Payout Approval** — review and approve/reject VENT COIN withdrawal requests
4. **Basic Platform Metrics** — total users, active tournaments, total VENT COINS in circulation

**Phase 2+ additions:**
5. **Event Management** — oversee events, manage ticketing disputes
6. **Financial Reports** — transaction volume, revenue (platform fees), withdrawal totals
7. **KYC Review** — approve/reject identity documents for withdrawal eligibility
8. **Content Moderation** — review AI-flagged content from Community and Marketplace
9. **Marketplace Dispute Resolution** — mediate buyer/seller disputes in Vermillion City
10. **Wager Management** — wager pool oversight, suspicious activity flags (Phase 6)

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Admin Login | ❌ Not designed |
| User Management | ❌ Not designed |
| Tournament Oversight | ❌ Not designed |
| Payout Approval | ❌ Not designed |
| Platform Metrics | ❌ Not designed |

> All screens require Track B: HTML mockup → CEO approval → build.

---

## Pages & Components Status

Nothing is built. No admin routes exist.

Planned structure — **admin routes must be in a separate route group**:

```
src/app/
└── (admin)/
    ├── layout.js                            # ⬜ Admin layout — no main-app chrome, admin nav only
    ├── admin/
    │   ├── page.js                          # ⬜ Admin dashboard home — key metrics overview
    │   ├── users/
    │   │   ├── page.js                      # ⬜ User list with search, filter, bulk actions
    │   │   └── [id]/
    │   │       └── page.js                  # ⬜ User detail — profile, activity, ban history
    │   ├── tournaments/
    │   │   ├── page.js                      # ⬜ All tournaments list — status, organizer, flag
    │   │   └── [id]/
    │   │       └── page.js                  # ⬜ Tournament detail — scores, disputes, override
    │   ├── payouts/
    │   │   └── page.js                      # ⬜ Pending withdrawal requests queue
    │   ├── kyc/
    │   │   └── page.js                      # ⬜ KYC document review queue (Phase 1)
    │   ├── events/
    │   │   └── page.js                      # ⬜ Event management (Phase 2)
    │   ├── marketplace/
    │   │   └── page.js                      # ⬜ Dispute resolution (Phase 4)
    │   └── wager/
    │       └── page.js                      # ⬜ Wager oversight (Phase 6)

src/components/
└── admin/
    ├── AdminNav.js                          # ⬜ Left sidebar navigation (admin-specific)
    ├── MetricsCard.js                       # ⬜ Stat card: label, value, delta
    ├── UserTable.js                         # ⬜ Sortable/filterable user data table
    ├── UserBanModal.js                      # ⬜ Confirm ban + reason input
    ├── TournamentTable.js                   # ⬜ Tournament list with status badges
    ├── DisputeCard.js                       # ⬜ Dispute detail with resolve/reject actions
    ├── ScoreOverrideModal.js                # ⬜ Admin score correction
    ├── PayoutQueueTable.js                  # ⬜ Withdrawal requests with approve/reject
    ├── KYCReviewCard.js                     # ⬜ Document image + approve/reject
    └── ContentFlagQueue.js                  # ⬜ AI-flagged content items (Phase 3)
```

---

## Authentication & Access Control

The admin dashboard must use a **separate authentication check** from the main app's NextAuth session.

Options:
1. **Separate admin login** — admin users log in at `/admin/login` with a username+password that checks against a Django admin user flag (`is_staff: true`). Main app session is not accepted.
2. **Role-based check on main session** — if main NextAuth session has `role: "admin"`, allow admin access. Simpler but less secure.

**Recommendation:** Option 1 — separate admin login is more secure. Admin credentials should be managed independently.

```js
// Admin middleware (src/middleware.js) — extend to protect /admin/* routes
// Check for admin-specific session token, not the main user session
```

> **Never expose admin endpoints to non-admin users.** All admin API endpoints must verify `is_staff: true` on the Django user object.

---

## API Endpoints (Needed)

### Platform Metrics

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/metrics/` | Total users, active tournaments, VENT COINS in circulation, daily active users |

### User Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/users/` | Paginated user list (search by username/email, filter by role/status) |
| `GET` | `/admin/users/{id}/` | User detail — profile, activity, wallet, ban history |
| `PATCH` | `/admin/users/{id}/ban/` | Ban or unban user (with reason) |
| `PATCH` | `/admin/users/{id}/role/` | Assign role (user, organizer, admin) |
| `DELETE` | `/admin/users/{id}/` | Delete account (irreversible — requires double confirmation) |

### Tournament Oversight

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/tournaments/` | All tournaments with status, organizer, dispute flags |
| `GET` | `/admin/tournaments/{id}/` | Tournament detail with match history |
| `POST` | `/admin/tournaments/{id}/dispute/resolve/` | Resolve a dispute (approve/reject with reason) |
| `PATCH` | `/admin/matches/{id}/score/` | Admin score override |
| `POST` | `/admin/tournaments/{id}/cancel/` | Cancel a tournament (refunds registration fees) |

### Payout Approval

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/payouts/pending/` | List of pending withdrawal requests |
| `POST` | `/admin/payouts/{id}/approve/` | Approve payout → triggers bank transfer |
| `POST` | `/admin/payouts/{id}/reject/` | Reject payout with reason |

### KYC Review

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/kyc/pending/` | Queue of pending KYC submissions |
| `POST` | `/admin/kyc/{id}/approve/` | Mark user as KYC verified |
| `POST` | `/admin/kyc/{id}/reject/` | Reject KYC with reason |

---

## Data Shape Reference

### Platform Metrics (GET /admin/metrics/)

```json
{
  "status": "success",
  "data": {
    "total_users": 4210,
    "new_users_today": 38,
    "active_tournaments": 7,
    "total_tournaments_all_time": 143,
    "vent_coins_in_circulation": 2450000,
    "pending_withdrawals": 12,
    "pending_kyc": 5,
    "open_disputes": 2
  }
}
```

### User List Item

```json
{
  "user_id": "u123",
  "username": "johndoe",
  "email": "john@example.com",
  "role": "user",
  "is_banned": false,
  "is_verified": true,
  "kyc_status": "approved",
  "wallet_balance": 1250,
  "tournaments_played": 18,
  "joined_at": "2025-09-01"
}
```

### Payout Request Item

```json
{
  "id": "payout001",
  "user": {
    "user_id": "u123",
    "username": "johndoe",
    "kyc_status": "approved"
  },
  "amount_vent_coins": 500,
  "amount_ngn": 10000,
  "bank_name": "GTBank",
  "account_number": "****1234",
  "account_name": "John Doe",
  "requested_at": "2026-03-21T09:00:00Z",
  "status": "pending"
}
```

### Dispute Item

```json
{
  "dispute_id": "d001",
  "tournament_id": "t1",
  "tournament_name": "FIFA Pro League",
  "match_id": "m01",
  "raised_by": { "user_id": "u5", "username": "player_x" },
  "reason": "Opponent disconnected but the win was awarded to them",
  "evidence_urls": ["https://..."],
  "status": "open",
  "raised_at": "2026-03-20T16:00:00Z"
}
```

---

## Django Models (Inferred)

```python
class AdminAction(models.Model):
    """Audit log of all admin actions"""
    admin = models.ForeignKey('User', related_name='admin_actions', on_delete=models.CASCADE)
    action_type = models.CharField(max_length=50)  # 'ban_user', 'approve_payout', 'resolve_dispute', etc.
    target_model = models.CharField(max_length=50)  # 'User', 'Tournament', 'WithdrawalRequest', etc.
    target_id = models.CharField(max_length=100)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    performed_at = models.DateTimeField(auto_now_add=True)

class TournamentDispute(models.Model):
    STATUS = [('open','Open'),('resolved_approved','Resolved - Approved'),('resolved_rejected','Resolved - Rejected')]
    tournament = models.ForeignKey('Tournament', on_delete=models.CASCADE)
    match = models.ForeignKey('Match', null=True, on_delete=models.SET_NULL)
    raised_by = models.ForeignKey('User', on_delete=models.CASCADE)
    reason = models.TextField()
    evidence = models.JSONField(default=list)  # list of image/video URLs
    status = models.CharField(max_length=30, choices=STATUS, default='open')
    resolved_by = models.ForeignKey('User', null=True, related_name='resolved_disputes', on_delete=models.SET_NULL)
    resolution_note = models.TextField(blank=True)
    raised_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True)
```

---

## Acceptance Criteria

### Admin Dashboard Home (`/admin`) — Track B

- [ ] Secure login required — separate from main app session
- [ ] Metrics cards: total users, active tournaments, pending payouts, open disputes, VENT COINS in circulation
- [ ] Quick links to: pending payouts, open disputes, pending KYC
- [ ] No Sidebar, Header, BottomMenu from main app

### User Management (`/admin/users`) — Track B

- [ ] Paginated table: username, email, role, joined date, ban status, wallet balance
- [ ] Search by username or email
- [ ] Filter by: role, ban status, KYC status
- [ ] Click user → `/admin/users/{id}` for full detail
- [ ] Ban/unban with reason — confirmation modal
- [ ] Role assignment (user → organizer → admin)
- [ ] All actions logged to `AdminAction` audit log

### Tournament Oversight (`/admin/tournaments`) — Track B

- [ ] List all tournaments with: name, organizer, game, status, dispute flag
- [ ] Filter by status (active, completed, cancelled, disputed)
- [ ] Open dispute → dispute detail card with raise reason, evidence, and resolve/reject buttons
- [ ] Score override modal — admin selects winner with reason
- [ ] Cancel tournament → confirms refund of all registration fees to participants' wallets

### Payout Approval (`/admin/payouts`) — Track B

- [ ] Queue of pending withdrawal requests sorted by oldest first
- [ ] Each row: username, KYC status, amount, bank details
- [ ] **Block approve if KYC not verified**
- [ ] Approve → triggers bank transfer (backend handles actual transfer via Paystack Transfers API or similar)
- [ ] Reject → sends reason to user (notification or email)
- [ ] Processed requests stay visible with "Approved/Rejected" status

### KYC Review (`/admin/kyc`) — Track B

- [ ] Queue of pending KYC submissions
- [ ] View document image(s) in review panel
- [ ] Approve → sets `wallet.kyc_verified = True` → user can now withdraw
- [ ] Reject → sends reason to user

---

## Task Checklist

### 🔴 Critical — Must Ship in Phase 1 MVP

- [ ] Design HTML mockup for Admin Dashboard home — CEO approval (Track B)
- [ ] Design HTML mockup for User Management — CEO approval
- [ ] Design HTML mockup for Payout Approval — CEO approval
- [ ] Design HTML mockup for Tournament Oversight — CEO approval
- [ ] Separate admin authentication (admin-only login route)
- [ ] Admin middleware: protect all `/admin/*` routes
- [ ] `AdminAction` audit log model
- [ ] `GET /admin/metrics/` endpoint
- [ ] User management CRUD endpoints (`GET /admin/users/`, ban, role assign)
- [ ] Payout queue + approve/reject endpoints
- [ ] Tournament oversight + dispute resolve endpoints
- [ ] KYC review endpoints
- [ ] Build: admin home metrics page
- [ ] Build: user management page + user detail
- [ ] Build: payout queue page
- [ ] Build: KYC review page
- [ ] Build: tournament oversight + dispute detail

### 🟡 Phase 2

- [ ] Event management panel
- [ ] Financial reports (transaction volume, fee revenue)
- [ ] Content flag queue (AI-flagged content from Community/Marketplace)

### ⬜ Phase 4+

- [ ] Marketplace dispute resolution panel
- [ ] Seller/listing management

### ⬜ Phase 6

- [ ] Wager pool oversight
- [ ] AML suspicious activity flag queue

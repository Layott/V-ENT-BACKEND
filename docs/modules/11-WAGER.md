# 11 — Wager System

**Phase:** 6 — Build LAST. Legal review required before any development begins.
**Status:** ❌ Not built — no design, no code. **Do not start until legal clearance.**
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** Wallet (06), Tournaments (01), User System (05), Admin Dashboard (13)

---

## Module Overview

> ⚠️ **LEGAL REVIEW REQUIRED BEFORE ANY DEVELOPMENT.** Online wagering/betting is regulated differently across African jurisdictions. Nigeria (NLRC), Ghana (GCB), Kenya (BCLB), and South Africa (NGB) all have distinct licensing requirements. Some jurisdictions classify skill-based wagering (e.g., gaming tournaments) differently from chance-based gambling. Do not ship any part of this module without explicit legal sign-off.

The Wager System allows users to place VENT COIN stakes on tournament match outcomes:

1. **Peer-to-peer wagers** — two users agree on a match outcome and stake VENT COINS; winner takes both stakes minus platform fee
2. **Pool wagers** — multiple users stake on the same match; payout distributed proportionally to correct predictors
3. **Organizer-created wager pools** — tournament organizer opens a pool for a specific match during their event

**What is NOT included:** Sports betting on external leagues (NBA, EPL, etc.) — V-ENT wagers apply only to tournaments hosted on the V-ENT platform.

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Wager Homepage | ❌ Not designed |
| Create Wager | ❌ Not designed |
| Active Wagers | ❌ Not designed |
| Wager History | ❌ Not designed |
| Pool Wager Detail | ❌ Not designed |

> All screens require Track B. Legal constraints may affect UI — wait for legal review before designing.

---

## Pages & Components Status

Nothing is built. No routes, no components exist.

Planned structure (subject to legal review):

```
src/app/
└── wager/
    ├── page.js                              # ⬜ Wager hub — active pools, my wagers
    ├── create/
    │   └── page.js                          # ⬜ Create a peer-to-peer wager challenge
    └── pool/
        └── page.js                          # ⬜ Pool wager detail (?id=...)

src/components/
└── wager/
    ├── WagerHub.js                          # ⬜ Active pools, featured matches
    ├── WagerCard.js                          # ⬜ Match + stake amount + odds + deadline
    ├── CreateWagerForm.js                    # ⬜ Select match, set stake, send challenge
    ├── PoolWagerDetail.js                    # ⬜ Pool breakdown: all stakers, your pick, payout calc
    ├── WagerConfirm.js                       # ⬜ PIN confirmation before stake
    └── WagerHistory.js                       # ⬜ Past wagers: won/lost/pending
```

---

## API Endpoints (Needed — Post Legal Review)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/wager/pools/` | Active wager pools for ongoing/upcoming matches |
| `GET` | `/wager/pools/{id}/` | Pool detail — stakers, odds, deadline |
| `POST` | `/wager/pools/{id}/stake/` | Stake VENT COINS on a pool outcome |
| `POST` | `/wager/challenge/create/` | Create a peer-to-peer wager challenge |
| `POST` | `/wager/challenge/{id}/accept/` | Accept a challenge |
| `POST` | `/wager/challenge/{id}/reject/` | Reject a challenge |
| `GET` | `/wager/my/` | User's active and past wagers |
| `POST` | `/wager/settle/{id}/` | Admin/system settles wager after match end |

---

## Data Shape Reference

### Wager Pool Object

```json
{
  "id": "pool001",
  "match": {
    "match_id": "m01",
    "tournament_id": "t1",
    "tournament_name": "FIFA Pro League",
    "team_a": "Team Alpha",
    "team_b": "Team Beta",
    "match_time": "2026-04-01T15:00:00Z"
  },
  "total_staked": 4500,
  "stakers": 18,
  "outcome_options": [
    { "outcome": "team_a_wins", "total_staked": 3000, "implied_odds": 1.5 },
    { "outcome": "team_b_wins", "total_staked": 1500, "implied_odds": 3.0 }
  ],
  "deadline": "2026-04-01T14:45:00Z",
  "status": "open",
  "platform_fee_pct": 5
}
```

### Stake Request

```json
{
  "pool_id": "pool001",
  "outcome": "team_a_wins",
  "amount_vent_coins": 200,
  "pin": "1234"
}
```

---

## Django Models (Inferred — Subject to Legal Review)

```python
class WagerPool(models.Model):
    STATUS = [('open','Open'),('locked','Locked'),('settled','Settled'),('cancelled','Cancelled')]
    match = models.ForeignKey('Match', on_delete=models.CASCADE)
    created_by = models.ForeignKey('User', null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=STATUS, default='open')
    deadline = models.DateTimeField()
    platform_fee_pct = models.IntegerField(default=5)
    winning_outcome = models.CharField(max_length=50, blank=True)
    settled_at = models.DateTimeField(null=True)

class WagerStake(models.Model):
    pool = models.ForeignKey(WagerPool, related_name='stakes', on_delete=models.CASCADE)
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    outcome = models.CharField(max_length=50)
    amount = models.IntegerField()
    payout = models.IntegerField(default=0)
    placed_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('pool', 'user')

class PeerWager(models.Model):
    STATUS = [('pending','Pending'),('active','Active'),('settled','Settled'),('cancelled','Cancelled')]
    match = models.ForeignKey('Match', on_delete=models.CASCADE)
    challenger = models.ForeignKey('User', related_name='wager_challenges', on_delete=models.CASCADE)
    challengee = models.ForeignKey('User', related_name='wager_challenged', on_delete=models.CASCADE)
    challenger_outcome = models.CharField(max_length=50)
    challengee_outcome = models.CharField(max_length=50)
    stake_amount = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    winner = models.ForeignKey('User', null=True, related_name='wager_wins', on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## Acceptance Criteria (Post-Legal Approval)

### Wager Hub — Track B

- [ ] Age verification gate: confirm user is 18+ before accessing wager features
- [ ] Responsible gambling notice displayed on all wager pages
- [ ] User can opt out of wager features in account settings
- [ ] Spending limit: users can set a daily/weekly VENT COIN wager cap
- [ ] All pools clearly show deadline, platform fee, and current odds

### Pool Wager — Track B

- [ ] User can view all active pools
- [ ] User selects outcome and enters stake amount
- [ ] Shows expected payout calculation before confirming
- [ ] PIN required before stake is placed
- [ ] Odds are recalculated and displayed live as more stakes come in
- [ ] Stakes locked when `deadline` passes or match starts
- [ ] Payout credited automatically when match result is confirmed

### Peer Wager — Track B

- [ ] User can challenge a specific other user on a specific match outcome
- [ ] Challengee can accept or reject
- [ ] Both parties' VENT COINS go into escrow on acceptance
- [ ] Payout on match result confirmation by system

---

## Task Checklist

### ⚠️ Legal Pre-Conditions (Must Complete Before Any Dev)

- [ ] Legal review: wagering licensing requirements for NG, GH, KE, ZA
- [ ] Confirm: is skill-based tournament wagering classified differently from chance gambling?
- [ ] Confirm: age verification mechanism (how to verify user age at signup vs. wager access?)
- [ ] Responsible gambling compliance requirements (cooling-off periods, spending limits, self-exclusion)
- [ ] Platform fee structure legal review
- [ ] AML (Anti-Money Laundering) obligations for high-volume wager activity
- [ ] CEO sign-off on legal review before any design or development begins

### ⬜ Phase 6 (all — after legal clearance)

- [ ] Design HTML mockups — CEO approval
- [ ] Django models: `WagerPool`, `WagerStake`, `PeerWager`
- [ ] Wager escrow system (separate from general wallet transactions)
- [ ] Age gate + user opt-out
- [ ] Responsible gambling features (spending limits, self-exclusion)
- [ ] Pool wager UI + stake flow
- [ ] Peer wager challenge + accept flow
- [ ] Admin wager oversight panel (13-ADMIN-DASHBOARD.md — Wager Management section)
- [ ] Automatic settlement on match result (integrates with 03-PRODUCTION.md match end hook)
- [ ] AML monitoring hooks (flag high-volume activity for admin review)

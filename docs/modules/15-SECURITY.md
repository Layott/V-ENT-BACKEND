# 15 — Security

**Phase:** Ongoing — applies to every phase
**Status:** 🟡 Partial — some measures in place, several critical gaps
**Design track:** Track C (no visual component — backend/logic only)
**Dependencies:** All modules

---

## Module Overview

This document covers the security posture of the V-ENT platform: what is currently in place, what is broken, and what must be fixed before public launch. Security is not a Phase 6 concern — it is a continuous requirement.

Sections:
1. Authentication & Session Security
2. API Security
3. Input Validation & Injection Prevention
4. Secrets & Environment Variables
5. Rate Limiting
6. IP Logging & Fraud Detection
7. Financial Security (Wallet, Wager)
8. Content Security Policy
9. Pre-Launch Security Checklist

---

## 1. Authentication & Session Security

### What's in place

- NextAuth v4 with JWT strategy (30-day maxAge)
- Credentials + Google + Facebook OAuth providers
- Django backend session token stored in JWT
- Middleware route protection for core protected routes
- `session` cookie as secondary auth check

### What's broken / needs fixing

| Issue | Severity | Location |
|-------|----------|----------|
| `console.log` statements logging session tokens | 🔴 Critical | `lib/authOptions.js` (lines with `session_token` in logs) |
| `console.log` in every middleware request | 🟡 High | `src/middleware.js` |
| Facebook OAuth likely misconfigured | 🟡 High | `lib/authOptions.js` — verify real app ID/secret in env |
| `/tournaments` routes not protected | 🟡 Medium | `src/middleware.js` — confirm if intentional |
| NextAuth secret must be a strong random value in prod | 🔴 Critical | `.env` — `NEXTAUTH_SECRET` must be 32+ char random string |
| JWT 30-day maxAge is long — consider shorter + refresh | 🟡 Medium | `lib/authOptions.js` |
| Admin routes not protected at all (no admin auth exists yet) | 🔴 Critical | Must be built with 13-ADMIN-DASHBOARD.md |

### Required before launch

```js
// lib/authOptions.js — remove ALL console.log statements
// Never log token data:
// console.log("token", token)  ← REMOVE
// console.log("session", session) ← REMOVE
```

```bash
# .env — ensure these are set with strong values in production:
NEXTAUTH_SECRET=<32+ random chars — use: openssl rand -base64 32>
NEXTAUTH_URL=https://yourdomain.com
NEXT_PUBLIC_API_URL=https://vermillionent.pythonanywhere.com
```

---

## 2. API Security

### Frontend API Call Pattern

All authenticated API calls must pass `Authorization: Bearer {session.user.sessionToken}`. The current codebase inconsistently uses:
- `Authorization: Bearer ${sessionToken}` ✅ (correct)
- `Authorization: Token ${sessionToken}` (some pages try this as fallback)
- Bare `session_token` as a form field body param (event creation wizard — incorrect)

**Standardize on `Authorization: Bearer`** across all components. Remove the `Token` prefix fallback — it suggests the backend was inconsistent at some point; resolve with backend team.

### Backend API Security (Django side — coordinate with backend team)

- [ ] All admin endpoints (`/admin/*`) must check `request.user.is_staff`
- [ ] Wallet deduction endpoints must verify ownership (user can only deduct from their own wallet)
- [ ] Tournament creation must verify the submitting user becomes the organizer
- [ ] Score update endpoints must verify the user is the tournament organizer
- [ ] File upload endpoints must validate file type and size (prevent arbitrary file upload)
- [ ] CORS: `ALLOWED_ORIGINS` must list only known frontend domains — no wildcard `*` in production

### HTTPS

- All API calls use `https://` (already the case with `vermillionent.pythonanywhere.com`)
- Ensure the production frontend deployment enforces HTTPS (no HTTP fallback)

---

## 3. Input Validation & Injection Prevention

### Current State

Validation is minimal — most form fields are submitted as-is to the backend. The backend is the primary validation layer (Django serializers).

### Frontend Validation Rules (to implement)

All forms must validate before submitting:

```js
// Text inputs: trim whitespace, enforce max length matching backend model
// Numeric inputs: enforce min/max range, integer vs decimal
// File uploads: validate type (image/jpeg, image/png, image/webp only for images)
//               validate size (max 5MB for avatars, max 10MB for banners)
// URLs: basic URL format check for social links
// Amounts (wallet): must be positive integer VENT COINS
// PINs: exactly 4 digits, numeric only
```

### XSS Prevention

- Next.js JSX escapes values by default — do not use `dangerouslySetInnerHTML` unless sanitizing first
- Rich text from `react-quill` (tournament/event rules) renders HTML — must use DOMPurify before rendering stored HTML:

```js
// Bad (current likely approach):
<div dangerouslySetInnerHTML={{ __html: tournament.rules }} />

// Good:
import DOMPurify from 'dompurify';
<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(tournament.rules) }} />
```

Install DOMPurify: `npm install dompurify`

### SQL Injection

Handled by Django ORM — do not use raw SQL queries. If any raw queries are used in the backend, they must use parameterized queries.

---

## 4. Secrets & Environment Variables

### Current `.env` variables (should be present)

```bash
NEXT_PUBLIC_API_URL=           # Django backend URL — public (safe to expose)
NEXTAUTH_SECRET=               # Must be strong random string — NEVER commit to git
NEXTAUTH_URL=                  # Full URL of the Next.js app
GOOGLE_CLIENT_ID=              # Google OAuth app ID
GOOGLE_CLIENT_SECRET=          # Google OAuth secret — NEVER commit to git
FACEBOOK_CLIENT_ID=            # Facebook OAuth app ID
FACEBOOK_CLIENT_SECRET=        # Facebook OAuth secret — NEVER commit to git
```

### Rules

- `.env.local` is in `.gitignore` — verify this
- Never commit secrets to the repository
- In production, set environment variables via hosting provider (Vercel, Railway, etc.) — not via `.env` files
- `NEXT_PUBLIC_*` prefix exposes vars to the browser — only use this prefix for truly public values (API URL, public app IDs)
- `NEXTAUTH_SECRET` and all OAuth secrets must NOT have `NEXT_PUBLIC_` prefix

### Hardcoded URL (Tech Debt)

Two components hardcode the backend URL instead of using `process.env.NEXT_PUBLIC_API_URL`:

- `src/components/create-tournament-component/CreateTournamentComponent.js:297`
- `src/app/events/view-event/page.js` (confirmed)

Fix: replace with `process.env.NEXT_PUBLIC_API_URL`.

---

## 5. Rate Limiting

### Current State

No rate limiting exists in the frontend. The Django backend may have rate limiting — confirm with backend team.

### Required Before Launch

**Backend (Django) — rate limiting targets:**

| Endpoint | Limit | Reason |
|----------|-------|--------|
| `POST /auth/login/` | 10 req/min per IP | Brute force protection |
| `POST /auth/signup/` | 5 req/min per IP | Registration spam |
| `POST /auth/forgot-password/` | 3 req/min per IP | Email spam prevention |
| `POST /auth/resend-link/` | 3 req/min per IP | Email spam prevention |
| `POST /wallet/topup/initiate/` | 5 req/min per user | Abuse prevention |
| `POST /wallet/send/` | 10 req/min per user | Transfer abuse |
| `POST /ai/chat/` | 20 req/min per user | Cost control |
| `POST /anime/amv/` | 5 req/hour per user | Upload spam |

Use `django-ratelimit` or `djangorestframework-throttling`.

**Frontend — debounce UX:**
- Search inputs: debounce 300ms before firing API call
- All form submit buttons: disable after click until response received (prevent double-submit)
- All currently exist without debounce or submit protection — add during API integration

---

## 6. IP Logging & Fraud Detection

### Required

The backend should log IP addresses for:
- All login attempts (failed and successful)
- Wallet transactions (top-up, send, withdraw)
- Tournament registration
- Wager placement (Phase 6 — required by regulators)

### Frontend Role

The frontend does not log IPs (cannot do so reliably — client IP is set server-side). However:
- Do not strip client IP headers in middleware
- If Next.js API routes are used for any sensitive actions, log `request.headers.get('x-forwarded-for')` or `request.ip`

### Fraud Signals (backend responsibility)

- Multiple registrations from same IP in short window → flag account
- Wallet top-up immediately followed by full withdrawal → flag for manual review
- Wager placement pattern analysis for collusion (Phase 6)

---

## 7. Financial Security (Wallet & Wager)

### Transaction PIN

All wallet send, withdrawal, and wager stake operations require a 4-digit PIN. Implementation rules:
- PIN is hashed server-side (bcrypt or Django's `make_password`)  — never stored as plain text
- PIN is submitted to a dedicated `POST /wallet/pin/verify/` endpoint before proceeding — not embedded in every transaction endpoint
- PIN input in frontend: masked input (type="password"), 4 digits only, numeric keyboard on mobile

### Double-Submit Prevention

Financial transactions must be idempotent. The backend must:
- Accept an `idempotency_key` (UUID generated client-side per action) on all write endpoints
- Reject duplicate requests with the same key
- Frontend generates a new UUID per form open — not per submit click

### Paystack Webhook Security

When Paystack calls the Django backend webhook:
- Verify the `x-paystack-signature` header using HMAC-SHA512 with your Paystack secret key
- Reject any webhook that fails signature verification
- Do not credit VENT COINS based on frontend callback alone — always verify via backend webhook

---

## 8. Content Security Policy

Add a Content Security Policy header to `next.config.js`:

```js
// next.config.js
const securityHeaders = [
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'origin-when-cross-origin' },
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-eval' 'unsafe-inline' https://js.paystack.co",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https://vermillionent.pythonanywhere.com",
      "connect-src 'self' https://vermillionent.pythonanywhere.com wss://vermillionent.pythonanywhere.com",
      "font-src 'self'",
    ].join('; ')
  }
];
```

> Adjust `img-src` and `connect-src` as the backend URL changes.

---

## 9. Pre-Launch Security Checklist

### 🔴 Must Fix Before Any Public Launch

- [ ] Remove all `console.log` from `lib/authOptions.js` (session tokens in logs)
- [ ] Remove all `console.log` from `src/middleware.js`
- [ ] Set `NEXTAUTH_SECRET` to a 32+ char random value in production env
- [ ] Verify `.env.local` is in `.gitignore` — check git history for accidental secret commits
- [ ] Fix hardcoded backend URLs → use `process.env.NEXT_PUBLIC_API_URL`
- [ ] Verify Google OAuth app is properly configured (correct redirect URIs for production domain)
- [ ] Verify Facebook OAuth app is configured (or disable Facebook login until it is)
- [ ] Wrap `react-quill` with `dynamic(() => import('react-quill'), { ssr: false })` — prevent SSR hydration crash
- [ ] Add DOMPurify for any HTML rendered from rich text fields
- [ ] Disable `NEXT_PUBLIC_` prefix on any variable that is not truly public

### 🔴 Must Fix Before Wallet Launch

- [ ] Backend rate limiting on auth and wallet endpoints
- [ ] PIN hashing — never store plain text PIN
- [ ] Paystack webhook signature verification
- [ ] Idempotency keys on wallet transaction endpoints
- [ ] IP logging for wallet transactions on backend

### 🟡 Must Fix Before Admin Dashboard Launch

- [ ] Admin route protection in middleware (`/admin/*` → admin session only)
- [ ] All Django admin endpoints check `is_staff: true`
- [ ] `AdminAction` audit log — every admin action logged

### 🟡 Should Fix Before Scale

- [ ] Content Security Policy headers in `next.config.js`
- [ ] Frontend form submit debounce / double-submit prevention
- [ ] Input validation on all form fields (type, length, format)
- [ ] Backend CORS: no wildcard `*` in `ALLOWED_ORIGINS`

### ⬜ Phase 6 (Wager-Specific)

- [ ] AML transaction monitoring
- [ ] Age verification gate
- [ ] Responsible gambling features (spending limits, self-exclusion)
- [ ] IP logging for wager placement (regulatory requirement)
- [ ] Legal review completed before any wager code ships

---

## Known Codebase Security Issues (Summary)

| Issue | File | Fix |
|-------|------|-----|
| Session tokens in console.log | `lib/authOptions.js` | Remove all console.log |
| Every request logged to console | `src/middleware.js` | Remove all console.log |
| Hardcoded API URL | `CreateTournamentComponent.js:297` | Use `process.env.NEXT_PUBLIC_API_URL` |
| Hardcoded API URL | `events/view-event/page.js` | Use `process.env.NEXT_PUBLIC_API_URL` |
| react-quill SSR (crash risk) | `create-tournament-component/` | Wrap with `dynamic()` |
| Simulated payment with hardcoded balance | `payment/Payment.js` | Real wallet API + Paystack |
| Dual auth prefix (Bearer vs Token) | Multiple components | Standardize on Bearer |
| Event creation sends session_token as body field | `CreateEventComponent.js` | Authorization header only |
| No input validation on file uploads | All file upload components | Validate type + size client-side |
| No DOMPurify on rich text | Tournament/event rules display | Install + use DOMPurify |

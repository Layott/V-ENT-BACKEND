# V-ENT External Tools & Services Guide

Every third-party tool, API, and service V-ENT needs — organized by module, with AWS-first approach using $1,000 AWS credits.

---

## AWS Credits Strategy

**Available:** $1,000 AWS credits

**Approach:** Use AWS for all infrastructure and commodity services (hosting, database, storage, email, caching). Use specialized third-party services only where AWS has no good equivalent (payments, IP geolocation, error tracking, analytics).

**Estimated monthly AWS spend:** ~$61-66/month → credits last ~15 months

| AWS Service | What For | Est. Monthly Cost |
|-------------|----------|-------------------|
| EC2 t3.small | Django + Celery workers + Daphne (WS) | $15 |
| RDS MySQL db.t3.micro | Primary database | $15 |
| S3 | File storage (avatars, logos, banners, media) | $2-5 |
| SES | Transactional + marketing emails | $1-5 |
| CloudFront | CDN for static assets and media | $3-5 |
| ElastiCache Redis t3.micro | Caching + WebSocket channel layer | $12 |
| Route 53 | DNS management | $1 |
| EC2 t3.micro (staging) | Staging/test environment | $8 |
| **Total** | | **~$61-66/month** |

**Credits buffer remaining:** ~$300-400 for traffic spikes, scaling, and experimentation.

---

## Quick Reference: All Services (MVP)

| Service | What For | Provider | Free/Credits | Cost When Scaling |
|---------|----------|----------|--------------|-------------------|
| Payment Gateway | Buy VENT COINS, fees | **Paystack** | No setup fee | 1.5% + ₦100/txn |
| Email (All) | Verification, alerts, marketing | **AWS SES** | AWS credits | $0.10 per 1K emails |
| IP Geolocation | Location, region features | **ipinfo.io** | 50K req/month free | From $99/month |
| File Storage | Avatars, logos, banners, media | **AWS S3** | AWS credits | $0.023/GB/month |
| CDN | Fast global asset delivery | **AWS CloudFront** | AWS credits | $0.085/GB transfer |
| Image Processing | Resize, compress uploads | **Sharp (npm)** / **Pillow (pip)** | Free (open source) | N/A |
| Real-time | Chat, live updates, brackets | **Django Channels + Redis** | On EC2 (credits) | N/A |
| Push Notifications | Match reminders, alerts | **Firebase Cloud Messaging** | Unlimited free | Free |
| SMS / 2FA | OTP codes, critical alerts | **Termii** | Free test credits | From ₦4/SMS |
| Authentication | Google/Facebook login | **NextAuth.js** | Free (already in use) | Free |
| QR Codes | Event tickets, check-in | **qrcode (npm/pip)** | Free (open source) | N/A |
| Error Monitoring | Crash tracking | **Sentry** | 5K events/month free | From $26/month |
| Analytics | User behavior | **PostHog** | 1M events/month free | Usage-based |
| Database | Primary data store | **AWS RDS MySQL** | AWS credits | ~$15/month |
| Backend Hosting | Django API server | **AWS EC2** | AWS credits | ~$15/month |
| Frontend Hosting | Next.js app | **Vercel** | Free hobby tier | $20/month |
| Caching | API cache, session store | **AWS ElastiCache Redis** | AWS credits | ~$12/month |
| Background Jobs | Async tasks, scheduled jobs | **Celery** (on EC2) | Free (open source) | N/A |
| DNS | Domain management | **AWS Route 53** | AWS credits | $0.50/zone/month |
| SSL/Security | DDoS, WAF, SSL | **Cloudflare** (free tier) | Free | Free |

---

## 1. PAYMENTS & FINANCIAL

### Primary Payment Gateway: Paystack
- **Website:** https://paystack.com
- **Why Paystack over AWS:** AWS has no African payment gateway. Paystack is the gold standard in Nigeria — Stripe-backed, best docs, widest local payment method support.
- **Free tier:** No setup fee, no monthly fee — pay only per transaction
- **Fees:**
  - Local cards (Visa, Mastercard, Verve): 1.5% + ₦100 (capped at ₦2,000)
  - International cards: 3.9% + ₦100
  - Bank transfers: 1.5% (capped at ₦2,000)
  - USSD: 1.5% (capped at ₦2,000)
- **Django integration:** `pip install paystackapi` or use REST API directly
- **Key endpoints needed:**
  - `POST /transaction/initialize` — start payment (buying VENT COINS, registration fees)
  - `GET /transaction/verify/:reference` — confirm payment went through
  - `POST /transferrecipient/create` — set up payout recipient
  - `POST /transfer` — send money (prize distribution, payouts)
  - Webhook handler at your endpoint — real-time payment confirmation
- **Action needed:** Your codebase has a simulated Paystack flow in `Payment.js` — replace with real API
- **Setup steps:**
  1. Create account at https://dashboard.paystack.com
  2. Get test keys (public + secret) from dashboard
  3. Add `PAYSTACK_SECRET_KEY` and `PAYSTACK_PUBLIC_KEY` to Django `.env`
  4. Build webhook endpoint at `/api/payments/webhook/`
  5. Switch to live keys when ready for production

### Crypto Payouts: NOWPayments (for USDT payouts)
- **Website:** https://nowpayments.io
- **Why:** Supports 350+ cryptocurrencies, works in Nigeria, simple API
- **Free tier:** No setup fee, 0.5% per transaction
- **Use case:** When users request USDT payouts from their VENT COIN balance
- **When to integrate:** Phase 2+ (after basic wallet is working with Paystack)
- **Alternative:** Handle USDT payouts manually via admin dashboard initially

### Alternative Payment Gateway: Flutterwave
- **Website:** https://flutterwave.com
- **Why keep as backup:** Broader African coverage when expanding beyond Nigeria
- **Fees:** Local: 1.4% + 0.6% platform fee. International: 4.8%
- **When to use:** Phase 3+ when expanding to Ghana, Kenya, South Africa

---

## 2. EMAIL SERVICES

### AWS SES (Simple Email Service)
- **Why SES over Brevo/SendGrid:** You have $1,000 credits. SES is dirt cheap ($0.10/1K emails), excellent deliverability, scales infinitely. No free tier limits to hit.
- **Cost:** ~$1-5/month for MVP volume (covered by credits)
- **Supports:** Transactional emails, bulk/marketing emails, templates
- **Django integration:** `pip install django-ses boto3`
```python
# settings.py
EMAIL_BACKEND = 'django_ses.SESBackend'
AWS_SES_REGION_NAME = 'eu-west-1'
AWS_SES_REGION_ENDPOINT = 'email.eu-west-1.amazonaws.com'
AWS_ACCESS_KEY_ID = 'your-access-key'
AWS_SECRET_ACCESS_KEY = 'your-secret-key'
DEFAULT_FROM_EMAIL = 'noreply@v-ent.co'
```
- **Emails V-ENT needs to send:**
  - Email verification (registration)
  - Password reset links
  - Tournament/event registration confirmation
  - Match reminders and check-in alerts
  - Payment receipts (VENT COIN purchase, registration fees)
  - Team invitations
  - Payout confirmations
  - Tournament announcements (bulk)
  - Premium subscription confirmations
- **Setup steps:**
  1. Enable SES in AWS Console
  2. Verify your domain (`v-ent.co`) — add DNS records (SPF, DKIM, DMARC)
  3. Request production access (SES starts in sandbox — can only send to verified emails)
  4. Create IAM user with SES permissions
  5. Add credentials to Django `.env`
  6. Build HTML email templates matching V-ENT dark theme (#000 background, #4caf50 accent)

---

## 3. IP & GEOLOCATION

### IP Geolocation: ipinfo.io
- **Website:** https://ipinfo.io
- **Why ipinfo over AWS:** AWS has no IP geolocation service. ipinfo.io is industry standard with generous free tier.
- **Free tier:** 50,000 requests/month (more than enough for MVP)
- **What it returns:** IP address, city, region, country, coordinates, ISP, timezone
- **Use cases for V-ENT:**
  - Collect IP on every authenticated request (security/compliance)
  - Region-based tournament restrictions (premium feature)
  - Location-based event filtering
  - Fraud detection (multiple accounts from same IP)
  - Wager compliance (geo-restrictions)
- **Django integration:**
```python
import requests

def get_ip_info(ip_address):
    response = requests.get(f"https://ipinfo.io/{ip_address}/json?token=YOUR_TOKEN")
    return response.json()

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
```
- **Setup:** Sign up free → get API token → add `IPINFO_TOKEN` to Django `.env`

---

## 4. FILE STORAGE & CDN

### AWS S3 (Simple Storage Service)
- **Why S3:** You have AWS credits. S3 is the most battle-tested object storage, and with CloudFront in front of it, you get a world-class CDN.
- **Cost:** ~$2-5/month (covered by credits)
- **Django integration:** `pip install django-storages boto3`
```python
# settings.py
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
AWS_STORAGE_BUCKET_NAME = 'v-ent-media'
AWS_S3_REGION_NAME = 'eu-west-1'
AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
AWS_DEFAULT_ACL = 'public-read'
AWS_S3_FILE_OVERWRITE = False
```
- **Bucket structure:**
```
v-ent-media/              (public bucket — served via CloudFront)
├── avatars/
├── esports-images/
├── team-logos/
├── team-banners/
├── tournament-media/
├── event-media/
├── manga/                (Phase 5)
├── marketplace/          (Phase 4)
└── shop/                 (Phase 3)

v-ent-private/            (private bucket — signed URLs only)
├── exports/
├── receipts/
└── kyc-documents/
```

### AWS CloudFront (CDN)
- **Why:** Serves S3 files from edge locations globally — fast loading for African users
- **Cost:** ~$3-5/month (covered by credits)
- **Setup:** Create distribution → point to S3 bucket → use CloudFront URL in app

### Image Processing: Sharp (npm) + Pillow (Django)
- **Frontend:** `npm install sharp`
- **Backend:** `pip install Pillow`
- **Use cases:** Resize avatars (140px, 80px, 60px, 48px), compress banners, generate thumbnails, convert to WebP
- **Cost:** Free (open source, runs locally)

---

## 5. REAL-TIME FEATURES

### Django Channels + Redis (on EC2 / ElastiCache)
- **Why:** Free, open source, integrates natively with Django. Redis needed anyway for caching.
- **Install:** `pip install channels channels-redis daphne`
- **Use cases:**
  - Team in-app chat
  - Live tournament bracket updates
  - Real-time leaderboard during matches
  - Co-reading synchronization (Phase 5)
  - Live event attendance count
  - Wager live updates (Phase 6)
  - Admin dashboard live metrics
  - Notification delivery
- **Architecture:** Browser (WebSocket) → Daphne → Redis Channel Layer → Broadcast
- **Cost:** Free software — only EC2/ElastiCache costs (covered by credits)

---

## 6. PUSH NOTIFICATIONS

### Firebase Cloud Messaging (FCM)
- **Website:** https://firebase.google.com/products/cloud-messaging
- **Why FCM over AWS SNS:** FCM is completely free with unlimited messages. AWS SNS charges per notification.
- **Free tier:** Unlimited — FCM is free with no caps, ever
- **Use cases:** Match reminders, check-in alerts, team invitations, payment notifications, new chapters, wager results
- **Django:** `pip install firebase-admin`
```python
import firebase_admin
from firebase_admin import credentials, messaging

cred = credentials.Certificate("path/to/serviceAccountKey.json")
firebase_admin.initialize_app(cred)

def send_push(token, title, body, data=None):
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=data or {},
        token=token,
    )
    return messaging.send(message)
```
- **Setup:** Create Firebase project (free) → download service account key → add to Django

---

## 7. SMS & 2FA

### Primary 2FA: TOTP (Google Authenticator / Authy) — FREE
- **Django:** `pip install django-otp pyotp qrcode`
- **How:** User scans QR code with authenticator app → enters 6-digit code on login
- **Cost:** Completely free (no per-message cost)

### SMS Fallback: Termii
- **Website:** https://termii.com
- **Why Termii over AWS SNS:** Nigerian company, best local SMS rates, built-in OTP. AWS SNS doesn't handle Nigerian local SMS well.
- **Free tier:** Free test credits for development
- **Production cost:** From ₦4 per SMS
- **When to integrate:** After TOTP is working — SMS is the fallback
- **Alternative:** Africa's Talking (https://africastalking.com)

---

## 8. QR CODES

### QR Code Generation: qrcode (npm) + python-qrcode
- **Frontend:** `npm install qrcode`
- **Backend:** `pip install qrcode[pil]`
- **Use cases:** Event tickets, tournament check-in, marketplace listing links, team invite QR
- **Cost:** Free (open source, runs locally, no API calls)

---

## 9. ERROR MONITORING & LOGGING

### Error Tracking: Sentry
- **Website:** https://sentry.io
- **Why Sentry over CloudWatch:** Sentry is purpose-built for app error tracking (stack traces, breadcrumbs, user context). CloudWatch is for infrastructure monitoring.
- **Free tier:** 5,000 events/month, 1 user
- **Django:** `pip install sentry-sdk[django]`
- **Next.js:** `npm install @sentry/nextjs`

### Application Logging: AWS CloudWatch Logs
- **Why:** Already included with EC2 — no extra cost
- **Use for:** Django request logs, Celery task logs, system health
- **Cost:** Covered by AWS credits

---

## 10. ANALYTICS

### Product Analytics: PostHog
- **Website:** https://posthog.com
- **Why PostHog over AWS:** AWS has no equivalent product analytics. PostHog has session recordings, feature flags, funnels.
- **Free tier:** 1M events/month, session recordings, feature flags, A/B testing
- **Django:** `pip install posthog`
- **Next.js:** `npm install posthog-js`

---

## 11. STREAMING & PRODUCTION (Phase 1 Priority)

### OBS/VMIX/Streamlabs Integration
- **No external service needed** — you build web pages that streaming software loads as Browser Sources
- **What you build:**
```
/production/overlay/leaderboard?tournament_id=X    → transparent leaderboard overlay
/production/overlay/bracket?tournament_id=X        → live bracket visualization
/production/overlay/mvp?tournament_id=X            → MVP stats card overlay
/production/overlay/timer?tournament_id=X          → match countdown timer
/production/overlay/lower-third?tournament_id=X    → player/team info bar
/production/control?tournament_id=X                → organizer control panel
```
- **Requirements:** Transparent backgrounds, WebSocket for real-time updates, V-ENT branding
- **Cost:** $0 — just web pages served from existing infrastructure

### AI Screen Scanning (future)
- **Tesseract.js** (free OCR) + browser Screen Capture API
- **Cost:** Free (open source)

---

## 12. MAPS & LOCATION

### Leaflet.js + OpenStreetMap — FREE
- **Install:** `npm install leaflet react-leaflet`
- **Why over Google Maps:** Completely free, no API key, no usage limits
- **Use cases:** Event venue maps, "Get Directions" button, location picker for events

---

## 13. KYC / IDENTITY VERIFICATION

### Smile ID
- **Website:** https://smileidentity.com
- **Why:** Africa-focused, supports Nigerian NIN, BVN, passport, driver's license
- **Free tier:** Free sandbox for testing
- **Production cost:** Pay per verification (~$0.10-0.50 per check)
- **When to integrate:** When wallet payouts go live (Phase 2)
- **Alternative:** Dojah (https://dojah.io)

---

## 14. SEARCH

### MVP: Django ORM Search — FREE
```python
Tournament.objects.filter(
    Q(name__icontains=query) | Q(game__name__icontains=query)
).order_by('-created_at')
```
- Start here — works fine for <10K records

### Scale-up: AWS OpenSearch (when needed)
- **Cost:** ~$25/month (covered by credits)
- **When:** 10K+ tournaments/events/listings and ORM search becomes slow

---

## 15. CACHING

### AWS ElastiCache Redis
- **Instance:** cache.t3.micro (~$12/month, covered by credits)
- **Use cases:** Cache tournament listings, user profiles, rate limiting counters, session storage, WebSocket channel layer, leaderboard sorting
- **Django:**
```python
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://your-elasticache-endpoint:6379/0',
    }
}
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': { 'hosts': [('your-elasticache-endpoint', 6379)] },
    },
}
```
- **Alternative (save credits):** Install Redis directly on EC2 ($0 extra)

---

## 16. TASK QUEUES

### Celery + Redis (on EC2)
- **Install:** `pip install celery django-celery-beat`
- **Use cases:** Async emails, image processing, tournament standings calculation, scheduled reminders, prize distribution, OCR processing, PDF/Excel exports, ranking recalculation
- **Cost:** Free (open source, runs on EC2)

---

## 17. SECURITY SERVICES

### Rate Limiting: django-ratelimit — FREE
```python
@ratelimit(key='ip', rate='5/m', method='POST')    # Login
@ratelimit(key='ip', rate='3/h', method='POST')    # Registration
@ratelimit(key='user', rate='100/m', method='GET')  # API
```

### CAPTCHA: Cloudflare Turnstile — FREE
- **Website:** https://www.cloudflare.com/products/turnstile/
- Invisible CAPTCHA (better UX than reCAPTCHA). Use on registration, login (after failures), password reset.

### DDoS / WAF / SSL: Cloudflare (Free Tier)
- Free DDoS protection, SSL, basic WAF, CDN, bot protection
- **Setup:** Point domain nameservers to Cloudflare → proxies traffic to AWS

### AWS Security (included):
- **IAM:** Role-based access for team
- **Security Groups:** Firewall (restrict RDS to EC2 only)
- **Secrets Manager:** Store API keys ($0.40/secret/month — credits)
- **CloudTrail:** Audit log of all AWS API calls (free)

---

## 18. AI FEATURES (Phase 1+)

### OCR: Tesseract — FREE
- **Backend:** `pip install pytesseract` + `sudo apt-get install tesseract-ocr`
- **Frontend:** `npm install tesseract.js`

### Chatbot: Anthropic API (Claude)
- **Model:** Claude Sonnet (~$3 per 1M input tokens)
- **Rule:** NEVER call API from frontend — always proxy through Django
- Track prompts per user in DB, enforce monthly limits

---

## 19. HOSTING & INFRASTRUCTURE

### Frontend: Vercel (Free)
- Built for Next.js, zero-config, preview deployments on every PR
- Free hobby tier: 100GB bandwidth

### Backend: AWS EC2 t3.small (~$15/month from credits)
- Runs: Django (Gunicorn + Nginx), Daphne (WebSockets), Celery worker, Celery Beat
- **Setup:** Ubuntu 24.04 → Python 3.12 → MySQL client → Redis → Nginx → Certbot (SSL)

### Database: AWS RDS MySQL db.t3.micro (~$15/month from credits)
- Automated backups, point-in-time recovery, automatic patching
- Security group: only allow connections from EC2

### Staging: AWS EC2 t3.micro (~$8/month from credits)
- Separate instance for testing before production deployment

---

## 20. DEVELOPMENT & TESTING

| Tool | Purpose | Cost |
|------|---------|------|
| **Postman** | API testing | Free |
| **AWS SES Sandbox** | Email testing (dev) | Free |
| **Mailtrap** | Alternative email testing | 100/month free |
| **GitHub Actions** | CI/CD pipeline | 2,000 min/month free |
| **ESLint + Prettier** | Code quality | Free |

---

## Module → Service Mapping

| Module | AWS Services | Third-Party Services |
|--------|-------------|---------------------|
| **01 Tournaments** | EC2, RDS, S3, SES, ElastiCache, CloudFront | Paystack, FCM, Sentry |
| **02 Events** | EC2, RDS, S3, SES, ElastiCache | Paystack, FCM, QR (local) |
| **03 Production** | EC2 (overlays), ElastiCache | Tesseract (local), OBS (user software) |
| **04 Teams** | EC2, RDS, S3, ElastiCache | FCM |
| **05 User System** | EC2, RDS, S3, SES, ElastiCache | ipinfo.io, Turnstile, TOTP/Termii |
| **06 Wallet** | EC2, RDS, SES | Paystack, NOWPayments, Smile ID (KYC) |
| **07 Organizations** | EC2, RDS, S3, SES | FCM |
| **08 Shop** | EC2, RDS, S3, SES, CloudFront | Paystack, shipping (GIG/Sendbox) |
| **09 Marketplace** | EC2, RDS, S3, SES | Paystack |
| **10 Anime** | EC2, RDS, S3, CloudFront, ElastiCache | Paystack |
| **11 Wager** | EC2, RDS, SES, ElastiCache | Paystack, ipinfo.io, Smile ID |
| **12 AI Features** | EC2, S3 | Anthropic API, Tesseract (local) |
| **13 Admin** | EC2, RDS, S3, CloudWatch | Sentry, PostHog |
| **14 Community** | EC2, RDS, S3, ElastiCache | FCM |
| **15 Security** | EC2, CloudTrail, Secrets Manager, CloudWatch | ipinfo.io, Turnstile, Cloudflare, Sentry |

---

## Setup Priority Order

### Set up IMMEDIATELY (before coding):
1. **AWS Account** — apply $1,000 credits
2. **EC2 t3.small** — Django backend server
3. **RDS MySQL db.t3.micro** — production database
4. **S3 buckets** — media storage (public + private)
5. **Cloudflare** — DNS proxy, SSL, DDoS (free)
6. **Sentry** — error tracking (free tier)
7. **GitHub Actions** — CI/CD (free)
8. **Vercel** — frontend deployment (free)

### Set up for Phase 1 (Tournaments):
9. **AWS SES** — emails (verify domain first — takes 24-48hrs)
10. **ElastiCache Redis** — caching + WebSocket channel layer
11. **CloudFront** — CDN for S3 media
12. **Paystack** — payment gateway (test mode first)
13. **Firebase Cloud Messaging** — push notifications
14. **ipinfo.io** — IP geolocation
15. **PostHog** — product analytics

### Set up for Phase 2 (Events):
16. **QR code library** (local install)
17. **Leaflet.js** — maps (local install)
18. **Celery Beat** — scheduled reminders

### Set up later (Phase 3+):
19. **Smile ID / Dojah** — KYC (when payouts go live)
20. **Termii** — SMS 2FA fallback
21. **NOWPayments** — crypto USDT payouts
22. **Anthropic API** — AI chatbot/assistant
23. **AWS OpenSearch** — full-text search (when content volume grows)

---

## AWS Architecture Diagram

```
                    ┌─────────────┐
                    │  Cloudflare  │  ← DDoS, WAF, SSL (FREE)
                    │  (DNS Proxy) │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
      ┌───────▼───────┐        ┌───────▼───────┐
      │    Vercel      │        │  AWS EC2       │
      │  (Next.js)     │        │  (Django API)  │
      │  FREE tier     │        │  t3.small      │
      └───────┬───────┘        │  + Celery      │
              │                 │  + Daphne (WS) │
              │ API calls       └───────┬───────┘
              └─────────────────────────┤
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
            ┌───────▼──────┐   ┌───────▼──────┐   ┌───────▼──────┐
            │  AWS RDS      │   │  ElastiCache  │   │  AWS S3       │
            │  MySQL 8.0    │   │  Redis        │   │  + CloudFront │
            │  db.t3.micro  │   │  t3.micro     │   │  (Media CDN)  │
            └──────────────┘   └──────────────┘   └──────────────┘

External Services (not on AWS):
├── Paystack (payments)
├── ipinfo.io (geolocation)
├── Firebase Cloud Messaging (push notifications)
├── Sentry (error tracking)
├── PostHog (analytics)
├── Cloudflare Turnstile (CAPTCHA)
└── Smile ID (KYC — when needed)
```

---

## Monthly Cost Summary

| Service | Provider | Monthly Cost | Source |
|---------|----------|-------------|--------|
| Django + Celery server | AWS EC2 t3.small | $15 | AWS Credits |
| MySQL database | AWS RDS db.t3.micro | $15 | AWS Credits |
| File storage (50GB) | AWS S3 | $2-5 | AWS Credits |
| CDN | AWS CloudFront | $3-5 | AWS Credits |
| Email (50K/month) | AWS SES | $5 | AWS Credits |
| Redis cache | AWS ElastiCache t3.micro | $12 | AWS Credits |
| DNS | AWS Route 53 | $1 | AWS Credits |
| Staging server | AWS EC2 t3.micro | $8 | AWS Credits |
| **AWS Total** | | **~$61-66/month** | **Credits last ~15 months** |
| | | | |
| Frontend hosting | Vercel | $0 | Free tier |
| DDoS/WAF/SSL | Cloudflare | $0 | Free tier |
| Error tracking | Sentry | $0 | Free tier |
| Analytics | PostHog | $0 | Free tier |
| IP geolocation | ipinfo.io | $0 | Free tier |
| Push notifications | Firebase | $0 | Free forever |
| CAPTCHA | Cloudflare Turnstile | $0 | Free forever |
| QR codes | Local library | $0 | Open source |
| Image processing | Sharp/Pillow | $0 | Open source |
| CI/CD | GitHub Actions | $0 | Free tier |
| **Third-Party Total** | | **$0/month** | |
| | | | |
| Payments | Paystack | Per transaction | 1.5% + ₦100 |
| **Grand Total (fixed)** | | **~$61-66/month** | **All from AWS credits** |

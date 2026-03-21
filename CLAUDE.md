# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Companion Repos

| Repo | Path | Notes |
|------|------|-------|
| Frontend (Next.js) | `C:\Users\Sweez\Desktop\LAYO\CLAUDE\V-ENT\V-ENT-FRONTEND` | Calls this backend via `NEXT_PUBLIC_API_URL` |
| Backend (Django) | `C:\Users\Sweez\Desktop\LAYO\CLAUDE\V-ENT\V-ENT-BACKEND` | This repo |

**All API responses must follow the shape the frontend expects:**
```json
{ "status": "success" | "error", "data": {...}, "message": "..." }
```

**Auth:** The frontend passes `Authorization: Bearer <session_token>` on authenticated requests. `session_token` maps to `Users.login_session_token` (16-char field on the custom Users model), not a DRF Token. Verify it on every protected endpoint.

---

## Commands

```bash
# Run development server
python manage.py runserver

# Apply migrations
python manage.py migrate

# Create migrations after model changes
python manage.py makemigrations

# Django shell
python manage.py shell

# Install dependencies
pip install -r requirements.txt

# Create superuser
python manage.py createsuperuser
```

No test suite is configured.

---

## Project Overview

V-ENT Backend is a **Django 5.0.7 / Python 3.11.9** REST API for an esports/gaming community platform (Vermillion Entertainment) targeting the African market. Framework is Django REST Framework 3.15.2. Database is MySQL.

---

## Architecture

### Settings & Config
- `vent/settings.py` — Main Django settings. Database, installed apps, CORS, auth backends, media, social providers.
- `config.py` — SMTP config and email constants. **Currently has hardcoded credentials — see Known Issues.**
- `vent/urls.py` — Root router, mounts all app URL confs.

### App Map
| App | Mount | Status | Purpose |
|-----|-------|--------|---------|
| `vent_auth` | `/auth/` | **Active** | Users, profiles, social auth, games, teams, wallets, waitlist |
| `vent_tournament` | `/tournament/` | **Active** | Tournament CRUD, registration, matches |
| `vent_event` | `/event/` | **Active** | Events |
| `vent_team` | `/team/` | **Active** | Team management |
| `vent_anime` | — | **Stub** | In `INSTALLED_APPS`, no views or URLs |
| `vent_marketplace` | — | **Stub** | In `INSTALLED_APPS`, no views or URLs |
| `vent_shop` | — | **Stub** | Not in `INSTALLED_APPS` |
| `vent_wallet` | — | **Stub** | Not in `INSTALLED_APPS` |

### Authentication Flow
1. **Custom backend** (`vent_auth/backends.py`): `EmailOrUsernameModelBackend` — allows login with email or username.
2. Custom `login_session_token` (16-char, stored on `Users` model) is generated on login and used by the frontend as the Bearer token. This is separate from DRF Token auth.
3. **Social auth** via `django-allauth` (Google OAuth2, Facebook). There is also a manual Google OAuth flow using `get_google_login_url` / `google_callback` / `verify_google_token`.
4. Email verification via `VerificationToken` model — 2-hour expiry, link sent via Gmail SMTP.
5. `dj-rest-auth` + DRF Token auth are installed but the frontend primarily uses `login_session_token` as Bearer.

### Important: Duplicate Teams Model
`Teams` is defined in **both** `vent_auth/models.py` and `vent_team/models.py`. They have different `related_name`s to avoid clashes (`vent_auth_teams` / `vent_team_teams`). The active app uses `vent_auth.Teams`. The `vent_team.Teams` is a near-duplicate. **Do not add new logic to `vent_team.Teams` without reconciling this.**

### Media & File Uploads
- `MEDIA_ROOT` → `media/` folder in project root.
- Uploads go to subfolders: `profile_pictures/`, `banners/`, `gallery/`, `team_logos/`, `team_banners/`, `tournament_logos/`, `tournament_banners/`, `event_logos/`, `event_banners/`, `game_logos/`, `achievements/`, `sponsor_logos/`.
- **Currently local storage.** Migration path → **AWS S3 via `django-storages`** (see External Services).

### Email
- Dev: Gmail SMTP via `config.py` (`smtp.gmail.com:465`) using app-specific password.
- Production target: **AWS SES** (see External Services).

---

## All API Endpoints

### `/auth/` — vent_auth

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/signup/` | Register new user |
| GET | `/auth/verify/<uidb64>/<token>/` | Email verification link |
| POST | `/auth/login/` | Login (email or username + password) |
| POST | `/auth/logout/` | Logout |
| POST | `/auth/change-fullname/` | Update display name |
| POST | `/auth/change-email/` | Initiate email change |
| POST | `/auth/verify-new-email/` | Confirm new email with code |
| POST | `/auth/verify-token-2/` | Verify email token (flow 2) |
| POST | `/auth/forgot-password/send-token/` | Send password reset token |
| POST | `/auth/forgot-password/verify-token/` | Verify password reset token |
| POST | `/auth/forgot-password/change-password/` | Set new password after reset |
| POST | `/auth/send-code/` | Send verification code |
| POST | `/auth/save-username/` | Save username after signup |
| POST | `/auth/admin/login/` | Admin login endpoint |
| GET | `/auth/admin/get-all-username-and-email/` | Admin: list all users |
| GET | `/auth/admin/user-count/` | Admin: total user count |
| GET | `/auth/admin/check-username-availability/` | Check if username is taken |
| GET | `/auth/get-username-with-email/` | Lookup username by email |
| POST | `/auth/edit-profile-info/` | Update profile (bio, DOB, etc.) |
| GET | `/auth/get-user-informations/` | Fetch full user profile |
| GET | `/auth/get-user-status/` | Check user account status |
| POST | `/auth/add-email-to-waitlist/` | Add email to waitlist |
| POST | `/auth/update-web-and-social-links/` | Update social/website links |
| POST | `/auth/social-auth/` | Social auth entry point |
| POST | `/auth/edit-favorite-games/` | Update favorite games list |
| POST | `/auth/resend-link/` | Resend email verification link |
| POST | `/auth/resend-forgot-password-token/` | Resend password reset token |
| GET | `/auth/get-google-login-url/` | Get Google OAuth redirect URL |
| GET | `/auth/google-callback/` | Google OAuth callback handler |
| POST | `/auth/verify-google-token/` | Verify Google ID token |
| POST | `/auth/upload-images/` | Upload profile/gallery images |
| GET | `/auth/get-user-gallery/` | Get user's gallery images |
| DELETE | `/auth/delete-gallery-image/` | Delete a gallery image |
| — | `/auth/dj-rest-auth/` | dj-rest-auth built-in routes |
| POST | `/auth/dj-rest-auth/google/` | Google login via dj-rest-auth |
| — | `/auth/accounts/` | django-allauth routes |

### `/tournament/` — vent_tournament

| Method | Path | Description |
|--------|------|-------------|
| POST | `/tournament/create-tournament/` | Create tournament (supports draft) |
| GET | `/tournament/search-tournament/` | Search tournaments |
| POST | `/tournament/join-tournament/` | Join/register for tournament |
| GET | `/tournament/get-all-tournaments/` | List all published tournaments |
| GET | `/tournament/view-tournament/<int:tournament_id>/` | Get tournament details |
| GET | `/tournament/view-user-drafted-tournaments/` | Get current user's drafts |

### `/event/` — vent_event

| Method | Path | Description |
|--------|------|-------------|
| POST | `/event/create-event/` | Create event |
| GET | `/event/get-all-events/` | List all events |

### `/team/` — vent_team

| Method | Path | Description |
|--------|------|-------------|
| POST | `/team/create-team/` | Create team |
| GET | `/team/get-team-details/<int:team_id>/` | Get team details |
| POST | `/team/transfer-ownership/` | Transfer team ownership |
| POST | `/team/assign-new-role/` | Assign member role |
| POST | `/team/remove-member/` | Remove team member |

---

## All Models and Fields

### `vent_auth` — models.py

**`Users`** (custom AbstractUser)
```
user_id              AutoField PK
full_name            CharField(148) null
username             CharField(128) unique
email                EmailField
password             CharField(256) null
country              CharField(256) null
state                CharField(256) null
login_session_token  CharField(16) null      ← frontend Bearer token
login_session_created_at  DateTimeField null
signup_type          CharField(32) default='normal'  # normal|google|facebook
provider_id          CharField(256) null
tst                  CharField(44) null
social_id            CharField(100) null
+ all AbstractUser fields (is_active, is_staff, date_joined, etc.)
```

**`UserProfile`**
```
profile_id       AutoField PK
user             FK(Users) CASCADE
profile_picture  ImageField(profile_pictures/)
date_of_birth    DateField null
banner           ImageField(banners/) null
description      CharField(140) null
penalty_point    IntegerField default=0
```

**`UserInterests`**
```
user       FK(Users) CASCADE
interests  CharField(30)
```

**`UserGallery`**
```
user        FK(Users) CASCADE
image       ImageField(gallery/) null
date_added  DateTimeField auto_now_add
```

**`VerificationToken`**
```
user_email  EmailField unique
token       CharField(64)
created_at  DateTimeField auto_now_add
```
`is_valid()` → expires after 120 minutes.

**`UserCommunity`**
```
user          FK(Users) CASCADE
is_gamer      BooleanField default=False
is_anime_enth BooleanField default=False
```

**`Genres`**
```
genre_id    AutoField PK
genre_name  CharField(40)
```

**`Games`**
```
game_id      AutoField PK
game_title   CharField(40) unique
description  TextField null
logo         ImageField(game_logos/) null
```

**`Achievement`**
```
name         CharField(100)
description  TextField null
logo         ImageField(achievements/) null
awarded_to   M2M(Users, related_name='achievements')
```

**`UserGameStats`**
```
user   FK(Users) CASCADE
game   FK(Games) CASCADE
kills  IntegerField default=0
```
`add_kills(n)` → auto-awards "100 Kills" achievement.

**`UserGenre`**
```
user   FK(Users) CASCADE
genre  FK(Genres) CASCADE
```

**`FavoriteGames`**
```
user  FK(Users) CASCADE
game  FK(Games) CASCADE
```

**`Teams`** (in vent_auth — the active one)
```
team_id                   AutoField PK
team_name                 CharField(60) unique
team_logo                 ImageField(teams_logos/) null
team_banner               ImageField(teams_banners/) null
game                      FK(Games) CASCADE, related_name='vent_auth_teams'
description               TextField
allow_membership_requests BooleanField default=True
creation_date             DateField default=now
team_creator              FK(Users) CASCADE, related_name='vent_auth_created_teams'
team_owner                FK(Users) CASCADE, related_name='vent_auth_owned_teams'
penalty_points            IntegerField
number_of_members         IntegerField
```

**`TeamProfile`** (vent_auth version)
```
team_profile_id   AutoField PK
team              OneToOne(Teams) CASCADE
matches           IntegerField default=0
tournament_played IntegerField default=0
```

**`TeamMembers`** (vent_auth version)
```
team_member_id  AutoField PK
team            FK(Teams) CASCADE
user            FK(Users) CASCADE
is_captain      BooleanField default=False
join_date       DateField default=now
```

**`GameAccount`**
```
game_account_id  AutoField PK
user             FK(Users) CASCADE
game             FK(Games) CASCADE
game_username    CharField(20)
```

**`Organization`**
```
org_id      AutoField PK
org_name    CharField(148) unique
org_creator FK(Users) CASCADE, related_name='created_organizations'
org_owner   FK(Users) CASCADE, related_name='owned_organizations'
```

**`UserWallet`**
```
user_wallet_id   CharField(10) PK
user             OneToOne(Users) CASCADE, related_name='wallet'
wallet_balance   IntegerField default=0
user_wallet_pin  IntegerField null
```

**`TeamWallet`**
```
team_wallet_id   CharField(10) PK
team             OneToOne(Teams) CASCADE, related_name='wallet'
wallet_balance   IntegerField default=0
team_wallet_pin  IntegerField null
```

**`OrgWallet`**
```
org_wallet_id   CharField(10) PK
org             OneToOne(Organization) CASCADE, related_name='wallet'
wallet_balance  IntegerField default=0
org_wallet_pin  IntegerField null
```

**`SocialLink`**
```
user   FK(Users) CASCADE, related_name='social_links'
title  CharField(100)
url    URLField(200)
```

**`Waitlist`**
```
email        EmailField unique
is_notified  BooleanField default=False
join_date    DateTimeField auto_now_add
```

---

### `vent_tournament` — models.py

**`Tournament`**
```
tournament_id           AutoField PK
tournament_title        CharField(148)
tournament_game         FK(Games) CASCADE
game_mode               CharField(50) null
tournament_logo         ImageField(tournament_logos/)
tournament_banner       ImageField(tournament_banners/)
tournament_description  TextField null
tournament_rules        TextField null
bracket_type            CharField(50) default='Single Elimination'
tournament_creator      FK(Users) CASCADE, related_name='tournament_creator'
tournament_organization FK(Organization) CASCADE null
start_date_and_time     DateTimeField
end_date_and_time       DateTimeField
tournament_visibility   CharField choices: public|private|protected
tournament_type         CharField choices: online|physical|hybrid
tournament_location     CharField(255) null
virtual_link            URLField null
team_size               PositiveIntegerField default=1
player_size             IntegerField null
min_number_of_teams     IntegerField null
max_number_of_teams     IntegerField null
prize_type              CharField choices: distributed|winner_takes_all|no_prize
tournament_access       CharField choices: team|individual|team_and_individual
entry_fee               CharField choices: Paid|Free
entry_fee_price         DecimalField(10,2) default=0.00
facebook_link           URLField null
twitter_link            URLField null
instagram_link          URLField null
youtube_link            URLField null
twitch_link             URLField null
kick_link               URLField null
tiktok_link             URLField null
bigolive_link           URLField null
sponsors                M2M(Sponsors)
interaction_count       PositiveIntegerField default=0
is_draft                BooleanField default=True
```

**`TournamentPrizeDistribution`**
```
id          AutoField PK
tournament  FK(Tournament) CASCADE, related_name='prize_distributions'
position    IntegerField
prize       DecimalField(10,2)
extras      CharField(40)
```

**`Sponsors`**
```
sponsor_id        AutoField PK
name              CharField(255)
sponsor_type      FK(ContentType) CASCADE null   ← GenericFK
sponsor_id_object PositiveIntegerField null
logo              ImageField(sponsor_logos/) null
website           URLField null
```

**`RegisteredTeams`**
```
tournament_id  FK(Tournament) CASCADE, related_name='registered_teams'
team_id        FK(Teams) CASCADE
```

**`Match`**
```
match_id              AutoField PK
tournament            FK(Tournament) CASCADE, related_name='matches'
match_check_in_time   TimeField
match_check_in_date   DateField
match_check_in_started BooleanField default=False
match_check_in_ended   BooleanField default=False
```

**`UnconfirmedTeams`**
```
match_id  AutoField PK   ← misnamed, functions as unconfirmed team ID
team_id   FK(Teams) CASCADE
```

---

### `vent_event` — models.py

**`Event`**
```
event_id         AutoField PK
name             CharField(40)
game             FK(Games) CASCADE, related_name='events'
creator          FK(Users) CASCADE
created_at       DateTimeField auto_now_add
last_updated     DateTimeField auto_now
event_type       CharField(8)   # physical|virtual|hybrid
desc             CharField(140)
entry_fee        DecimalField(10,2)
reg_start_date   DateTimeField
reg_end_date     DateTimeField
event_date       DateField
start_time       TimeField
end_time         TimeField
location         CharField(255) null
event_link       CharField(255) null
logo             ImageField(event_logos/) null
banner           ImageField(event_banners/) null
is_active        BooleanField default=True
interaction_count PositiveIntegerField default=0
```

**`Sponsor`** (vent_event — event-specific, different from vent_tournament.Sponsors)
```
sponsor_id  AutoField PK
event       FK(Event) CASCADE, related_name='sponsors'
name        CharField(100)
logo        ImageField(sponsor_logos/)
```

**`SocialLink`** (vent_event — event-specific)
```
social_link_id  AutoField PK
event           FK(Event) CASCADE, related_name='social_links'
platform        CharField(50)
url             URLField
```

---

### `vent_team` — models.py

**`Teams`** (duplicate of vent_auth.Teams — see Known Issues)
```
team_id                   AutoField PK
team_name                 CharField(60) unique
team_logo                 ImageField(teams_logos/) null
team_banner               ImageField(teams_banners/) null
game                      FK(Games) CASCADE, related_name='vent_team_teams'
description               TextField
allow_membership_requests BooleanField default=True
creation_date             DateField default=now
team_creator              FK(Users) CASCADE, related_name='vent_team_created_teams'
team_owner                FK(Users) CASCADE, related_name='vent_team_owned_teams'
penalty_points            IntegerField
number_of_members         IntegerField
```

**`TeamProfile`** (richer than vent_auth version — adds country + social links)
```
team_profile_id  AutoField PK
team             FK(Teams) CASCADE
country          CharField(40)
facebook_link    URLField null
twitter_link     URLField null
instagram_link   URLField null
youtube_link     URLField null
twitch_link      URLField null
kick_link        URLField null
```

**`TeamInterests`**
```
team       FK(Teams) CASCADE
interests  CharField(40)
```

**`TeamMembers`** (richer than vent_auth version — has full role choices)
```
team    FK(Teams) CASCADE
member  FK(GameAccount) CASCADE
role    CharField choices: captain|vice_captain|member|coach|manager|analyst
```

---

## External Services

This backend integrates with or is planned to integrate with the following services. Before suggesting alternatives, check if one of these already covers the need.

| Service | Purpose | Status |
|---------|---------|--------|
| **AWS RDS MySQL** | Production database (replaces localhost MySQL) | Planned |
| **AWS S3** | Media/file storage via `django-storages` — buckets: `v-ent-media` (public), `v-ent-private` (private) | Planned |
| **AWS CloudFront** | CDN in front of S3 | Planned |
| **AWS SES** | Transactional email (replaces Gmail SMTP) | Planned |
| **AWS ElastiCache (Redis)** | Celery broker + Django cache backend | Planned |
| **AWS EC2** | Hosting — t3.small, runs Django + Celery + Daphne | Planned |
| **Paystack** | All payment flows — Nigerian gateway. Never simulate payments. Never add a second payment provider. | Planned |
| **Firebase Admin SDK** | Push notifications (FCM) | Planned |
| **ipinfo.io** | IP geolocation | Planned |
| **Sentry** | Error tracking | Planned |
| **PostHog** | Analytics | Planned |

> **AWS-first rule:** Use AWS services where possible — covered by $1,000 AWS credit budget (~$61–66/month, ~15 months).
>
> Reference: `docs/V-ENT_External_Tools_and_Services.md` in the frontend repo for the full external services doc.

---

## Build Priority

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1 MVP** | Tournament brackets + join/leave, production/streaming integration (OBS/VMIX/Streamlabs), wallet system (buy/send VENT COINS, payouts), admin dashboard (user mgmt, tournament oversight, payout approval) | 🔴 In progress |
| **Phase 2** | Events + ticketing + tournament-event linking + vendor shop system | Not started |
| **Phase 3** | E-Commerce Shop (Vent Shop) | Not started |
| **Phase 4** | Marketplace (Vermillion City) | Not started |
| **Phase 5** | Anime Features (manga, AMV, co-reading) | Not started |
| **Phase 6** | Wager System — build LAST, legal review required first | Not started |

Admin dashboard must ship in Phase 1 MVP. Do not start Phase 2 until Phase 1 is stable.

---

## Verification Protocol

Every new endpoint or model must follow one of these tracks before it's considered done:

- **Track A (Frontend Figma screen exists):** Confirm API contract with frontend Figma spec → Build endpoint → Test with frontend → Mark **VERIFIED**
- **Track B (New feature, no Figma):** Define request/response shape → Get approval → Build → Integration test with frontend → Mark **VERIFIED (SELF-DESIGNED)**
- **Track C (Backend/infrastructure only):** Standard code review, no frontend integration needed → Mark **VERIFIED**

Never build an endpoint that is not coordinated with the frontend contract in `src/constants/vent.js` (frontend repo).

---

## Known Issues

### Security (Fix before production)

1. **Hardcoded MySQL password** in `vent/settings.py:132` — `Trust.2308` committed to git. Rotate immediately and move to environment variable.
2. **Hardcoded Django SECRET_KEY** in `vent/settings.py:28` — committed to git. Rotate immediately.
3. **Hardcoded Google OAuth credentials** in `vent/settings.py:192-193` — `client_id` and `secret` committed to git. Also: the second `SOCIALACCOUNT_PROVIDERS` block at line 198 **overwrites** the first, so Google credentials are currently non-functional.
4. **Hardcoded Gmail SMTP password** `rglb ssfs xhip psma` in `config.py:4` and a second password `xxcn nisk evik iisc` hardcoded inside `vent_auth/views.py` — both committed to git. Revoke app passwords and move to AWS SES + environment variables.
5. **`DEBUG = True`** in `vent/settings.py:31` — exposes full tracebacks. Must be `False` in production, controlled by environment variable.
6. **`ALLOWED_HOSTS = ['*']`** in `vent/settings.py:33` — allows any host header. Lock down to actual domains in production.
7. **`CORS_ORIGIN_ALLOW_ALL = True`** in `vent/settings.py:79` — overrides the `CORS_ALLOWED_ORIGINS` whitelist beneath it. Remove `CORS_ORIGIN_ALLOW_ALL` and rely on `CORS_ALLOWED_ORIGINS`.
8. **No `.env` file** — `python-dotenv` is installed but never used. All secrets are inline. Migrate all secrets to a `.env` file (never commit it).

### Model / Data Issues

9. **Duplicate `Teams` model** — defined in both `vent_auth/models.py` and `vent_team/models.py` with different `related_name`s. Both create separate DB tables. This is confusing and will cause bugs. Decide on one canonical model; the other should be removed or made a proxy.
10. **`UnconfirmedTeams.match_id`** — the primary key field is named `match_id` but this model has no FK to `Match`. Appears to be a copy-paste error from the `Match` model.
11. **`TeamProfile` exists in two apps** — `vent_auth.TeamProfile` (tracks matches/tournaments_played) and `vent_team.TeamProfile` (tracks country + social links). No single model has all fields.
12. **`wallet_balance` is `IntegerField`** — stores currency as whole numbers. If VENT COINS is ever a decimal currency, this needs to change to `DecimalField`.
13. **`UserProfile.date_of_birth`** has no `null=True, blank=True` — the field is `null=True` but not `blank=True`, meaning the Django form will require it even though the DB allows null. Fix: add `blank=True`.
14. **`Users.email` is not `unique=True`** — EmailField without `unique=True`. Multiple users can register with the same email.

### Settings Issues

15. **Double `SOCIALACCOUNT_PROVIDERS` definition** (`settings.py:189` and `settings.py:198`) — the second block replaces the first. The actual Google `client_id`/`secret` from the first block is silently lost. The second block only sets `SCOPE` and `AUTH_PARAMS`. Merge them into one.
16. **`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` placeholders** in `settings.py:215-216` — set to `'your_email@gmail.com'`/`'your_email_password'`. Email via settings.py currently won't work; the actual credentials are in `config.py` and used directly in views via `smtplib`. Consolidate to one email approach.

### Architecture Issues

17. **`vent_auth/views.py` is ~2200 lines** — all auth, profile, social, gallery, and utility logic in one file. Hard to navigate. Split into feature-specific view files as new endpoints are added.
18. **No `.env` usage** — `python-dotenv` is in requirements but never loaded. Add `load_dotenv()` to `manage.py` and `wsgi.py`.
19. **Flask installed but unused** — `Flask`, `Flask-Login`, `Flask-Mail`, `Flask-SQLAlchemy`, `Flask-WTF` are in `requirements.txt`. This is a Django project. Remove these to reduce install size and avoid confusion.
20. **Celery configured but no tasks defined** — `celery` and `redis` are installed. No `celery.py` or `tasks.py` exists in any app. Infrastructure is ready but no background jobs are wired up yet.
21. **`delete_user_data.py`** — standalone script in project root that deletes all user data. Should be converted to a Django management command with `--dry-run` support before production use.
22. **`vent_shop` and `vent_wallet` not in `INSTALLED_APPS`** — these apps exist on disk but are not registered. Either add them or delete them.
23. **`imports/` folder** — purpose unknown. No documentation. Investigate before shipping.
24. **Media files committed to git** — `media/` folder appears to be in the repo. Add `media/` to `.gitignore`.

---

## Key Conventions

- Views are function-based with `@api_view` decorators throughout.
- Wallet objects (`UserWallet`, `TeamWallet`) are created automatically via helper functions in `vent_auth/views.py` — do not create them manually.
- Profile pictures are auto-generated from user initials using PIL when no image is uploaded.
- URL names use `snake_case` strings. No `reverse()` name lookups enforced.
- Tournament `is_draft=True` by default — must be explicitly published.
- All apps' `urls.py` append `static(settings.MEDIA_URL, ...)` — this is a dev-only pattern; it won't serve files in production with a real web server.

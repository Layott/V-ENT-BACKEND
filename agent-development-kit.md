# Agent Development Kit - V-ENT-BACKEND

Repo-specific application of the five-layer blueprint. Full framework + truth/accuracy/verification/best-practice rules live at `../agent-development-kit.md`. This file documents only how the layers map to this Django repo.

**Repo:** Django 5.0.7 + DRF 3.15.2 + MySQL + Celery + Redis. Python 3.11.9.

**Frontend:** separate repo (`../V-ENT-FRONTEND/`). Calls this API via `NEXT_PUBLIC_API_URL`. Every response must follow `{ status: "success" | "error", data: {...}, message: "..." }`.

---

## Stack

| Layer | Tech |
|---|---|
| Framework | Django 5.0.7 |
| API | Django REST Framework 3.15.2 |
| Language | Python 3.11.9 |
| Database | MySQL (AWS RDS `db.t3.micro` in prod) |
| Auth | Custom `login_session_token` (16-char on `Users` model) + django-allauth (Google/Facebook) + dj-rest-auth |
| Realtime | Django Channels + Daphne + WebSockets |
| Async/queue | Celery + Redis (AWS ElastiCache `t3.micro` in prod) |
| Storage | Local `media/` in dev → AWS S3 via django-storages in prod |
| Email | Gmail SMTP (dev) → AWS SES (prod) |
| Payments | Paystack (NGN) - primary. USDT for payouts. Never simulate. |
| Hosting | AWS EC2 `t3.small` (Daphne) |

---

## Directory tree

```
V-ENT-BACKEND/
├── CLAUDE.md                       # L1 repo constitution (envelope shape, auth pattern)
├── agent-development-kit.md        # this file
├── manage.py                       # Django CLI
├── config.py                       # SMTP config - reads COMPANY_EMAIL / COMPANY_EMAIL_PASSWORD
├── requirements.txt                # dev dependencies
├── requirements-prod.txt           # prod dependencies
├── runtime.txt                     # python-3.11.9
├── Procfile                        # process types (web: daphne / worker: celery)
├── vent/                           # Django project (settings + URL root)
│   ├── settings.py                 # DB, INSTALLED_APPS, CORS, auth backends, media, social
│   ├── urls.py                     # mounts all app URL confs
│   ├── asgi.py                     # ASGI (Channels + Daphne)
│   ├── wsgi.py                     # WSGI fallback
│   ├── Lib/                        # local virtualenv lib (not committed in clean repo)
│   └── Scripts/                    # virtualenv entry points
├── vent_auth/                      # ACTIVE - users, profiles, teams, wallets, social, games
│   ├── models.py                   # Users (custom), Profile, Teams (duplicate of vent_team.Teams)
│   ├── backends.py                 # EmailOrUsernameModelBackend
│   ├── views.py / views_auth.py / views_admin.py / views_profile.py / views_social.py / views_gallery.py / views_helpers.py
│   ├── serializers.py
│   ├── functions.py
│   ├── urls.py                     # mounted at /auth/
│   ├── admin.py / apps.py
│   ├── management/                 # custom manage.py commands
│   ├── migrations/
│   ├── templates/                  # email templates
│   └── tests.py
├── vent_tournament/                # ACTIVE - mounted at /tournament/
│   ├── models.py / views.py / urls.py / admin.py / apps.py / migrations/
├── vent_event/                     # ACTIVE - mounted at /event/
├── vent_team/                      # ACTIVE - mounted at /team/. Teams model duplicates vent_auth.Teams (different related_name)
├── vent_marketplace/               # STUB - INSTALLED_APPS only, no views/urls
├── vent_anime/                     # STUB - INSTALLED_APPS only, no views/urls
├── imports/                        # data import scripts
├── media/                          # local uploads (profile_pictures, banners, gallery, *_logos, *_banners, achievements, sponsor_logos)
├── docs/
└── venv/                           # local virtualenv (not committed)
```

---

## Five-layer mapping

| Layer | Backend location | Notes |
|---|---|---|
| **L1 CLAUDE.md** | `V-ENT-BACKEND/CLAUDE.md` (this repo) + `../CLAUDE.md` (workspace) + `~/.claude/CLAUDE.md` (global) | Repo CLAUDE.md sets envelope shape `{status,data,message}`, `Bearer <login_session_token>` pattern, duplicate Teams warning, AWS migration path. |
| **L2 Skills** | Global `~/.claude/skills/` only - no project-local skills. | Most-used here: caveman + graphify + superpowers:systematic-debugging + superpowers:test-driven-development + context7 (Django / DRF / Celery doc fetches). |
| **L3 Hooks** | None project-local. | Hooks fire from caveman + superpowers SessionStart hooks. |
| **L4 Subagents** | Defaults + plugin-shipped. | Frequent: `Explore` (mapping Django apps), `Plan` (migration + endpoint design), `general-purpose` (parallel app work), `code-simplifier:code-simplifier`. |
| **L5 Plugins** | Same as workspace global. | Most-used here: `superpowers` (TDD, debugging), `caveman`, `context7` (Django/DRF docs). Vercel plugin not relevant - backend on AWS EC2. |

---

## App map

| App | Mount | Status | Purpose |
|---|---|---|---|
| `vent_auth` | `/auth/` | Active | Users, profiles, social auth, games, teams (dup), wallets, waitlist |
| `vent_tournament` | `/tournament/` | Active | Tournament CRUD, registration, matches |
| `vent_event` | `/event/` | Active | Events |
| `vent_team` | `/team/` | Active | Team management (Teams model duplicated in vent_auth - see below) |
| `vent_marketplace` | - | Stub | INSTALLED_APPS only, no views/urls |
| `vent_anime` | - | Stub | INSTALLED_APPS only, no views/urls |

---

## Auth pattern (non-negotiable)

1. Custom `login_session_token` on `Users` model - 16-char string, generated on login.
2. Frontend sends `Authorization: Bearer <login_session_token>` on protected requests.
3. Verify token on every protected view - NOT a DRF Token, NOT a JWT. Cross-reference `Users.login_session_token`.
4. `EmailOrUsernameModelBackend` (`vent_auth/backends.py`) allows login by email OR username.
5. Social auth = django-allauth (Google + Facebook) + manual Google flow (`get_google_login_url` / `google_callback` / `verify_google_token`).
6. Email verification = `VerificationToken` model, 2-hour expiry, Gmail SMTP link.
7. `dj-rest-auth` + DRF Token are installed but `login_session_token` is canonical.

---

## Response envelope (non-negotiable)

```json
{ "status": "success" | "error", "data": {...}, "message": "..." }
```

Every endpoint. No exceptions. Frontend depends on this shape.

---

## Duplicate Teams model (known landmine)

`Teams` model defined in BOTH `vent_auth/models.py` AND `vent_team/models.py`. Different `related_name`s (`vent_auth_teams` / `vent_team_teams`) avoid clash. Active app uses `vent_auth.Teams`. `vent_team.Teams` is near-duplicate. **Do not add new logic to `vent_team.Teams` without reconciling.**

---

## Media uploads

Subfolders under `MEDIA_ROOT` (=`media/`):

```
profile_pictures/  banners/        gallery/
team_logos/        team_banners/
tournament_logos/  tournament_banners/
event_logos/       event_banners/
game_logos/        achievements/   sponsor_logos/
```

Local in dev. Production migration target: AWS S3 via `django-storages` → CloudFront in front.

---

## Required env vars

| Var | Purpose |
|---|---|
| `SECRET_KEY` | Django secret |
| `DB_*` | MySQL connection (host, port, name, user, password) |
| `COMPANY_EMAIL` / `COMPANY_EMAIL_PASSWORD` | Gmail SMTP (dev) |
| `PAYSTACK_SECRET_KEY` | Paystack - payments |
| `VENT_COINS_PER_100_NGN` | Default 50 (= 0.5 coins / NGN) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth |
| `ADMIN_PASSWORD` | Legacy admin bootstrap |

Never commit `.env`. Never hardcode secrets.

---

## Commands

```bash
pip install -r requirements.txt    # install deps
python manage.py migrate           # apply migrations
python manage.py makemigrations    # create migrations after model changes
python manage.py runserver         # localhost:8000
python manage.py shell             # Django shell
python manage.py createsuperuser   # admin user
```

No test suite configured yet.

---

## Verification flow (backend-specific)

1. Apply migrations: `python manage.py migrate`.
2. Start server: `python manage.py runserver`.
3. Hit affected endpoint (curl / httpie / DRF browsable API / frontend dev).
4. Confirm envelope `{status, data, message}` intact.
5. Watch stdout for tracebacks - must be clean.
6. Re-test the exact path until symptom gone.

For Celery work: start Redis, start worker (`celery -A vent worker -l info`), enqueue task, confirm task picked up + finished.

---

## Repo-specific rules (recap from CLAUDE.md)

1. Verify before hand-off - migrate + runserver + endpoint walk + envelope check + log scan.
2. Design parity at API boundary - mirror shape + naming of existing endpoints. Read `vent_tournament` / `vent_event` / `vent_auth` before introducing new field names.
3. Use agents for parallel build work.
4. Update `tasks/lessons.md` after every correction.
5. Never break the envelope shape.
6. Never weaken auth on protected endpoints.
7. Never simulate payments. Paystack is the only NGN gateway.
8. Never commit secrets - env only, with managed-secret-store path for prod.
9. AWS-first for new infra (S3 → SES → ElastiCache → CloudFront).
10. Watch the duplicate `Teams` model - don't add to `vent_team.Teams` without reconciling.

Full repo rules live in `V-ENT-BACKEND/CLAUDE.md`.

---

## Branching and release flow (hard rule)

**Never commit or push directly to `main` (or `master`).** Applies to every project. Solo repo, one-line fix, nobody else on the project: still no. Work goes `branch -> PR -> merge`, every single time. Belongs in `CLAUDE.md` (Layer 1) so it is always loaded, and is best enforced deterministically with a Layer 3 hook.

### The flow

```
feature/*  ->  dev  ->  staging  ->  main
   |            |          |           |
 one branch   active    dress       PRODUCTION
 per feature  dev line  rehearsal   what users see
```

| Branch | Purpose | Rules |
|---|---|---|
| `feature/<slug>`, `fix/<slug>`, `chore/<slug>` | Build one thing | Branch off `dev`. One branch per feature. Delete after merge |
| `dev` | Active development, everything integrated | PR-only. No direct commits |
| `staging` | Dress rehearsal, mirrors production (same env vars, same data shape, same build) | PR from `dev`. Catch anything weird here, not in production |
| `main` | Sacred = production, what users see | PR from `staging` only. Branch protection ON, required reviews, required status checks, linear history, no force-push |

Small repos with no `staging` tier: `feature/* -> dev -> main` still holds. `main` never takes a direct commit.

### Agent behavior (non-negotiable)

1. **Check the branch before any edit.** Run `git rev-parse --abbrev-ref HEAD`. If on `main`, `master`, `dev`, or `staging`, cut a branch FIRST (`git switch -c feature/<slug>`), then edit.
2. **Never push to a protected branch.** No `git push origin main`, no `git push -f`. Push the feature branch, open a PR (`gh pr create`, see Best practices rule 35), report the URL.
3. **Never self-merge.** Do not merge a PR unless the user says so. Never bypass protection (`--no-verify`, admin merge, force-push).
4. **Recover, do not hide.** Already committed to `main` by mistake: STOP, tell the user, move the commits to a branch (`git branch <slug>; git reset --hard origin/main`) before anything else.
5. **Commit and push only when asked.** Confirm before anything irreversible.

### Hook enforcement (Layer 3)

```sh
# PreToolUse.sh - block commits and pushes on protected branches
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
case "$BRANCH" in
  main|master|dev|staging)
    echo "Blocked: direct commit/push to '$BRANCH'. Cut a feature branch and open a PR." >&2
    exit 2
    ;;
esac
```

**Why:** at 2am when production breaks (and it will), a protected `main` plus PR history means a one-click rollback instead of forensics.


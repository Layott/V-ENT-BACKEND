# 05 — User System (Auth, Profiles, Settings)

**Phase:** 1 MVP — auth and profiles are prerequisites for everything else
**Status:** 🟡 Partially built — auth flow complete, profiles partially wired, settings is a stub
**Design track:** Track A (Figma designs exist at nodes `0:1` and `7:376`)
**Dependencies:** None (this module is a dependency of all others)

---

## Module Overview

The User System covers every user-facing identity surface:

1. **Auth Flow** — signup, login (email + Google + Facebook), verify email, forgot/reset password
2. **User Profile** — public view of any user; stats, games, achievements, interests, activity
3. **Edit Profile** — self-edit: avatar, banner, bio, interests, favorite games, gaming accounts, social links
4. **Account Settings** — login/security, notifications, PIN, delete account (Figma partial)

**What's working:** Auth flow is complete (NextAuth v4 with Django backend). User profile fetches real API data. Edit profile sub-components exist but API wiring status is inconsistent.

**What's broken/missing:** Settings page is a stub. Mobile activity tables not designed. Edit profile does not always use `session.user.sessionToken` correctly across all sub-components.

---

## Figma Node IDs

| Screen | nodeId | Status |
|--------|--------|--------|
| Login | `0:1` (auth section) | ✅ Complete (web + mobile) |
| Sign Up | `0:1` (auth section) | ✅ Multiple states |
| Verify Email | `0:1` (auth section) | ✅ Complete |
| Forgot Password | `0:1` (auth section) | ✅ Complete |
| Reset Link Sent | `0:1` (auth section) | ✅ Complete |
| Reset Password | `0:1` (auth section) | ✅ Complete |
| Profile (filled) | `7:376` | ✅ Web + mobile |
| Profile (empty state) | `7:376` | ✅ Web + mobile |
| Viewing Another User | `7:376` | ✅ Report button, no edit |
| Activity — Tournaments | `7:376` | ✅ Web, ❌ Mobile missing |
| Activity — Events | `7:376` | ✅ Web, ❌ Mobile missing |
| Image/Esports Gallery | `7:376` | ✅ Web + mobile |
| Edit Profile — Personal Info | `7:376` | ✅ Web + mobile |
| Edit Profile — Social Links | `7:376` | ✅ Web + mobile |
| Edit Profile — Favorite Games | `7:376` | ✅ Web, 🟡 Mobile incomplete |
| Account Settings | `7:376` | 🟡 Login/Security partial, rest missing |

---

## Pages & Components Status

```
src/app/
├── login/
│   ├── page.js                              # ✅ NextAuth signIn("credentials") + Google + Facebook
│   └── Actions.js                           # ✅ Server action for auth (if used)
├── signup/
│   └── page.js                              # ✅ Email regex, username check, country/state, password strength
├── verify-email/
│   └── page.js                              # ✅ Token-based email verification
├── email-verified/
│   └── page.js                              # ✅ Confirmation screen
├── forgot-password/
│   └── page.js                              # ✅ POST to VENT.FORGOT_PASSWORD
├── reset-password/
│   └── page.js                              # ✅ FORGOT_PASSWORD_TOKEN + RESET_PASSWORD
├── user-profile/
│   └── page.js                              # 🟡 Fetches real API; remaps profile_pic→profile_picture; tries Bearer then Token prefix
└── edit-user-profile/
    └── page.js                              # 🟡 Shell with 3 sidebar tabs

src/components/
├── edit-user-profile/
│   ├── edit-user-profile-info/
│   │   ├── EditUserProfileInfo.js           # ⚠️ Renders sub-components; API wiring status unknown
│   │   ├── edit-profile-image-avatar/
│   │   │   └── EditProfileImageAvatar.js    # ⚠️ Avatar upload — wiring status unknown
│   │   ├── edit-user-profile-banner/
│   │   │   └── EditUserProfileBanner.js     # ⚠️ Banner upload — wiring status unknown
│   │   ├── edit-user-profile-details/
│   │   │   └── EditUserProfileDetails.js    # ⚠️ Name, bio, country fields
│   │   └── edit-user-profile-interests/
│   │       ├── EditUserProfileInterests.js  # ⚠️ Multi-select
│   │       └── interests.js                 # Static interests list
│   ├── edit-user-profile-links/
│   │   └── EditUserProfileLinks.js          # ⚠️ Social links form
│   ├── edit-user-profile-gaming-accounts/
│   │   └── EditUserProfileGamingAccounts.js # ⚠️ Gaming platform accounts
│   └── edit-user-profile-favourite-games/
│       └── EditUserProfileFavouriteGames.js # ⚠️ Favorite games selector
└── settings/
    └── page.js (in app/settings/)           # ❌ Stub: renders "Settings Page" heading only

lib/
└── authOptions.js                           # ✅ NextAuth config — but has ~15 console.log including token data
```

---

## API Endpoints

### Current (Wired)

| Method | Endpoint | Constant | Used In |
|--------|----------|----------|---------|
| `POST` | `/auth/signup/` | `VENT.SIGNUP` | `signup/page.js` |
| `GET` | `/auth/verify/?token=` | `VENT.VERIFY` | `verify-email/page.js` |
| `POST` | `/auth/login/` | `VENT.LOGIN` | `lib/authOptions.js` (Credentials) |
| `POST` | `/auth/forgot-password/` | `VENT.FORGOT_PASSWORD` | `forgot-password/page.js` |
| `GET` | `/auth/forgot-password/?token=` | `VENT.FORGOT_PASSWORD_TOKEN` | `reset-password/page.js` |
| `POST` | `/auth/reset-password/` | `VENT.RESET_PASSWORD` | `reset-password/page.js` |
| `GET/POST` | `/auth/user-profile/?user_id=` | `VENT.USER_PROFILE` | `user-profile/page.js` |
| `POST` | `/auth/resend-link/` | `VENT.RESEND_LINK` | `verify-email/page.js` |
| `POST` | `/auth/edit-profile/` | `VENT.EDIT_PROFILE` | `edit-user-profile/` |
| `POST` | `/auth/edit-links/` | `VENT.EDIT_LINKS` | `edit-user-profile/` |

### Needed

| Method | Endpoint | Description |
|--------|----------|-------------|
| `PATCH` | `/auth/edit-gaming-accounts/` | Update linked gaming platform IDs |
| `PATCH` | `/auth/edit-favourite-games/` | Update favorite games list |
| `GET` | `/auth/user-activity/tournaments/` | User's tournament participation history |
| `GET` | `/auth/user-activity/events/` | User's event attendance history |
| `POST` | `/auth/change-password/` | Account settings: change password |
| `POST` | `/auth/set-pin/` | Account settings: set transaction PIN |
| `POST` | `/auth/delete-account/` | Account settings: delete account |
| `PATCH` | `/auth/notification-settings/` | Toggle notification preferences |
| `POST` | `/auth/report-user/` | Report another user |
| `GET` | `/auth/username-check/?username=` | Check username availability during signup |

---

## Data Shape Reference

### User Profile Object (GET /auth/user-profile/?user_id=)

```json
{
  "user_id": "u123",
  "username": "johndoe",
  "email": "john@example.com",
  "profile_pic": "https://...",
  "profile_picture": "https://...",
  "banner": "https://...",
  "bio": "Competitive FIFA player from Lagos",
  "country": "Nigeria",
  "state": "Lagos",
  "interests": ["FPS","Sports","Battle Royale"],
  "favourite_games": ["FIFA 25","PUBG Mobile","Mobile Legends"],
  "gaming_accounts": {
    "psn": "johndoe_ps",
    "xbox": "",
    "steam": "johndoe_steam",
    "riot": ""
  },
  "social_links": {
    "twitter": "https://twitter.com/johndoe",
    "instagram": "",
    "twitch": "",
    "youtube": ""
  },
  "stats": {
    "tournaments_played": 18,
    "tournaments_won": 4,
    "events_attended": 7,
    "win_rate": 0.38
  },
  "achievements": [],
  "created_at": "2025-09-01"
}
```

> **Known issue:** Backend returns both `profile_pic` and `profile_picture` for different endpoints. `user-profile/page.js` handles this by remapping: if `profile_pic` exists and `profile_picture` doesn't, it sets `profile_picture = profile_pic`. Watch for this inconsistency.

### Login Response (from Django, consumed by NextAuth)

```json
{
  "status": "success",
  "data": {
    "session_token": "abc123...",
    "user_id": "u123",
    "username": "johndoe",
    "email": "john@example.com",
    "profile_pic": "https://..."
  }
}
```

Stored in NextAuth JWT as `token.session_token`. Exposed to components as `session.user.sessionToken`.

### Edit Profile Request (POST /auth/edit-profile/)

```js
// multipart/form-data
{
  user_id: "u123",
  username: "johndoe",
  bio: "...",
  country: "Nigeria",
  state: "Lagos",
  interests: JSON.stringify(["FPS","Sports"]),
  profile_pic: <File>,     // optional
  banner: <File>           // optional
}
```

---

## Django Models (Inferred)

```python
class User(AbstractUser):
    # Extends Django default User
    profile_pic = models.ImageField(upload_to='profile_pics/', null=True, blank=True)
    banner = models.ImageField(upload_to='banners/', null=True, blank=True)
    bio = models.TextField(blank=True)
    country = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    interests = models.JSONField(default=list)
    favourite_games = models.JSONField(default=list)
    session_token = models.CharField(max_length=255, blank=True)
    is_verified = models.BooleanField(default=False)

class GamingAccount(models.Model):
    user = models.OneToOneField(User, related_name='gaming_accounts', on_delete=models.CASCADE)
    psn = models.CharField(max_length=100, blank=True)
    xbox = models.CharField(max_length=100, blank=True)
    steam = models.CharField(max_length=100, blank=True)
    riot = models.CharField(max_length=100, blank=True)
    battlenet = models.CharField(max_length=100, blank=True)
    epic = models.CharField(max_length=100, blank=True)

class SocialLink(models.Model):
    user = models.OneToOneField(User, related_name='social_links', on_delete=models.CASCADE)
    twitter = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    twitch = models.URLField(blank=True)
    youtube = models.URLField(blank=True)
    facebook = models.URLField(blank=True)
    discord = models.URLField(blank=True)

class EmailVerificationToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)
```

---

## Acceptance Criteria

### Auth Flow (Login, Signup, Verify, Reset) — Track A

**Login (`/login`):**
- [ ] Email + password submits via NextAuth `signIn("credentials")` → calls Django `/auth/login/`
- [ ] Google OAuth button works
- [ ] Facebook OAuth button works (verify Facebook app is configured)
- [ ] On success: redirects to `/user-profile`
- [ ] On failure: shows error message (MUI Snackbar or MessageSnackbar)
- [ ] Loading state during submission (MUI CircularProgress present)

**Signup (`/signup`):**
- [ ] Email format validated before submit
- [ ] Username uniqueness checked against `/auth/username-check/` (if endpoint exists) or on submit
- [ ] Country/state dropdowns work
- [ ] Password strength indicator shows
- [ ] Successful signup → redirect to `/verify-email`

**Verify Email (`/verify-email`):**
- [ ] Reads token from URL param; calls `GET /auth/verify/?token=`
- [ ] Success → `/email-verified`
- [ ] Failure → shows error + "Resend verification link" button
- [ ] Resend calls `POST /auth/resend-link/`

**Forgot/Reset Password:**
- [ ] `/forgot-password` sends email via `POST /auth/forgot-password/`
- [ ] `/reset-password` validates token via `GET /auth/forgot-password/?token=`
- [ ] New password submitted via `POST /auth/reset-password/`

### User Profile (`/user-profile?user_id=...`) — Track A

- [ ] Reads `user_id` from URL param (or defaults to current user if no param)
- [ ] Fetches `GET /auth/user-profile/?user_id=` with `Authorization: Bearer {sessionToken}`
- [ ] Handles both `profile_pic` and `profile_picture` field names
- [ ] Displays: avatar, banner, username, bio, country, stats, interests, games, social links
- [ ] If viewing own profile: "Edit Profile" button visible
- [ ] If viewing another user: "Report" button visible, no edit options
- [ ] Activity tabs (Tournaments, Events) fetch from activity endpoints
- [ ] Gallery tab shows uploaded images

### Edit User Profile (`/edit-user-profile`) — Track A

- [ ] Only accessible to authenticated user editing their own profile
- [ ] **Personal Info tab:** avatar upload, banner upload, username, bio, country, state, interests — submits to `POST /auth/edit-profile/` as multipart/form-data
- [ ] **Social Links tab:** all platform fields — submits to `POST /auth/edit-links/`
- [ ] **Favorite Games tab:** multi-select game picker — submits to `PATCH /auth/edit-favourite-games/`
- [ ] **Gaming Accounts tab:** PSN, Xbox, Steam, Riot, etc. — submits to `PATCH /auth/edit-gaming-accounts/`
- [ ] Each tab saves independently; success/error feedback after each save
- [ ] Avatar and banner upload preview before confirm

### Account Settings (`/settings`) — Track B (Partial design exists)

**Current state:** The page is a complete stub (renders only "Settings Page" heading).

- [ ] Build layout matching existing edit-profile shell pattern
- [ ] **Login & Security tab:** change email, change password form
- [ ] **Notifications tab:** toggle email/push notification preferences
- [ ] **PIN tab:** set or change 4-digit transaction PIN (needed for wallet)
- [ ] **Delete Account tab:** confirmation flow with password re-entry
- [ ] All settings persist to backend before considered done

---

## Task Checklist

### 🔴 Critical — Production Blockers

- [ ] Remove all `console.log` from `lib/authOptions.js` (logs include session tokens — security risk)
- [ ] Remove all `console.log` from `src/middleware.js`
- [ ] Verify Facebook OAuth is actually configured with a real app ID + secret in env vars
- [ ] Verify `POST /auth/edit-profile/` wiring — confirm all edit-profile sub-components send `Authorization: Bearer {sessionToken}`

### 🔴 Critical — Functionality

- [ ] Account Settings page (`/settings`) — build out from stub (Track B: self-design, CEO approval)
- [ ] Wire activity tabs in User Profile to real API endpoints
- [ ] Standardize `profile_pic` vs `profile_picture` — backend should return one consistent field name

### 🟡 Important

- [ ] Edit Profile — Favorite Games: verify API endpoint and field name
- [ ] Edit Profile — Gaming Accounts: verify API endpoint and wiring
- [ ] Add `PATCH /auth/notification-settings/` and wire to Settings page
- [ ] Add `POST /auth/set-pin/` and wire to Settings page (needed before wallet launch)
- [ ] Add `POST /auth/delete-account/` and wire to Settings page
- [ ] Add `POST /auth/report-user/` and wire to "Report" button on other user's profile

### 🟢 Verification (after build)

- [ ] Pull Figma via `get_design_context` node `0:1` — verify all auth screens
- [ ] Pull Figma via `get_design_context` node `7:376` — verify user profile and edit profile
- [ ] Check mobile responsiveness for all auth screens (375px)
- [ ] Mark auth flow **VERIFIED**
- [ ] Mark user profile **VERIFIED**
- [ ] Mark edit profile **VERIFIED**

### ⬜ Phase 2+

- [ ] Mobile activity tables in user profile (not yet designed in Figma)
- [ ] Esports gallery upload UX improvements
- [ ] 2FA / authenticator app support
- [ ] OAuth account linking (connect Google + credentials to same account)
- [ ] Public profile privacy controls

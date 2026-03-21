# Module 01: Tournaments
**Phase:** 1 (MVP — highest priority)
**Last Updated:** March 2026
**Figma File:** [VENT-Main](https://www.figma.com/design/Ne1xquUxx1yZc0NhkN8kUE/VENT-Main) — fileKey: `Ne1xquUxx1yZc0NhkN8kUE`

---

## Table of Contents
1. [Module Overview](#1-module-overview)
2. [Figma Node IDs](#2-figma-node-ids)
3. [Pages & Components — Status](#3-pages--components--status)
4. [API Endpoints](#4-api-endpoints)
5. [Data Shape Reference](#5-data-shape-reference)
6. [Django Models](#6-django-models)
7. [Acceptance Criteria](#7-acceptance-criteria)
8. [Task Checklist](#8-task-checklist)

---

## 1. Module Overview

The tournament module is the core of V-ENT's Phase 1 MVP. It covers:
- Browsing and discovering tournaments (public, no login required — **currently unprotected by middleware**)
- Viewing full tournament details across five tabs
- Creating a tournament via a 5-step wizard (with draft support)
- Registering for a tournament as an individual or team (modal flow)
- Bracket visualization (not yet built)
- Tournament management by the organizer (not yet built)

**What's live and wired to the real backend:** Tournament homepage (listing), tournament detail view (all tabs except bracket), tournament creation (full submit + draft), draft listing.

**What's UI only / partially wired:** Registration modal (payment step uses hardcoded wallet balance of 526 and simulated Paystack — not connected to real wallet or Paystack).

**What's a stub:** Bracket tab (`TournamentDetailsBracket.js` renders only the text "TournamentDetailsBracket").

**What's not built:** Tournament search/filter, tournament management (post-creation: leaderboards, score updates, match management, invites), organizer production screen.

---

## 2. Figma Node IDs

| Screen | Node ID | Status |
|--------|---------|--------|
| Explore Tournaments (homepage) | `458:3639` | ✅ Built — needs visual comparison |
| Tournament Details | `2338:20196` | ✅ Built — needs visual comparison |
| Tournament Creation Wizard | `4052:20591` | ✅ Built — needs visual comparison |
| Tournament Management (organizer) | *(within `4052:20591` section)* | ❌ Not built |
| Tournament Brackets | *(within `2338:20196`)* | 🟡 Stub only |
| Join/Register flow | *(within `2338:20196`)* | ✅ Built (modal) — needs visual comparison |

**How to pull a screenshot:**
Use `get_design_context` with `fileKey: "Ne1xquUxx1yZc0NhkN8kUE"` and the nodeId above. Always compare the screenshot against the built page before marking VERIFIED.

---

## 3. Pages & Components — Status

### Pages (`src/app/tournaments/`)

| Route | File | Auth Required | Status |
|-------|------|--------------|--------|
| `/tournaments` | `page.js` | ❌ No (not in middleware protectedRoutes) | ✅ Built |
| `/tournaments/view-tournament?id={id}` | `view-tournament/page.js` | ❌ No | ✅ Built |
| `/tournaments/create-tournament` | `create-tournament/page.js` | ❌ No (should be protected) | ✅ Built |
| `/tournaments/drafts` | `drafts/page.js` | ❌ No (should be protected) | ✅ Built |
| `/tournaments/register-tournament` | `register-tournament/page.js` | ❌ No | ⚠️ Stub (`<div>page</div>`) — real flow is modal |

> ⚠️ **Security gap:** `/tournaments`, `/tournaments/create-tournament`, and `/tournaments/drafts` are all accessible without authentication. Tournament creation and draft management should require login. Add these to `protectedRoutes` in `src/middleware.js`.

### Component Tree

```
src/components/tournaments/
├── TournamentsComponent.js          ✅ Fetches real API data; renders featured/new/by_game sections
├── tournaments-featured/
│   └── TournamentsFeatured.js       ✅ Featured carousel/list
├── new-tournaments/
│   └── NewTournaments.js            ✅ Newest tournaments section
└── all-tournaments/
    ├── AllTournaments.js            ✅ Tabbed by game
    ├── tournaments-by-game/
    │   └── TournamentByGame.js      ✅ Per-game tab content
    ├── fifa-tournaments/
    │   ├── FIFATournaments.js       ⚠️ Check: may use hardcoded fifaTournamentsList.js
    │   └── fifaTournamentsList.js   ⚠️ Hardcoded mock data — needs removal once API returns by_game data
    ├── fortnite-tournaments/        ⚠️ Same — check for hardcoded data
    ├── minecraft-tournaments/       ⚠️ Same
    └── pubg-tournaments/            ⚠️ Same

src/components/view-tournament/
├── tournament-details-banner/
│   └── TournamentDetailsBanner.js   ✅ Banner, countdown timer, status, Join button — triggers registration modal
├── tournament-details-overview/
│   ├── TournamentDetailsOverview.js ✅ Two-column layout (left + right)
│   ├── tournament-details-overview-left/
│   │   └── TournamentDetailsOverviewLeft.js  ✅
│   └── tournament-details-overview-right/
│       └── TournamentDetailsOverviewRight.js ✅
├── tournament-details-rules/
│   ├── TournamentDetailsRules.js    ✅
│   └── tournament-details-rules-left/
│       └── TournamentDetailsRulesLeft.js     ✅
├── tournament-details-bracket/
│   └── TournamentDetailsBracket.js  ❌ STUB — renders text only, no visualization
├── tournament-details-participants/
│   ├── TournamentDetailsParticipants.js ✅
│   └── participantsList.js          ⚠️ Check if hardcoded or API-driven
├── tournament-details-prize/
│   ├── TournamentDetailsPrize.js    ✅
│   └── tournamentResults.js         ⚠️ Check if hardcoded
└── tournament-register/             ✅ Full multi-step registration modal
    ├── TournamentRegister.js        ✅ Root modal — manages all sub-modal state
    ├── team/Team.js                 ✅ Choose team step
    ├── edit-team/EditTeam.js        ✅ Edit roster step
    ├── review-team/Review.js        ✅ Review step
    ├── payment/Payment.js           ⚠️ UI complete — wallet balance hardcoded (526), Paystack simulated
    └── success/Success.js           ✅ Success screen

src/components/create-tournament-component/   ✅ Full 5-step wizard
├── CreateTournamentComponent.js     ✅ Parent — manages state, localStorage persistence, submit/draft
├── progress-menu/ProgressMenu.js    ✅ Step indicator
├── basic-info/                      ✅ Step 1: title, game, mode, description, type, dates, location, visibility
│   ├── create-tournament-logo/      ✅ Logo + banner upload
│   ├── create-tournament-schedule/  ✅ Start/end datetime pickers
│   ├── create-tournament-title/     ✅ Title + game selector
│   ├── create-tournament-type/      ✅ Tournament type (online/offline/hybrid)
│   └── create-tournament-visibility/✅ Public/private/invite-only
├── format-participants/             ✅ Step 2: bracket type, team size, min/max participants, entry fee
│   ├── tournament-format/           ✅ Bracket type selector
│   ├── tournament-rules/            ✅ Rich text rules editor (react-quill — needs dynamic import)
│   └── participants/                ✅ Team size + capacity settings
├── prize-distribution/              ✅ Step 3: prize type, prize per position or winner-takes-all
│   └── prize-distribution-inside/   ✅
├── sponsors-links/                  ✅ Step 4: sponsor entries + social/web links
│   ├── sponsors/                    ✅ Add/remove sponsors
│   └── web-social-links/            ✅ Facebook, Twitter, Instagram, YouTube, Twitch, Kick, TikTok, BIGOLive
└── review/                          ✅ Step 5: full review of all steps + Publish / Save Draft buttons
    ├── review-basic-info/           ✅
    ├── review-format-participants/  ✅
    ├── review-header-component/     ✅
    ├── review-prize-distribution/   ✅
    └── review-sponsor-links/        ✅

src/components/drafts/
└── DraftCard.js                     ✅ Individual draft card (title, game, dates, edit/publish actions)
```

---

## 4. API Endpoints

All endpoints hit `process.env.NEXT_PUBLIC_API_URL` (backend: `https://vermillionent.pythonanywhere.com`).

| Method | Endpoint | Auth | Used By | Status |
|--------|----------|------|---------|--------|
| `GET` | `/tournament/get-all-tournaments/` | No | Tournament homepage listing | ✅ Wired — returns `{ featured, new, by_game }` |
| `GET` | `/tournament/view-tournament/{id}` | No | View tournament detail page | ✅ Wired — id via `?id=` URL param |
| `POST` | `/tournament/create-tournament/` | Bearer token | Create wizard submit + draft | ✅ Wired — multipart/form-data |
| `GET` | `/tournament/view-user-drafted-tournaments/` | Bearer token | Drafts page | ✅ Wired |
| `GET` | `/get-all-tournaments/` | Bearer token | Create wizard (game name validation) | ⚠️ Different URL — verify this is correct or consolidate with above |

### Endpoints Still Needed (not yet called anywhere in frontend)

| Method | Endpoint (suggested) | Purpose |
|--------|---------------------|---------|
| `POST` | `/tournament/register-tournament/` | Submit tournament registration (individual or team) |
| `GET` | `/tournament/get-tournament-participants/{id}/` | Participants tab — list registered participants |
| `GET` | `/tournament/get-tournament-brackets/{id}/` | Bracket tab — bracket data |
| `POST` | `/tournament/update-bracket/{id}/` | Organizer: update match scores / advance bracket |
| `GET` | `/tournament/get-organizer-tournaments/` | My Tournaments list (organizer management) |
| `GET` | `/tournament/get-tournament-leaderboard/{id}/` | Leaderboard tab |
| `POST` | `/tournament/invite-participant/` | Organizer: invite a player or team |
| `DELETE` | `/tournament/delete-draft/{id}/` | Delete a draft tournament |
| `PUT` | `/tournament/edit-tournament/{id}/` | Edit a published tournament |

---

## 5. Data Shape Reference

### Tournament object (from `GET /tournament/view-tournament/{id}`)
```js
{
  id: number,
  tournament_title: string,
  game: string,               // e.g. "Free Fire", "PUBG Mobile"
  game_mode: string,
  tournament_description: string,
  tournament_type: string,    // "online" | "offline" | "hybrid"
  tournament_visibility: string, // "public" | "private" | "invite_only"
  start_date_and_time: string,   // ISO 8601
  end_date_and_time: string,
  tournament_location: string,
  virtual_link: string,
  hide_location: boolean,
  entry_type: string,         // "free" | "paid"
  entry_fee: number | string, // "0.00" for free
  entry_fee_price: number | string,
  tournament_access: string,
  team_size: number,
  min_number_of_participants: number,
  max_number_of_participants: number,
  bracket_type: string,       // "single_elimination" | "double_elimination" | "round_robin" | "swiss" | "king_of_the_hill" | "battle_royale"
  tournament_rules: string,   // HTML from Quill editor
  prize_type: string,         // "distributed" | "winner_takes_all"
  prize_data: Array<{ position: number, prize: number }>,
  winner_prize: number,
  sponsor_names: string[],
  sponsor_types: string[],
  sponsor_usernames: string[],
  is_draft: boolean,
  tournament_logo: string,    // URL or /media/ path
  tournament_banner: string,  // URL or /media/ path
  facebook_link: string,
  twitter_link: string,
  instagram_link: string,
  youtube_link: string,
  twitch_link: string,
  kick_link: string,
  tiktok_link: string,
  bigolive_link: string,
}
```

### Tournament listing response (from `GET /tournament/get-all-tournaments/`)
```js
{
  status: "success",
  data: {
    featured: Tournament[],
    new: Tournament[],
    by_game: {
      [gameName: string]: Tournament[]
    }
  }
}
```

### Tournament creation payload (multipart/form-data fields)
All text values sent as FormData string entries. Key notes:
- `is_draft`: `"1"` or `"0"` (not true/false)
- `prize_data`: JSON stringified array `[{ position: 1, prize: 500 }, ...]`
- `sponsor_names`, `sponsor_types`, `sponsor_usernames`: JSON stringified arrays
- `hide_location`: `"true"` or `"false"` string
- Logo and banner: raw `File` objects appended directly to FormData
- Do NOT set `Content-Type` header — let the browser set it with the multipart boundary

### Image URL resolution (from TournamentDetailsBanner.js)
```js
// Already a full URL → use as-is
// Starts with /media → prepend https://vermillionent.pythonanywhere.com
// Otherwise → https://vermillionent.pythonanywhere.com/media/tournament_banners/{filename}
```

---

## 6. Django Models

These are inferred from the API payloads and the `vent_*` app naming convention described in the Best Practices guide.

### `vent_tournaments` app

```python
# Tournament model (inferred)
class Tournament(TimestampMixin):  # inherits id, created_at, updated_at
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organizer = models.ForeignKey('vent_auth.User', on_delete=models.CASCADE)
    tournament_title = models.CharField(max_length=255)
    game = models.ForeignKey('Game', on_delete=models.PROTECT)
    game_mode = models.CharField(max_length=100, blank=True)
    tournament_description = models.TextField(blank=True)
    tournament_type = models.CharField(max_length=20)   # online/offline/hybrid
    tournament_visibility = models.CharField(max_length=20)  # public/private/invite_only
    tournament_access = models.CharField(max_length=20, blank=True)
    start_date_and_time = models.DateTimeField()
    end_date_and_time = models.DateTimeField()
    tournament_location = models.CharField(max_length=255, blank=True)
    virtual_link = models.URLField(blank=True)
    hide_location = models.BooleanField(default=False)
    entry_type = models.CharField(max_length=20)        # free/paid
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    team_size = models.PositiveIntegerField(default=1)
    min_number_of_participants = models.PositiveIntegerField(default=8)
    max_number_of_participants = models.PositiveIntegerField(default=32)
    bracket_type = models.CharField(max_length=30)
    tournament_rules = models.TextField(blank=True)     # HTML from Quill
    prize_type = models.CharField(max_length=20)        # distributed/winner_takes_all
    winner_prize = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tournament_logo = models.ImageField(upload_to='tournament_logos/', blank=True)
    tournament_banner = models.ImageField(upload_to='tournament_banners/', blank=True)
    is_draft = models.BooleanField(default=False)
    facebook_link = models.URLField(blank=True)
    twitter_link = models.URLField(blank=True)
    instagram_link = models.URLField(blank=True)
    youtube_link = models.URLField(blank=True)
    twitch_link = models.URLField(blank=True)
    kick_link = models.URLField(blank=True)
    tiktok_link = models.URLField(blank=True)
    bigolive_link = models.URLField(blank=True)

class TournamentPrize(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='prizes')
    position = models.PositiveIntegerField()
    prize = models.DecimalField(max_digits=10, decimal_places=2)

class TournamentSponsor(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='sponsors')
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=20)   # individual/organization
    username = models.CharField(max_length=255, blank=True)

class Game(models.Model):
    name = models.CharField(max_length=100, unique=True)
    # Used for game validation during tournament creation

class TournamentRegistration(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE)
    user = models.ForeignKey('vent_auth.User', on_delete=models.CASCADE)
    team = models.ForeignKey('vent_teams.Team', on_delete=models.SET_NULL, null=True, blank=True)
    registration_type = models.CharField(max_length=20)  # individual/team
    payment_method = models.CharField(max_length=20)     # wallet/paystack
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='pending')  # pending/confirmed/cancelled
    registered_at = models.DateTimeField(auto_now_add=True)

class BracketMatch(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE)
    round_number = models.PositiveIntegerField()
    match_number = models.PositiveIntegerField()
    participant_1 = models.ForeignKey(TournamentRegistration, on_delete=models.SET_NULL, null=True, related_name='match_as_p1')
    participant_2 = models.ForeignKey(TournamentRegistration, on_delete=models.SET_NULL, null=True, related_name='match_as_p2')
    winner = models.ForeignKey(TournamentRegistration, on_delete=models.SET_NULL, null=True, related_name='won_matches', blank=True)
    score_p1 = models.CharField(max_length=50, blank=True)
    score_p2 = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, default='pending')  # pending/in_progress/completed
    scheduled_at = models.DateTimeField(null=True, blank=True)
```

---

## 7. Acceptance Criteria

Each page is accepted when all criteria pass AND the verification track is complete.

---

### Page 1: Tournament Homepage (`/tournaments`)
**Track A** — Figma node `458:3639`

**Functional:**
- [ ] Page loads and fetches real tournaments from `GET /tournament/get-all-tournaments/`
- [ ] Featured section displays at least the tournament title, game, banner image, dates, and entry fee
- [ ] "New Tournaments" section shows recently created tournaments
- [ ] "All Tournaments" section is tabbed by game (tab labels match games returned by API)
- [ ] Clicking a tournament card navigates to `/tournaments/view-tournament?id={id}`
- [ ] "Create Tournament" button navigates to `/tournaments/create-tournament`
- [ ] Search bar appears when search icon is clicked; submitting a query filters results
- [ ] Filter dropdown filters tournaments by type
- [ ] When no tournaments exist, an empty state message is shown (not a blank area)
- [ ] Page works on 375px mobile and 1440px desktop

**Design (Track A):**
- [ ] Screenshot Figma node `458:3639` via `get_design_context`
- [ ] Screenshot the built page
- [ ] Compare: layout, card appearance, typography, colors, spacing all match
- [ ] Mark **VERIFIED** once all gaps are fixed

---

### Page 2: Tournament Detail View (`/tournaments/view-tournament?id={id}`)
**Track A** — Figma node `2338:20196`

**Functional:**
- [ ] Fetches tournament data from `GET /tournament/view-tournament/{id}` where id is the `?id=` search param
- [ ] Banner shows: tournament name, game, banner image, countdown timer, entry fee, organizer, status badge
- [ ] Status badge correctly shows "Upcoming" / "Live" / "Ended" based on start/end dates
- [ ] Countdown timer displays correct days/hours/mins remaining
- [ ] Five tabs work: Overview, Rules, Bracket, Participants, Prize — tab switches without page reload
- [ ] **Overview tab:** Shows description, game mode, type, dates, location/virtual link, organizer info
- [ ] **Rules tab:** Renders the Quill HTML rules content correctly (not raw HTML string)
- [ ] **Bracket tab:** Shows "coming soon" or a placeholder message (not raw component name text)
- [ ] **Participants tab:** Lists registered participants (or empty state if none)
- [ ] **Prize tab:** Shows prize distribution table or winner-takes-all amount
- [ ] "Join Tournament" button opens the registration modal
- [ ] If tournament is "Ended", Join button is disabled or hidden
- [ ] Share button copies tournament URL to clipboard
- [ ] Page works on 375px mobile and 1440px desktop

**Design (Track A):**
- [ ] Screenshot Figma node `2338:20196`
- [ ] Screenshot the built page (each tab)
- [ ] Compare and fix all layout/visual gaps
- [ ] Mark **VERIFIED**

---

### Page 3: Tournament Creation Wizard (`/tournaments/create-tournament`)
**Track A** — Figma node `4052:20591`

**Functional — Step 1 (Basic Info):**
- [ ] Tournament title field validates: required, max 255 chars
- [ ] Game selector shows all available games (from API or predefined list)
- [ ] Game mode field populates based on selected game
- [ ] Start date must be before end date — shows inline error if violated
- [ ] Tournament type (Online/Offline/Hybrid) updates location/virtual link visibility
- [ ] Visibility selector: Public, Private, Invite Only
- [ ] Logo upload: image preview shown after selection; file stored as File object
- [ ] Banner upload: same as logo
- [ ] Form data persists in localStorage between steps and on page refresh

**Functional — Step 2 (Format & Participants):**
- [ ] Bracket type: 6 options (Single Elimination, Double Elimination, Round Robin, Swiss, King of the Hill, Battle Royale)
- [ ] Team size, min/max participants: numeric inputs with validation (min ≤ max)
- [ ] Entry type toggle (Free / Paid) shows/hides entry fee field
- [ ] Tournament rules: Quill rich text editor — **must be loaded with `dynamic(() => import('react-quill'), { ssr: false })`**

**Functional — Step 3 (Prize Distribution):**
- [ ] Prize type: Distributed (per position) or Winner Takes All
- [ ] Distributed: allows adding prize amounts per position (1st, 2nd, 3rd, etc.)
- [ ] Winner Takes All: single prize amount field
- [ ] Total prize amount displayed and updated in real time

**Functional — Step 4 (Sponsors & Links):**
- [ ] Add sponsor: name, type (individual/organization), username fields; multiple sponsors supported
- [ ] Remove sponsor entries
- [ ] Social links: Facebook, Twitter, Instagram, YouTube, Twitch, Kick, TikTok, BIGOLive
- [ ] Web link field

**Functional — Step 5 (Review):**
- [ ] All entered data displayed clearly across sections
- [ ] "Save Draft" button: submits with `is_draft: "1"`, shows success message, clears localStorage
- [ ] "Publish" button: submits with `is_draft: "0"`, shows success message, clears localStorage
- [ ] Both buttons show loading state during submission
- [ ] Both buttons disabled while submission is in progress
- [ ] Validation errors navigate back to the relevant step with an alert
- [ ] On success, redirect to tournament homepage or draft page (currently uses `alert()` — replace with proper navigation)

**General:**
- [ ] Progress menu (step indicator) at top shows current step; clicking a previous step navigates to it
- [ ] "Next" and "Back" buttons work on each step
- [ ] User must be logged in (add `/tournaments/create-tournament` to `protectedRoutes`)
- [ ] `react-quill` must not cause SSR hydration errors

**Design (Track A):**
- [ ] Screenshot Figma node `4052:20591` for each step
- [ ] Screenshot each built step
- [ ] Compare and fix
- [ ] Mark **VERIFIED**

---

### Page 4: Tournament Drafts (`/tournaments/drafts`)
**Track B** — No Figma design exists

**Functional:**
- [ ] Fetches user's draft tournaments from `GET /tournament/view-user-drafted-tournaments/` (requires auth)
- [ ] Each DraftCard shows: tournament title, game, start/end dates
- [ ] Empty state: shows message "No saved drafts" (already implemented)
- [ ] Loading state: shows "Loading your drafts..." (already implemented)
- [ ] DraftCard has: "Edit" action (opens creation wizard with draft pre-filled) and "Publish" action
- [ ] "Delete Draft" action with confirmation dialog
- [ ] User must be logged in (add `/tournaments/drafts` to `protectedRoutes`)

**Design (Track B):**
- [ ] Create HTML mockup of DraftCard and page layout
- [ ] Get CEO approval on mockup
- [ ] Build matches approved mockup
- [ ] Mark **VERIFIED (SELF-DESIGNED)**

---

### Page 5: Tournament Registration Modal
**Track A** — Within Figma node `2338:20196`

**Functional — Individual flow:**
- [ ] Select "As an individual" → Next → Payment modal opens
- [ ] Payment: shows entry fee amount, VENT COINS balance (from real wallet API — not hardcoded 526)
- [ ] Payment method options: VENT COINS Wallet and Paystack
- [ ] VENT COINS: deduct from wallet via API call; show insufficient funds error if balance is too low
- [ ] Paystack: initiate real Paystack payment (replace setTimeout simulation)
- [ ] On payment success → Success modal with tournament name and confirmation details
- [ ] Success modal: "View Tournament" link returns to tournament detail page

**Functional — Team flow:**
- [ ] Select "As a team" → Next → Choose Team modal
- [ ] Choose Team: lists user's teams from API (currently uses hardcoded/mock data — wire to real API)
- [ ] Only teams where user is owner/admin are listed (organizer registers the team)
- [ ] Proceed → Edit Roster: select which team members to include (up to team_size limit)
- [ ] Review: shows selected team, members, tournament name, entry fee
- [ ] Proceed to Payment → same payment flow as individual
- [ ] Success modal shows team name + members registered

**General:**
- [ ] Modal closes cleanly and resets all state when clicking X or Cancel at any step
- [ ] Back button at each step returns to the previous step (not to the start)
- [ ] Registration API call (`POST /tournament/register-tournament/`) must be wired in the payment completion handler

**Design (Track A):**
- [ ] Screenshot Figma node for registration flow
- [ ] Compare each modal step with built output
- [ ] Mark **VERIFIED**

---

### Page 6: Bracket Visualization (NOT YET BUILT)
**Track A** — Within Figma node `2338:20196`

**Functional:**
- [ ] Fetches bracket data from `GET /tournament/get-tournament-brackets/{id}/`
- [ ] Renders bracket visually based on `bracket_type` (Single Elim, Double Elim, Round Robin, Swiss, KotH, Battle Royale)
- [ ] Each match shows: participant 1 vs participant 2, scores (if entered), winner highlighted
- [ ] "Upcoming" matches show TBD for unresolved slots
- [ ] Updates in real time (or on page refresh) as organizer enters scores
- [ ] Works on mobile (scrollable horizontally if needed)

**Design (Track A):**
- [ ] Screenshot Figma bracket node
- [ ] Build component in `TournamentDetailsBracket.js` (replace placeholder stub)
- [ ] Compare and verify

---

### Pages 7–X: Tournament Management (NOT YET BUILT)
**Track A** — Within Figma node `4052:20591`

These screens are for tournament organizers to manage a tournament after creation.

**Screens needed:**
- My Tournaments list (grid/list of organizer's tournaments with status badges)
- Match management (list of matches, update scores, advance bracket)
- Participant management (view registered participants, check payment status, remove/ban)
- Invite Players/Teams (search and invite specific users or teams)
- Leaderboard view
- Tournament Production screen (Phase 1 MVP — basic version; OBS/VMIX/Streamlabs integration is Phase 1 as well)

---

## 8. Task Checklist

### 🔴 Critical — Blocking MVP Launch

- [ ] **Fix `/tournaments` auth:** Add `/tournaments/create-tournament` and `/tournaments/drafts` to `protectedRoutes` in `src/middleware.js`
- [ ] **Fix react-quill hydration:** Wrap react-quill import in `FormatParticipants/tournament-rules/TournamentRules.js` with `dynamic(() => import('react-quill'), { ssr: false })`
- [ ] **Remove hardcoded wallet balance (526):** Wire `Payment.js` to real wallet balance API
- [ ] **Wire real Paystack:** Replace `setTimeout` simulation in `Payment.js` with real Paystack SDK/popup
- [ ] **Wire registration API:** Add `POST /tournament/register-tournament/` call in `handlePaymentComplete` in `Payment.js`
- [ ] **Replace hardcoded team list in Choose Team modal:** Fetch user's teams from API
- [ ] **Build bracket visualization:** Replace `TournamentDetailsBracket.js` stub with real bracket component
- [ ] **Remove all `console.log` statements** from tournament components before production

### 🟡 Important — Should be done before launch

- [ ] **Verify game-by-game listing:** Confirm whether `FIFATournaments.js`, `PUBGTournaments.js` etc. use hardcoded data or the `by_game` API response. Remove hardcoded lists if API data is available
- [ ] **Replace `alert()` in CreateTournamentComponent:** After successful publish/draft, navigate programmatically (e.g., `router.push('/tournaments')` or `/tournaments/drafts`) instead of `alert()`
- [ ] **Fix hardcoded backend URL in CreateTournamentComponent:** Line 297 uses `https://vermillionent.pythonanywhere.com/tournament/create-tournament/` directly instead of `process.env.NEXT_PUBLIC_API_URL`. Consolidate to use the env var
- [ ] **Fix hardcoded backend URL in TournamentDetailsBanner:** Image URL construction hardcodes `vermillionent.pythonanywhere.com`. Move to env var
- [ ] **Verify `/get-all-tournaments/` vs `/tournament/get-all-tournaments/`:** The create wizard uses a different URL for game fetching than the tournament homepage. Confirm which is correct
- [ ] **Add "Delete Draft" to DraftCard:** API endpoint needed from backend
- [ ] **Add "Edit Draft" to DraftCard:** Pre-populate creation wizard from draft data
- [ ] **Bracket tab placeholder:** Replace "TournamentDetailsBracket" text with a proper "Coming Soon" or skeleton UI until the bracket is built

### 🟢 Verification — Do for each built page

- [ ] Pull Figma screenshot for Tournament Homepage (`458:3639`) and compare to built `/tournaments`
- [ ] Pull Figma screenshot for Tournament Details (`2338:20196`) and compare to built `/tournaments/view-tournament`
- [ ] Pull Figma screenshot for Tournament Creation (`4052:20591`) and compare each step
- [ ] Pull Figma screenshot for Registration modal and compare each step
- [ ] Create HTML mockup for Drafts page, get CEO approval, then verify built page matches
- [ ] Mark each page as VERIFIED in this document once complete

### ⬜ Not yet started — Phase 1 remaining tournament work

- [ ] Tournament management — My Tournaments list
- [ ] Tournament management — Match scoring and bracket advancement
- [ ] Tournament management — Participant management
- [ ] Tournament management — Invite system
- [ ] Tournament management — Leaderboard
- [ ] Tournament search (frontend UI + backend endpoint)
- [ ] Tournament filter by game/type/status (partially built in UI — needs backend query param support)
- [ ] Tournament Production screen (basic version — OBS/VMIX/Streamlabs integration planned for Phase 1)

---

*This document reflects the state of the codebase as of March 2026. Update task checkboxes as items are completed.*

# 04 — Teams

**Phase:** 1 MVP (team selection is required for tournament registration)
**Status:** 🟡 Partially built — UI shells exist, all data is hardcoded
**Design track:** Track A (Figma designs exist at node `1126:15009`)
**Dependencies:** User System (05), Tournament Registration (01)

---

## Module Overview

Teams allow players to group together under a shared identity and compete as a unit in team-format tournaments. The Teams module covers:

1. **All Teams page** — browsable list of all teams; tabs for "All", "Owned by me", "Other teams"
2. **Team Profile** — public view with tabs: Overview, Members, Activity (tournament/event history), Stats
3. **Edit Team Profile** — organizer view with tabs: Profile Info, Web & Social Links, Membership settings
4. **Team Modals** — Create Team, Add Member, Transfer Ownership, Assign Role, Join Request (designed in Figma)

**Critical issue:** `AllTeams.js` renders hardcoded `cardsData` from `cardDataList.js` — no API call is made. Team Profile tabs also render hardcoded list files (`membersList.js`, `requestList.js`, `teamProfileTournamentsList.js`, `teamEventsList.js`). No API integration exists anywhere in this module.

---

## Figma Node IDs

| Screen | nodeId | Status |
|--------|--------|--------|
| All Teams (grid) | `1126:15009` | ✅ Designed (web + mobile) |
| Team Profile — Overview | within `1126:15009` | ✅ Designed (web + mobile) |
| Team Profile — Members | within `1126:15009` | ✅ Designed (web + mobile) |
| Team Profile — Join Requests | within `1126:15009` | ✅ Designed (web + mobile) |
| Activity — Tournament History | within `1126:15009` | ✅ Web, ❌ Mobile missing |
| Activity — Event History | within `1126:15009` | ✅ Web, ❌ Mobile missing |
| Edit Team Profile | within `1126:15009` | ✅ Designed (web + mobile) |
| Edit Social Links | within `1126:15009` | ✅ Designed |
| Team Settings — Membership | within `1126:15009` | ✅ Designed |
| Team Stats Overview | within `1126:15009` | ✅ Web, ❌ Mobile missing |
| Create Team (modal) | within `1126:15009` | ✅ Multi-step modal |
| Add Member (modal) | within `1126:15009` | ✅ Search / invite / invited states |
| Transfer Ownership (modal) | within `1126:15009` | ✅ |
| Assign Role (modal) | within `1126:15009` | ✅ |
| Team Wallet | BIN section | 🗑️ Deprecated — needs redesign |

---

## Pages & Components Status

```
src/app/
├── teams/
│   ├── page.js                              # ✅ Shell exists — renders AllTeams component
│   └── team-profile/
│       └── page.js                          # ✅ Shell exists — renders tab layout
└── edit-team-profile/
    └── page.js                              # ✅ Shell exists — 3 sidebar tabs

src/components/
├── teams/
│   └── all-teams/
│       ├── AllTeams.js                      # ⚠️ Hardcoded cardsData, no API call, "View Profile" link goes to /teams/team-profile (no ID)
│       └── cardDataList.js                  # ❌ Hardcoded mock data — must be replaced with API

├── team-profile/
│   ├── team-profile-banner/
│   │   └── TeamProfileBanner.js             # ⚠️ Likely hardcoded — no props, no fetch
│   ├── team-profile-bio/
│   │   └── TeamProfileBio.js                # ⚠️ Likely hardcoded — no props, no fetch
│   ├── team-profile-overview-left/
│   │   └── TeamProfileOverviewLeft.js       # ⚠️ Status unknown — likely static
│   ├── team-profile-overview-right/
│   │   ├── TeamProfileOverviewRight.js      # ⚠️ Status unknown
│   │   ├── team-profile-achievements/
│   │   │   └── TeamProfileAchievements.js   # ⚠️ Status unknown
│   │   ├── team-profile-stats/
│   │   │   └── TeamProfileStats.js          # ⚠️ Status unknown
│   │   └── team-profile-wallet-penalty/
│   │       └── TeamProfileWalletPenalty.js  # ⚠️ Wallet penalty — status unknown
│   ├── team-profile-gallery/
│   │   └── TeamProfileGallery.js            # ⚠️ Used for "Stats" tab — status unknown
│   ├── team-profile-members/
│   │   ├── TeamProfileMembers.js            # ⚠️ Renders tabs (Members / Join Requests)
│   │   ├── MembersTabComponent.js           # ⚠️ Uses hardcoded membersList.js
│   │   ├── MembersRequestsTabComponent.js   # ⚠️ Uses hardcoded requestList.js
│   │   ├── membersList.js                   # ❌ Hardcoded mock data
│   │   └── requestList.js                   # ❌ Hardcoded mock data
│   └── team-profile-activity/
│       ├── TeamProfileActivity.js           # ⚠️ Renders tournament + event history
│       ├── team-profile-tournaments-history/
│       │   ├── TeamProfileTournamentsHistory.js       # ⚠️ Status unknown
│       │   ├── TeamProfileTournamentsDetails.js       # ⚠️ Status unknown
│       │   └── teamProfileTournamentsList.js          # ❌ Hardcoded mock data
│       └── team-profile-events-history/
│           ├── TeamEventsHistory.js          # ⚠️ Status unknown
│           ├── TeamEventsDetails.js          # ⚠️ Status unknown
│           └── teamEventsList.js             # ❌ Hardcoded mock data

└── edit-team-profile/
    ├── edit-team-profile-info/
    │   ├── EditTeamProfileInfo.js            # ⚠️ Renders sub-components (likely hardcoded)
    │   ├── edit-profile-image-avatar/
    │   │   └── EditProfileImageAvatar.js     # ⚠️ Avatar upload — status unknown
    │   ├── edit-team-profile-banner/
    │   │   └── EditTeamProfileBanner.js      # ⚠️ Banner upload — status unknown
    │   ├── edit-team-profile-core-game/
    │   │   ├── EditTeamProfileCoreGame.js    # ⚠️ Game selector
    │   │   └── interests.js                  # Static list of games
    │   ├── edit-team-profile-details/
    │   │   └── EditTeamProfileDetails.js     # ⚠️ Name, bio, etc.
    │   └── edit-team-profile-interests/
    │       ├── EditTeamProfileInterests.js   # ⚠️ Multi-select interests
    │       └── interests.js                  # Static interests list
    ├── edit-team-profile-links/
    │   └── EditTeamProfileLinks.js           # ⚠️ Social links form
    └── edit-team-profile-membership/
        └── EditTeamProfileMembership.js      # ⚠️ Toggle join requests on/off
```

**Key routing issue:** `AllTeams.js` links to `/teams/team-profile` with no `?id=` parameter. Team profile will need to read from URL search params (`?id=...`) the same way tournaments and events do.

---

## API Endpoints

### Current (None — all data is hardcoded)

No API calls exist in the Teams module.

### Needed

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/team/get-all-teams/` | List all teams (paginated) |
| `GET` | `/team/get-user-teams/` | Teams the current user belongs to or owns |
| `GET` | `/team/view-team/{id}/` | Team detail (overview, members, stats) |
| `POST` | `/team/create-team/` | Create a new team |
| `PATCH` | `/team/edit-team/{id}/` | Update team info, logo, banner |
| `POST` | `/team/add-member/` | Invite a player by username/email |
| `DELETE` | `/team/remove-member/` | Remove a member |
| `POST` | `/team/accept-request/{id}/` | Accept a join request |
| `DELETE` | `/team/reject-request/{id}/` | Reject a join request |
| `POST` | `/team/transfer-ownership/` | Transfer team captain role |
| `PATCH` | `/team/assign-role/` | Assign manager/member role to player |
| `PATCH` | `/team/membership-settings/{id}/` | Toggle open/closed join requests |
| `GET` | `/team/tournaments/{id}/` | Team tournament history |
| `GET` | `/team/events/{id}/` | Team event history |

---

## Data Shape Reference

### Team Object (GET /team/view-team/{id}/)

```json
{
  "id": "team123",
  "name": "Team Alpha",
  "logo_url": "https://...",
  "banner_url": "https://...",
  "bio": "West Africa's top FIFA squad",
  "core_game": "FIFA 25",
  "interests": ["FPS", "Sports"],
  "social_links": {
    "twitter": "https://twitter.com/...",
    "discord": "https://discord.gg/...",
    "instagram": "..."
  },
  "member_count": 6,
  "max_members": 10,
  "open_to_join": true,
  "owner": {
    "user_id": "u1",
    "username": "johndoe",
    "profile_pic": "..."
  },
  "members": [
    {
      "user_id": "u1",
      "username": "johndoe",
      "role": "captain",
      "profile_pic": "...",
      "joined_at": "2026-01-15"
    }
  ],
  "stats": {
    "tournaments_played": 12,
    "tournaments_won": 3,
    "events_attended": 5,
    "win_rate": 0.42
  },
  "created_at": "2025-12-01"
}
```

### Team Listing (GET /team/get-all-teams/)

```json
{
  "status": "success",
  "data": {
    "teams": [
      {
        "id": "team123",
        "name": "Team Alpha",
        "logo_url": "...",
        "banner_url": "...",
        "core_game": "FIFA 25",
        "member_count": 6,
        "open_to_join": true
      }
    ],
    "total": 48,
    "page": 1,
    "per_page": 12
  }
}
```

### Create Team Request (POST /team/create-team/)

```js
// multipart/form-data
{
  name: "Team Alpha",
  core_game: "FIFA 25",
  bio: "...",
  interests: JSON.stringify(["FPS", "Sports"]),
  open_to_join: "1",
  logo: <File>,
  banner: <File>
}
```

---

## Django Models (Inferred)

```python
class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey('User', related_name='owned_teams', on_delete=models.CASCADE)
    logo = models.ImageField(upload_to='team_logos/', null=True)
    banner = models.ImageField(upload_to='team_banners/', null=True)
    bio = models.TextField(blank=True)
    core_game = models.CharField(max_length=100, blank=True)
    interests = models.JSONField(default=list)
    open_to_join = models.BooleanField(default=True)
    max_members = models.IntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)

class TeamMember(models.Model):
    ROLES = [('captain','Captain'),('manager','Manager'),('member','Member')]
    team = models.ForeignKey(Team, related_name='memberships', on_delete=models.CASCADE)
    user = models.ForeignKey('User', related_name='team_memberships', on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLES, default='member')
    joined_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('team', 'user')

class TeamJoinRequest(models.Model):
    STATUS = [('pending','Pending'),('accepted','Accepted'),('rejected','Rejected')]
    team = models.ForeignKey(Team, related_name='join_requests', on_delete=models.CASCADE)
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)

class TeamSocialLink(models.Model):
    team = models.OneToOneField(Team, related_name='social_links', on_delete=models.CASCADE)
    twitter = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    discord = models.URLField(blank=True)
    facebook = models.URLField(blank=True)
    youtube = models.URLField(blank=True)
    twitch = models.URLField(blank=True)
```

---

## Acceptance Criteria

### All Teams Page (`/teams`) — Track A

**Pre-condition:** User is authenticated.

- [ ] Fetches teams from `GET /team/get-all-teams/` on mount; not from `cardDataList.js`
- [ ] "All" tab shows all teams; "Owned by me" tab filters to user's teams; "Other teams" tab shows teams the user is a member of but does not own
- [ ] Team card shows: banner image, logo, team name, core game, member count
- [ ] "View Profile" links to `/teams/team-profile?id={team_id}` (not the current hardcoded `/teams/team-profile`)
- [ ] "Create Team" button opens the Create Team modal
- [ ] Pagination or infinite scroll for large result sets
- [ ] Empty state shown if user has no owned/other teams in those tabs
- [ ] Loading skeleton while fetching

### Team Profile (`/teams/team-profile?id=...`) — Track A

**Pre-condition:** Publicly viewable (or requires auth — confirm with CEO).

- [ ] Reads team ID from `?id=` URL param; fetches `GET /team/view-team/{id}/`
- [ ] Banner and bio render real data from API
- [ ] **Overview tab:** game focus, member count, social links, win rate stat
- [ ] **Members tab:** lists all members with avatar, username, role badge; separates members from join requests if viewer is captain
- [ ] **Activity tab:** tournament history table and event history table from real API
- [ ] **Stats tab:** win/loss record, tournaments played, events attended
- [ ] If viewer is the team captain: edit button visible, join requests visible in Members tab
- [ ] If viewer is not captain: "Request to Join" button (if team `open_to_join: true`)

### Edit Team Profile (`/edit-team-profile?id=...`) — Track A

**Pre-condition:** User is the team captain.

- [ ] Only accessible if current user is team owner — redirect otherwise
- [ ] **Profile Info tab:** logo upload, banner upload, name, bio, core game selector, interests multi-select; submits `PATCH /team/edit-team/{id}/` as multipart/form-data
- [ ] **Web & Social Links tab:** fields for Twitter, Instagram, Discord, Facebook, YouTube, Twitch; submits to same endpoint
- [ ] **Membership tab:** toggle open/closed join requests; submits `PATCH /team/membership-settings/{id}/`
- [ ] Success/error feedback after each save

### Create Team Modal — Track A

- [ ] Multi-step modal matching Figma design
- [ ] Step 1: team name, core game, bio
- [ ] Step 2: logo upload, banner upload
- [ ] Step 3: interests, join request toggle
- [ ] Step 4: review + submit
- [ ] Submits `POST /team/create-team/`
- [ ] On success: redirects to new team profile

### Add Member Modal — Track A

- [ ] Search field searches users by username
- [ ] Shows "Invite" button per result; "Invited" state after click
- [ ] Submits `POST /team/add-member/`

---

## Task Checklist

### 🔴 Critical (blocking tournament registration)

- [ ] Build `GET /team/get-all-teams/` and `GET /team/get-user-teams/` — needed for tournament registration team selector
- [ ] Fix `AllTeams.js` — replace `cardDataList.js` with real API fetch
- [ ] Fix team profile links — add `?id=` to "View Profile" anchor
- [ ] Build `GET /team/view-team/{id}/` and wire to team profile components

### 🔴 Critical (blocking team self-management)

- [ ] Build `POST /team/create-team/` and wire Create Team modal
- [ ] Build `PATCH /team/edit-team/{id}/` and wire Edit Team Profile form
- [ ] Replace all hardcoded list files: `membersList.js`, `requestList.js`, `teamProfileTournamentsList.js`, `teamEventsList.js`

### 🟡 Important

- [ ] Add Member modal → `POST /team/add-member/`
- [ ] Accept/reject join requests in Members tab
- [ ] Transfer Ownership modal → `POST /team/transfer-ownership/`
- [ ] Assign Role modal → `PATCH /team/assign-role/`
- [ ] Membership toggle → `PATCH /team/membership-settings/{id}/`
- [ ] Request to Join button for non-members

### 🟢 Verification (after build)

- [ ] Pull Figma screenshot via `get_design_context` for node `1126:15009`
- [ ] Compare All Teams page against Figma
- [ ] Compare Team Profile against Figma (all 4 tabs)
- [ ] Compare Edit Team Profile against Figma (all 3 tabs)
- [ ] Mark pages **VERIFIED** once gaps resolved

### ⬜ Phase 2+

- [ ] Team search / filter by game
- [ ] Team wallet (redesign from BIN version)
- [ ] Team analytics / advanced stats
- [ ] Mobile activity tables (missing from Figma design)

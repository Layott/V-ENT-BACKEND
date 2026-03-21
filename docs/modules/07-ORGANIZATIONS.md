# 07 — Organizations

**Phase:** 2
**Status:** ❌ Not built — no design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** User System (05), Wallet (06), Teams (04)

---

## Module Overview

Organizations are the corporate layer of V-ENT — the entity above teams. An Organization can:
- Own multiple teams
- Create and manage tournaments and events under the organization's brand
- Have an organization wallet separate from user wallets
- Assign staff roles (owner, manager, staff, analyst) to members
- Appear as the organizer/host on all tournaments and events they run

Organizations are primarily for esports organizations, gaming clubs, universities, and event companies operating on the platform at scale.

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Organization Creation | ❌ Not designed |
| Organization Profile | ❌ Not designed |
| Organization Roles & Permissions | ❌ Not designed |
| Organization Wallet | ❌ Not designed |

> All screens require Track B: HTML mockup → CEO approval → build.

---

## Pages & Components Status

Nothing is built. No routes, no components exist.

Planned structure:

```
src/app/
├── organizations/
│   ├── page.js                              # ⬜ List all organizations / search
│   ├── profile/
│   │   └── page.js                          # ⬜ Organization profile (?id=...)
│   └── create/
│       └── page.js                          # ⬜ Organization creation wizard

src/components/
└── organizations/
    ├── OrgCard.js                           # ⬜ Organization listing card
    ├── OrgProfile/
    │   ├── OrgBanner.js                     # ⬜ Logo, banner, name, bio
    │   ├── OrgTeams.js                      # ⬜ Teams under this org
    │   ├── OrgTournaments.js                # ⬜ Tournaments hosted
    │   ├── OrgEvents.js                     # ⬜ Events hosted
    │   └── OrgMembers.js                    # ⬜ Staff roster with roles
    ├── OrgCreateWizard.js                   # ⬜ Multi-step creation (mirrors team creation pattern)
    └── OrgSettings/
        ├── OrgRoles.js                      # ⬜ Assign/manage staff roles
        └── OrgWallet.js                     # ⬜ Org wallet management
```

---

## API Endpoints (Needed)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/org/list/` | List all organizations |
| `GET` | `/org/view/{id}/` | Organization detail |
| `POST` | `/org/create/` | Create an organization |
| `PATCH` | `/org/edit/{id}/` | Update org info, logo, banner |
| `POST` | `/org/add-member/` | Invite user to org with a role |
| `PATCH` | `/org/assign-role/` | Change a member's role |
| `DELETE` | `/org/remove-member/` | Remove staff from org |
| `POST` | `/org/add-team/` | Link an existing team to this org |
| `DELETE` | `/org/remove-team/` | Unlink a team |
| `GET` | `/org/tournaments/{id}/` | Tournaments created by this org |
| `GET` | `/org/events/{id}/` | Events hosted by this org |
| `GET` | `/org/wallet/{id}/` | Org wallet balance + transactions |

---

## Data Shape Reference

### Organization Object

```json
{
  "id": "org123",
  "name": "Vermillion Esports",
  "logo_url": "https://...",
  "banner_url": "https://...",
  "bio": "Premier esports org in West Africa",
  "owner": { "user_id": "u1", "username": "johndoe" },
  "members": [
    { "user_id": "u2", "username": "janedoe", "role": "manager" }
  ],
  "teams": [
    { "team_id": "t1", "name": "Team Alpha", "logo_url": "..." }
  ],
  "social_links": { "twitter": "...", "discord": "..." },
  "stats": {
    "tournaments_hosted": 10,
    "events_hosted": 3,
    "total_prize_pool_distributed": 150000
  },
  "verified": false,
  "created_at": "2025-10-01"
}
```

---

## Django Models (Inferred)

```python
class Organization(models.Model):
    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey('User', related_name='owned_orgs', on_delete=models.CASCADE)
    logo = models.ImageField(upload_to='org_logos/', null=True)
    banner = models.ImageField(upload_to='org_banners/', null=True)
    bio = models.TextField(blank=True)
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class OrgMember(models.Model):
    ROLES = [('owner','Owner'),('manager','Manager'),('staff','Staff'),('analyst','Analyst')]
    org = models.ForeignKey(Organization, related_name='memberships', on_delete=models.CASCADE)
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLES)
    joined_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('org', 'user')

class OrgTeam(models.Model):
    org = models.ForeignKey(Organization, related_name='teams', on_delete=models.CASCADE)
    team = models.ForeignKey('Team', on_delete=models.CASCADE)
    linked_at = models.DateTimeField(auto_now_add=True)
```

---

## Acceptance Criteria

### Organization Profile — Track B

- [ ] Shows org name, logo, banner, bio, verified badge (if applicable)
- [ ] Tabs: Overview (stats, top teams), Teams, Tournaments, Events, Members
- [ ] Owner sees "Edit" and "Settings" buttons
- [ ] Public users see "Follow" (Phase 2+)

### Organization Creation — Track B

- [ ] Multi-step: org name, logo/banner, bio, social links → review → submit
- [ ] On success: redirects to new org profile
- [ ] Only authenticated users can create an org

### Roles & Permissions — Track B

- [ ] Owner can assign roles to members
- [ ] Roles determine what actions can be taken: create tournaments (manager+), edit org (owner only), etc.
- [ ] Owner can transfer ownership to another manager

---

## Task Checklist

### ⬜ Phase 2 (all)

- [ ] Design HTML mockup for org profile — CEO approval
- [ ] Design HTML mockup for org creation wizard — CEO approval
- [ ] Design HTML mockup for roles/permissions settings — CEO approval
- [ ] Django models: `Organization`, `OrgMember`, `OrgTeam`
- [ ] All CRUD endpoints
- [ ] Organization profile page
- [ ] Organization creation wizard
- [ ] Roles management UI
- [ ] Link org to tournament creation (organizer field)
- [ ] Org wallet (separate from user wallet)
- [ ] Org verification badge system (admin approves)

# 14 — Community

**Phase:** 2 (basic), 3+ (advanced features)
**Status:** ❌ Not built — no design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** User System (05), Teams (04), Events (02), Anime (10)

---

## Module Overview

The Community module is V-ENT's social layer — the connective tissue between competitive gaming, events, and anime culture. It surfaces user-generated conversations and content in structured community spaces.

Core features:
1. **Community Feed** — activity feed showing recent tournaments, events, team activity, and posts from followed users/teams
2. **Discussion Forums / Threads** — game-specific or topic-specific discussion boards (FIFA tips, PUBG strategies, event announcements)
3. **User Following** — follow other players, teams, and organizers
4. **Notifications** — alerts for tournament results, join requests, messages, mentions
5. **Direct Messages** — 1:1 messaging between users (Phase 3)
6. **Watch Parties** — scheduled group viewing sessions for live tournament streams (Phase 3)

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Community / Feed | ❌ Not designed |
| Discussion Forums | ❌ Not designed |
| User Following | ❌ Not designed |
| Notifications | ❌ Not designed |
| Direct Messages | ❌ Not designed |
| Watch Parties | ❌ Not designed |

> All screens require Track B.

---

## Pages & Components Status

Nothing is built. No community routes exist.

Planned structure:

```
src/app/
├── community/
│   ├── page.js                              # ⬜ Community hub — feed + trending topics
│   ├── forum/
│   │   ├── page.js                          # ⬜ Forum category list
│   │   └── thread/
│   │       └── page.js                      # ⬜ Thread detail (?id=...)
│   └── notifications/
│       └── page.js                          # ⬜ All notifications (already partially needed for other modules)
└── messages/
    └── page.js                              # ⬜ DM inbox (Phase 3)

src/components/
└── community/
    ├── Feed.js                              # ⬜ Activity feed: posts, tournament results, announcements
    ├── PostCard.js                          # ⬜ Post with avatar, text, images, like/comment/share
    ├── ForumCategoryList.js                 # ⬜ List of forum categories with post counts
    ├── ThreadList.js                        # ⬜ Threads in a category
    ├── ThreadDetail.js                      # ⬜ Thread + replies
    ├── ReplyForm.js                         # ⬜ Compose a reply
    ├── NotificationBell.js                  # ⬜ Header notification icon with unread count badge
    ├── NotificationList.js                  # ⬜ Full notification page
    ├── NotificationItem.js                  # ⬜ Individual notification with action button
    ├── FollowButton.js                      # ⬜ Follow/unfollow a user or team
    └── DirectMessages/
        ├── DMInbox.js                       # ⬜ Conversation list
        └── DMThread.js                      # ⬜ Conversation view (Phase 3 — WebSocket)
```

---

## API Endpoints (Needed)

### Community Feed

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/community/feed/` | Personalized activity feed for current user |
| `POST` | `/community/post/` | Create a post |
| `POST` | `/community/post/{id}/like/` | Like a post |
| `POST` | `/community/post/{id}/comment/` | Comment on a post |
| `DELETE` | `/community/post/{id}/` | Delete own post |

### Forums

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/community/forums/` | List forum categories |
| `GET` | `/community/forums/{category}/threads/` | Threads in a category (paginated) |
| `GET` | `/community/forums/thread/{id}/` | Thread detail with replies |
| `POST` | `/community/forums/{category}/threads/` | Create a new thread |
| `POST` | `/community/forums/thread/{id}/reply/` | Reply to a thread |
| `POST` | `/community/forums/thread/{id}/like/` | Like a thread |

### Following

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/community/follow/user/{id}/` | Follow a user |
| `DELETE` | `/community/follow/user/{id}/` | Unfollow a user |
| `POST` | `/community/follow/team/{id}/` | Follow a team |
| `DELETE` | `/community/follow/team/{id}/` | Unfollow a team |
| `GET` | `/community/following/` | List who the current user follows |
| `GET` | `/community/followers/` | List who follows the current user |

### Notifications

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/notifications/` | All notifications (paginated) |
| `GET` | `/notifications/unread-count/` | Unread count for badge |
| `POST` | `/notifications/read/{id}/` | Mark notification as read |
| `POST` | `/notifications/read-all/` | Mark all as read |

### Direct Messages (Phase 3)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/messages/` | All DM conversations |
| `GET` | `/messages/{user_id}/` | Message thread with a user |
| `POST` | `/messages/{user_id}/` | Send a message |
| `WS` | `/ws/messages/{user_id}/` | Real-time WebSocket for DMs |

---

## Data Shape Reference

### Feed Item

```json
{
  "id": "feed001",
  "type": "tournament_result",
  "actor": { "user_id": "u1", "username": "johndoe", "profile_pic": "..." },
  "content": "johndoe won 1st place in FIFA Pro League",
  "metadata": { "tournament_id": "t1", "placement": 1 },
  "likes": 42,
  "comments": 8,
  "created_at": "2026-03-21T15:00:00Z"
}
```

Feed item types: `tournament_result`, `event_attended`, `team_joined`, `post`, `achievement`, `announcement`.

### Notification Item

```json
{
  "id": "notif001",
  "type": "join_request",
  "title": "New join request",
  "body": "janedoe wants to join Team Alpha",
  "action_url": "/teams/team-profile?id=team123",
  "is_read": false,
  "created_at": "2026-03-21T10:00:00Z"
}
```

Notification types: `join_request`, `join_accepted`, `tournament_result`, `tournament_invite`, `event_reminder`, `message`, `mention`, `wager_result`, `payout_approved`.

---

## Django Models (Inferred)

```python
class Post(models.Model):
    author = models.ForeignKey('User', on_delete=models.CASCADE)
    content = models.TextField()
    images = models.JSONField(default=list)
    likes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

class ForumCategory(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    icon = models.CharField(max_length=50, blank=True)
    order = models.IntegerField(default=0)

class ForumThread(models.Model):
    category = models.ForeignKey(ForumCategory, related_name='threads', on_delete=models.CASCADE)
    author = models.ForeignKey('User', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    body = models.TextField()
    likes = models.IntegerField(default=0)
    reply_count = models.IntegerField(default=0)
    is_pinned = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class ForumReply(models.Model):
    thread = models.ForeignKey(ForumThread, related_name='replies', on_delete=models.CASCADE)
    author = models.ForeignKey('User', on_delete=models.CASCADE)
    body = models.TextField()
    likes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

class UserFollow(models.Model):
    follower = models.ForeignKey('User', related_name='following', on_delete=models.CASCADE)
    following = models.ForeignKey('User', related_name='followers', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('follower', 'following')

class Notification(models.Model):
    TYPES = [
        ('join_request','Join Request'), ('join_accepted','Join Accepted'),
        ('tournament_result','Tournament Result'), ('tournament_invite','Tournament Invite'),
        ('event_reminder','Event Reminder'), ('message','Message'),
        ('mention','Mention'), ('wager_result','Wager Result'), ('payout_approved','Payout Approved'),
    ]
    recipient = models.ForeignKey('User', related_name='notifications', on_delete=models.CASCADE)
    type = models.CharField(max_length=30, choices=TYPES)
    title = models.CharField(max_length=200)
    body = models.TextField()
    action_url = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class DirectMessage(models.Model):
    sender = models.ForeignKey('User', related_name='sent_messages', on_delete=models.CASCADE)
    recipient = models.ForeignKey('User', related_name='received_messages', on_delete=models.CASCADE)
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)
```

---

## Acceptance Criteria

### Notification Bell (all pages, Phase 1) — Track C (logic only)

This should be built in Phase 1 even though the full community module is Phase 2 — notifications are needed for team join requests, tournament invites, and payout approvals.

- [ ] `NotificationBell` embedded in `Header` and `MobileHeader`
- [ ] Polls `GET /notifications/unread-count/` every 60 seconds (or WebSocket in Phase 3)
- [ ] Badge shows unread count; disappears at 0
- [ ] Click → `/community/notifications` page or dropdown list

### Notifications Page (`/community/notifications`) — Track B

- [ ] Lists all notifications sorted newest first
- [ ] Unread notifications visually highlighted
- [ ] "Mark all read" button
- [ ] Clicking a notification marks it read and navigates to `action_url`

### Community Feed (`/community`) — Track B

- [ ] Fetches personalized feed for current user
- [ ] Shows: tournament results, event attendances, team joins, posts
- [ ] Like and comment on posts inline
- [ ] Infinite scroll or "Load more" pagination
- [ ] "Post something" text input at top

### Forums — Track B

- [ ] Category list shows: name, description, thread count, last activity
- [ ] Thread list shows: title, author, reply count, last reply date
- [ ] Thread detail: original post + all replies in chronological order
- [ ] Reply form at bottom (authenticated users only)
- [ ] Pin/lock threads visible to admins only

### Following — Track B

- [ ] `FollowButton` on user profile, team profile pages
- [ ] Following a user adds their posts/activity to your feed
- [ ] Follower/following counts shown on profiles

---

## Task Checklist

### 🔴 Phase 1 (Partial — Notifications Only)

- [ ] `Notification` Django model + notification endpoints
- [ ] `NotificationBell` component added to `Header` and `MobileHeader`
- [ ] Notifications page (`/community/notifications`)
- [ ] Notification triggers wired to: team join requests, tournament results, payout approvals

### ⬜ Phase 2 (Full Community)

- [ ] Design HTML mockups for all community screens — CEO approval
- [ ] Community feed page + post creation
- [ ] Forum categories + thread system
- [ ] Following system (user follow + team follow)
- [ ] Like/comment on posts and threads
- [ ] AI content moderation integration (flags to admin — see 12-AI-FEATURES.md)

### ⬜ Phase 3

- [ ] Direct Messages (WebSocket — django-channels)
- [ ] Watch parties for live tournament streams
- [ ] Mention system (`@username` in posts/replies)
- [ ] Push notifications (browser or mobile PWA)

### ⬜ Phase 5

- [ ] Anime discussion sub-forums
- [ ] AMV like/comment from community feed
- [ ] Co-reading room announcements in feed

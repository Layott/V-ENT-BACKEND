# 03 — Production & Streaming Integration

**Phase:** 1 MVP (must ship with bracket visualization)
**Status:** ❌ Nothing built — no Figma design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** Tournament brackets (01-TOURNAMENTS.md), backend match data API

---

## Module Overview

The Production module gives tournament organizers a live broadcast control room inside V-ENT. It covers:

1. **Tournament Overlay Engine** — a configurable overlay for OBS/VMIX/Streamlabs that displays match info, scores, player cards, and sponsor logos over the stream
2. **Score & Match Control** — organizer UI for starting/pausing/ending matches, updating scores in real time, and advancing brackets
3. **Spectator Screen Scanning** — camera-based tool that reads the game screen (score HUD, kill feed) and auto-updates match data
4. **Stream Dashboard** — organizer HQ for a live tournament: match queue, current match status, chat/alerts feed

This is a Phase 1 MVP requirement. Without it, tournament organizers have no way to run a broadcast event through the platform.

---

## Figma Node IDs

| Screen | nodeId | Status |
|--------|--------|--------|
| Tournament Production | `4052:20591` area | 🟡 Basic version only — needs redesign for streaming software integration |
| Overlay Config | ❌ None | Not designed |
| Score Update UI | see Tournament Management `4052:20591` | 🟡 Partial |
| Screen Scanning | ❌ None | Not designed — "screen scanning feature needs original design" |

> **Verified in Figma audit:** "Production screen needs redesign for streaming software integration (OBS/VMIX/Streamlabs). Screen scanning feature needs original design."

---

## Pages & Components Status

**Nothing is built.** No routes, no components, no API calls exist for this module.

Planned page structure once built:

```
src/app/
└── tournaments/
    └── production/
        └── page.js                  # ⬜ Tournament production dashboard (route: /tournaments/production?id=...)

src/components/
└── production/
    ├── StreamDashboard.js            # ⬜ Main organizer HQ layout
    ├── MatchController.js            # ⬜ Start/pause/end match, score inputs
    ├── BracketAdvancer.js            # ⬜ Advance winner to next round
    ├── OverlayConfigurator.js        # ⬜ Configure OBS/VMIX overlay appearance
    ├── overlay/
    │   ├── OverlayRenderer.js        # ⬜ The actual overlay output page (used as browser source in OBS)
    │   ├── OverlayMatchCard.js       # ⬜ Player/team vs. card with scores
    │   ├── OverlaySponsor.js         # ⬜ Sponsor logo banner
    │   └── OverlayKillFeed.js        # ⬜ Live kill feed (if game supports)
    └── screen-scanning/
        ├── ScreenScanner.js          # ⬜ Camera capture + OCR/CV processing
        └── ScanResultPreview.js      # ⬜ Confirm scanned scores before submission
```

---

## API Endpoints

### Current (None)

No production-related endpoints have been called from the frontend.

### Needed

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/tournament/production/{id}/` | Fetch live production state for tournament |
| `POST` | `/tournament/match/start/` | Start a match |
| `POST` | `/tournament/match/end/` | End a match, record result |
| `PATCH` | `/tournament/match/score/` | Update score mid-match |
| `POST` | `/tournament/match/advance/` | Advance winner to next bracket slot |
| `GET` | `/tournament/overlay/{id}/` | Fetch overlay config (public, no auth — used by OBS browser source) |
| `PATCH` | `/tournament/overlay/{id}/` | Update overlay settings |
| `POST` | `/tournament/scan/upload/` | Upload screen scan image → returns extracted score data |
| `GET` | `/tournament/production/{id}/live/` | WebSocket or long-poll for real-time state |

> **Real-time requirement:** Match score updates must be visible to viewers within ~2 seconds. Consider WebSocket (`django-channels`) or server-sent events for the overlay endpoint. Do not use polling for the overlay itself.

---

## Data Shape Reference

### Production State Object (GET /tournament/production/{id}/)

```json
{
  "tournament_id": "abc123",
  "current_match": {
    "match_id": "m01",
    "round": 2,
    "team_a": { "team_id": "t1", "name": "Team Alpha", "score": 3, "logo_url": "..." },
    "team_b": { "team_id": "t2", "name": "Team Beta", "score": 1, "logo_url": "..." },
    "status": "in_progress",
    "started_at": "2026-03-21T14:00:00Z",
    "game": "FIFA 25"
  },
  "match_queue": [...],
  "completed_matches": [...],
  "overlay_config": {
    "theme": "dark",
    "show_sponsors": true,
    "sponsor_logos": ["..."],
    "primary_color": "#4caf50"
  }
}
```

### Overlay Page (public, no auth)

The overlay is a separate Next.js page (`/tournaments/overlay?id=...`) intended to be used as an OBS Browser Source. It:
- Must be publicly accessible (no auth)
- Polls or subscribes to live match data
- Renders as a transparent-background 1920×1080 page
- Must support CSS `background: transparent` for compositing

### Screen Scan Request (POST /tournament/scan/upload/)

```js
// multipart/form-data
{
  tournament_id: "abc123",
  match_id: "m01",
  game: "FIFA 25",
  image: <File>  // screenshot or camera capture
}
// Response:
{
  "status": "success",
  "data": {
    "team_a_score": 3,
    "team_b_score": 1,
    "confidence": 0.92,
    "raw_text": "3 - 1"
  }
}
```

---

## Django Models (Inferred — Does Not Exist Yet)

```python
class Match(models.Model):
    tournament = models.ForeignKey('Tournament', on_delete=models.CASCADE)
    round_number = models.IntegerField()
    bracket_slot = models.IntegerField()
    team_a = models.ForeignKey('Team', null=True, related_name='match_team_a', on_delete=models.SET_NULL)
    team_b = models.ForeignKey('Team', null=True, related_name='match_team_b', on_delete=models.SET_NULL)
    score_a = models.IntegerField(default=0)
    score_b = models.IntegerField(default=0)
    winner = models.ForeignKey('Team', null=True, related_name='match_winner', on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=[('pending','Pending'),('in_progress','In Progress'),('completed','Completed')])
    started_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)

class OverlayConfig(models.Model):
    tournament = models.OneToOneField('Tournament', on_delete=models.CASCADE)
    theme = models.CharField(max_length=20, default='dark')
    show_sponsors = models.BooleanField(default=True)
    primary_color = models.CharField(max_length=7, default='#4caf50')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

---

## Acceptance Criteria

### Stream Dashboard (`/tournaments/production?id=...`) — Track B

**Pre-condition:** User is authenticated, owns/manages the tournament.

- [ ] Page is only accessible to the tournament organizer (not public)
- [ ] Shows current match: team names, logos, live scores
- [ ] Shows match queue: upcoming matches in bracket order
- [ ] Organizer can start, pause, and end matches with confirmation
- [ ] Score update inputs (numeric) submit immediately; scores update for all connected overlay views
- [ ] On match end, organizer selects winner → bracket advances automatically
- [ ] Any error (network, unauthorized) shows a user-facing error message — never silently fails

### Overlay Page (`/tournaments/overlay?id=...`) — Track B

**Pre-condition:** Page is public (no auth required). Intended as OBS Browser Source.

- [ ] Renders at 1920×1080 with `background: transparent`
- [ ] Shows current match: team A name + score vs. team B name + score
- [ ] Auto-refreshes scores (polling or WebSocket) without page reload
- [ ] Shows sponsor logos in rotation if `show_sponsors: true`
- [ ] No navigation, header, sidebar, or footer rendered
- [ ] Works at `/tournaments/overlay?id={tournament_id}`

### Overlay Configurator — Track B

- [ ] Organizer can toggle sponsor visibility, choose theme color
- [ ] Live preview of overlay before going live
- [ ] Save config persists to backend

### Score Updater — Track B

- [ ] Separate modal/panel within stream dashboard
- [ ] Input fields for both team scores
- [ ] Submit sends `PATCH /tournament/match/score/`
- [ ] Confirmation dialog before submission
- [ ] Optimistic UI update, rolls back on error

### Screen Scanner — Track B

**This is the most complex component in this module.**

- [ ] Activates device camera or accepts screenshot upload
- [ ] Sends image to `/tournament/scan/upload/`
- [ ] Shows confidence score and extracted numbers
- [ ] Organizer confirms before auto-filling score fields
- [ ] Works for at minimum: FIFA, PUBG, Mobile Legends (the 3 games currently in the platform)
- [ ] Shows "low confidence" warning if confidence < 0.70 — do not auto-submit

---

## Task Checklist

### 🔴 Must Design Before Any Build

- [ ] Create HTML mockup for Stream Dashboard — get CEO approval (Track B)
- [ ] Create HTML mockup for Overlay page (transparent bg, 1920×1080) — get CEO approval
- [ ] Create HTML mockup for Screen Scanner UI — get CEO approval
- [ ] Determine: WebSocket (django-channels) or long-poll for real-time? Decision needed before backend starts

### 🔴 Critical Build (Phase 1 MVP)

- [ ] `Match` and `OverlayConfig` Django models — backend ticket required
- [ ] `POST /tournament/match/start|end/` and `PATCH /tournament/match/score/` endpoints
- [ ] `GET /tournament/overlay/{id}/` public endpoint
- [ ] `POST /tournament/scan/upload/` endpoint (OCR/CV backend)
- [ ] `/tournaments/production?id=...` page — stream dashboard
- [ ] `/tournaments/overlay?id=...` page — OBS browser source
- [ ] Score update integration: overlay auto-updates when score changes

### 🟡 Important (Phase 1, can ship slightly after MVP)

- [ ] `OverlayConfigurator` — theme + sponsor config UI
- [ ] `ScreenScanner` — camera capture + OCR confirmation flow
- [ ] Bracket advance on match completion (ties into 01-TOURNAMENTS.md bracket task)
- [ ] Match queue display in stream dashboard
- [ ] OBS/VMIX/Streamlabs setup guide page or tooltip within configurator

### ⬜ Phase 2+

- [ ] Multiple concurrent match views in stream dashboard (multi-table tournament)
- [ ] Chat/alerts feed integration in stream dashboard
- [ ] Kill feed overlay component
- [ ] Custom overlay themes / branding per tournament
- [ ] Replay highlight system
- [ ] Viewer-side spectator mode (public-facing bracket + score tracker)

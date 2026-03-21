# 12 — AI Features

**Phase:** 2+ (some Phase 2 features, most Phase 3+)
**Status:** ❌ Not built — no design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** Tournaments (01), Events (02), User System (05), Community (14)

---

## Module Overview

V-ENT AI features augment the platform with intelligent assistance and automation. Planned features from the BRD:

1. **Tournament Bracket Auto-Generation** — AI suggests bracket format, seeding, and schedule based on participant count and game type (Phase 2)
2. **Match Prediction** — predict match outcomes based on team/player history; shown as fun stat, not wager-related (Phase 2)
3. **Personalized Feed** — recommendations for tournaments, events, and content based on user's games, interests, and activity (Phase 2)
4. **Chatbot / Help Assistant** — in-platform support bot that answers questions about platform features, rules, and navigation (Phase 2)
5. **Content Moderation Assist** — AI-flagged content for admin review; not autonomous deletion (Phase 3)
6. **Performance Analytics** — player/team stat insights, win pattern analysis shown in profile (Phase 3)
7. **Anime/Content Recommendations** — suggest manga series, AMVs, and co-reading rooms based on user preferences (Phase 5)

> **Note on model choice:** When building any AI-powered feature, use the latest available Claude model. Current recommended IDs: `claude-opus-4-6` (most capable), `claude-sonnet-4-6` (balanced), `claude-haiku-4-5-20251001` (fast/cheap). Do not hardcode model IDs — make them configurable via environment variables.

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| All AI feature screens | ❌ Not designed |

> All screens require Track B.

---

## Pages & Components Status

Nothing is built. No AI-related routes or components exist.

Planned structure (Phase 2 first):

```
src/app/
└── (AI features are embedded in existing pages — no standalone /ai/ route needed initially)

src/components/
└── ai/
    ├── TournamentBracketSuggestor.js        # ⬜ Wizard step enhancement in CreateTournamentComponent
    ├── MatchPredictionBadge.js              # ⬜ Small prediction display on tournament detail
    ├── PersonalizedFeed.js                  # ⬜ "Recommended for you" section on home page
    ├── HelpChatbot.js                       # ⬜ Floating chat widget available platform-wide
    └── ContentModerationFlag.js             # ⬜ Admin panel integration — flagged items queue
```

---

## API Endpoints (Needed)

> These could be:
> a) Backend Django endpoints that call Claude API server-side (recommended — keeps API key off client)
> b) Next.js API route handlers that proxy to Claude API (acceptable if backend not ready)
> Never call Claude API directly from the browser — API key would be exposed.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/ai/bracket-suggest/` | Input: participant count, game, format → Output: recommended bracket structure |
| `GET` | `/ai/match-prediction/{match_id}/` | Prediction for a specific upcoming match |
| `GET` | `/ai/feed/recommendations/` | Personalized tournament/event/content recommendations |
| `POST` | `/ai/chat/` | Chatbot: send message → receive assistant response |
| `POST` | `/ai/moderation/flag/` | Submit content for AI moderation review |
| `GET` | `/ai/analytics/player/{user_id}/` | Player performance insights |
| `GET` | `/ai/analytics/team/{team_id}/` | Team performance insights |

---

## Data Shape Reference

### Bracket Suggestion Request/Response

```json
// POST /ai/bracket-suggest/
// Request:
{
  "participant_count": 16,
  "game": "FIFA 25",
  "format_preference": "single_elimination",
  "max_duration_hours": 8
}

// Response:
{
  "suggested_format": "single_elimination",
  "rounds": 4,
  "matches_per_round": [8, 4, 2, 1],
  "estimated_duration_hours": 6,
  "seeding_recommendation": "random",
  "explanation": "With 16 participants and a max 8-hour window, single elimination gives 15 total matches. Estimated ~24 min/match for FIFA."
}
```

### Match Prediction Response

```json
{
  "match_id": "m01",
  "team_a": { "team_id": "t1", "name": "Team Alpha", "win_probability": 0.63 },
  "team_b": { "team_id": "t2", "name": "Team Beta", "win_probability": 0.37 },
  "confidence": "medium",
  "basis": "Based on 12 previous matches: Team Alpha won 8, lost 4",
  "disclaimer": "This is an entertainment estimate only. Results are not guaranteed."
}
```

### Chatbot (POST /ai/chat/)

```json
// Request:
{
  "message": "How do I create a tournament?",
  "conversation_id": "conv_123",  // null for new conversation
  "context": "user is on /tournaments page"
}

// Response:
{
  "reply": "To create a tournament, click the 'Create Tournament' button on the Tournaments page. You'll go through a 5-step wizard covering basic info, format, prizes, sponsors, and review...",
  "conversation_id": "conv_123",
  "suggested_actions": [
    { "label": "Go to Create Tournament", "url": "/tournaments/create-tournament" }
  ]
}
```

---

## Implementation Notes

### Backend Architecture (Recommended)

```python
# Backend Django view — calls Claude API server-side
import anthropic

def bracket_suggest(request):
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    data = request.data

    message = client.messages.create(
        model=settings.AI_MODEL,  # e.g., "claude-sonnet-4-6" — from env, not hardcoded
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"Suggest a tournament bracket structure for {data['participant_count']} participants playing {data['game']}..."
        }]
    )
    # Parse response and return structured JSON
```

**Environment variables needed:**
```
ANTHROPIC_API_KEY=sk-ant-...
AI_MODEL=claude-sonnet-4-6
```

### Chatbot Conversation State

Conversation history must be stored server-side (not in browser localStorage) to keep API keys secure and to enable conversation continuity across sessions.

---

## Acceptance Criteria

### Bracket Suggestor (embedded in tournament creation wizard) — Track B

- [ ] Step 2 "Format & Participants" shows an "AI Suggest" button
- [ ] On click: calls `/ai/bracket-suggest/` with current form values → pre-fills format fields
- [ ] User can override any suggestion
- [ ] Suggestion includes an explanation ("why this format") in a collapsible tooltip

### Match Prediction Badge (tournament detail page) — Track B

- [ ] Shown on upcoming match cards in the bracket view
- [ ] Displays win probability bars for each team
- [ ] Clear "entertainment only" disclaimer
- [ ] Only shows when sufficient match history exists (confidence ≠ "insufficient_data")

### Help Chatbot — Track B

- [ ] Floating button visible platform-wide (except overlay page and auth pages)
- [ ] Opens as a slide-in panel
- [ ] Maintains conversation history for the session
- [ ] Has quick-reply suggestion chips for common questions
- [ ] Falls back gracefully if AI endpoint is unavailable (shows "Support unavailable — try again later")

### Personalized Feed — Track B

- [ ] "Recommended for you" section on logged-in home page
- [ ] Recommendations based on user's games and interests from profile
- [ ] Shows tournament cards, event cards, and content links
- [ ] "Not interested" button removes a recommendation from the feed

---

## Task Checklist

### ⬜ Phase 2

- [ ] Design HTML mockup for chatbot widget — CEO approval
- [ ] Design HTML mockup for personalized feed section — CEO approval
- [ ] Backend: `/ai/bracket-suggest/` endpoint (calls Claude API server-side)
- [ ] Backend: `/ai/chat/` endpoint with conversation history storage
- [ ] Backend: `/ai/feed/recommendations/` endpoint
- [ ] Build `HelpChatbot.js` widget
- [ ] Build `PersonalizedFeed.js` for home page
- [ ] Embed bracket suggestor in tournament creation wizard (Step 2)
- [ ] Set `ANTHROPIC_API_KEY` and `AI_MODEL` in backend env

### ⬜ Phase 3

- [ ] Match prediction badge on tournament bracket view
- [ ] Player performance analytics in user profile
- [ ] Team performance analytics in team profile
- [ ] Content moderation AI flag queue in admin dashboard

### ⬜ Phase 5

- [ ] Anime/content recommendations (integrates with anime catalog data)

### ⬜ Ongoing

- [ ] Monitor AI response quality; add feedback mechanism ("Was this helpful?")
- [ ] Rate limiting on AI endpoints to control API costs
- [ ] Fallback handling when AI endpoint is slow or unavailable

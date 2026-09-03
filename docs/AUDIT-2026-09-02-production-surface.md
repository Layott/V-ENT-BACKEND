# Audit: the production surface, 2 September 2026

CEO: "the production surface, how far has it been built? what has been wired?
check in detail." Ledger: `V-ENT/GATES-PRODUCTION-AUDIT.md`, 10 of 10.

Nothing was changed. Every number below was measured on 2 September against
`main` (backend `41f5385b`, frontend `0577083`) and against production.

---

## 1. The short answer

The production surface is two things, both built, both live, both verified on
production this week:

| | What | State |
|---|---|---|
| **Studio** | Eight graphics V-ENT draws itself, driven from an operator console, each a browser-source URL | Built for tournaments only. Spine, console, element pages, feed, URL fix, retire fix all shipped and walked |
| **Overlays** | A designer's own HTML uploaded, marked with `data-vent` attributes, filled from a live feed | Built for tournaments AND events, with 14 starter templates (8 + 6) |

Plus one older piece: a Twitch, YouTube or Kick link on a tournament, embedded on
the public page (`StreamPanel` in view-tournament).

**What is not built:** anything from the spec beyond overlays: screen scanning /
OCR, a stream dashboard with a match queue, an in-app overlay configurator,
sponsor graphics for tournaments, kill feed, viewership numbers, paid streams,
"livestream directly to the platform", any plan gating, and a studio for events.

**What this audit found wrong (new, not previously recorded):** nine items in
section 9. The two that matter most: the screen contradicts itself about whether
production exists (three signals, one page), and the Rivalry Series that the studio
was built for is cancelled twice with no teams, two days before it runs.

---

## 2. Backend routes (13 paths, 17 method-endpoints)

Prefixes: `/tournament/` = `vent_tournament/urls.py`, `/event/` = `vent_event/urls.py`,
bare = `vent/urls.py`. Auth: Bearer = `login_session_token` in the header; token =
credential in the URL because a browser source has no cookie.

### Studio (`vent_tournament/views_studio.py`, 15.2 KB)

| Method | Path | View | Auth | Does |
|---|---|---|---|---|
| GET | `/tournament/<ref>/studio/sessions/` | `sessions` | Bearer, `may_use_studio` | Last 20 sessions + the 8 kinds |
| POST | same | `sessions` | same | Starts a broadcast; ends any live one first |
| GET | `/tournament/<ref>/studio/sessions/<id>/` | `session_detail` | same | One session (no frontend caller; the panel reads the list) |
| POST | same, `{"end": true}` | `session_detail` | same | Ends it, clears every element |
| POST | `/tournament/<ref>/studio/sessions/<id>/element/<kind>/` | `element` | same | `{active, payload}`; payload merged, 409 if ended |
| GET | `/studio/<token>/feed/` | `feed` | token | One request for every element plus the tournament's own teams/live/standings; `retired` payload once ended |

`<ref>` accepts id or slug (`_tournament`). `may_use_studio` = tournament creator,
or `may_override(user, 'manage_tournaments')`. No plan check, by decision.

### Overlays (`vent_tournament/views_overlays.py`, 28.8 KB; `views_overlay_feed.py`, 13.5 KB)

| Method | Path | View | Auth | Does |
|---|---|---|---|---|
| GET | `/tournament/<ref>/overlay-feed/` | `overlay_feed` | public | tournament, teams (with players, standings), live matches, version |
| GET | `/tournament/<ref>/overlays/` | `overlays` | Bearer, organiser | list + `fields`, `field_help`, `repeat_help`, `prompt`, `templates` |
| POST | same | `overlays` | same | upload a file, or `{"template": key}` |
| DELETE | `/tournament/<ref>/overlays/<id>/` | `overlay_detail` | same | delete |
| POST | `/tournament/<ref>/overlays/<id>/rotate/` | `rotate` | same | new token, old URL dead |
| GET | `/event/<ref>/overlay-feed/` | `event_overlay_feed` | public | event, programme, sponsors, doors, version |
| GET/POST | `/event/<ref>/overlays/` | `event_overlays` | Bearer, organiser | same as tournament |
| DELETE | `/event/<ref>/overlays/<id>/` | `event_overlay_detail` | same | |
| POST | `/event/<ref>/overlays/<id>/rotate/` | `event_overlay_rotate` | same | |
| GET | `/overlay/<token>/` | `serve_overlay` | token | the uploaded file with `static/overlay-runtime.js` injected, `data-every="4000"`, feed chosen by owner kind |

Supporting modules: `overlay_binding.py` (marked / scripted / none, danger list),
`overlay_templates.py` (22.6 KB, 14 generated files, house palette, no loops, no
strokes), `static/overlay-runtime.js` (7.4 KB binder + poller),
`docs/OVERLAY-PROMPT.md`.

## 3. Models (3, in `vent_tournament/models.py` 1139-1330)

| Model | Migration | Fields |
|---|---|---|
| `TournamentOverlay` | 0030; `event` FK in 0033 | tournament FK (null), event FK (null), name, file (`tournament_overlays/`), token (48, unique), binding, bound_fields JSON, created_at/updated_at/created_by. `owner` property. Token minted in `save()`, added to `update_fields` |
| `BroadcastSession` | 0031 | tournament FK, name, token (48, unique), status live/ended, started_by, started_at, ended_at. `is_live` |
| `BroadcastElement` | 0031 | session FK, kind (8 choices), payload JSON, is_active, updated_at. `unique_together (session, kind)` |

Kinds: scorebar, standings, lower_third, player_card, bracket, ticker, intro, outro.

**Note:** `BroadcastSession` has a tournament FK only. There is no event studio.

## 4. Frontend (1 route, 2 panels, 3 redirects, 2 orphan CSS files)

| File | Lines | Role | Rendered by |
|---|---|---|---|
| `src/app/studio/[token]/[kind]/page.js` | 296 | Element page. Polls the feed every 1200 ms, redraws only when `version` moves, keeps last frame on error, stops on `retired`, transparent, 8 components | OBS / vMix as a browser source |
| `src/components/studio/StudioPanel.js` | 315 | Operator console: start, end, copy URL, put on air / take off, per-kind fields, refreshes every 5 s while live, earlier broadcasts | `tournaments/manage` tab `production` |
| `src/components/overlays/OverlaysPanel.js` | 342 | Upload, list, copy URL, rotate, delete, the prompt (fetched, never local), field help, templates | `tournaments/manage` tab `production` (kind=tournament); `events/manage` tab `overlays` (kind=event) |
| `src/app/production/page.js` | 15 | ComingSoon "Production hub", phase "Live now", points at My tournaments | route `/production` |
| `src/app/tournaments/production/page.js` | 15 | ComingSoon "Production panel" | route |
| `src/app/tournaments/overlay/page.js` | 15 | ComingSoon "Stream overlay" | route |
| `src/app/production/overlay-editor/overlay-editor.module.css` | 16,464 B | **Orphan.** No page.js beside it, nothing imports it | none |
| `src/app/production/scene-editor/scene-editor.module.css` | 11,557 B | **Orphan.** Same | none |

Other touch points: `robots.js` disallows `/studio/` and `/tournaments/overlay`
for every crawler including the AI list; `PageHelp` is silent on
`/tournaments/overlay`; `pageGuides.js` still carries guides for the three
redirect routes; `Sidebar.js:98-104` renders Production as a non-link with an
"Unavailable" tag; `tournaments/manage/page.js:208-213` renders a disabled
"Production Panel" button titled "Production is not available yet".

Translation: 38 `studio.*` keys per language (114 across en/fr/pt), `check-keys`
5143 checked 0 missing, `dict-parity` en=fr=pt 5854.

Design rules: `studio-panel.module.css`, `studio.module.css`,
`overlays-panel.module.css` contain no `animation`, no `@keyframes`, no
`box-shadow`, no `1px solid`. The live dot is a static filled circle.

## 5. Wiring, route by route

`python tools/endpoint-callers.py --list`: 214 called, 55 deliberately not
called. Orphans printed: `event/<str:event_id>/overlay-feed/` and `wallet/deduct/`.

| Backend | Caller | Wired |
|---|---|---|
| studio sessions GET/POST | `StudioPanel.load` / `start` | yes |
| studio session_detail POST end | `StudioPanel.end` | yes |
| studio session_detail GET | nobody | by design, harmless |
| studio element POST | `StudioPanel.push` | yes |
| studio feed | element page `read()` | yes |
| tournament overlay-feed | `overlay-runtime.js` inside OBS | yes, named in the deliberate list |
| tournament overlays GET/POST/DELETE/rotate | `OverlaysPanel` (`base`) | yes |
| event overlay-feed | `overlay-runtime.js` inside OBS | yes, but **not in the deliberate list**, so the checker reports it as an orphan on every run |
| event overlays GET/POST/DELETE/rotate | `OverlaysPanel kind="event"` | yes |
| serve_overlay | OBS, URL shown by `OverlaysPanel` | yes |
| templates (POST `{template}`) | `OverlaysPanel` "Start from one of ours" | yes |

Both consoles pass the tab through the URL (`?tab=production`, `?tab=overlays`)
so the tab survives a reload and can be linked.

## 6. Tests, live probes, production data

**Tests:** 6 modules, 98 tests, OK in 9.8 s (`tests_studio` 16, `tests_studio_urls`
10, `tests_overlays` 24, `tests_event_overlays` 19, `tests_overlay_vocabulary` 15,
`tests_overlay_templates` 14). The vocabulary chain template -> vocabulary -> feed
is pinned; the URL split (pages from `FRONTEND_URL`, feed from
`build_absolute_uri`) is pinned; the retired feed is pinned.

**Live, 2 September:**

| URL | Answer |
|---|---|
| `api/studio/nosuchtoken/feed/` | 404 JSON `NOT_FOUND` (route present) |
| `api/overlay/nosuchtoken/` | 404 HTML "Not found" |
| `api/tournament/lagos-codm-clash/overlay-feed/` | 200, 3039 B, 4 teams |
| `api/tournament/lagos-codm-clash/overlays/` | 401 `AUTH_REQUIRED` |
| `api/tournament/lagos-codm-clash/studio/sessions/` | 403 `NOT_ORGANIZER` (no token) |
| `api/tournament/rivalvry-series-s2/overlay-feed/` | 200, `teams: []`, `live: []` |
| `v-ent.co/studio/<x>/scorebar`, `/bracket` | 200 (client renders nothing without a live token) |
| `/production`, `/tournaments/production`, `/tournaments/overlay` | 200 ComingSoon |

**Production database (SSH, Django shell):** BroadcastSession 1 (id 3, "9/2/2026",
ended, tournament 26, started 2 Sep); BroadcastElement 1; TournamentOverlay 0 (no
organiser has uploaded or started from a template).

**The Rivalry Series rows:** 26 `rivalvry-series-s2` and 28 `rivalvry-series-s2-2`,
both titled "RIVALVRY SERIES S2", both **cancelled**, both **0 registrations**,
both starting 2026-09-04 10:00 UTC, both with a YouTube link. `GATES-DRAFT-DUP.md`
A5 (which row to keep) is still open and is the CEO's call.

## 7. Against the reference material

Sources: FEATURES DEEP p20-22 (organiser section c/e/f), PREMIUM #16, #27, #28,
#33, BRD 4.2, `V-ENT-FRONTEND/docs/modules/03-PRODUCTION.md`, and the
ESOCCER reference map section 7.

| Promised | State | Where |
|---|---|---|
| Integrate with Twitch / YouTube for broadcasting (p20) | **built** | `youtube_link`, `twitch_link`, `kick_link`; `StreamPanel` embeds |
| Send metrics and visuals to streaming software (p20, PREMIUM #27) | **built** | studio feed + 8 elements; overlays + runtime |
| Real-time data and graphics in the stream (#27) | **built** | 1.2 s poll with `version`; `data-every` 4 s for uploads |
| Premium graphic options (#28) | **partial** | 14 templates exist; nothing is gated, nothing is "premium" |
| Auto-generated visuals for leaderboards, MVPs, kills (p22) | **partial** | standings and ticker draw the table; no MVP or per-metric graphic |
| Infographics of structure and standings (p22) | **partial** | bracket element shows live rows only, not the whole bracket |
| Sponsor integrations during the tournament (p22) | **absent for tournaments** | event feed has `sponsors` and a sponsor wall; tournament feed has no sponsors key |
| Paid streams, pay to watch (#33) | **absent** | |
| Livestream directly to the platform (PREMIUM org #16) | **absent** | |
| Viewership data, peak viewers (p21) | **absent** | |
| Spectator screen scanning / OCR (BRD 4.2, module doc) | **absent** | |
| Stream dashboard with match queue and current match (module doc) | **partial** | Match Control is its own tab; the studio shows elements, not the queue |
| Overlay configurator: theme, sponsor toggle, live preview (module doc) | **absent** | a template is a file the organiser edits |
| Kill feed (module doc) | **absent** | |
| Real-time via WebSocket, "do not use polling" (module doc) | **deliberately not** | polling chosen; one request per second per element page, and a venue hotspot was the reason |
| ESOCCER trigger grid / session + view token / transparent pages | **built** | the console and `/studio/<token>/<kind>` are that pattern |
| ESOCCER design tokens + branding per tenant | **absent** | house palette hard-coded in `overlay_templates.py` |
| ESOCCER visual builder | **absent** | the two orphan CSS files are the only trace |
| vMix API bridge | **absent** | browser source only |

The module doc still opens with "Status: Nothing built" and plans routes
(`/tournaments/production?id=`, `/tournaments/overlay?id=`) that are now
ComingSoon redirects with numeric-id addresses the slug rule forbids. It is a
historical document at this point.

## 8. Gating and pricing

`may_use_studio()` is ownership or the `manage_tournaments` override. The
comment says the subscription check goes there and only there. Overlays use
`_may_manage` / `_may_manage_event`, same shape.

The premium PDF puts "Advanced Streaming Integration" in Super USER premium; the
pricing proposal moves it to an organisation tier because everything it reads
belongs to whoever runs the tournament. Nothing can be gated until the tier
contents and the ticketing fee are signed off (`tasks/todo.md`,
`tasks/pricing-proposal.md`).

## 9. What this audit found, new

1. **One page says production is unavailable, available, and live at once.**
   Sidebar: "Production UNAVAILABLE" (non-link). Console header: a disabled
   "Production Panel" button titled "Production is not available yet", beside
   a Production tab that works. `/production`: "LIVE NOW". Seen in Chrome and on
   the emulator. Commit `8c03cb6` fixed three ComingSoon pages and left the
   sidebar and the header button.
2. **The refusal on a tournament is written for an event.** `api.NOT_ORGANIZER`
   in all three dictionaries reads "Only the event organizer or their door staff
   can do that." The code is raised at 20 backend sites: 9 in `vent_event`, 11 in
   `vent_tournament` (studio 3, overlays 6, invitations 2). A signed-in
   non-owner opening a tournament's Production tab reads about door staff.
   Same class as `project_api_error_translation`: count a code's distinct
   messages before translating it.
3. **The console renders live controls to a signed-in non-owner.** As
   `naijagameevo` (not the organiser), `/tournaments/rivalvry-series-s2/manage`
   loads every tab, the Production tab shows "Start a broadcast" and "Choose
   File" live, and the network shows 403 on `studio/sessions/` and `overlays/`.
   `check-signed-out` cannot see this because the viewer is signed in. It is the
   middle role from `feedback_walk_as_the_middle_role`.
4. **The tournament the studio was built for has nothing to feed.** Rivalry
   Series S2 runs 4 September. Both rows are cancelled with 0 registrations; the
   feed answers `teams: []`. Scorebar, standings, player card, bracket and
   ticker all return null on an empty feed. Only lower third, intro and outro
   (operator-typed) would draw.
5. **No studio for events.** `BroadcastSession` is tournament-only. Events have
   upload and 6 templates but no operator console. `tools/check-parity.py` has 8
   rows and none for the studio, so the gap is recorded nowhere.
6. **A standing false orphan.** `event/<id>/overlay-feed/` is fetched by the
   runtime, exactly like the tournament feed, but only the tournament feed is in
   the deliberate list. The checker prints it on every run.
7. **Two orphan stylesheets, 28 KB.** `src/app/production/overlay-editor/` and
   `scene-editor/` hold a CSS module each and no page. From the mock era.
8. **Eight duplicate requests on console load.** view-tournament, participants,
   brackets and setting are each fetched twice when the console opens.
9. **"Thirteen" templates is fourteen.** `TEMPLATES_FOR_TOURNAMENT` 8 +
   `TEMPLATES_FOR_EVENT` 6. The handover and memory say thirteen.

## 10. Previously recorded and still open

- `GATES-DRAFT-DUP.md` A5: which Rivalry row to keep. CEO decision.
- Plan gating: waits on pricing.
- `docs/modules/03-PRODUCTION.md` is stale.

## 11. Ledger

`GATES-PRODUCTION-AUDIT.md`: G1-G10, 10 of 10 checked, evidence on each.

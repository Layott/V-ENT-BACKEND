# Module 02: Events
**Phase:** 2
**Last Updated:** March 2026
**Figma File:** [VENT-Main](https://www.figma.com/design/Ne1xquUxx1yZc0NhkN8kUE/VENT-Main) — fileKey: `Ne1xquUxx1yZc0NhkN8kUE`

---

## 1. Module Overview

The events module covers:
- Browsing and discovering events (physical, virtual, hybrid)
- Viewing full event details across five tabs
- Creating events via a 5-step wizard (organizer flow)
- Ticketing system (general admission, VIP, tiered pricing)
- QR-based check-in and attendance tracking
- Tournament-event linking (tournaments embedded inside events)
- Vendor shop system for event-day commerce

**What's live:** Event listing (`/events`) and view event (`/events/view-event?id=`) are built and wired to real API. Event creation wizard (`/events/create-event`) is built but has API field mapping issues and no draft support.

**What's a stub/broken:** `EventDetailsBracket.js` and `EventDetailsTournaments.js` status unknown — check if stubs. The event creation form sends `session_token` as a form field instead of Authorization header, which may conflict with how the backend handles auth.

**What's not built:** Ticketing system, QR check-in, tournament-event linking, vendor shop system, event management dashboard, event search/filter, event registration/attendees flow.

---

## 2. Figma Node IDs

| Screen | Node ID | Status |
|--------|---------|--------|
| Explore Events (homepage) | `783:7978` | ✅ Built — needs visual comparison |
| Event Details (view) | *(within `783:7978`)* | ✅ Built — needs visual comparison |
| Event Creation Wizard | ❌ No Figma design | ✅ Built without design — needs CEO approval |
| Event Management Dashboard | ❌ No Figma design | ❌ Not built |
| Ticketing System | ❌ No Figma design | ❌ Not built |
| Tournament-Event Linking UI | ❌ No Figma design | ❌ Not built |
| Vendor Shop System | ❌ No Figma design | ❌ Not built |

---

## 3. Pages & Components — Status

### Pages (`src/app/events/`)

| Route | File | Auth Required | Status |
|-------|------|--------------|--------|
| `/events` | `page.js` | ✅ Yes (in middleware `protectedRoutes`) | ✅ Built |
| `/events/view-event?id={id}` | `view-event/page.js` | ✅ Yes | ✅ Built |
| `/events/create-event` | `create-event/page.js` | ✅ Yes | ✅ Built (wizard UI) — API issues |

> ⚠️ **Auth gap:** `/events/create-event` starts with `/events` so it IS protected by middleware. But the event creation form sends `session_token` as a FormData field AND as an Authorization header — backend should only use the header. Verify which the backend expects and remove the redundant field.

### Component Tree

```
src/components/events/
├── EventsComponent.js               ✅ Uses axios; fetches from GET /event/get-all-events/
│                                       Separates featured and upcoming events
│                                       Uses session token (required — events listing is auth-gated)
├── events-featured/
│   ├── EventsFeatured.js            ✅ Featured events display
│   └── eventsFeaturedList.js        ⚠️ Check: may be hardcoded fallback data
├── upcoming-events/
│   ├── UpcomingEvents.js            ✅ Upcoming events list
│   └── upcomingEventsList.js        ⚠️ Check: may be hardcoded fallback data
└── all-events/
    ├── AllEvents.js                 ✅ Game-tabbed events (check if API-driven or hardcoded)
    ├── fifa-events/                 ⚠️ Likely hardcoded fifaEventsList.js
    ├── fortnite-events/             ⚠️ Likely hardcoded
    ├── freefire-events/             ⚠️ Likely hardcoded
    ├── minecraft-events/            ⚠️ Likely hardcoded
    └── pubg-events/                 ⚠️ Likely hardcoded

src/components/view-event/
├── event-details-banner/
│   └── EventDetailsBanner.js        ✅ Banner, register button — check if Join/Register is wired
├── event-details-overview/
│   ├── EventDetailsOverview.js      ✅ Two-column layout
│   ├── event-details-overview-left/
│   │   └── EventDetailsOverviewLeft.js  ✅
│   └── event-details-overview-right/
│       ├── EventDetailsOverviewRight.js ✅
│       └── SocialIcons.js               ✅
├── event-details-tournament/
│   ├── EventDetailsTournaments.js   ⚠️ Status unknown — likely stub or hardcoded
│   └── event-details-rules-left/
│       └── EventDetailsTournamentsLeft.js ⚠️ Status unknown
├── event-details-bracket/
│   └── EventDetailsBracket.js       ⚠️ Likely stub — verify
├── event-details-participants/
│   ├── EventDetailsParticipants.js  ⚠️ Check if API-driven or uses participantsList.js hardcoded
│   └── participantsList.js          ⚠️ Likely hardcoded
└── event-details-prize/
    ├── EventDetailsPrize.js         ⚠️ Check if API-driven or uses eventResults.js hardcoded
    └── eventResults.js              ⚠️ Likely hardcoded

src/components/create-event-component/  ✅ 5-step wizard (mirrors tournament wizard)
├── CreateEventComponent.js          ✅ Parent — uses useRouter, no localStorage persistence (unlike tournament)
│                                       ⚠️ Sends session_token as form field AND Authorization header
│                                       ⚠️ Tries 4 different game field names (game, game_title, game_name, title)
│                                       ⚠️ No draft support (no is_draft field)
│                                       ⚠️ No success redirect — unclear what happens after submit
├── progress-menu/ProgressMenu.js    ✅
├── basic-info/                      ✅ (uses tournament field names — needs event-specific fields)
├── format-participants/             ✅
├── prize-distribution/              ✅
├── sponsors-links/                  ✅
└── review/                          ✅
```

> ⚠️ **Critical:** The event creation wizard was built by copying the tournament wizard. It uses `tournament_title`, `tournament_type`, `tournament_banner`, etc. — all tournament field names. The event API likely expects different field names (`name`, `event_type`, `banner`, etc.). This field mismatch is likely causing submission failures.

---

## 4. API Endpoints

| Method | Endpoint | Auth | Used By | Status |
|--------|----------|------|---------|--------|
| `GET` | `/event/get-all-events/` | Bearer token | Events homepage + view-event fallback | ✅ Wired — returns `{ featured, upcoming, by_game }` |
| `GET` | `/event/view-event/{id}` | Optional Bearer | View event detail (primary) | ✅ Wired — id via `?id=` URL param |
| `POST` | `/event/create-event/` | Bearer token | Event creation wizard | ✅ Wired — but field names may be mismatched |

### Endpoints Still Needed

| Method | Endpoint (suggested) | Purpose |
|--------|---------------------|---------|
| `POST` | `/event/register-event/` | Attendee registration / ticket purchase |
| `GET` | `/event/view-event/{id}/attendees/` | Attendees tab — registered attendees |
| `GET` | `/event/view-event/{id}/tournaments/` | Tournaments tab — linked tournaments |
| `GET` | `/event/get-organizer-events/` | Event management — organizer's event list |
| `PUT` | `/event/edit-event/{id}/` | Edit a published event |
| `DELETE` | `/event/delete-event/{id}/` | Delete or cancel an event |
| `POST` | `/event/create-ticket-type/` | Create ticket tiers (GA, VIP, etc.) |
| `POST` | `/event/verify-ticket/` | QR code check-in — validate ticket |
| `GET` | `/event/get-attendance/{id}/` | Attendance tracking |
| `POST` | `/event/link-tournament/` | Link a tournament to an event |
| `POST` | `/event/vendor-shop/create/` | Create vendor shop for an event |

---

## 5. Data Shape Reference

### Event object (from `/event/view-event/{id}`)
```js
{
  event_id: number,   // Note: some places use 'id', some use 'event_id'
  id: number,
  name: string,                // Event title
  desc: string,                // Description
  event_type: string,          // "virtual" | "physical" | "hybrid"
  start_date: string,          // ISO 8601 (field name may differ from tournament)
  end_date: string,
  location: string,
  virtual_link: string,
  entry_fee: number | string,
  banner: string,              // Relative /media/ path or full URL
  logo: string,                // Organizer logo
  // Image processing in EventsComponent:
  // banner_image = getAbsoluteUrl(event.banner)
  // organizer_logo = getAbsoluteUrl(event.logo)
  featured: boolean,
  upcoming: boolean,
}
```

### Event creation payload — CURRENT (broken field names)
The wizard currently sends these tournament-named fields to the event API:
```js
// What the wizard sends vs. what the event API likely expects:
'name'        ← formData.tournament_title   // ✅ Correct
'event_type'  ← mapped from tournament_type // ✅ "virtual"/"physical"
'desc'        ← tournament_description      // ✅ Correct
'game' / 'game_title' / 'game_name' / 'title' ← game  // ❌ Unclear which field API uses
'session_token' ← sessionToken              // ⚠️ Redundant with Authorization header
// Missing: ticket fields, vendor shop fields, tournament linking
```

---

## 6. Django Models

```python
# vent_events app

class Event(TimestampMixin):
    organizer = models.ForeignKey('vent_auth.User', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    desc = models.TextField(blank=True)
    event_type = models.CharField(max_length=20)  # virtual/physical/hybrid
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True)
    virtual_link = models.URLField(blank=True)
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    banner = models.ImageField(upload_to='event_banners/', blank=True)
    logo = models.ImageField(upload_to='event_logos/', blank=True)
    is_featured = models.BooleanField(default=False)
    status = models.CharField(max_length=20, default='upcoming')  # upcoming/live/ended
    facebook_link = models.URLField(blank=True)
    twitter_link = models.URLField(blank=True)
    instagram_link = models.URLField(blank=True)
    youtube_link = models.URLField(blank=True)
    twitch_link = models.URLField(blank=True)

class TicketType(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_types')
    name = models.CharField(max_length=100)         # "General Admission", "VIP"
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    quantity_sold = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)

class Ticket(models.Model):
    ticket_type = models.ForeignKey(TicketType, on_delete=models.CASCADE)
    attendee = models.ForeignKey('vent_auth.User', on_delete=models.CASCADE)
    qr_code = models.UUIDField(default=uuid.uuid4, unique=True)
    is_checked_in = models.BooleanField(default=False)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    purchased_at = models.DateTimeField(auto_now_add=True)
    payment_method = models.CharField(max_length=20)  # wallet/paystack

class EventTournamentLink(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    tournament = models.ForeignKey('vent_tournaments.Tournament', on_delete=models.CASCADE)
    shared_ticketing = models.BooleanField(default=False)

class VendorShop(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='vendor_shops')
    vendor = models.ForeignKey('vent_auth.User', on_delete=models.CASCADE)
    shop_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

class VendorProduct(models.Model):
    shop = models.ForeignKey(VendorShop, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    image = models.ImageField(upload_to='vendor_products/', blank=True)
```

---

## 7. Acceptance Criteria

### Page 1: Events Homepage (`/events`)
**Track A** — Figma node `783:7978`

- [ ] Requires login (already enforced by middleware)
- [ ] Fetches events via `GET /event/get-all-events/` using session token
- [ ] Featured events section displays event name, banner, date, type badge
- [ ] Upcoming events section shows correctly sorted list
- [ ] Game-tabbed "All Events" section works (confirm API vs. hardcoded data)
- [ ] Search bar filters events by name when submitted
- [ ] Filter dropdown filters by event type (virtual/physical/hybrid)
- [ ] "Create Event" button navigates to `/events/create-event`
- [ ] Clicking an event card navigates to `/events/view-event?id={id}`
- [ ] Empty state shown when no events are returned
- [ ] Works on 375px and 1440px
- [ ] Pull Figma node `783:7978`, compare, mark **VERIFIED**

### Page 2: View Event (`/events/view-event?id={id}`)
**Track A** — Within Figma node `783:7978`

- [ ] Fetches event via `GET /event/view-event/{id}` first, falls back to searching `get-all-events` if needed
- [ ] localStorage caching of event data works and is cleared when stale
- [ ] Banner shows event name, type, dates, location/virtual link, organizer
- [ ] Five tabs work: Overview, Tournaments, Bracket, Participants, Prize
- [ ] **Overview tab:** Description, dates, location, virtual link, social links
- [ ] **Tournaments tab:** Lists linked tournaments (or "No tournaments linked" empty state)
- [ ] **Bracket tab:** Replace stub with either bracket view or "No bracket for this event"
- [ ] **Participants tab:** Lists attendees (not hardcoded `participantsList.js`)
- [ ] **Prize tab:** Shows prize distribution (not hardcoded `eventResults.js`)
- [ ] "Register" / "Get Tickets" button opens ticket purchase flow
- [ ] Error state shows Retry button (already implemented)
- [ ] Compare to Figma node, mark **VERIFIED**

### Page 3: Event Creation Wizard (`/events/create-event`)
**Track B** — No Figma design

**First: Fix the broken API integration**
- [ ] Audit all field names sent to `POST /event/create-event/` — align with backend API docs
- [ ] Remove duplicate `session_token` form field; use only Authorization header
- [ ] Resolve game field naming issue (only send the field the backend accepts)
- [ ] Add `is_draft` support (save as draft like tournament wizard)
- [ ] After submit, redirect to events page or event detail (not silent)
- [ ] Add localStorage persistence (wizard state lost on refresh currently)

**Then: Verify it's a true event creation flow (not just renamed tournament)**
- [ ] "Basic Info" step collects event-specific fields: name, type (virtual/physical/hybrid), description, dates, location OR virtual link, organizer logo, banner
- [ ] "Format & Participants" step for events: capacity, ticket types, entry fee (not tournament bracket type)
- [ ] "Prize Distribution" step: optional for events (not all events have prizes)
- [ ] "Sponsors & Links" step: same as tournament
- [ ] "Review" step: shows all event data correctly

**CEO approval (Track B):**
- [ ] Create HTML mockup of correct event creation flow
- [ ] Get CEO approval
- [ ] Build/fix to match approved mockup
- [ ] Mark **VERIFIED (SELF-DESIGNED)**

### Page 4: Ticketing System (NOT BUILT)
**Track B** — No Figma design

- [ ] Create ticket types for an event (name, price, quantity, description)
- [ ] Attendee purchases ticket: select type → payment (VENT COINS / Paystack) → confirmation
- [ ] QR code generated per ticket and emailed/displayed to attendee
- [ ] Organizer QR scanner: camera-based or manual QR entry for check-in
- [ ] Attendance dashboard: total registered, checked in, remaining capacity
- [ ] Ticket sales report per type

### Page 5: Tournament-Event Linking (NOT BUILT)
**Track B** — No Figma design

- [ ] Organizer can link one or more tournaments to an event
- [ ] Linked tournaments appear in the "Tournaments" tab of view-event
- [ ] Option to share ticketing (buying event ticket grants tournament entry)
- [ ] Event-embedded tournament shows event branding on tournament detail page

### Page 6: Vendor Shop System (NOT BUILT)
**Track B** — No Figma design

- [ ] Event organizer can create a vendor shop slot for an event
- [ ] Vendor applies to sell at event; organizer approves
- [ ] Vendor adds products (name, price, quantity, image)
- [ ] Attendees can browse and purchase from vendor shops during event
- [ ] Platform takes commission on vendor sales (per BRD revenue model)

---

## 8. Task Checklist

### 🔴 Critical — Fix broken event creation

- [ ] **Audit and fix event creation field names:** Compare `CreateEventComponent.js` form fields against actual `POST /event/create-event/` API spec — every field name must match
- [ ] **Remove redundant `session_token` form field:** Use only `Authorization: Bearer` header
- [ ] **Resolve game field ambiguity:** Remove the 4-field workaround (`game`, `game_title`, `game_name`, `title`) — confirm which field the backend uses and send only that
- [ ] **Add localStorage persistence to event creation wizard:** Currently loses data on refresh (tournament wizard has this, event wizard does not)
- [ ] **Add success redirect after event creation:** Currently unclear what happens after submit
- [ ] **Add draft support to event creation:** Match tournament wizard's `is_draft` field

### 🟡 Important — Data and UI fixes

- [ ] **Audit game-tab components for hardcoded data:** Confirm if `fifaEventsList.js`, `fortniteEventsList.js` etc. are hardcoded — remove and use API `by_game` response if available
- [ ] **Fix EventDetailsTournaments stub:** Implement or show empty state
- [ ] **Fix EventDetailsBracket stub:** Show empty state or remove tab if events don't have brackets
- [ ] **Wire EventDetailsParticipants to API:** Remove hardcoded `participantsList.js`
- [ ] **Wire EventDetailsPrize to API:** Remove hardcoded `eventResults.js`
- [ ] **Remove inline styles from view-event error/retry buttons:** Use CSS Modules and global button classes

### 🟢 Verification

- [ ] Pull Figma node `783:7978` and compare to `/events` page
- [ ] Compare view-event built page to Figma
- [ ] Get CEO approval on event creation mockup
- [ ] Mark each page **VERIFIED** once fixed

### ⬜ Phase 2 — Not yet started

- [ ] Ticketing system (ticket types, purchase, QR generation)
- [ ] QR check-in scanner (organizer-facing)
- [ ] Attendance tracking dashboard
- [ ] Tournament-event linking UI
- [ ] Vendor shop system
- [ ] Event management dashboard (organizer post-creation management)
- [ ] Event search and filter (backend query param support needed)

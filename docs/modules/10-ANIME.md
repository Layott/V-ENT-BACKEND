# 10 — Anime Features

**Phase:** 5
**Status:** ❌ Not built — no code; Landing Page Figma exists showing the feature section; app page stub may exist
**Design track:** Track A for features referenced in Landing Page Figma; Track B for all other screens
**Dependencies:** User System (05), Community (14)

---

## Module Overview

V-ENT targets the intersection of competitive gaming and anime culture — a significant overlap in the African youth demographic. The Anime module covers:

1. **Manga Reader** — browser-based manga reading with chapter navigation
2. **AMV (Anime Music Video) Gallery** — user-uploaded AMVs, like/comment/share
3. **Co-Reading Rooms** — synchronized reading sessions where multiple users read together in real time
4. **Anime Catalog** — browsable database of anime series with episode tracking
5. **Anime Community** — discussions, recommendations, watch parties (overlaps with 14-COMMUNITY.md)

**Landing Page reference:** The Figma Landing Page (`3538:12281`) shows an "Anime Feature" section with a tag, heading, description, CTA button, and image grid. This is marketing content — the actual feature screens are not designed.

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Anime Feature (Landing Page) | ✅ `3538:12281` (web), `4078:26151` (mobile) — marketing only |
| Manga Reader | ❌ Not designed |
| AMV Gallery | ❌ Not designed |
| Co-Reading Room | ❌ Not designed |
| Anime Catalog | ❌ Not designed |
| User Watchlist / Read-list | ❌ Not designed |

---

## Pages & Components Status

```
src/app/
└── anime/
    └── page.js                              # ⚠️ Likely exists (protected route in middleware) — status unknown, probably a stub
```

Check `src/app/anime/page.js` — it is listed in middleware's `protectedRoutes` which means the file exists but the page content is unknown. Read before building.

Planned full structure:

```
src/app/
└── anime/
    ├── page.js                              # ⬜ Anime hub homepage
    ├── manga/
    │   ├── page.js                          # ⬜ Manga catalog / browse
    │   └── read/
    │       └── page.js                      # ⬜ Manga reader (?series=...&chapter=...)
    ├── amv/
    │   └── page.js                          # ⬜ AMV gallery browse + upload
    ├── catalog/
    │   └── page.js                          # ⬜ Anime series catalog
    └── co-read/
        └── page.js                          # ⬜ Co-reading room (?room=...)

src/components/
└── anime/
    ├── AnimeHub.js                          # ⬜ Homepage: featured manga, AMVs, trending anime
    ├── MangaCatalog.js                      # ⬜ Series grid with genre filter
    ├── MangaReader.js                       # ⬜ Page-flip reader, chapter navigation
    ├── AMVGallery.js                        # ⬜ Video grid with like/view counts
    ├── AMVUploader.js                       # ⬜ Upload video, title, tags
    ├── AnimeCatalog.js                      # ⬜ Series list with episode tracking
    ├── AnimeCard.js                         # ⬜ Series card with watchlist add
    └── CoReadingRoom.js                     # ⬜ Real-time synchronized reading (WebSocket)
```

---

## API Endpoints (Needed)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/anime/manga/` | Browse manga series |
| `GET` | `/anime/manga/{id}/` | Series detail + chapter list |
| `GET` | `/anime/manga/{id}/chapter/{n}/` | Chapter pages (image URLs) |
| `GET` | `/anime/catalog/` | Browse anime series |
| `GET` | `/anime/catalog/{id}/` | Anime series detail + episode list |
| `POST` | `/anime/watchlist/` | Add anime to user watchlist |
| `GET` | `/anime/watchlist/` | User's watchlist |
| `PATCH` | `/anime/progress/` | Update read/watch progress |
| `GET` | `/anime/amv/` | Browse AMVs (paginated) |
| `POST` | `/anime/amv/` | Upload an AMV |
| `POST` | `/anime/amv/{id}/like/` | Like an AMV |
| `POST` | `/anime/co-read/create/` | Create a co-reading room |
| `GET` | `/anime/co-read/{id}/` | Get room state |
| `WS` | `/ws/co-read/{id}/` | WebSocket for real-time page sync |

---

## Data Shape Reference

### Manga Series Object

```json
{
  "id": "manga001",
  "title": "Attack on Titan",
  "cover_url": "https://...",
  "genres": ["Action","Dark Fantasy"],
  "description": "...",
  "status": "completed",
  "chapters": 139,
  "author": "Hajime Isayama",
  "user_progress": { "last_chapter": 85, "last_page": 12 }
}
```

### AMV Object

```json
{
  "id": "amv001",
  "title": "Naruto - Pain's Arc AMV",
  "uploader": { "user_id": "u3", "username": "anime_editor" },
  "video_url": "https://...",
  "thumbnail_url": "https://...",
  "tags": ["Naruto","Pain","Emotional"],
  "views": 4200,
  "likes": 310,
  "uploaded_at": "2026-02-14"
}
```

---

## Django Models (Inferred)

```python
class MangaSeries(models.Model):
    title = models.CharField(max_length=200)
    cover = models.ImageField(upload_to='manga/covers/')
    description = models.TextField()
    genres = models.JSONField(default=list)
    author = models.CharField(max_length=100)
    status = models.CharField(max_length=20)  # 'ongoing', 'completed', 'hiatus'
    chapter_count = models.IntegerField(default=0)

class MangaChapter(models.Model):
    series = models.ForeignKey(MangaSeries, related_name='chapters', on_delete=models.CASCADE)
    number = models.IntegerField()
    title = models.CharField(max_length=200, blank=True)
    page_count = models.IntegerField()

class MangaPage(models.Model):
    chapter = models.ForeignKey(MangaChapter, related_name='pages', on_delete=models.CASCADE)
    page_number = models.IntegerField()
    image = models.ImageField(upload_to='manga/pages/')

class UserMangaProgress(models.Model):
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    series = models.ForeignKey(MangaSeries, on_delete=models.CASCADE)
    last_chapter = models.IntegerField(default=0)
    last_page = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ('user', 'series')

class AMV(models.Model):
    uploader = models.ForeignKey('User', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    video = models.FileField(upload_to='amv/')
    thumbnail = models.ImageField(upload_to='amv/thumbnails/')
    tags = models.JSONField(default=list)
    views = models.IntegerField(default=0)
    likes = models.IntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

class CoReadingRoom(models.Model):
    host = models.ForeignKey('User', on_delete=models.CASCADE)
    series = models.ForeignKey(MangaSeries, on_delete=models.CASCADE)
    current_chapter = models.IntegerField(default=1)
    current_page = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## Task Checklist

### ⬜ Phase 5 (all)

- [ ] Check `src/app/anime/page.js` — read actual content before assuming it's a stub
- [ ] Design HTML mockups for all anime screens — CEO approval
- [ ] Manga catalog + reader
- [ ] AMV gallery + upload
- [ ] Anime series catalog + watchlist
- [ ] User progress tracking
- [ ] Co-reading room (requires WebSocket — django-channels)
- [ ] AMV likes + comments
- [ ] Content moderation for AMV uploads (admin panel integration)
- [ ] Mobile manga reader UX (swipe page-flip, full screen mode)
- [ ] Copyright compliance plan for manga hosting (legal review required)

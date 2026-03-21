# 09 — Marketplace (Vermillion City)

**Phase:** 4
**Status:** ❌ Not built — no design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** Wallet (06), User System (05), Organizations (07)

---

## Module Overview

Vermillion City is V-ENT's peer-to-peer marketplace. Unlike the Vent Shop (which is V-ENT-operated), Vermillion City is user/vendor-generated. Sellers list:
- Gaming peripherals (secondhand or new)
- Collectibles, figures, anime merchandise
- Digital items: game keys, accounts, in-game items
- Services: coaching sessions, graphic design, tournament hosting

V-ENT takes a platform fee (% of each transaction). Transactions are settled in VENT COINS with buyer-protection escrow.

Distinct from the Vendor Shop system within Events (which is for physical vendors at event venues — see 02-EVENTS.md).

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Marketplace Homepage | ❌ Not designed |
| Product Listing | ❌ Not designed |
| Product Detail | ❌ Not designed |
| Seller Profile | ❌ Not designed |
| Seller Dashboard | ❌ Not designed |
| Create Listing | ❌ Not designed |
| Checkout / Escrow | ❌ Not designed |
| Disputes | ❌ Not designed |

> All screens require Track B.

---

## Pages & Components Status

Nothing is built. No routes, no components exist.

Planned structure:

```
src/app/
├── marketplace/
│   ├── page.js                              # ⬜ Marketplace homepage — categories, featured, search
│   ├── listing/
│   │   └── page.js                          # ⬜ Product detail (?id=...)
│   ├── seller/
│   │   └── page.js                          # ⬜ Seller profile (?id=...)
│   ├── create-listing/
│   │   └── page.js                          # ⬜ Create / edit listing
│   └── dashboard/
│       └── page.js                          # ⬜ Seller dashboard (my listings, orders, earnings)

src/components/
└── marketplace/
    ├── MarketplaceHero.js                   # ⬜ Search bar, category pills, featured banner
    ├── ListingCard.js                        # ⬜ Grid card: image, title, price, seller name, rating
    ├── ListingDetail.js                      # ⬜ Full listing: images, description, condition, seller info
    ├── SellerProfile.js                      # ⬜ Seller bio, rating, all active listings
    ├── CreateListingForm.js                  # ⬜ Title, description, category, price, images, condition
    ├── CheckoutEscrow.js                     # ⬜ Confirm purchase, escrow explanation, PIN
    ├── SellerDashboard.js                    # ⬜ Active listings, orders to fulfill, earnings
    └── DisputeForm.js                        # ⬜ Raise a dispute on a purchase
```

---

## API Endpoints (Needed)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/marketplace/listings/` | Browse listings (paginated, filterable by category, price, condition) |
| `GET` | `/marketplace/listings/{id}/` | Listing detail |
| `POST` | `/marketplace/listings/` | Create a listing |
| `PATCH` | `/marketplace/listings/{id}/` | Edit a listing |
| `DELETE` | `/marketplace/listings/{id}/` | Remove a listing |
| `GET` | `/marketplace/seller/{user_id}/` | Seller profile + active listings |
| `POST` | `/marketplace/purchase/` | Initiate purchase → puts coins in escrow |
| `POST` | `/marketplace/purchase/{id}/confirm/` | Buyer confirms delivery → releases escrow to seller |
| `POST` | `/marketplace/purchase/{id}/dispute/` | Buyer raises dispute |
| `GET` | `/marketplace/dashboard/` | Seller's orders, earnings, listing stats |
| `GET` | `/marketplace/categories/` | Category list |

---

## Data Shape Reference

### Listing Object

```json
{
  "id": "lst001",
  "title": "Razer DeathAdder Elite — Like New",
  "description": "Used for 3 months, no scratches...",
  "category": "peripherals",
  "condition": "like_new",
  "price_vent_coins": 800,
  "images": ["https://...", "https://..."],
  "seller": {
    "user_id": "u5",
    "username": "gadget_king",
    "profile_pic": "...",
    "seller_rating": 4.8,
    "total_sales": 23
  },
  "stock": 1,
  "views": 142,
  "created_at": "2026-03-10"
}
```

### Purchase / Escrow Flow

```
Buyer → POST /marketplace/purchase/  →  VENT COINS deducted from buyer, held in escrow
Seller fulfills (ships item or delivers service)
Buyer → POST /marketplace/purchase/{id}/confirm/  →  VENT COINS released to seller (minus platform fee)
If dispute:
Buyer → POST /marketplace/purchase/{id}/dispute/  →  Admin reviews → admin resolves
```

Platform fee: e.g., 5% per transaction (exact rate set by backend config, not hardcoded in frontend).

---

## Django Models (Inferred)

```python
class Listing(models.Model):
    CONDITION = [('new','New'),('like_new','Like New'),('good','Good'),('fair','Fair')]
    seller = models.ForeignKey('User', related_name='listings', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=100)
    condition = models.CharField(max_length=20, choices=CONDITION)
    price_vent_coins = models.IntegerField()
    stock = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)
    views = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

class ListingImage(models.Model):
    listing = models.ForeignKey(Listing, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='marketplace/')
    order = models.IntegerField(default=0)

class Purchase(models.Model):
    STATUS = [('escrow','In Escrow'),('delivered','Delivered'),('completed','Completed'),('disputed','Disputed'),('refunded','Refunded')]
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE)
    buyer = models.ForeignKey('User', related_name='purchases', on_delete=models.CASCADE)
    seller = models.ForeignKey('User', related_name='sales', on_delete=models.CASCADE)
    amount_vent_coins = models.IntegerField()
    platform_fee = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS, default='escrow')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

class SellerRating(models.Model):
    seller = models.ForeignKey('User', related_name='ratings_received', on_delete=models.CASCADE)
    buyer = models.ForeignKey('User', related_name='ratings_given', on_delete=models.CASCADE)
    purchase = models.OneToOneField(Purchase, on_delete=models.CASCADE)
    rating = models.IntegerField()  # 1-5
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## Task Checklist

### ⬜ Phase 4 (all)

- [ ] Design HTML mockups for all marketplace screens — CEO approval
- [ ] Django models: `Listing`, `ListingImage`, `Purchase`, `SellerRating`
- [ ] All marketplace API endpoints
- [ ] Listing browse page with category filters + search
- [ ] Listing detail page
- [ ] Create/edit listing form
- [ ] Seller profile page
- [ ] Purchase + escrow flow
- [ ] Seller dashboard (orders, earnings, listing management)
- [ ] Dispute flow
- [ ] Seller rating system (after purchase completion)
- [ ] Admin dispute resolution panel (13-ADMIN-DASHBOARD.md)
- [ ] Platform fee config (admin-controlled, not hardcoded)

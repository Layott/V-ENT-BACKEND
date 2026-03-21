# 08 — Vent Shop (E-Commerce)

**Phase:** 3
**Status:** ❌ Not built — no design, no code
**Design track:** Track B (self-design required, CEO approval before build)
**Dependencies:** Wallet (06), User System (05)

---

## Module Overview

The Vent Shop is V-ENT's first-party e-commerce storefront. V-ENT sells gaming peripherals, merchandise, apparel, and digital items (game keys, in-game currency, etc.) directly to users. Users pay with VENT COINS or fiat (Paystack). The Shop is separate from the Marketplace (09-MARKETPLACE.md) — the Shop is run by V-ENT; the Marketplace is user-to-user.

Key features:
1. **Product catalog** — browsable with categories, search, and filters
2. **Product detail page** — images, description, variants (size, color), stock
3. **Cart + checkout** — multi-item cart, VENT COIN payment, delivery address
4. **Order management** — order history, status tracking, returns (Phase 3+)

---

## Figma Node IDs

| Screen | Status |
|--------|--------|
| Shop Homepage | ❌ Not designed (referenced in landing page Figma) |
| Product Listing | ❌ Not designed |
| Product Detail | ❌ Not designed |
| Cart | ❌ Not designed |
| Checkout | ❌ Not designed |
| Order History | ❌ Not designed |

> All screens require Track B. Note: the Landing Page Figma (`3322:11575`) shows a "Shop Feature" section — use that as visual direction for the shop theme.

---

## Pages & Components Status

Nothing is built. No routes, no components exist.

Planned structure:

```
src/app/
├── shop/
│   ├── page.js                              # ⬜ Shop homepage — featured, categories, deals
│   ├── product/
│   │   └── page.js                          # ⬜ Product detail (?id=...)
│   ├── cart/
│   │   └── page.js                          # ⬜ Cart and checkout
│   └── orders/
│       └── page.js                          # ⬜ Order history

src/components/
└── shop/
    ├── ShopHero.js                          # ⬜ Featured products / banner
    ├── ProductCard.js                        # ⬜ Grid card: image, name, price, add to cart
    ├── ProductDetail.js                      # ⬜ Full product view with image gallery
    ├── ProductVariants.js                    # ⬜ Size/color selector
    ├── Cart.js                               # ⬜ Cart sidebar or page
    ├── CartItem.js                           # ⬜ Individual cart row
    ├── CheckoutForm.js                       # ⬜ Delivery address, payment method
    └── OrderCard.js                          # ⬜ Order history item with status
```

---

## API Endpoints (Needed)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/shop/products/` | List products (paginated, filterable) |
| `GET` | `/shop/products/{id}/` | Product detail |
| `GET` | `/shop/categories/` | Product categories list |
| `POST` | `/shop/cart/` | Add item to cart |
| `GET` | `/shop/cart/` | Get current user's cart |
| `PATCH` | `/shop/cart/item/{id}/` | Update quantity |
| `DELETE` | `/shop/cart/item/{id}/` | Remove from cart |
| `POST` | `/shop/checkout/` | Create order, deduct VENT COINS, trigger fulfillment |
| `GET` | `/shop/orders/` | User's order history |
| `GET` | `/shop/orders/{id}/` | Order detail + status |

---

## Data Shape Reference

### Product Object

```json
{
  "id": "prod001",
  "name": "V-ENT Gaming Headset",
  "description": "...",
  "images": ["https://...", "https://..."],
  "category": "peripherals",
  "price_vent_coins": 2500,
  "price_ngn": 15000,
  "stock": 42,
  "variants": [
    { "id": "v1", "type": "color", "value": "Black", "stock": 20 },
    { "id": "v2", "type": "color", "value": "White", "stock": 22 }
  ],
  "in_stock": true,
  "tags": ["headset", "gaming", "audio"]
}
```

### Checkout Request

```json
{
  "cart_items": [
    { "product_id": "prod001", "variant_id": "v1", "quantity": 1 }
  ],
  "delivery_address": {
    "street": "12 Adeola Odeku",
    "city": "Lagos",
    "state": "Lagos",
    "country": "Nigeria",
    "postal_code": "101001"
  },
  "payment_method": "vent_coins",
  "pin": "1234"
}
```

---

## Django Models (Inferred)

```python
class Product(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=100)
    price_vent_coins = models.IntegerField()
    price_ngn = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='shop/products/')
    order = models.IntegerField(default=0)

class ProductVariant(models.Model):
    product = models.ForeignKey(Product, related_name='variants', on_delete=models.CASCADE)
    type = models.CharField(max_length=50)   # 'color', 'size'
    value = models.CharField(max_length=100)
    stock = models.IntegerField(default=0)

class Order(models.Model):
    STATUS = [('pending','Pending'),('paid','Paid'),('processing','Processing'),('shipped','Shipped'),('delivered','Delivered'),('cancelled','Cancelled')]
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    total_vent_coins = models.IntegerField()
    delivery_address = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, null=True, on_delete=models.SET_NULL)
    quantity = models.IntegerField()
    price_vent_coins = models.IntegerField()
```

---

## Task Checklist

### ⬜ Phase 3 (all)

- [ ] Design HTML mockups for all shop screens — CEO approval
- [ ] Django models: `Product`, `ProductImage`, `ProductVariant`, `Order`, `OrderItem`
- [ ] All shop API endpoints
- [ ] Product catalog page with categories + search + filters
- [ ] Product detail page with image gallery and variant selector
- [ ] Cart (persistent, stored server-side)
- [ ] Checkout flow with VENT COINS payment + delivery address
- [ ] Order confirmation + order history
- [ ] Admin product management (add/edit/remove products, update stock) — see 13-ADMIN-DASHBOARD.md
- [ ] Delivery/fulfillment tracking integration (Phase 3+)

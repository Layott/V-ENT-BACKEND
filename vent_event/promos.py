"""Redeeming a promo code at the checkout.

The organiser's side of promo codes was built on 3 September 2026: create,
edit, switch off, scope to a tier, a ticket limit, a window, and a "uses"
column. The buyer's side did not exist. No quote read a code, no purchase
took one, `used_tickets` was never written by anything, and the model's own
`is_usable()` and `discount_for()` had no caller. A code the organiser
printed on a flyer did nothing at the till (walk, 18 September 2026).

    resolve(event, code, tier, quantity) -> (promo, None) or (None, reason)
    redeem(promo, quantity)               -> used_tickets += quantity

The discount is applied in `ledger.quote`, the one pricing function, so the
panel and the charge cannot disagree about what a code is worth. It lands on
the unit price the same way a membership discount does, and the coin price
is the whole-coin floor of the discounted naira, which is the convention
every other discount on the platform already follows.
"""
from django.db.models import F

from .models import EventPromo

UNKNOWN = 'PROMO_UNKNOWN'
WRONG_TIER = 'PROMO_WRONG_TIER'


def resolve(event, code, tier=None, quantity=1):
    """The promo this code names on this event, if it may be used now.

    Returns `(promo, None)` or `(None, reason)`, the reason being one of the
    codes the model's `is_usable` speaks plus PROMO_UNKNOWN and
    PROMO_WRONG_TIER, so the screen can say why in the reader's language.
    """
    code = str(code or '').strip()
    if not code or len(code) > 40:
        return None, UNKNOWN
    promo = EventPromo.objects.filter(event=event, code__iexact=code).first()
    if promo is None:
        return None, UNKNOWN
    if promo.tier_id and tier is not None and promo.tier_id != tier.id:
        return None, WRONG_TIER
    usable, why = promo.is_usable(quantity=max(1, int(quantity or 1)))
    if not usable:
        return None, why
    return promo, None


def redeem(promo, quantity):
    """Spend the code on `quantity` tickets. Inside the purchase transaction,
    so a code is used if and only if the tickets it was used on exist."""
    if promo is None or not quantity:
        return
    EventPromo.objects.filter(pk=promo.pk).update(
        used_tickets=F('used_tickets') + int(quantity))


def honour(event, code, tier=None):
    """A code that was accepted when the money was TAKEN is honoured when the
    tickets are ISSUED, whatever happened to its limit or its window in the
    minutes between: the buyer paid the discounted price at the card. Returns
    the promo or None; never refuses."""
    code = str(code or '').strip()
    if not code:
        return None
    promo = EventPromo.objects.filter(event=event, code__iexact=code).first()
    if promo is None:
        return None
    if promo.tier_id and tier is not None and promo.tier_id != tier.id:
        return None
    return promo

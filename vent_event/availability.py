"""What is actually sellable, in one place.

Three things reduce what somebody can buy, and before this each was checked
somewhere different, or not at all:

  * the ticket type's own allocation
  * the venue's capacity, which is a SECOND ceiling and the lower one wins
  * tickets held back: guest list, press, venue, an influencer's allocation

They were three rules in three files that could disagree, and one of them
(capacity) was not enforced at all, so a venue set to 200 could sell 150
standard plus 100 VIP. Two rules that can disagree about the same number is how
a door ends up with more people than chairs.

Everything here counts from the tickets themselves rather than from a stored
`sold` column wherever it can, because the tickets are what somebody holds at
the door and a stale counter is exactly what must not be able to oversell a
room.
"""
from django.db.models import Sum


def held_on_tier(tier):
    """Tickets on this type that are reserved and not yet issued."""
    from .models import TicketHold

    rows = TicketHold.objects.filter(tier=tier, released_at__isnull=True)
    total = 0
    for hold in rows:
        total += hold.outstanding

    return total


def held_by_referrals(event):
    """An influencer's allocation: tickets reserved for somebody to sell.

    The same idea as a hold with a different name, so it is counted by the same
    function rather than by a second rule that can disagree with this one.

    Event-wide rather than per type, because `EventReferral` has no tier: an
    allocation of 50 is 50 tickets, not 50 of one kind. Counting it against a
    single type would hold the same 50 back from every type at once.
    """
    totals = (event.referrals
              .filter(is_active=True)
              .aggregate(held=Sum('allocation'), gone=Sum('sold')))
    return max((totals['held'] or 0) - (totals['gone'] or 0), 0)


def held_on_event(event):
    """Tickets held against the event as a whole rather than one type."""
    from .models import TicketHold

    rows = TicketHold.objects.filter(
        event=event, tier__isnull=True, released_at__isnull=True)
    return sum(hold.outstanding for hold in rows) + held_by_referrals(event)


def sold_on_event(event):
    """Tickets that exist and have not been cancelled or refunded.

    Counted rather than summed from `sold`, deliberately: `TicketTier.sold` is a
    counter and a counter can drift. The tickets cannot.
    """
    return event.tickets.exclude(status__in=('cancelled', 'refunded')).count()


def tier_available(tier):
    """How many of this type somebody could buy right now."""
    on_sale = max(int(tier.quantity) - int(tier.sold), 0)
    return max(on_sale - held_on_tier(tier), 0)


def event_room(event):
    """How many more people the venue will take, or None when uncapped.

    None rather than a big number, so a caller cannot accidentally treat "no
    ceiling" as "some ceiling I have not reached yet" and then compare against
    it.
    """
    if not event.capacity:
        return None
    return max(int(event.capacity) - sold_on_event(event) - held_on_event(event), 0)


def available(tier):
    """The real answer: the lower of the type's own room and the venue's."""
    room = event_room(tier.event)
    by_tier = tier_available(tier)
    if room is None:
        return by_tier
    return min(by_tier, room)


def snapshot(event):
    """What an organiser and a buy screen both need, computed once."""
    tiers = list(event.ticket_tiers.all())
    return {
        'capacity': event.capacity or None,
        'sold': sold_on_event(event),
        'held': held_on_event(event) + sum(held_on_tier(t) for t in tiers),
        'room': event_room(event),
        'tiers': {
            t.id: {
                'quantity': t.quantity,
                'sold': t.sold,
                'held': held_on_tier(t),
                'available': available(t),
            }
            for t in tiers
        },
    }

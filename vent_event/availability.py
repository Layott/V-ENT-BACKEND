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
from django.db import models
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


def sold_on_event(event, day=None):
    """Tickets that exist and have not been cancelled or refunded.

    Counted rather than summed from `sold`, deliberately: `TicketTier.sold` is a
    counter and a counter can drift. The tickets cannot.

    `day` narrows it to the tickets admitting on that date. See `event_room`
    for why that matters.
    """
    rows = event.tickets.exclude(status__in=('cancelled', 'refunded'))
    if day is not None:
        # A ticket with no day of its own is a full pass: it admits on every
        # day, so it occupies a place on this one too.
        rows = rows.filter(models.Q(tier__day=day) | models.Q(tier__day__isnull=True))
    return rows.count()


def tier_available(tier):
    """How many of this type somebody could buy right now."""
    on_sale = max(int(tier.quantity) - int(tier.sold), 0)
    return max(on_sale - held_on_tier(tier), 0)


def event_room(event, day=None):
    """How many more people the venue will take, or None when uncapped.

    None rather than a big number, so a caller cannot accidentally treat "no
    ceiling" as "some ceiling I have not reached yet" and then compare against
    it.

    **What the capacity counts is `event.capacity_mode`, and it is the
    organiser's to set.** Under `per_day` a venue that holds 400 holds 400 on
    Saturday and 400 again on Sunday; it does not hold 200 each. Counting
    every ticket for a two-day event against a single 400 was what made
    RIVALRY SERIES SEASON 2 report itself sold out with 186 sold on day one
    and 114 on day two. Under `total` the same 400 bounds the whole event,
    which is right for a residential weekend where nobody goes home.

    A ticket with no day is a full pass and counts against every day, which is
    the honest reading: that person is in the room on all of them.
    """
    if not event.capacity:
        return None

    # PER_DAY means the room empties overnight: a 5000-seat venue running two
    # days sells 5000 for Saturday and 5000 more for Sunday, because those are
    # different people in the same chairs. TOTAL means the same people stay,
    # so 5000 is the ceiling across the whole engagement.
    #
    # This is the organiser's choice and never inferred. Guessing wrongly is
    # expensive both ways: guess TOTAL and half the tickets never go on sale,
    # guess PER_DAY and the room is oversold.
    if getattr(event, 'capacity_mode', 'per_day') != 'per_day':
        day = None

    used = sold_on_event(event, day) + held_on_event(event)
    return max(int(event.capacity) - used, 0)


def available(tier):
    """The real answer: the lower of the type's own room and the venue's.

    The venue's room is measured on the day this type admits, because capacity
    is a property of the room on a day rather than of the whole engagement.
    """
    room = event_room(tier.event, getattr(tier, 'day', None))
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

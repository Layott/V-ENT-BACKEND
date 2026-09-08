"""Where an event loses people, which the tickets table cannot say.

CEO, 7 September 2026: "organizers hsould be able o see mad metric for thier
events and tickets, how many clicks, how many people opened it up, how many
tapped buy, how many check out vendor, stuff like that, very detailed stuff."

## The one number that is not recorded here

`sold` is COUNTED from the tickets that exist, never accumulated from a beacon.
Every other step in the funnel is a browser saying what it did, and a browser
can say it twice, or be a crawler, or fail to say it at all on a flaky
connection. The last step has to be the one that cannot drift, because it is
the one an organiser reconciles money against. The same rule the referral
counts are built to, and for the same reason.

So the funnel reads as: these top numbers are indicative and this bottom one is
true. Which is honest, and is why the conversion percentages are labelled as
being of the step above rather than of the whole.

## What is deliberately not stored

No address, no user agent, no row per visitor. A count against a day, per step.
`people` is what the browser reports about itself - that it had not done this
step on this event before - and nothing is kept to verify it, because verifying
it would mean storing the thing the design is avoiding.
"""

from datetime import date

from django.db.models import F, Sum

from .models import EventFunnelDay, Ticket

STEPS = EventFunnelDay.STEPS

# Only these steps carry a sub-name. Anything else has its `ref` dropped, so a
# bug on the page cannot turn one step into ten thousand rows.
STEPS_WITH_REF = {EventFunnelDay.STEP_STALL}


def record(event, step, first_time=False, ref='', when=None):
    """Add one to today's count. Returns the row, or None if the step is unknown.

    Unknown rather than invalid: a step this build does not know about is an
    older or newer page talking to this server, and dropping it silently is
    better than either storing junk or answering an error to a beacon nobody
    is waiting on.
    """
    if event is None or step not in STEPS:
        return None
    ref = str(ref or '').strip()[:80] if step in STEPS_WITH_REF else ''
    day = when or date.today()
    row, _ = EventFunnelDay.objects.get_or_create(
        event=event, day=day, step=step, ref=ref)
    # F() rather than read-modify-write. Two people opening the page in the
    # same second is the ordinary case for an event that is working, and that
    # is exactly when a lost update happens.
    EventFunnelDay.objects.filter(pk=row.pk).update(
        count=F('count') + 1,
        people=F('people') + (1 if first_time else 0),
    )
    row.refresh_from_db()
    return row


def _sold(event):
    """Tickets that exist and were not undone. Counted, never accumulated."""
    return (Ticket.objects.filter(event=event)
            .exclude(status__in=('refunded', 'cancelled')).count())


def summary(event):
    """The funnel, the per-day curve, the stalls, and what converted.

    Every label is a step key. Nothing here builds an English sentence, because
    a sentence built in Python cannot be translated.
    """
    rows = list(EventFunnelDay.objects.filter(event=event)
                .values('day', 'step', 'ref', 'count', 'people'))

    totals = {}
    for r in rows:
        agg = totals.setdefault(r['step'], {'count': 0, 'people': 0})
        agg['count'] += r['count']
        agg['people'] += r['people']

    sold = _sold(event)

    steps = [{'step': s,
              'count': totals.get(s, {}).get('count', 0),
              'people': totals.get(s, {}).get('people', 0)}
             for s in STEPS]
    # `sold` sits at the end of the same list so a screen draws one funnel
    # rather than a funnel and a footnote. It carries the same shape, and
    # `people` equals `count` because a ticket is a ticket.
    steps.append({'step': 'sold', 'count': sold, 'people': sold})

    by_day = {}
    for r in rows:
        key = r['day'].isoformat()
        by_day.setdefault(key, {'date': key})
        day = by_day[key]
        day[r['step']] = day.get(r['step'], 0) + r['count']

    stalls = {}
    for r in rows:
        if r['step'] != EventFunnelDay.STEP_STALL or not r['ref']:
            continue
        s = stalls.setdefault(r['ref'], {'stall': r['ref'], 'visits': 0,
                                         'people': 0, 'name': r['ref']})
        s['visits'] += r['count']
        s['people'] += r['people']

    if stalls:
        # Name them, so the organiser reads "Mama T Kitchen" rather than a slug
        # they have never seen. A stall deleted since keeps its slug, which is
        # the honest answer rather than a blank.
        from .models import Vendor
        for v in Vendor.objects.filter(event=event, slug__in=list(stalls)):
            stalls[v.slug]['name'] = v.name

    def rate(top, bottom):
        """Of the step above, as a percentage. None when nothing was above it.

        Zero per cent and unanswerable are different facts, and rounding the
        second into the first is how an organiser concludes their checkout is
        broken when nobody has opened the page yet.
        """
        if not bottom:
            return None
        return round(top * 100.0 / bottom, 1)

    opens = totals.get(EventFunnelDay.STEP_OPEN, {}).get('count', 0)
    buys = totals.get(EventFunnelDay.STEP_BUY, {}).get('count', 0)
    checkouts = totals.get(EventFunnelDay.STEP_CHECKOUT, {}).get('count', 0)

    return {
        'steps': steps,
        'sold': sold,
        'by_day': [by_day[k] for k in sorted(by_day)],
        'stalls': sorted(stalls.values(), key=lambda s: -s['visits']),
        'conversion': {
            'open_to_buy': rate(buys, opens),
            'buy_to_checkout': rate(checkouts, buys),
            'checkout_to_sold': rate(sold, checkouts),
            'open_to_sold': rate(sold, opens),
        },
        # Said plainly once, in the payload, so nothing downstream has to
        # remember it: the top of this funnel is what browsers reported and the
        # bottom is what the tickets table holds.
        'sold_is_counted': True,
        # More tickets than page opens, which is arithmetically possible and
        # always means the same thing: sales exist that predate the counting,
        # or people bought through a route that never loaded the event page.
        # Reported rather than capped at 100 - a rate silently pinned to 100%
        # would hide the fact that the tracking is incomplete, and that is
        # exactly what an organiser needs to know before trusting the rest.
        'sales_predate_tracking': bool(sold and sold > opens),
    }


def totals_for(event):
    """Just the headline counts, for a listing that cannot afford the full pass."""
    agg = (EventFunnelDay.objects.filter(event=event)
           .values('step').annotate(n=Sum('count')))
    return {r['step']: r['n'] or 0 for r in agg}

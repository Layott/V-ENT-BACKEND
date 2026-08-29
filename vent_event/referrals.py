"""Crediting an influencer link for what it actually brought in.

CEO, 29 August 2026: "apart from the code for influencers, having an option for
links also that can be tracked, is good."

The link already existed. `EventReferral` has carried a `code` since the
ticketing work, with a comment saying it goes in `/events/x?ref=CODE`, and the
organiser could create one, name it, cap it and switch it off. What none of it
did was count anything:

- nothing on the site read `?ref=` at all, so an arrival through an
  influencer's link was indistinguishable from any other arrival;
- `EventReferral.sold` was read in two places, to refuse a delete and to check
  an allocation, and was never once incremented.

So an organiser could hand out four links, and afterwards every one of them
said nought tickets, which is the same answer they would have got for a link
nobody clicked. This module is what makes the number mean something.

Two rules it is built to:

1. **Counted, not accumulated.** The number of tickets a link sold is counted
   from the tickets that carry it. A counter incremented at the till drifts the
   first time a payment fails half way, a refund lands, or an issue runs twice,
   and once it has drifted there is no way to find out by how much.
   `EventReferral.sold` is kept up to date as well, because the allocation cap
   needs a cheap number to check, but nothing the organiser is shown comes from
   it.

2. **A day, not a person.** A visit is recorded as a count against a day. The
   obvious alternative - a row per arrival with an address and a user agent -
   is a log of who read what, and the question an organiser actually has is
   "did this influencer bring anybody".
"""

from datetime import date

from django.db import transaction
from django.db.models import Count, Sum, Q

from .models import EventReferral, ReferralDay, Ticket


def resolve(event, code):
    """The active link on this event with this code, or None.

    Case-insensitive, because a code is read off a video and typed by hand as
    often as it is clicked, and an inactive link resolves to nothing so that
    switching one off actually stops it rather than merely hiding it.
    """
    if not code:
        return None
    code = str(code).strip()
    if not code or len(code) > 40:
        return None
    return (EventReferral.objects
            .filter(event=event, code__iexact=code, is_active=True)
            .first())


def record_visit(referral, first_time=False, when=None):
    """Add one arrival to today's count for this link.

    `first_time` is what the browser reports about itself: it had not been sent
    here by this link before. Nothing is stored to work that out, which is the
    point - the browser knows, and it is the only party that needs to.
    """
    if referral is None:
        return None
    day = when or date.today()
    row, _ = ReferralDay.objects.get_or_create(referral=referral, day=day)
    # F() rather than read-modify-write: two arrivals in the same second are
    # the ordinary case for a link that is working, and that is exactly when a
    # lost update happens.
    from django.db.models import F
    ReferralDay.objects.filter(pk=row.pk).update(
        visits=F('visits') + 1,
        visitors=F('visitors') + (1 if first_time else 0),
    )
    return row


def attribute(tickets, referral):
    """Credit a link for tickets that were just issued.

    Called inside the same transaction as the issue where possible, so a link
    is credited if and only if the tickets exist.
    """
    if referral is None or not tickets:
        return 0
    ids = [t.pk for t in tickets]
    updated = Ticket.objects.filter(pk__in=ids).update(referral=referral)
    from django.db.models import F
    EventReferral.objects.filter(pk=referral.pk).update(sold=F('sold') + updated)
    for t in tickets:
        t.referral = referral
    return updated


def stats_for(event):
    """Per-link numbers for one event, counted from the tickets themselves.

    Two queries, not one. Annotating tickets and visit-days on the same
    queryset multiplies them together: Django joins both tables, so ten visits
    against two tickets is reported as twenty visits. The first version of this
    did exactly that, and a test that bought two tickets after ten visits is
    what caught it. Aggregates over two different one-to-many relations belong
    in two queries.

    Ordered by what each link actually brought in, because the organiser's
    question is which influencer to use again.
    """
    live = Q(tickets__status__in=['valid', 'checked_in'])

    sales = (EventReferral.objects
             .filter(event=event)
             .annotate(
                 tickets_sold=Count('tickets', filter=live, distinct=True),
                 revenue_vc=Sum('tickets__price_vc', filter=live),
                 revenue_ngn=Sum('tickets__price_ngn', filter=live),
             ))

    seen = dict(
        ReferralDay.objects
        .filter(referral__event=event)
        .values_list('referral_id')
        .annotate(v=Sum('visits'))
        .values_list('referral_id', 'v')
    )
    people = dict(
        ReferralDay.objects
        .filter(referral__event=event)
        .values_list('referral_id')
        .annotate(v=Sum('visitors'))
        .values_list('referral_id', 'v')
    )

    out = []
    for r in sales:
        visits = int(seen.get(r.id) or 0)
        sold = int(r.tickets_sold or 0)
        out.append({
            'id': r.id,
            'name': r.name,
            'code': r.code,
            'url': r.url,
            'is_active': r.is_active,
            'allocation': r.allocation,
            'remaining': r.remaining,
            'visits': visits,
            'visitors': int(people.get(r.id) or 0),
            'tickets_sold': sold,
            'revenue_vc': int(r.revenue_vc or 0),
            'revenue_ngn': str(r.revenue_ngn or 0),
            # Out of a hundred, to one decimal, and None rather than zero when
            # nobody has arrived: a link nobody clicked has no conversion rate,
            # and showing it as 0% reads as a link that failed.
            'conversion': round(sold * 100.0 / visits, 1) if visits else None,
        })
    out.sort(key=lambda r: (-r['tickets_sold'], -r['visits'], r['name']))
    return out


def daily_for(event):
    """Visits per link per day, for drawing a line rather than a total."""
    rows = (ReferralDay.objects
            .filter(referral__event=event)
            .select_related('referral')
            .order_by('day'))
    out = {}
    for row in rows:
        out.setdefault(row.referral.code, []).append({
            'day': row.day.isoformat(),
            'visits': row.visits,
            'visitors': row.visitors,
        })
    return out


def share_url(event, code):
    """The address the influencer actually posts.

    Built here rather than on the page, because the organiser copies it out of
    the console and sends it to somebody else, and a link that is right only
    when the console happens to be open on the right host is a link that goes
    out wrong. FRONTEND_URL is the same setting the emails are built from, and
    it is guarded at startup for exactly this reason.
    """
    from django.conf import settings
    # The apex, not FRONTEND_URL's default. Two reasons, and the first one has
    # already cost this platform a week: FRONTEND_URL defaulted to
    # `test.app.v-ent.co`, a host that has never resolved, and every emailed
    # link built from it went nowhere until a startup guard was added. This is
    # a new consumer of the same setting, and a link an influencer posts to
    # their audience is even less recoverable than an email, because nobody
    # reports it - they just do not arrive.
    #
    # Second: app.v-ent.co 301s to the apex, so building on it adds a redirect
    # to every click, and some link previews do not follow one.
    base = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    if not base.startswith('https://v-ent.co'):
        base = 'https://v-ent.co'
    slug = event.slug or event.event_id
    return f"{base}/events/{slug}?ref={code}"

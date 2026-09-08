"""Who reached the payment page and never paid, and one way to ask them back.

The last GAP on the tix and selar research (inbox row 148). Everything else on
that list - the fee bearer, affiliates that pay, ticket transfer and the
settlement run - is built and shipped.

## The list is not a mailing list

`AbandonedCheckout` holds an address somebody typed in order to pay. So:

- **One reminder, ever.** `reminded_at` is checked before every send and set
  after it. There is no scheduler and no sequence. An automatic drip to
  somebody who did not buy is a marketing list assembled out of a checkout.
- **A person presses it.** The organiser chooses, per row or for everybody
  still open, and the send says out loud that it is the only one.
- **The address is shown to the organiser and nothing else.** No answers they
  typed, no attendee names, no card. The list answers "who nearly bought" and
  refuses to answer anything more.
- **Thirty days and it is gone**, converted or not. `AbandonedCheckout.sweep`.

## Why the count here can differ from the funnel

The funnel's `checkout_start` is a browser saying it reached the page. This
table is written by the SERVER at the moment Paystack accepted an
initialisation, so it counts attempts that really became a payable order. The
funnel number is larger and indicative; this one is smaller and exact, and
that is the same split the funnel module already documents about `sold`.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import AbandonedCheckout
from .views_promos import _actor_for_event, _err, _ok, event_by_ref

# The most reminders one press may send. An organiser with a thousand open rows
# pressing "remind everybody" should not become a thousand-message send nobody
# can stop halfway.
MAX_PER_PRESS = 200


def _row(item):
    return {
        'id': item.id,
        'email': item.email,
        'quantity': item.quantity,
        'tier': item.tier.name if item.tier_id else '',
        'total_ngn': float(item.total_ngn or 0),
        'started_at': item.started_at.isoformat(),
        'reminded_at': item.reminded_at.isoformat() if item.reminded_at else None,
        'converted_at': item.converted_at.isoformat() if item.converted_at else None,
    }


@api_view(['GET'])
def abandoned_checkouts(request, event_id):
    """GET /event/<id>/abandoned/ - everybody who started paying and stopped.

    `?include=all` also returns the ones who came back, which is the only way
    to see the recovery rate rather than just the backlog.
    """
    event = event_by_ref(event_id)
    _, err = _actor_for_event(request, event)
    if err:
        return err

    rows = AbandonedCheckout.objects.filter(event=event).select_related('tier')
    if request.GET.get('include') != 'all':
        rows = rows.filter(converted_at__isnull=True)

    rows = list(rows[:500])
    every = AbandonedCheckout.objects.filter(event=event)
    open_count = every.filter(converted_at__isnull=True).count()
    recovered = every.filter(converted_at__isnull=False).count()

    return _ok({
        'results': [_row(r) for r in rows],
        'open': open_count,
        'recovered': recovered,
        # What is worth chasing, in money rather than in rows. An organiser
        # deciding whether to press this wants the number, not the count.
        'open_ngn': float(sum(r.total_ngn or 0 for r in
                              every.filter(converted_at__isnull=True))),
        'remindable': every.filter(converted_at__isnull=True,
                                   reminded_at__isnull=True).count(),
        'kept_days': 30,
    })


@api_view(['POST'])
def remind_abandoned(request, event_id):
    """POST /event/<id>/abandoned/remind/ - send the one reminder.

    `{"id": 12}` for one, or nothing for everybody still open who has not had
    theirs. A row that has been reminded is skipped rather than refused, so
    pressing twice sends nothing rather than erroring on the first row and
    leaving the rest.
    """
    event = event_by_ref(event_id)
    _, err = _actor_for_event(request, event)
    if err:
        return err

    rows = AbandonedCheckout.objects.filter(
        event=event, converted_at__isnull=True, reminded_at__isnull=True
    ).select_related('event', 'tier')

    one = request.data.get('id')
    if one:
        rows = rows.filter(id=one)
        if not rows.exists():
            # Deliberately one message for "no such row", "already reminded"
            # and "they came back": all three mean there is nothing to send,
            # and the list the organiser is looking at will say which.
            return _err('There is nothing to send for that one.',
                        'NOTHING_TO_SEND', status.HTTP_409_CONFLICT)

    sent = 0
    failed = 0
    from vent_auth import emails
    for item in list(rows[:MAX_PER_PRESS]):
        ok = False
        try:
            ok = emails.send_checkout_unfinished(item)
        except Exception:
            ok = False
        if ok:
            # Stamped only on a send that worked, so a mail outage leaves the
            # row remindable rather than silently burning its one chance.
            item.reminded_at = timezone.now()
            item.save(update_fields=['reminded_at'])
            sent += 1
        else:
            failed += 1

    return _ok({'sent': sent, 'failed': failed},
               'Reminder sent.' if sent else 'Nothing was sent.')

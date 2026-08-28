"""The queue for a sold-out event, and what happens when a ticket comes back.

Built in the DICE shape: the waitlist is the return valve that makes a
face-value-only policy workable, not a way to capture demand. Somebody whose
plans change has a way out that is not a resale site, and the ticket goes to the
next person in the queue at the price it was always sold at.

The mechanism is four moves:

  join    somebody puts their name down on a sold-out event
  offer   a ticket comes back, and the first person waiting is given a window
  take    they buy it inside the window
  lapse   they do not, and it passes to the next person

The window is the part that is easy to leave out and impossible to run without.
Without a clock, one person who stops reading their email freezes the queue
behind them for ever.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from . import availability
from .models import WaitlistEntry

# How long somebody has to take an offer. Long enough to see a notification and
# act, short enough that a sold-out event does not sit frozen behind one person
# who has gone to bed.
OFFER_HOURS = 12


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message,
                     'code': code}, status=http_status)


def _event(event_id):
    from .views import _event_by_ref
    return _event_by_ref(event_id)


def expire_stale_offers(event):
    """Anybody whose window has closed loses their place, and the next person
    gets one.

    Run on every read and every write rather than on a schedule, so the queue is
    correct whenever anybody looks at it without needing a worker to be running.
    A cron job that is not running is the most common reason a queue is wrong.
    """
    now = timezone.now()
    lapsed = event.waitlist.filter(
        status='offered', offer_expires_at__lt=now)
    count = lapsed.count()
    if count:
        lapsed.update(status='missed', resolved_at=now)
    return count


def offer_next(event, how_many=1):
    """Give the next people in the queue a window to buy.

    Only ever offers what is genuinely available, so two returns do not produce
    three offers.
    """
    room = availability.event_room(event)
    sellable = sum(availability.available(t) for t in event.ticket_tiers.all())
    if room is not None:
        sellable = min(sellable, room)

    already_offered = event.waitlist.filter(status='offered').count()
    can_offer = max(min(how_many, sellable - already_offered), 0)
    if can_offer <= 0:
        return []

    now = timezone.now()
    waiting = list(event.waitlist.filter(status='waiting')[:can_offer])
    for entry in waiting:
        entry.status = 'offered'
        entry.offered_at = now
        entry.offer_expires_at = now + timedelta(hours=OFFER_HOURS)
        entry.save(update_fields=['status', 'offered_at', 'offer_expires_at'])
    return waiting


def _row(entry, position=None):
    return {
        'id': entry.id,
        'status': entry.status,
        'position': position,
        'joined_at': entry.joined_at,
        'offer_expires_at': entry.offer_expires_at,
        'user': entry.user.username,
        'tier': entry.tier_id,
    }


@api_view(['POST', 'DELETE'])
def join_waitlist(request, event_id):
    """POST   /event/<id>/waitlist/ - put my name down.
       DELETE /event/<id>/waitlist/ - take it off again.
    """
    user, err = actor_from_request(request)
    if err:
        return err

    event = _event(event_id)
    if event is None:
        return _err('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    expire_stale_offers(event)

    if request.method == 'DELETE':
        entry = event.waitlist.filter(user=user).first()
        if entry is None:
            return _err('You are not on this waitlist.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        entry.status = 'left'
        entry.resolved_at = timezone.now()
        entry.save(update_fields=['status', 'resolved_at'])
        # Leaving frees a place, so somebody behind may now be offered one.
        offer_next(event)
        return _ok({}, 'Taken off the waitlist.')

    # A waitlist on an event somebody could simply buy a ticket for is a
    # confusing thing to offer. Say so rather than silently queueing them.
    sellable = sum(availability.available(t) for t in event.ticket_tiers.all())
    room = availability.event_room(event)
    if room is not None:
        sellable = min(sellable, room)
    if sellable > 0:
        return _err('Tickets are still on sale for this event, so there is no '
                    'queue to join.', 'NOT_SOLD_OUT', status.HTTP_409_CONFLICT)

    tier = None
    if request.data.get('tier') not in ('', None):
        tier = event.ticket_tiers.filter(pk=request.data.get('tier')).first()

    entry, created = WaitlistEntry.objects.get_or_create(
        event=event, user=user, defaults={'tier': tier})
    if not created and entry.status in ('left', 'missed'):
        # Coming back is allowed, and it puts them at the back rather than
        # restoring the place they gave up.
        entry.status = 'waiting'
        entry.joined_at = timezone.now()
        entry.offered_at = None
        entry.offer_expires_at = None
        entry.resolved_at = None
        entry.save()
        created = True

    ahead = event.waitlist.filter(
        status__in=('waiting', 'offered'), joined_at__lt=entry.joined_at).count()

    return _ok({'entry': _row(entry, position=ahead + 1)},
               'You are number %s in the queue.' % (ahead + 1),
               status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['GET'])
def my_waitlist_place(request, event_id):
    """GET /event/<id>/waitlist/mine/ - where am I, and do I have an offer?"""
    user, err = actor_from_request(request)
    if err:
        return err

    event = _event(event_id)
    if event is None:
        return _err('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    expire_stale_offers(event)

    entry = event.waitlist.filter(user=user).first()
    if entry is None:
        return _ok({'entry': None, 'on_the_list': False}, 'Not on the waitlist')

    ahead = event.waitlist.filter(
        status__in=('waiting', 'offered'), joined_at__lt=entry.joined_at).count()
    return _ok({'entry': _row(entry, position=ahead + 1), 'on_the_list': True},
               'Your place')


@api_view(['GET'])
def event_waitlist(request, event_id):
    """GET /event/<id>/waitlist/all/ - the queue, for the organiser."""
    user, err = actor_from_request(request)
    if err:
        return err

    event = _event(event_id)
    if event is None:
        return _err('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return _err('Only the event organizer can see the queue.',
                    'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)

    expired = expire_stale_offers(event)

    rows = list(event.waitlist.all())
    live = [e for e in rows if e.status in ('waiting', 'offered')]
    return _ok({
        'waitlist': [_row(e, position=i + 1) for i, e in enumerate(live)],
        'counts': {
            'waiting': sum(1 for e in rows if e.status == 'waiting'),
            'offered': sum(1 for e in rows if e.status == 'offered'),
            'taken': sum(1 for e in rows if e.status == 'taken'),
            'missed': sum(1 for e in rows if e.status == 'missed'),
        },
        'expired_just_now': expired,
    }, 'The queue')

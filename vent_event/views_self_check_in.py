"""Admitting yourself.

PRD section 4: attendees marking their own attendance.

The door flow in `views_tickets.check_in_ticket` is the right one for a gate
with staff on it, and this does not replace it. This is for the rest: a virtual
event, a meet-up of thirty people, a session inside a venue somebody already
walked into. Off unless the organiser turns it on, because an attendee who can
admit themselves can do it from home.

What stands in for a steward:

1. **The window.** It opens a set number of minutes before the doors and closes
   when the event ends. A ticket marked used at 9am for a 7pm event tells the
   organiser nothing about who turned up, and attendance is the number they act
   on. It closes at the END, not the start, because somebody arriving late is
   still somebody who came.

2. **The code plus the address it was sent to.** A guest has no account, so the
   code is the whole credential, and a code is a thing people screenshot into
   group chats. Asking for the email as well means holding the ticket is not
   enough on its own; you have to be the person it was issued to. A signed-in
   owner skips it, because they already proved that.

A self check-in is recorded as one: `checked_in_gate` reads "self" and
`checked_in_by` is the attendee or nobody, never a steward. The organiser's
attendance list can then tell the two apart, which matters when they are
deciding whether the number is real.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from .models import Event, Ticket

SELF_GATE = 'self'


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    payload = {'status': 'error', 'code': code, 'message': message, 'data': {}}
    if extra:
        payload['data'] = extra
    return Response(payload, status=http)


def _viewer(request):
    """The signed-in user, or None. Signing in is optional here on purpose."""
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


@api_view(['GET', 'POST'])
def self_check_in(request, code):
    """GET: may I, and when. POST: I am here.

    GET answers before anybody types anything, so the page can say "opens at
    5pm" rather than offering a button that fails. Telling somebody what they
    need before they spend effort, not after.
    """
    ticket = (Ticket.objects.select_related('event', 'tier', 'user')
              .filter(code=str(code).upper()).first())
    if ticket is None:
        return _error('No ticket with that code.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    event = ticket.event
    opens, closes = event.self_check_in_window()
    now = timezone.now()

    state = {
        'enabled': bool(event.self_check_in),
        'opens_at': opens,
        'closes_at': closes,
        'now': now,
        'already': ticket.status == 'checked_in',
        'event': {'name': event.name, 'slug': event.slug},
    }

    if request.method == 'GET':
        if not event.self_check_in:
            state['reason'] = 'SELF_CHECK_IN_OFF'
        elif ticket.status == 'checked_in':
            state['reason'] = 'ALREADY_CHECKED_IN'
            state['checked_in_at'] = ticket.checked_in_at
        elif ticket.status != 'valid':
            state['reason'] = 'TICKET_NOT_VALID'
        elif opens is None:
            state['reason'] = 'NO_EVENT_TIME'
        elif now < opens:
            state['reason'] = 'TOO_EARLY'
        elif closes is not None and now > closes:
            state['reason'] = 'TOO_LATE'
        else:
            state['reason'] = ''
        state['may_check_in'] = state['reason'] == ''
        return Response({'status': 'success', 'data': state, 'message': ''})

    if not event.self_check_in:
        return _error('This event checks tickets in at the door.',
                      'SELF_CHECK_IN_OFF', status.HTTP_403_FORBIDDEN)

    if ticket.status == 'checked_in':
        return _error('This ticket is already checked in.',
                      'ALREADY_CHECKED_IN', status.HTTP_409_CONFLICT,
                      extra={'checked_in_at': ticket.checked_in_at,
                             'gate': ticket.checked_in_gate})

    if ticket.status != 'valid':
        return _error('This ticket is not valid.', 'TICKET_NOT_VALID',
                      status.HTTP_409_CONFLICT)

    if opens is None:
        return _error('This event has no start time set.', 'NO_EVENT_TIME')
    if now < opens:
        return _error('Check-in has not opened yet.', 'TOO_EARLY',
                      status.HTTP_409_CONFLICT, extra={'opens_at': opens})
    if closes is not None and now > closes:
        return _error('Check-in has closed.', 'TOO_LATE',
                      status.HTTP_409_CONFLICT, extra={'closed_at': closes})

    # The second factor. A signed-in owner has already proved it.
    viewer = _viewer(request)
    owns_it = viewer is not None and ticket.user_id == viewer.user_id
    if not owns_it:
        given = str(request.data.get('email') or '').strip().lower()
        held = (ticket.attendee_email or '').strip().lower()
        if not held:
            # Nothing to check against. The door is the only honest answer.
            return _error('This ticket has no email on it, so it has to be '
                          'checked in at the door.', 'NO_EMAIL_ON_TICKET',
                          status.HTTP_409_CONFLICT)
        if given != held:
            return _error('That email does not match this ticket.',
                          'EMAIL_MISMATCH', status.HTTP_403_FORBIDDEN)

    with transaction.atomic():
        # Re-read under the lock. Two taps on a slow connection are two
        # requests, and both would otherwise pass the check above.
        locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if locked.status == 'checked_in':
            return _error('This ticket is already checked in.',
                          'ALREADY_CHECKED_IN', status.HTTP_409_CONFLICT,
                          extra={'checked_in_at': locked.checked_in_at})
        locked.status = 'checked_in'
        locked.checked_in_at = timezone.now()
        locked.checked_in_gate = SELF_GATE
        locked.checked_in_by = viewer if owns_it else None
        locked.save(update_fields=['status', 'checked_in_at',
                                   'checked_in_gate', 'checked_in_by'])

    return Response({
        'status': 'success',
        'data': {
            'code': locked.code,
            'name': locked.attendee_name,
            'tier': locked.tier.name,
            'checked_in_at': locked.checked_in_at,
            'gate': SELF_GATE,
            'event': {'name': event.name, 'slug': event.slug},
        },
        'message': '',
    })


@api_view(['GET'])
def self_check_in_settings(_request, event_id):
    """What the event's own page needs to decide whether to show the control."""
    from vent_auth.slugs import resolve_or_redirect
    try:
        event, _moved = resolve_or_redirect(
            event_id, entity_type='event', id_field='event_id', model=Event)
    except Exception:
        event = None
    if event is None:
        event = (Event.objects.filter(pk=int(event_id)).first()
                 if str(event_id).isdigit()
                 else Event.objects.filter(slug=event_id).first())
    if event is None:
        return _error('Event not found', 'EVENT_NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    opens, closes = event.self_check_in_window()
    return Response({'status': 'success', 'data': {
        'enabled': bool(event.self_check_in),
        'opens_minutes_before': event.self_check_in_opens_minutes,
        'opens_at': opens,
        'closes_at': closes,
    }, 'message': ''})

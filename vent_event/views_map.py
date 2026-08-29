"""The map on an event page, and the markers on it.

Three endpoints:

    GET    /event/<event>/origins/     the cells, and how many people share each
    POST   /event/<event>/origins/     "I am coming, and I am happy to be counted"
    DELETE /event/<event>/origins/     "stop counting me"

The response is a list of cells and counts. It has no names, no usernames, no
ids and no exact coordinates, and a cell below `MIN_PER_CELL` is not in it at
all - not returned with a count of one, not returned with a flag, absent. A
marker for one person is that person's neighbourhood, and the point of the
feature is "there are people around you going", not "here is who".

Only somebody holding a ticket may be counted. Otherwise the map is a map of
whoever pressed the button, which is both untrue and a way to probe the
mechanism with any account.
"""

from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from .geo import MIN_PER_CELL, BadCoordinate, to_cell
from .models import Event, EventAttendeeOrigin, Ticket


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message})


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _event(event_id):
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


def _authenticate(request):
    user = _viewer(request)
    if user is None:
        return None, _error('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


def _has_ticket(event, user):
    """Somebody holding a live ticket to this event.

    A ticket bought as a guest and later claimed carries `user`, so this covers
    both. A refunded or cancelled ticket does not count: they are not coming.
    """
    return Ticket.objects.filter(
        event=event, user=user, status__in=['valid', 'checked_in'],
    ).exists()


def _cells(event):
    rows = (EventAttendeeOrigin.objects
            .filter(event=event)
            .values('cell_latitude', 'cell_longitude')
            .annotate(people=Count('id'))
            .filter(people__gte=MIN_PER_CELL)
            .order_by('-people'))
    return [
        {
            'lat': float(row['cell_latitude']),
            'lng': float(row['cell_longitude']),
            'people': row['people'],
        }
        for row in rows
    ]


@api_view(['GET', 'POST', 'DELETE'])
def event_origins(request, event_id):
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        # Public: the whole point is that somebody deciding whether to go can
        # see that people near them are going.
        user = _viewer(request)
        mine = bool(user and EventAttendeeOrigin.objects.filter(
            event=event, user=user).exists())
        # Whether the control should be drawn at all. A POST from somebody
        # without a ticket is refused, so showing them the button would be a
        # control that fails only once it has been pressed.
        may_share = bool(user and _has_ticket(event, user))
        return _ok({
            'cells': _cells(event),
            'min_per_cell': MIN_PER_CELL,
            'sharing': mine,
            'can_share': may_share,
        }, 'Origins retrieved.')

    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    if request.method == 'DELETE':
        EventAttendeeOrigin.objects.filter(event=event, user=user).delete()
        return _ok({'sharing': False, 'cells': _cells(event)}, 'You are no longer counted.')

    if not _has_ticket(event, user):
        return _error(
            'Only somebody holding a ticket can be counted on this map.',
            'TICKET_REQUIRED', status.HTTP_403_FORBIDDEN)

    try:
        cell_lat, cell_lng = to_cell(
            request.data.get('latitude'), request.data.get('longitude'))
    except BadCoordinate as exc:
        return _error(str(exc), 'BAD_COORDINATE', status.HTTP_400_BAD_REQUEST)

    # `update_or_create` on the cell, never on the point: there is no field on
    # this model that could hold the point, which is deliberate.
    EventAttendeeOrigin.objects.update_or_create(
        event=event, user=user,
        defaults={'cell_latitude': cell_lat, 'cell_longitude': cell_lng},
    )
    return _ok({'sharing': True, 'cells': _cells(event)}, 'You are counted on the map.')

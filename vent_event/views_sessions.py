"""The programme: what is happening at an event, when, and where in the venue.

The Schedule tab used to be a function that invented a two-day programme from
the event's start date, so every event on the platform showed the same "Doors
open + Vendor zone activation" and "Cosplay parade". It was removed rather than
left behind a flag, and this is what replaces it.

A session carries its own capacity, which is the reason to have sessions at all
rather than a list of times: a convention holding 900 has a panel room holding
80, and the panel is a session with a capacity of 80. Timed entry works the same
way on every platform that sells it.

Reading is public. The programme is the first thing somebody deciding whether to
come wants to see, and it is the most shareable page an event has.
"""
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import EventSession


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _event(event_id):
    from .views import _event_by_ref
    return _event_by_ref(event_id)


def _organiser(request, event_id):
    user, err = actor_from_request(request)
    if err:
        return None, None, err
    event = _event(event_id)
    if event is None:
        return None, None, _err('Event not found.', 'NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return None, None, _err('Only the event organizer can change the '
                                'programme.', 'ONLY_EVENT_ORGANIZER_CAN',
                                status.HTTP_403_FORBIDDEN)
    return event, user, None


def _row(session):
    return {
        'id': session.session_id,
        'title': session.title,
        'description': session.description,
        'starts_at': session.starts_at,
        'ends_at': session.ends_at,
        'stage': session.stage,
        'capacity': session.capacity or None,
        'tournament': session.tournament_id,
        'is_published': session.is_published,
    }


def _read_when(raw, field):
    """A datetime, made aware, or an error.

    A naive value from a browser is read as local time. Storing it as UTC would
    shift every session by the offset, which on a 7pm doors time is the
    difference between right and wrong.
    """
    parsed = parse_datetime(raw) if isinstance(raw, str) else raw
    if parsed is None:
        return None, _err('%s has to be a date and time.' % field,
                          'INVALID_DATETIME', field=field)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed, None


@api_view(['GET'])
@permission_classes([AllowAny])
def sessions(request, event_id):
    """GET /event/<id>/sessions/ - the programme, grouped by day.

    The day is derived from the start time rather than stored: a session at 1am
    after a Friday night belongs to Friday in every way that matters to somebody
    reading a schedule, and asking an organiser to resolve that is asking the
    wrong person.
    """
    event = _event(event_id)
    if event is None:
        return _err('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    rows = list(event.sessions.filter(is_published=True))
    days = []
    for session in rows:
        local = timezone.localtime(session.starts_at)
        # Anything before 5am belongs to the night before: a set at 1am on
        # Saturday is Friday's programme to everybody who was there.
        day = local.date() if local.hour >= 5 else (local - timedelta(days=1)).date()
        if not days or days[-1]['date'] != day:
            days.append({'date': day, 'sessions': []})
        days[-1]['sessions'].append(_row(session))

    for index, day in enumerate(days):
        day['label'] = 'Day %s' % (index + 1)
        day['date'] = day['date'].isoformat()

    return _ok({
        'days': days,
        'count': len(rows),
        # An empty programme is the normal case for most events, and it must
        # read as "nothing published" rather than as a broken page.
        'published': bool(rows),
    }, 'Programme')


@api_view(['GET', 'POST'])
def manage_sessions(request, event_id):
    """GET  /event/<id>/sessions/manage/ - everything, published or not.
       POST /event/<id>/sessions/manage/ - add one.
    """
    event, _user, err = _organiser(request, event_id)
    if err:
        return err

    if request.method == 'GET':
        return _ok({'sessions': [_row(s) for s in event.sessions.all()]},
                   'Programme')

    title = str(request.data.get('title') or '').strip()
    if not title:
        return _err('A session needs a title.', 'VALIDATION_FAILED',
                    field='title')

    starts_at, err = _read_when(request.data.get('starts_at'), 'starts_at')
    if err:
        return err

    ends_at = None
    if request.data.get('ends_at'):
        ends_at, err = _read_when(request.data.get('ends_at'), 'ends_at')
        if err:
            return err
        if ends_at < starts_at:
            return _err('A session cannot end before it starts.',
                        'END_BEFORE_START', field='ends_at')

    try:
        capacity = int(request.data.get('capacity') or 0)
    except (TypeError, ValueError):
        return _err('The capacity has to be a number.', 'INVALID_NUMBER',
                    field='capacity')

    session = EventSession.objects.create(
        event=event,
        title=title[:140],
        description=str(request.data.get('description') or '')[:400],
        starts_at=starts_at,
        ends_at=ends_at,
        stage=str(request.data.get('stage') or '')[:100],
        capacity=max(capacity, 0),
        is_published=request.data.get('is_published', True) is not False,
    )
    return _ok({'session': _row(session),
                'sessions': [_row(s) for s in event.sessions.all()]},
               'Added to the programme.', status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def session_detail(request, event_id, session_id):
    """PATCH  /event/<id>/sessions/<sid>/ - correct one.
       DELETE /event/<id>/sessions/<sid>/ - take it off the programme.
    """
    event, _user, err = _organiser(request, event_id)
    if err:
        return err

    session = event.sessions.filter(pk=session_id).first()
    if session is None:
        return _err('No such session on this event.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        session.delete()
        return _ok({'sessions': [_row(s) for s in event.sessions.all()]},
                   'Taken off the programme.')

    updated = []

    if 'title' in request.data:
        title = str(request.data.get('title') or '').strip()
        if not title:
            return _err('A session needs a title.', 'VALIDATION_FAILED',
                        field='title')
        session.title = title[:140]
        updated.append('title')

    for field in ('starts_at', 'ends_at'):
        if field in request.data:
            raw = request.data.get(field)
            if not raw and field == 'ends_at':
                session.ends_at = None
                updated.append('ends_at')
                continue
            when, err = _read_when(raw, field)
            if err:
                return err
            setattr(session, field, when)
            updated.append(field)

    if (session.ends_at and session.starts_at
            and session.ends_at < session.starts_at):
        return _err('A session cannot end before it starts.',
                    'END_BEFORE_START', field='ends_at')

    if 'description' in request.data:
        session.description = str(request.data.get('description') or '')[:400]
        updated.append('description')

    if 'stage' in request.data:
        session.stage = str(request.data.get('stage') or '')[:100]
        updated.append('stage')

    if 'capacity' in request.data:
        try:
            session.capacity = max(int(request.data.get('capacity') or 0), 0)
        except (TypeError, ValueError):
            return _err('The capacity has to be a number.', 'INVALID_NUMBER',
                        field='capacity')
        updated.append('capacity')

    if 'is_published' in request.data:
        session.is_published = request.data.get('is_published') is not False
        updated.append('is_published')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    session.save(update_fields=updated + ['last_updated'])
    return _ok({'session': _row(session),
                'sessions': [_row(s) for s in event.sessions.all()]},
               'Session updated.')

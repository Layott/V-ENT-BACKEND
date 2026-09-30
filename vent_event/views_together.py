"""Going together at an event: the doors (inbox 305, 305a to 305c).

Every decision is made in `together.py`; these views only ask it and answer in
the envelope, with a code on every refusal so the screen can say it in the
reader's language.

  GET    /event/<ref>/together/                 my state, who I may see, pings
  POST   /event/<ref>/together/me/              my settings and my area
  POST   /event/<ref>/together/approve/         let one person see my area
  DELETE /event/<ref>/together/approve/         take that back
  POST   /event/<ref>/together/ping/            ask somebody to meet up
  POST   /event/<ref>/together/ping/<id>/answer/  accept or decline
  GET    /event/admin/location-history/         admins with view_location_history
"""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.decorators import ROLE_PERMISSIONS, admin_role_required
from vent_auth.models import AdminAction, Users

from . import together
from .models import DepartureApproval, DepartureRecord, EventPing, EventPresence
from .refs import event_by_ref


def _ok(data, message='OK'):
    return Response({'status': 'success', 'code': 'OK', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': data or {}},
                    status=http)


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _person(user):
    from vent_auth.views_community import _person as described
    return described(None, user)


REFUSALS = {
    'SIGN_IN': ('Sign in to take part.', status.HTTP_401_UNAUTHORIZED),
    'NO_TICKET': ('Only people with a ticket to this event can take part.', status.HTTP_403_FORBIDDEN),
    'NEEDS_BIRTHDAY': ('Add your date of birth to your profile first.', status.HTTP_403_FORBIDDEN),
    'TOO_YOUNG': ('This is not available under 13.', status.HTTP_403_FORBIDDEN),
    'ADULTS_ONLY_EVENT': ('This event is 18+, so this is not available to you here.', status.HTTP_403_FORBIDDEN),
}


def _refuse(code):
    message, http = REFUSALS[code]
    return _err(message, code, http)


def _event(ref):
    return event_by_ref(ref, is_active=True)


def _my_presence(presence, event):
    over = together.event_over(event)
    if presence is None:
        return {'attendance_visibility': 'off', 'departure_visibility': 'off',
                'departure_area': '', 'pings_open': False, 'approved': []}
    return {
        'attendance_visibility': presence.attendance_visibility,
        'departure_visibility': presence.departure_visibility,
        # Soft deleted after the event: not even its owner reads it back.
        'departure_area': '' if over else presence.departure_area,
        'pings_open': presence.pings_open,
        'approved': [_person(a.approved) for a in presence.approvals.select_related('approved')],
    }


def _ping_row(ping, viewer):
    other = ping.recipient if ping.sender_id == viewer.user_id else ping.sender
    return {
        'id': ping.id, 'status': ping.status, 'message': ping.message,
        'person': _person(other), 'created_at': ping.created_at, 'answered_at': ping.answered_at,
        'conversation': ping.conversation.slug if ping.conversation_id else None,
    }


@api_view(['GET'])
def together_state(request, event_id):
    event = _event(event_id)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    showing = [p for p in EventPresence.objects.filter(event=event)
               .exclude(attendance_visibility='off').select_related('user')
               if not together.refusal(event, p.user)]
    base = {'count': len(showing), 'min_age': event.min_age, 'event_over': together.event_over(event)}
    if viewer is None:
        return _ok(dict(base, signed_in=False, reason='SIGN_IN'))

    reason = together.refusal(event, viewer)
    mine = EventPresence.objects.filter(event=event, user=viewer).first()
    data = dict(base, signed_in=True, reason=reason, band=together.band_of(viewer),
                allowed=together.allowed(event, viewer), me=_my_presence(mine, event),
                people=[], pings={'incoming': [], 'outgoing': []},
                pings_left_today=max(0, together.PINGS_PER_DAY - together.pings_today(event, viewer)))
    if reason:
        return _ok(data)

    pings = {(p.sender_id, p.recipient_id): p for p in
             EventPing.objects.filter(event=event).filter(Q(sender=viewer) | Q(recipient=viewer))}
    for presence in showing:
        if presence.user_id == viewer.user_id or not together.may_see_attendance(presence, viewer):
            continue
        other = presence.user
        sent = pings.get((viewer.user_id, other.user_id))
        received = pings.get((other.user_id, viewer.user_id))
        data['people'].append({
            'person': _person(other),
            'shared': together.shared_taste(viewer, other),
            'departure_area': presence.departure_area if together.may_see_departure(presence, viewer) else '',
            'ping_refusal': '' if (sent or received) else together.ping_refusal(event, viewer, other, presence),
            'ping_sent': sent.status if sent else '',
            'ping_received': received.status if received else '',
        })
    data['pings']['incoming'] = [_ping_row(p, viewer) for p in pings.values() if p.recipient_id == viewer.user_id]
    data['pings']['outgoing'] = [_ping_row(p, viewer) for p in pings.values() if p.sender_id == viewer.user_id]
    return _ok(data)


@api_view(['POST'])
def together_me(request, event_id):
    event = _event(event_id)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    code = together.refusal(event, viewer)
    if code:
        return _refuse(code)
    options = together.allowed(event, viewer)
    body = request.data if isinstance(request.data, dict) else {}

    presence, _ = EventPresence.objects.get_or_create(event=event, user=viewer)
    attendance = body.get('attendance_visibility', presence.attendance_visibility)
    departure = body.get('departure_visibility', presence.departure_visibility)
    area = str(body.get('departure_area', presence.departure_area) or '').strip()[:80]
    pings_open = bool(body.get('pings_open', presence.pings_open))

    if attendance not in options['attendance']:
        return _err('That choice is not available to you.', 'TOGETHER_NOT_ALLOWED')
    area_changed = area != presence.departure_area or departure != presence.departure_visibility
    if area_changed and together.event_over(event):
        return _err('The event is over, so the area can no longer be changed.', 'EVENT_OVER',
                    status.HTTP_409_CONFLICT)
    if departure != 'off' and departure not in options['departure']:
        return _err('That choice is not available to you.', 'TOGETHER_NOT_ALLOWED')
    if departure != 'off' and not area:
        return _err('Say which area you are leaving from, or keep it hidden.', 'AREA_REQUIRED')
    if pings_open and not options['pings']:
        return _err('That choice is not available to you.', 'TOGETHER_NOT_ALLOWED')

    with transaction.atomic():
        presence.attendance_visibility = attendance
        presence.departure_visibility = departure
        presence.departure_area = area
        presence.pings_open = pings_open
        if area_changed:
            presence.departure_set_at = timezone.now()
            # Kept for admins whatever happens to the presence (305b).
            DepartureRecord.objects.create(event=event, user=viewer, area=area, visibility=departure)
        presence.save()
    return _ok(_my_presence(presence, event), 'Saved.')


@api_view(['POST', 'DELETE'])
def together_approve(request, event_id):
    event = _event(event_id)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    code = together.refusal(event, viewer)
    if code:
        return _refuse(code)
    if 'approved' not in together.allowed(event, viewer)['departure']:
        return _err('That choice is not available to you.', 'TOGETHER_NOT_ALLOWED')
    username = str((request.data or {}).get('username') or '').strip().lstrip('@')
    other = Users.objects.filter(username__iexact=username).first() if username else None
    if other is None or other.user_id == viewer.user_id:
        return _err('No such person.', 'USER_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    presence, _ = EventPresence.objects.get_or_create(event=event, user=viewer)
    if request.method == 'DELETE':
        presence.approvals.filter(approved=other).delete()
        return _ok(_my_presence(presence, event), 'Removed.')
    if together.is_minor(together.band_of(other)):
        return _err('An adult cannot share where they are leaving from with somebody under 18.',
                    'PING_AGE', status.HTTP_403_FORBIDDEN)
    DepartureApproval.objects.get_or_create(presence=presence, approved=other)
    return _ok(_my_presence(presence, event), 'Approved.')


@api_view(['POST'])
def together_ping(request, event_id):
    from vent_auth.views_notifications import create_notification
    event = _event(event_id)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    code = together.refusal(event, viewer)
    if code:
        return _refuse(code)
    username = str((request.data or {}).get('username') or '').strip().lstrip('@')
    other = Users.objects.filter(username__iexact=username).first() if username else None
    if other is None:
        return _err('No such person.', 'USER_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    presence = EventPresence.objects.filter(event=event, user=other).first()
    why = together.ping_refusal(event, viewer, other, presence)
    if why:
        return _err('You cannot ping this person here.', why, status.HTTP_403_FORBIDDEN)
    if together.pings_today(event, viewer) >= together.PINGS_PER_DAY:
        return _err('That is the most pings for one day at this event.', 'PING_LIMIT',
                    status.HTTP_429_TOO_MANY_REQUESTS)
    if EventPing.objects.filter(event=event, sender=other, recipient=viewer).exists():
        return _err('They have already pinged you; answer theirs.', 'PING_THEIRS_WAITING',
                    status.HTTP_409_CONFLICT)
    message = str((request.data or {}).get('message') or '').strip()[:140]
    if EventPing.objects.filter(event=event, sender=viewer, recipient=other).exists():
        return _err('You have already pinged them at this event.', 'PING_ALREADY',
                    status.HTTP_409_CONFLICT)
    try:
        # Its own savepoint: two presses at once must not leave the request's
        # transaction broken behind a unique-constraint error.
        with transaction.atomic():
            ping = EventPing.objects.create(event=event, sender=viewer, recipient=other, message=message)
    except IntegrityError:
        return _err('You have already pinged them at this event.', 'PING_ALREADY',
                    status.HTTP_409_CONFLICT)
    create_notification(other, 'event', 'Somebody at an event wants to meet up',
                        body='%s, %s' % (viewer.username, event.name),
                        link='/events/%s?tab=together' % event.slug,
                        metadata={'code': 'PING_RECEIVED', 'event': event.slug})
    return _ok(_ping_row(ping, viewer), 'Sent.')


@api_view(['POST'])
def together_ping_answer(request, event_id, ping_id):
    from vent_auth.views_community import _conversation_for
    from vent_auth.views_notifications import create_notification
    event = _event(event_id)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    if viewer is None:
        return _refuse('SIGN_IN')
    ping = EventPing.objects.filter(event=event, id=ping_id, recipient=viewer).select_related('sender').first()
    if ping is None:
        # Somebody else's ping answers exactly like a missing one.
        return _err('No such ping.', 'PING_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if ping.status != 'sent':
        return _err('This ping has already been answered.', 'PING_ANSWERED', status.HTTP_409_CONFLICT)
    accept = bool((request.data or {}).get('accept'))
    with transaction.atomic():
        ping.status = 'accepted' if accept else 'declined'
        ping.answered_at = timezone.now()
        if accept:
            ping.conversation = _conversation_for(ping.sender, viewer)
        ping.save()
    if accept:
        create_notification(ping.sender, 'event', 'Your meet-up ping was accepted',
                            body='%s, %s' % (viewer.username, event.name),
                            link='/events/%s?tab=together' % event.slug,
                            metadata={'code': 'PING_ACCEPTED', 'event': event.slug})
    return _ok(_ping_row(ping, viewer), 'Answered.')


@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['view_location_history'])
def location_history(request):
    """Where people said they were leaving from, for a stated reason.

    Search by event (slug) and/or by person (username); at least one. The
    reason is required and every search is written to AdminAction, whether it
    finds anything or not (305c).
    """
    reason = str(request.query_params.get('reason') or '').strip()
    event_ref = str(request.query_params.get('event') or '').strip()
    person = str(request.query_params.get('person') or '').strip().lstrip('@')
    if len(reason) < 5:
        return _err('Say why you are looking, for example the report number.', 'REASON_REQUIRED')
    if not event_ref and not person:
        return _err('Search by an event, a person, or both.', 'SEARCH_TERM_REQUIRED')
    rows = DepartureRecord.objects.select_related('event', 'user')
    if event_ref:
        event = event_by_ref(event_ref)
        rows = rows.filter(event=event) if event else rows.none()
    if person:
        rows = rows.filter(user__username__iexact=person)
    rows = list(rows[:500])
    AdminAction.objects.create(
        admin=request.admin_user, action_type='search_location_history',
        target_model='DepartureRecord', target_id=(person or event_ref)[:100], reason=reason[:1000],
        metadata={'event': event_ref, 'person': person, 'results': len(rows)})
    return _ok({'results': [{
        'event': {'name': r.event.name, 'slug': r.event.slug},
        'person': _person(r.user),
        'area': r.area, 'visibility': r.visibility, 'set_at': r.set_at,
    } for r in rows]}, 'Location history')

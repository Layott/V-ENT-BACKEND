"""Telling everybody holding a ticket something.

PRD section 4: notifications to registered attendees.

"The gate has changed", "doors are an hour later", "bring photo ID". These are
the messages that decide whether people arrive at the right place at the right
time, and an organiser had no way to send one except by finding everybody
themselves.

Three things this gets right that a mail-merge does not:

**Guests are included.** Most ticket holders here have no account. An
announcement that only reached members would miss the majority of the room,
which is the same as not sending it.

**One email per address, never a bcc list.** A bcc field is one mis-click away
from publishing the attendee list, and that list is the thing people handed over
an address to be on rather than to be shown. Addresses are deduplicated, so
somebody who bought four tickets is told once.

**The row is written before the sending starts.** An announcement that half
sent is a record with a count and an error on it, not an event nobody can prove
happened.
"""
import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import emails
from vent_auth.models import Users
from vent_auth.views_notifications import create_notification

from .models import Event, EventAnnouncement, EventManager, Ticket

logger = logging.getLogger(__name__)

# An organiser with a send button is an organiser who can empty an inbox. Five
# a day is more than any real event needs and few enough that nobody
# unsubscribes from the platform over one.
DAILY_LIMIT = 5


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _authenticate(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is '
                            'required.', 'UNAUTHORIZED',
                            status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED',
                            status.HTTP_401_UNAUTHORIZED)
    return user, None


def _event(event_id):
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


def _may_send(user, event):
    if event.creator_id == user.user_id:
        return True
    return EventManager.objects.filter(
        event=event, user=user, role='manager').exists()


def _recipients(event, audience):
    """(rows, addresses) for the audience the organiser picked.

    Refunded and cancelled tickets are excluded everywhere: somebody who got
    their money back is not going, and telling them the gate moved is noise
    they did not ask for.
    """
    rows = Ticket.objects.filter(event=event).exclude(
        status__in=('refunded', 'cancelled')).select_related('user')
    if audience == 'checked_in':
        rows = rows.filter(status='checked_in')
    elif audience == 'not_checked_in':
        rows = rows.exclude(status='checked_in')
    return rows


def serialize_announcement(row):
    return {
        'id': row.id,
        'subject': row.subject,
        'body': row.body,
        'audience': row.audience,
        'recipients': row.recipients,
        'notified_in_app': row.notified_in_app,
        'sent_at': row.sent_at,
        'sent_by': row.sent_by.username if row.sent_by_id else '',
        'email_error': row.email_error,
    }


@api_view(['GET', 'POST'])
def announcements(request, event_id):
    """GET: what has been sent. POST: send one.

    GET is open to anybody who can see the event. An announcement is public
    information about a public event, and a reader deciding whether to buy a
    ticket benefits from seeing that the organiser moved the doors twice.
    """
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        rows = EventAnnouncement.objects.filter(event=event).select_related('sent_by')
        return Response({'status': 'success', 'data': {
            'announcements': [serialize_announcement(r) for r in rows],
        }, 'message': ''})

    user, err = _authenticate(request)
    if err:
        return err
    if not _may_send(user, event):
        return _error('Only the event organizer can message ticket holders.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    subject = str(request.data.get('subject') or '').strip()
    body = str(request.data.get('body') or '').strip()
    audience = str(request.data.get('audience') or 'all').strip()

    if not subject:
        return _error('Give the message a subject.', 'VALIDATION_ERROR')
    if not body:
        return _error('The message is empty.', 'VALIDATION_ERROR')
    if len(subject) > 140:
        return _error('Keep the subject under 140 characters.',
                      'VALIDATION_ERROR')
    if len(body) > 2000:
        return _error('Keep the message under 2000 characters.',
                      'VALIDATION_ERROR')
    if audience not in dict(EventAnnouncement.AUDIENCE_CHOICES):
        return _error('Send to everybody, to people who have arrived, or to '
                      'people who have not.', 'VALIDATION_ERROR')

    since = timezone.now() - timezone.timedelta(days=1)
    already = EventAnnouncement.objects.filter(
        event=event, sent_at__gte=since).count()
    if already >= DAILY_LIMIT:
        return _error('That is %d messages to this event today. Ticket holders '
                      'stop reading, so the rest have to wait until tomorrow.'
                      % already, 'RATE_LIMITED', status.HTTP_429_TOO_MANY_REQUESTS,
                      extra={'limit': DAILY_LIMIT, 'sent_today': already})

    tickets = list(_recipients(event, audience))

    # One send per address. Somebody who bought four tickets is one person and
    # gets told once.
    addresses = {}
    account_ids = set()
    for ticket in tickets:
        address = (ticket.attendee_email or
                   (ticket.user.email if ticket.user_id else '')).strip().lower()
        if address:
            addresses.setdefault(address, ticket)
        if ticket.user_id:
            account_ids.add(ticket.user_id)

    row = EventAnnouncement.objects.create(
        event=event, sent_by=user, subject=subject, body=body,
        audience=audience, recipients=len(addresses),
        notified_in_app=len(account_ids))

    # The inbox first: it is a database write, it cannot time out, and it is
    # what a signed-in reader sees when they come back.
    link = '/events/%s' % (event.slug or event.event_id)
    for user_id in account_ids:
        create_notification(user_id, 'event', subject, body=body[:500],
                            link=link,
                            metadata={'event_id': event.event_id,
                                      'announcement_id': row.id})

    failures = 0
    for address in addresses:
        try:
            if not emails.send_event_announcement(
                    address, event=event, subject=subject, body=body):
                failures += 1
        except Exception:
            logger.exception('announcement %s failed for %s', row.id, address)
            failures += 1

    if failures:
        # Recorded on the row rather than raised. The message went to most
        # people, and an organiser needs to know which half rather than a 500.
        row.email_error = '%d of %d emails did not send.' % (
            failures, len(addresses))
        row.save(update_fields=['email_error'])

    return Response({'status': 'success', 'data': {
        'announcement': serialize_announcement(row),
    }, 'message': ''}, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def announcement_audience(request, event_id):
    """How many people each choice would reach, before anybody writes anything.

    Sending to "people who have not arrived" without knowing that is nobody is
    how an organiser writes a message twice.
    """
    user, err = _authenticate(request)
    if err:
        return err
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not _may_send(user, event):
        return _error('Only the event organizer can message ticket holders.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    counts = {}
    for key, _label in EventAnnouncement.AUDIENCE_CHOICES:
        rows = _recipients(event, key)
        addresses = {
            (t.attendee_email or (t.user.email if t.user_id else '')).strip().lower()
            for t in rows
        }
        addresses.discard('')
        counts[key] = len(addresses)

    since = timezone.now() - timezone.timedelta(days=1)
    return Response({'status': 'success', 'data': {
        'audiences': counts,
        'sent_today': EventAnnouncement.objects.filter(
            event=event, sent_at__gte=since).count(),
        'daily_limit': DAILY_LIMIT,
    }, 'message': ''})

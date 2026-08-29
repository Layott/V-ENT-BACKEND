"""Asking the room.

PRD section 4: polls for attendees.

"Which day should the finals be", "what should we play next", "did the food
arrive". An organiser holding an event has questions whose answer is whatever
the people in the room say, and no way to ask them.

The decisions here:

**A vote belongs to a ticket, not to an account.** Most people holding a ticket
have no account, and a poll only members could answer would be a poll of the
wrong room. One ticket is one vote, which is also the only definition that
cannot be gamed by signing up twice.

**Results are hidden until you have answered**, unless the organiser says
otherwise. A visible tally moves later answers toward whatever is winning, and
an organiser asking "which day suits you" wants the answer rather than the
bandwagon. The organiser always sees it; so does everybody once it closes.

**A finished poll is closed, never deleted.** The answers are the point, and
deleting the question throws them away.
"""
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from .models import (Event, EventManager, EventPoll, EventPollOption,
                     EventPollVote, Ticket)

MAX_OPTIONS = 10


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


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


def _may_manage(user, event):
    if user is None:
        return False
    if event.creator_id == user.user_id:
        return True
    return EventManager.objects.filter(
        event=event, user=user, role='manager').exists()


def _ticket_for(request, event):
    """The ticket this request may vote with, or None.

    A code in the body, or a ticket the signed-in viewer already holds. The
    code is what a guest has, and it is the same credential the door reads.
    """
    code = str(request.data.get('ticket_code') or '').strip().upper()
    if code:
        return Ticket.objects.filter(event=event, code=code).exclude(
            status__in=('refunded', 'cancelled')).first()
    viewer = _viewer(request)
    if viewer is None:
        return None
    return Ticket.objects.filter(event=event, user=viewer).exclude(
        status__in=('refunded', 'cancelled')).first()


def serialize_poll(poll, *, ticket=None, is_organiser=False):
    """One poll, with the counts only when the reader is allowed to see them."""
    options = list(poll.options.all())
    mine = None
    if ticket is not None:
        vote = EventPollVote.objects.filter(poll=poll, ticket=ticket).first()
        mine = vote.option_id if vote else None

    closed = poll.closed()
    # The organiser is running the thing and needs the numbers to run it. A
    # closed poll has nothing left to influence. Everybody else sees the count
    # once they have answered, or if the organiser chose to show it.
    may_see = is_organiser or closed or poll.show_results_before_voting or mine is not None

    total = EventPollVote.objects.filter(poll=poll).count()
    rows = []
    for option in options:
        row = {'id': option.id, 'text': option.text,
               'position': option.position}
        if may_see:
            count = option.votes.count()
            row['votes'] = count
            # Percentages of nothing are not zero, they are unanswerable, and a
            # bar chart drawn from a made-up zero reads as a real result.
            row['share'] = round(count * 100.0 / total, 1) if total else None
        rows.append(row)

    return {
        'id': poll.id,
        'question': poll.question,
        'options': rows,
        'is_open': not closed,
        'closes_at': poll.closes_at,
        'show_results_before_voting': poll.show_results_before_voting,
        'results_visible': may_see,
        'total_votes': total if may_see else None,
        'my_option_id': mine,
        'created_at': poll.created_at,
    }


@api_view(['GET', 'POST'])
def polls(request, event_id):
    """GET: the event's polls. POST: the organiser adds one."""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        viewer = _viewer(request)
        is_organiser = _may_manage(viewer, event)
        # A code in the query string so a guest reading the page sees what they
        # already answered. GET carries no body.
        code = str(request.GET.get('ticket_code') or '').strip().upper()
        ticket = None
        if code:
            ticket = Ticket.objects.filter(event=event, code=code).first()
        elif viewer is not None:
            ticket = Ticket.objects.filter(event=event, user=viewer).first()

        rows = (EventPoll.objects.filter(event=event)
                .prefetch_related('options'))
        return Response({'status': 'success', 'data': {
            'polls': [serialize_poll(p, ticket=ticket,
                                     is_organiser=is_organiser) for p in rows],
        }, 'message': ''})

    viewer = _viewer(request)
    if viewer is None:
        return _error('Authorization header with a Bearer token is required.',
                      'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(viewer, event):
        return _error('Only the event organizer can add a poll.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    question = str(request.data.get('question') or '').strip()
    raw_options = request.data.get('options') or []
    if not question:
        return _error('Ask a question.', 'VALIDATION_ERROR')
    if len(question) > 200:
        return _error('Keep the question under 200 characters.',
                      'VALIDATION_ERROR')
    if not isinstance(raw_options, list):
        return _error('Options have to be a list.', 'VALIDATION_ERROR')

    texts = []
    for value in raw_options:
        text = str(value or '').strip()[:140]
        if text and text.lower() not in [t.lower() for t in texts]:
            texts.append(text)
    if len(texts) < 2:
        return _error('A poll needs at least two things to choose between.',
                      'VALIDATION_ERROR')
    if len(texts) > MAX_OPTIONS:
        return _error('Keep it to %d options. Past that nobody reads to the '
                      'bottom.' % MAX_OPTIONS, 'VALIDATION_ERROR')

    closes_at = None
    raw_closes = request.data.get('closes_at')
    if raw_closes:
        closes_at = parse_datetime(str(raw_closes))
        if closes_at is None:
            return _error('That closing time is not a date.',
                          'VALIDATION_ERROR')
        if timezone.is_naive(closes_at):
            closes_at = timezone.make_aware(closes_at)
        if closes_at <= timezone.now():
            return _error('A poll that closes in the past collects nothing.',
                          'VALIDATION_ERROR')

    with transaction.atomic():
        poll = EventPoll.objects.create(
            event=event, question=question, created_by=viewer,
            closes_at=closes_at,
            show_results_before_voting=bool(
                request.data.get('show_results_before_voting')))
        EventPollOption.objects.bulk_create([
            EventPollOption(poll=poll, text=text, position=index)
            for index, text in enumerate(texts)
        ])

    poll.refresh_from_db()
    return Response({'status': 'success', 'data': {
        'poll': serialize_poll(poll, is_organiser=True),
    }, 'message': ''}, status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def poll_detail(request, event_id, poll_id):
    """Close it, reopen it, or remove one that should never have been asked."""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    poll = EventPoll.objects.filter(event=event, pk=poll_id).first()
    if poll is None:
        return _error('Poll not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    viewer = _viewer(request)
    if viewer is None:
        return _error('Authorization header with a Bearer token is required.',
                      'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(viewer, event):
        return _error('Only the event organizer can change a poll.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        # Deleting takes the answers with it, so it is refused once anybody has
        # answered. Closing is what the organiser almost always means.
        if EventPollVote.objects.filter(poll=poll).exists():
            return _error('People have already answered this. Close it instead '
                          'of deleting their answers.', 'POLL_HAS_VOTES',
                          status.HTTP_409_CONFLICT)
        poll.delete()
        return Response({'status': 'success', 'data': {}, 'message': ''})

    if 'is_open' in request.data:
        poll.is_open = bool(request.data.get('is_open'))
        # Reopening a poll whose deadline has passed would close it again on the
        # next read, which reads as the button not working.
        if poll.is_open and poll.closes_at and poll.closes_at <= timezone.now():
            poll.closes_at = None
    if 'show_results_before_voting' in request.data:
        poll.show_results_before_voting = bool(
            request.data.get('show_results_before_voting'))
    poll.save()

    return Response({'status': 'success', 'data': {
        'poll': serialize_poll(poll, is_organiser=True),
    }, 'message': ''})


@api_view(['POST'])
def vote(request, event_id, poll_id):
    """One ticket, one answer. Sending a different option changes it."""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    poll = EventPoll.objects.filter(event=event, pk=poll_id).first()
    if poll is None:
        return _error('Poll not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if poll.closed():
        return _error('This poll has closed.', 'POLL_CLOSED',
                      status.HTTP_409_CONFLICT)

    ticket = _ticket_for(request, event)
    if ticket is None:
        # Said before anybody picks an option, not after.
        return _error('Only people holding a ticket for this event can answer.',
                      'TICKET_REQUIRED', status.HTTP_403_FORBIDDEN)

    option = EventPollOption.objects.filter(
        poll=poll, pk=request.data.get('option_id')).first()
    if option is None:
        return _error('Pick one of the options.', 'VALIDATION_ERROR')

    EventPollVote.objects.update_or_create(
        poll=poll, ticket=ticket, defaults={'option': option})

    return Response({'status': 'success', 'data': {
        'poll': serialize_poll(poll, ticket=ticket),
    }, 'message': ''})

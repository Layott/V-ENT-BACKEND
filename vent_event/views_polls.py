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

from .models import (Event, EventManager, EventPoll, EventPollChoice,
                     EventPollOption, EventPollVote, Ticket)

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
    # One rule, in permissions.py, so the organisation's own managers reach
    # this screen exactly as they reach every other one.
    from .permissions import may_run_event
    return may_run_event(user, event)


def _votable_ticket(event, code, viewer):
    """The ticket that may answer, or None. ONE definition, used by both paths.

    The read path and the vote path each had their own lookup and they drifted:
    the read path did not exclude refunded tickets, so somebody who had been
    refunded was shown a live button that answered 403 when pressed. Both now
    call this.
    """
    live = Ticket.objects.filter(event=event).exclude(
        status__in=('refunded', 'cancelled'))
    if code:
        return live.filter(code=str(code).strip().upper()).first()
    if viewer is None:
        return None
    return live.filter(user=viewer).first()


def _ticket_for(request, event):
    """The ticket this request may vote with, or None.

    A code in the body, or a ticket the signed-in viewer already holds. The
    code is what a guest has, and it is the same credential the door reads.
    """
    return _votable_ticket(event, request.data.get('ticket_code'),
                           _viewer(request))


def _my_answer(poll, ticket):
    """What this ticket answered, in the shape the question was asked in."""
    if ticket is None:
        return None, {}
    vote = (EventPollVote.objects
            .filter(poll=poll, ticket=ticket)
            .prefetch_related('choices').first())
    if vote is None:
        return None, {}
    mine = {
        'my_option_id': vote.option_id,
        'my_option_ids': [c.option_id for c in vote.choices.all()],
        'my_number': vote.number,
        'my_text': vote.text,
    }
    return vote, mine


def _option_counts(poll, options, total):
    """How many answers each option is in, for the kinds built from options."""
    rows = []
    for option in options:
        # `votes` is the single-choice answer; `choices` is every other kind.
        count = option.votes.count() + option.choices.count()
        row = {'id': option.id, 'text': option.text,
               'position': option.position, 'votes': count}
        # Percentages of nothing are not zero, they are unanswerable, and a bar
        # chart drawn from a made-up zero reads as a real result.
        row['share'] = round(count * 100.0 / total, 1) if total else None
        if poll.kind == EventPoll.RANKING:
            # Where people put it on average. Lower is higher up the list, and
            # it is the only number that means anything for a ranking: a count
            # says everybody used the option, not where they put it.
            places = [c.position + 1 for c in option.choices.all()]
            row['average_place'] = (
                round(sum(places) / len(places), 2) if places else None)
        rows.append(row)
    return rows


def serialize_poll(poll, *, ticket=None, is_organiser=False):
    """One poll, with the results only when the reader is allowed to see them."""
    options = list(poll.options.all())
    # Whether this reader is being asked this question at all. The organiser
    # always sees every question, because they are looking at the form rather
    # than filling it in.
    shown = is_organiser or poll.visible_for(ticket)
    vote, mine = _my_answer(poll, ticket)
    answered = vote is not None

    closed = poll.closed()
    # The organiser is running the thing and needs the numbers to run it. A
    # closed poll has nothing left to influence. Everybody else sees the count
    # once they have answered, or if the organiser chose to show it.
    # And never the results of a question this reader is not being asked.
    may_see = (is_organiser
               or (shown and (closed or poll.show_results_before_voting
                              or answered)))

    total = EventPollVote.objects.filter(poll=poll).count()

    data = {
        'id': poll.id,
        'question': poll.question,
        'kind': poll.kind,
        # What the page uses to decide whether to draw it, and what the
        # organiser's form uses to show the link.
        'visible': shown,
        'depends_on': poll.depends_on_id,
        'depends_on_option': poll.depends_on_option_id,
        'depends_on_min': poll.depends_on_min,
        'depends_on_max': poll.depends_on_max,
        'help_text': poll.help_text,
        'required': poll.required,
        'options': [{'id': o.id, 'text': o.text, 'position': o.position}
                    for o in options],
        'is_open': not closed,
        'closes_at': poll.closes_at,
        'show_results_before_voting': poll.show_results_before_voting,
        'results_visible': may_see,
        'total_votes': total if may_see else None,
        'answered': answered,
        'created_at': poll.created_at,
        # Kept for the screens that were written against the original shape.
        'my_option_id': mine.get('my_option_id'),
    }
    data.update(mine)

    if poll.kind == EventPoll.MULTIPLE:
        data['min_choices'] = poll.min_choices
        data['max_choices'] = poll.max_choices
    if poll.kind == EventPoll.SCALE:
        data.update({
            'scale_min': poll.scale_min,
            'scale_max': poll.scale_max,
            'scale_min_label': poll.scale_min_label,
            'scale_max_label': poll.scale_max_label,
        })

    if not may_see:
        return data

    if poll.kind in EventPoll.OPTION_KINDS:
        data['options'] = _option_counts(poll, options, total)
    elif poll.kind == EventPoll.SCALE:
        numbers = [v.number for v in EventPollVote.objects.filter(poll=poll)
                   if v.number is not None]
        data['average'] = round(sum(numbers) / len(numbers), 2) if numbers else None
        data['distribution'] = [
            {'value': n, 'votes': numbers.count(n)}
            for n in range(poll.scale_min, poll.scale_max + 1)
        ]
    elif poll.kind in EventPoll.TEXT_KINDS:
        # Only the organiser reads the sentences. A count is anonymous; a
        # sentence somebody typed is not, and publishing it back to the room is
        # not what they agreed to by answering a poll.
        data['answers'] = (
            [v.text for v in EventPollVote.objects.filter(poll=poll).exclude(text='')]
            if is_organiser else None)
        data['answers_visible_to'] = 'organiser'

    return data


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
        ticket = _votable_ticket(event, code, viewer)

        rows = (EventPoll.objects.filter(event=event)
                .prefetch_related('options'))
        return Response({'status': 'success', 'data': {
            'polls': [serialize_poll(p, ticket=ticket,
                                     is_organiser=is_organiser) for p in rows],
            # Whether THIS reader can answer at all, so the page can say so
            # before they press something. Signing in is not the same as
            # holding a ticket: somebody with an account and no ticket, and
            # somebody who bought as a guest under another address, both reach
            # this page, and a live button that answers 403 tells them only
            # after they have chosen.
            'can_answer': ticket is not None,
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

    kind = str(request.data.get('kind') or EventPoll.SINGLE).strip()
    if kind not in dict(EventPoll.KIND_CHOICES):
        return _error('That is not a kind of question this can ask.',
                      'VALIDATION_ERROR')

    if not isinstance(raw_options, list):
        return _error('Options have to be a list.', 'VALIDATION_ERROR')

    texts = []
    for value in raw_options:
        text = str(value or '').strip()[:140]
        if text and text.lower() not in [t.lower() for t in texts]:
            texts.append(text)

    # A question built from options needs options; one that is answered in
    # words must not have them, because an option list nobody can pick from is
    # a control that does nothing.
    if kind in EventPoll.OPTION_KINDS:
        if len(texts) < 2:
            return _error('A poll needs at least two things to choose between.',
                          'VALIDATION_ERROR')
        if len(texts) > MAX_OPTIONS:
            return _error('Keep it to %d options. Past that nobody reads to the '
                          'bottom.' % MAX_OPTIONS, 'VALIDATION_ERROR')
    else:
        texts = []

    def _whole(name, default):
        raw = request.data.get(name)
        if raw in (None, ''):
            return default, None
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, _error('%s has to be a whole number.' % name,
                                'VALIDATION_ERROR')

    min_choices, err = _whole('min_choices', 0)
    if err:
        return err
    max_choices, err = _whole('max_choices', 0)
    if err:
        return err
    scale_min, err = _whole('scale_min', 1)
    if err:
        return err
    scale_max, err = _whole('scale_max', 5)
    if err:
        return err

    if kind == EventPoll.MULTIPLE:
        if max_choices and min_choices and min_choices > max_choices:
            return _error('The smallest number of choices cannot be more than '
                          'the largest.', 'VALIDATION_ERROR')
        if max_choices and max_choices > len(texts):
            return _error('You cannot ask for more choices than there are '
                          'options.', 'VALIDATION_ERROR')
    if kind == EventPoll.SCALE:
        if scale_max - scale_min < 1:
            return _error('A scale needs at least two points.',
                          'VALIDATION_ERROR')
        if scale_max - scale_min > 10:
            return _error('Keep a scale to eleven points or fewer. Past that '
                          'people pick the middle.', 'VALIDATION_ERROR')

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

    # Which earlier question reveals this one, if any.
    depends_on = None
    depends_on_option = None
    depends_on_min = None
    depends_on_max = None
    raw_depends = request.data.get('depends_on')
    if raw_depends not in (None, '', 0):
        depends_on = EventPoll.objects.filter(event=event, pk=raw_depends).first()
        if depends_on is None:
            return _error('That question is not on this event.', 'VALIDATION_ERROR')

        raw_option = request.data.get('depends_on_option')
        if raw_option not in (None, '', 0):
            depends_on_option = EventPollOption.objects.filter(
                poll=depends_on, pk=raw_option).first()
            if depends_on_option is None:
                return _error('That answer is not an option on that question.',
                              'VALIDATION_ERROR')

        for name in ('depends_on_min', 'depends_on_max'):
            raw = request.data.get(name)
            if raw in (None, ''):
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return _error('%s has to be a whole number.' % name,
                              'VALIDATION_ERROR')
            if name == 'depends_on_min':
                depends_on_min = value
            else:
                depends_on_max = value

        if (depends_on_option is not None
                and (depends_on_min is not None or depends_on_max is not None)):
            return _error('Depend on an answer or on a range, not both.',
                          'VALIDATION_ERROR')
        if depends_on_option is not None and depends_on.kind not in EventPoll.OPTION_KINDS:
            return _error('That question is not answered by picking an option.',
                          'VALIDATION_ERROR')
        if ((depends_on_min is not None or depends_on_max is not None)
                and depends_on.kind != EventPoll.SCALE):
            return _error('A range only makes sense on a question with a scale.',
                          'VALIDATION_ERROR')

        # A chain that loops back never becomes visible, and would sit in the
        # form looking like a question nobody can reach.
        seen = set()
        walk = depends_on
        while walk is not None:
            if walk.id in seen:
                return _error('That would make the questions depend on each other.',
                              'VALIDATION_ERROR')
            seen.add(walk.id)
            walk = walk.depends_on

    with transaction.atomic():
        poll = EventPoll.objects.create(
            event=event, question=question, created_by=viewer,
            kind=kind,
            help_text=str(request.data.get('help_text') or '').strip()[:280],
            required=bool(request.data.get('required')),
            min_choices=min_choices, max_choices=max_choices,
            scale_min=scale_min, scale_max=scale_max,
            scale_min_label=str(request.data.get('scale_min_label') or '').strip()[:40],
            scale_max_label=str(request.data.get('scale_max_label') or '').strip()[:40],
            closes_at=closes_at,
            depends_on=depends_on, depends_on_option=depends_on_option,
            depends_on_min=depends_on_min, depends_on_max=depends_on_max,
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

    # A question revealed by an earlier answer is not merely hidden in the page.
    # Hiding a control and leaving its endpoint open is not hiding anything: the
    # address is in the same response that hid it.
    if not poll.visible_for(ticket):
        return _error('That question is not being asked of you yet.',
                      'POLL_NOT_VISIBLE', status.HTTP_403_FORBIDDEN)

    # Everything is validated before anything is written, so a refused answer
    # leaves the previous one intact. Somebody correcting an answer and getting
    # it wrong should not lose the answer they had.
    fields = {'option': None, 'number': None, 'text': ''}
    chosen = []

    if poll.kind == EventPoll.SINGLE:
        option = EventPollOption.objects.filter(
            poll=poll, pk=request.data.get('option_id')).first()
        if option is None:
            return _error('Pick one of the options.', 'VALIDATION_ERROR')
        fields['option'] = option

    elif poll.kind in (EventPoll.MULTIPLE, EventPoll.RANKING):
        raw = request.data.get('option_ids')
        if not isinstance(raw, list):
            return _error('Send the options you picked as a list.',
                          'VALIDATION_ERROR')
        seen = []
        for value in raw:
            option = EventPollOption.objects.filter(poll=poll, pk=value).first()
            if option is None:
                return _error('One of those is not an option on this poll.',
                              'VALIDATION_ERROR')
            if option.id in [o.id for o in seen]:
                return _error('You picked the same option twice.',
                              'VALIDATION_ERROR')
            seen.append(option)

        if poll.kind == EventPoll.MULTIPLE:
            if not seen:
                return _error('Pick at least one.', 'VALIDATION_ERROR')
            if poll.min_choices and len(seen) < poll.min_choices:
                return _error('Pick at least %d.' % poll.min_choices,
                              'VALIDATION_ERROR')
            if poll.max_choices and len(seen) > poll.max_choices:
                return _error('Pick no more than %d.' % poll.max_choices,
                              'VALIDATION_ERROR')
        else:
            # A ranking is an order over all of them; a partial order is not an
            # answer to "put these in order".
            if len(seen) != poll.options.count():
                return _error('Put all of them in order.', 'VALIDATION_ERROR')
        chosen = seen

    elif poll.kind == EventPoll.SCALE:
        try:
            number = int(request.data.get('number'))
        except (TypeError, ValueError):
            return _error('Pick a number on the scale.', 'VALIDATION_ERROR')
        if not (poll.scale_min <= number <= poll.scale_max):
            return _error('That is not on the scale.', 'VALIDATION_ERROR')
        fields['number'] = number

    else:
        text = str(request.data.get('text') or '').strip()
        if not text:
            return _error('Write an answer.', 'VALIDATION_ERROR')
        limit = 120 if poll.kind == EventPoll.SHORT_TEXT else 2000
        if len(text) > limit:
            return _error('Keep it under %d characters.' % limit,
                          'VALIDATION_ERROR')
        fields['text'] = text

    with transaction.atomic():
        answer, _ = EventPollVote.objects.update_or_create(
            poll=poll, ticket=ticket, defaults=fields)
        # Replaced rather than added to: answering again is a correction, not a
        # second answer.
        answer.choices.all().delete()
        if chosen:
            EventPollChoice.objects.bulk_create([
                EventPollChoice(vote=answer, option=option, position=index)
                for index, option in enumerate(chosen)
            ])

    return Response({'status': 'success', 'data': {
        'poll': serialize_poll(poll, ticket=ticket),
    }, 'message': ''})

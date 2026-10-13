"""Party games at an event (inbox 434, 435, 439).

CEO, 12 October 2026: organisers add games "to their events under like a games
section, so people can register for them when they buy tickets or after they
register ... only people who register for an event can participate". Approved
as the mockup V-ENT-FRONTEND/docs/mockups/party-games.html.

The decisions, as the mockup put them:

**Reading is public, playing takes a ticket.** The list of games and how each
one is played answer anybody, signed in or not, because a visitor deciding
whether to buy a ticket is exactly who wants to know what happens there.
Signing up takes a live ticket for this event.

**A place belongs to a ticket, not an account**, as a poll vote does. Most
ticket holders bought as guests. A signed-in reader plays with their own
ticket; a guest types the code printed on theirs, the same credential the door
reads. A buyer holding tickets for friends signs each one up separately.

**Places, then a waiting list.** The rules live in game_entries.py, because the
refund path moves people as well as these doors.
"""
import csv

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import inputs
from vent_auth.throttle import limited

from . import game_entries, party_games
from .models import (EventGame, EventGameEntry, EventPoll, EventPollOption,
                     EventPollVote, Ticket)
from .views_polls import _error, _event, _may_manage, _viewer, serialize_poll
from .views_tickets import _holder

#: More than any event has asked for; enough that nobody meets it by accident.
MAX_GAMES = 60
MAX_PLACES = 100000
EDITABLE = ('starts_at', 'place', 'places', 'prize', 'status', 'position',
            'custom_name', 'custom_steps', 'custom_needs', 'custom_players',
            'custom_length')


def _live_tickets(event, viewer):
    """The signed-in reader's live tickets for this event, oldest first."""
    if viewer is None:
        return []
    return list(Ticket.objects.filter(event=event, user=viewer)
                .exclude(status__in=('refunded', 'cancelled'))
                .select_related('tier', 'user').order_by('purchased_at', 'id'))


def _ticket_by_code(event, code):
    code = str(code or '').strip().upper()
    if not code:
        return None
    return (Ticket.objects.filter(event=event, code=code)
            .exclude(status__in=('refunded', 'cancelled'))
            .select_related('tier', 'user').first())


def _ticket_row(ticket):
    return {'code': ticket.code, 'name': _holder(ticket),
            'tier': ticket.tier.name if ticket.tier_id else ''}


def serialize_game(game, *, playing, waiting, mine=None):
    """One game, in the one shape every reader gets.

    `mine` maps a ticket code to where that ticket stands in this game, for the
    tickets the reader holds (or typed in). Nobody else's codes are ever here.
    """
    meta = party_games.get(game.catalogue_key) if game.catalogue_key else None
    return {
        'id': game.id,
        'catalogue_key': game.catalogue_key or None,
        'catalogue': party_games.as_json(meta) if meta else None,
        'custom': None if meta else {
            'name': game.custom_name,
            'steps': [line.strip() for line in game.custom_steps.splitlines() if line.strip()],
            'needs': game.custom_needs,
            'players': game.custom_players,
            'length': game.custom_length,
        },
        'starts_at': game.starts_at,
        'place': game.place,
        'places': game.places,
        'prize': game.prize,
        'fee_vc': float(game.fee_vc or 0),
        'status': game.status,
        'position': game.position,
        'playing': playing,
        'waiting': waiting,
        'full': game.places is not None and playing >= game.places,
        'mine': mine or {},
    }


def games_vote_for(event):
    """The event's vote over games: the open one, else the latest. None if never."""
    polls = (EventPoll.objects.filter(event=event, options__game__isnull=False)
             .distinct().order_by('-created_at'))
    for poll in polls:
        if not poll.closed():
            return poll
    return polls.first()


def _vote_payload(event, *, tickets, is_organiser, games_by_id):
    """The vote as this reader sees it, with each option's game attached.

    Answers are per ticket, like every poll here: a buyer holding three tickets
    votes three times, once for each person going.
    """
    poll = games_vote_for(event)
    if poll is None:
        return None
    answers = {}
    for vote in (EventPollVote.objects.filter(poll=poll, ticket__in=tickets)
                 .select_related('ticket').prefetch_related('choices')):
        answers[vote.ticket.code] = [c.option_id for c in vote.choices.all()]
    # Whether the count shows is decided on a ticket that has voted, when one
    # has: somebody holding two tickets who voted with the second has voted,
    # and was told so while the count stayed hidden (walk, 13 October).
    voted = [t for t in tickets if t.code in answers]
    reader = voted[0] if voted else (tickets[0] if tickets else None)
    data = serialize_poll(poll, ticket=reader, is_organiser=is_organiser)
    for option in data['options']:
        game = games_by_id.get(option.get('game'))
        option['game'] = game
    data['answers_by_ticket'] = answers
    return data


def _games_payload(event, *, viewer, tickets, is_organiser):
    every = list(EventGame.objects.filter(event=event))
    # A game put to the vote is not on yet: the public list leaves it to the
    # vote panel, and an organiser sees it where they run the vote.
    rows = every if is_organiser else [
        g for g in every if g.status not in (EventGame.OFF, EventGame.PROPOSED)]

    live = {}
    for entry in EventGameEntry.objects.filter(
            game__in=every, state__in=game_entries.LIVE).values('game_id', 'state'):
        seat = live.setdefault(entry['game_id'], [0, 0])
        seat[0 if entry['state'] == EventGameEntry.IN else 1] += 1

    mine = {}
    if tickets:
        for entry in (EventGameEntry.objects
                      .filter(game__in=every, ticket__in=tickets)
                      .exclude(state=EventGameEntry.LEFT)
                      .select_related('ticket')):
            mine.setdefault(entry.game_id, {})[entry.ticket.code] = {
                'state': entry.state,
                'place_in_line': game_entries.position_on_list(entry),
            }

    every_json = {g.id: serialize_game(g, playing=live.get(g.id, [0, 0])[0],
                                       waiting=live.get(g.id, [0, 0])[1],
                                       mine=mine.get(g.id)) for g in every}
    return {
        'games': [every_json[g.id] for g in rows],
        'vote': _vote_payload(event, tickets=tickets, is_organiser=is_organiser,
                              games_by_id=every_json),
        'tickets': [_ticket_row(t) for t in tickets],
        'can_play': bool(tickets) and event.is_active,
        'is_organiser': is_organiser,
        'signed_in': viewer is not None,
    }


@api_view(['GET'])
def catalogue(request):
    """The fifty games every organiser picks from. Words are on the frontend."""
    return Response({'status': 'success', 'data': {
        'games': party_games.catalogue(),
        'regions': list(party_games.REGIONS),
        'kinds': list(party_games.KINDS),
    }, 'message': ''})


@api_view(['GET', 'POST'])
def games(request, event_id):
    """GET: the event's games, public. POST: the organiser adds one."""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)

    if request.method == 'GET':
        is_organiser = _may_manage(viewer, event)
        return Response({'status': 'success', 'data': _games_payload(
            event, viewer=viewer, tickets=_live_tickets(event, viewer),
            is_organiser=is_organiser), 'message': ''})

    if viewer is None:
        return _error('Authorization header with a Bearer token is required.',
                      'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(viewer, event):
        return _error('Only the event organizer can add a game.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    if EventGame.objects.filter(event=event).count() >= MAX_GAMES:
        return _error('An event can carry %d games.' % MAX_GAMES, 'GAME_LIMIT',
                      status.HTTP_409_CONFLICT, {'limit': MAX_GAMES})

    key = inputs.read_text(request.data, 'catalogue_key', max_length=40).lower()
    custom_name = inputs.read_text(request.data, 'custom_name', max_length=80)
    if key and party_games.get(key) is None:
        return _error('That game is not in the catalogue.', 'UNKNOWN_GAME')
    if not key and not custom_name:
        return _error('Pick a game from the list or name your own.',
                      'GAME_NAME_REQUIRED')

    game = EventGame(event=event, catalogue_key=key, created_by=viewer,
                     position=EventGame.objects.filter(event=event).count())
    if not key:
        game.custom_name = custom_name
    refused = _apply(game, request.data, creating=True)
    if refused is not None:
        return refused
    game.save()
    return Response({'status': 'success', 'code': 'GAME_ADDED', 'data': {
        'game': serialize_game(game, playing=0, waiting=0),
    }, 'message': ''}, status=status.HTTP_201_CREATED)


def _apply(game, data, *, creating=False):
    """Copy the editable fields that were sent onto `game`. A refusal or None."""
    if 'starts_at' in data:
        game.starts_at = inputs.read_datetime(data, 'starts_at')
    if 'place' in data:
        game.place = inputs.read_text(data, 'place', max_length=120)
    if 'places' in data:
        game.places = inputs.read_int(data, 'places', minimum=1, maximum=MAX_PLACES)
    if 'prize' in data:
        game.prize = inputs.read_text(data, 'prize', max_length=160)
    if 'status' in data:
        # `proposed` is set by the vote door alone: a game put there by hand
        # would leave the public list with no vote to bring it back.
        game.status = inputs.read_choice(
            data, 'status', [c for c, _ in EventGame.STATUS_CHOICES if c != EventGame.PROPOSED],
            default=game.status)
    if 'position' in data:
        game.position = inputs.read_int(data, 'position', minimum=0, maximum=MAX_GAMES * 10,
                                        default=game.position)
    if not game.catalogue_key:
        if 'custom_name' in data and not creating:
            name = inputs.read_text(data, 'custom_name', max_length=80)
            if not name:
                return _error('Your game needs a name.', 'GAME_NAME_REQUIRED')
            game.custom_name = name
        if 'custom_steps' in data:
            game.custom_steps = inputs.read_text(data, 'custom_steps', max_length=2000)
        if 'custom_needs' in data:
            game.custom_needs = inputs.read_text(data, 'custom_needs', max_length=160)
        if 'custom_players' in data:
            game.custom_players = inputs.read_text(data, 'custom_players', max_length=40)
        if 'custom_length' in data:
            game.custom_length = inputs.read_text(data, 'custom_length', max_length=40)
    return None


def _organiser_game(request, event_id, game_id):
    """(event, game, viewer, refusal) for a door only the organiser may use."""
    event = _event(event_id)
    if event is None:
        return None, None, None, _error('Event not found.', 'NOT_FOUND',
                                        status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    if viewer is None:
        return event, None, None, _error(
            'Authorization header with a Bearer token is required.',
            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    # Permission before the lookup, so a stranger learns nothing about which
    # game numbers exist on somebody else's event (R88).
    if not _may_manage(viewer, event):
        return event, None, viewer, _error('Only the event organizer can change a game.',
                                           'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    game = EventGame.objects.filter(event=event, pk=game_id).first()
    if game is None:
        return event, None, viewer, _error('Game not found.', 'NOT_FOUND',
                                           status.HTTP_404_NOT_FOUND)
    return event, game, viewer, None


@api_view(['PATCH', 'DELETE'])
def game_detail(request, event_id, game_id):
    """Change when, where, places, prize or status; or remove a game nobody joined."""
    event, game, viewer, refused = _organiser_game(request, event_id, game_id)
    if refused is not None:
        return refused

    if request.method == 'DELETE':
        # Removing it would take people's places with it. Taking it off keeps
        # them on record, and is what the organiser almost always means.
        if EventGameEntry.objects.filter(game=game).exists():
            return _error('People have signed up for this. Take it off instead.',
                          'GAME_HAS_ENTRIES', status.HTTP_409_CONFLICT)
        game.delete()
        return Response({'status': 'success', 'code': 'GAME_REMOVED', 'data': {},
                         'message': ''})

    before = game.places
    refused = _apply(game, request.data)
    if refused is not None:
        return refused
    game.save()
    if 'places' in request.data and (game.places is None
                                     or (before is not None and game.places > before)):
        game_entries.places_changed(game)
    playing, waiting = game_entries.counts(game)
    return Response({'status': 'success', 'code': 'GAME_SAVED', 'data': {
        'game': serialize_game(game, playing=playing, waiting=waiting),
    }, 'message': ''})


@api_view(['GET'])
def game_entries_list(request, event_id, game_id):
    """Who wants to play, in order: playing, then waiting, then who left.

    `?download=csv` gives the same list as a file to print for whoever runs the
    game on the night. A file is an HttpResponse: DRF's Response would encode it
    as a JSON string.
    """
    event, game, viewer, refused = _organiser_game(request, event_id, game_id)
    if refused is not None:
        return refused

    order = {EventGameEntry.IN: 0, EventGameEntry.WAITING: 1, EventGameEntry.LEFT: 2}
    rows = sorted(EventGameEntry.objects.filter(game=game)
                  .select_related('ticket', 'ticket__tier', 'ticket__user'),
                  key=lambda e: (order.get(e.state, 3), e.joined_at, e.id))
    people = [{
        'name': _holder(e.ticket),
        'username': e.ticket.user.username if e.ticket.user_id else None,
        'guest': not e.ticket.user_id,
        'tier': e.ticket.tier.name if e.ticket.tier_id else '',
        'code': e.ticket.code,
        'checked_in': e.ticket.status == 'checked_in',
        'state': e.state,
        'joined_at': e.joined_at,
        'left_at': e.left_at,
    } for e in rows]

    if str(request.GET.get('download') or '').lower() == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="game-%s-players.csv"' % game.id
        writer = csv.writer(response)
        writer.writerow(['name', 'username', 'ticket', 'tier', 'state', 'joined_at',
                         'checked_in'])
        for p in people:
            writer.writerow([p['name'], p['username'] or '', p['code'], p['tier'], p['state'],
                             timezone.localtime(p['joined_at']).isoformat(),
                             'yes' if p['checked_in'] else 'no'])
        return response

    playing, waiting = game_entries.counts(game)
    return Response({'status': 'success', 'data': {
        'game': serialize_game(game, playing=playing, waiting=waiting),
        'people': people,
    }, 'message': ''})


def _player_ticket(request, event):
    """The ticket this request plays with: a typed code, or the reader's own.

    A signed-in reader holding several tickets names which one with
    `ticket_code`; with no code, their first.
    """
    code = request.data.get('ticket_code')
    if code:
        return _ticket_by_code(event, code)
    tickets = _live_tickets(event, _viewer(request))
    return tickets[0] if tickets else None


def _player_door(request, event_id, game_id):
    event = _event(event_id)
    if event is None:
        return None, None, None, _error('Event not found.', 'NOT_FOUND',
                                        status.HTTP_404_NOT_FOUND)
    game = (EventGame.objects.filter(event=event, pk=game_id)
            .exclude(status=EventGame.OFF).first())
    if game is None:
        return event, None, None, _error('Game not found.', 'NOT_FOUND',
                                         status.HTTP_404_NOT_FOUND)
    ticket = _player_ticket(request, event)
    if ticket is None:
        # Said the same way for a wrong code and for no ticket at all, so the
        # door cannot be used to find out which codes exist.
        return event, game, None, _error(
            'Only people holding a ticket for this event can play.',
            'GAME_TICKET_REQUIRED', status.HTTP_403_FORBIDDEN)
    return event, game, ticket, None


def _answer(event, game, ticket, code):
    playing, waiting = game_entries.counts(game)
    entry = EventGameEntry.objects.filter(game=game, ticket=ticket).first()
    mine = {}
    if entry is not None and entry.state != EventGameEntry.LEFT:
        mine[ticket.code] = {'state': entry.state,
                             'place_in_line': game_entries.position_on_list(entry)}
    return Response({'status': 'success', 'code': code, 'data': {
        'game': serialize_game(game, playing=playing, waiting=waiting, mine=mine),
        'ticket': _ticket_row(ticket),
    }, 'message': ''})


@api_view(['POST'])
@limited('event_game_play', 30)
def join_game(request, event_id, game_id):
    """I want to play. In if there is a place, on the waiting list if not."""
    event, game, ticket, refused = _player_door(request, event_id, game_id)
    if refused is not None:
        return refused
    if not event.is_active:
        return _error('This event was cancelled.', 'EVENT_CANCELLED',
                      status.HTTP_409_CONFLICT)
    if game.status != EventGame.OPEN:
        return _error('Sign ups for this game are closed.', 'GAME_NOT_OPEN',
                      status.HTTP_409_CONFLICT)
    entry = game_entries.join(game, ticket)
    return _answer(event, game, ticket,
                   'GAME_JOINED' if entry.state == EventGameEntry.IN else 'GAME_WAITING')


@api_view(['POST'])
@limited('event_game_play', 30)
def leave_game(request, event_id, game_id):
    """Leave a game, or its waiting list. A place freed goes to the next in line."""
    event, game, ticket, refused = _player_door(request, event_id, game_id)
    if refused is not None:
        return refused
    game_entries.leave(game, ticket)
    return _answer(event, game, ticket, 'GAME_LEFT')


@api_view(['POST'])
@limited('event_game_play', 30)
def my_games(request, event_id):
    """The games list as a guest holding this ticket code sees it.

    A POST, so the code travels in the body and never in an address that a
    server log or a browser history keeps.
    """
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    ticket = _ticket_by_code(event, request.data.get('ticket_code'))
    if ticket is None:
        return _error('Only people holding a ticket for this event can play.',
                      'GAME_TICKET_REQUIRED', status.HTTP_403_FORBIDDEN)
    viewer = _viewer(request)
    return Response({'status': 'success', 'data': _games_payload(
        event, viewer=viewer, tickets=[ticket],
        is_organiser=_may_manage(viewer, event)), 'message': ''})


# ---------------------------------------------------------------- the vote (G2)
#
# CEO, 12 October 2026 (inbox 439): organisers "can even create a voting system
# for people to vote on" which games run. The vote is an ordinary EventPoll of
# the "pick several" kind whose options point at games, so it also sits under
# Polls with every other question, and one ticket is one vote exactly as there.
# A catalogue game put to the vote becomes a game in the `proposed` state: it
# is not on yet, and the public list shows it only inside the vote. Running the
# result opens the winners for sign ups and takes the rest of the proposed ones
# off.

MAX_VOTE_OPTIONS = 10


@api_view(['POST'])
def games_vote(request, event_id):
    """Open a vote over chosen games. One open games vote at a time."""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    if viewer is None:
        return _error('Authorization header with a Bearer token is required.',
                      'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(viewer, event):
        return _error('Only the event organizer can open a vote.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    current = games_vote_for(event)
    if current is not None and not current.closed():
        return _error('A vote over games is already open. Run it or close it first.',
                      'GAME_VOTE_OPEN', status.HTTP_409_CONFLICT)

    raw = request.data.get('games')
    if not isinstance(raw, list):
        return _error('Send the games as a list.', 'VALIDATION_ERROR')
    names = request.data.get('names') if isinstance(request.data.get('names'), dict) else {}
    question = inputs.read_text(request.data, 'question', max_length=200) or 'Which games should we run?'
    closes_at = inputs.read_datetime(request.data, 'closes_at')

    picked = []
    for item in raw:
        if isinstance(item, bool):
            return _error('That is not a game.', 'UNKNOWN_GAME')
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
            game = EventGame.objects.filter(event=event, pk=int(item)).exclude(
                status=EventGame.OFF).first()
            if game is None:
                return _error('That game is not on this event.', 'UNKNOWN_GAME')
            key = 'id:%d' % game.id
        else:
            meta = party_games.get(item)
            if meta is None:
                return _error('That game is not in the catalogue.', 'UNKNOWN_GAME')
            # Already on the event: the vote is over that game, not a second
            # copy of it (walk, 13 October: Jenga twice on one event).
            on_event = (EventGame.objects.filter(event=event, catalogue_key=meta.key)
                        .exclude(status=EventGame.OFF).first())
            if on_event is not None:
                game, key = on_event, 'id:%d' % on_event.id
            else:
                game, key = meta.key, 'key:%s' % meta.key
        if key not in [k for k, _ in picked]:
            picked.append((key, game))
    if len(picked) < 2:
        return _error('A vote needs at least two games to choose between.', 'GAME_VOTE_TOO_FEW')
    if len(picked) > MAX_VOTE_OPTIONS:
        return _error('Keep it to %d games.' % MAX_VOTE_OPTIONS, 'GAME_VOTE_TOO_MANY',
                      extra={'limit': MAX_VOTE_OPTIONS})
    pick = inputs.read_int(request.data, 'pick', minimum=1, maximum=len(picked), default=1)

    with transaction.atomic():
        poll = EventPoll.objects.create(
            event=event, question=question, kind=EventPoll.MULTIPLE,
            min_choices=0, max_choices=pick, closes_at=closes_at, created_by=viewer)
        position = EventGame.objects.filter(event=event).count()
        for n, (key, game) in enumerate(picked):
            if not isinstance(game, EventGame):
                game = EventGame.objects.create(
                    event=event, catalogue_key=game, status=EventGame.PROPOSED,
                    created_by=viewer, position=position)
                position += 1
            label = str(names.get(game.catalogue_key) or names.get(str(game.id))
                        or game.custom_name or game.catalogue_key.replace('-', ' ').title())[:140]
            EventPollOption.objects.create(poll=poll, text=label, position=n, game=game)

    return Response({'status': 'success', 'code': 'GAME_VOTE_OPENED', 'data': _games_payload(
        event, viewer=viewer, tickets=_live_tickets(event, viewer), is_organiser=True),
        'message': ''}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
def games_vote_run(request, event_id, poll_id):
    """Run the top N: winners open for sign ups, the other proposed games off.

    `top` 0 closes the vote and runs none of them, so closing a vote never
    leaves games stranded as proposed, on nobody's list.
    """
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _viewer(request)
    if viewer is None:
        return _error('Authorization header with a Bearer token is required.',
                      'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(viewer, event):
        return _error('Only the event organizer can run a vote.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    poll = (EventPoll.objects.filter(event=event, pk=poll_id, options__game__isnull=False)
            .distinct().first())
    if poll is None:
        return _error('Vote not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    options = list(poll.options.filter(game__isnull=False).select_related('game'))
    top = inputs.read_int(request.data, 'top', minimum=0, maximum=len(options),
                          default=min(poll.max_choices or 1, len(options)))
    ranked = sorted(options, key=lambda o: (-(o.votes.count() + o.choices.count()), o.position))
    winners = ranked[:top]
    with transaction.atomic():
        for option in ranked:
            game = option.game
            if option in winners:
                if game.status == EventGame.PROPOSED:
                    game.status = EventGame.OPEN
                    game.save(update_fields=['status', 'updated_at'])
            elif game.status == EventGame.PROPOSED:
                game.status = EventGame.OFF
                game.save(update_fields=['status', 'updated_at'])
        poll.is_open = False
        poll.save()

    return Response({'status': 'success', 'code': 'GAME_VOTE_RUN', 'data': {
        **_games_payload(event, viewer=viewer, tickets=_live_tickets(event, viewer),
                         is_organiser=True),
        'winners': [o.game_id for o in winners],
    }, 'message': ''})

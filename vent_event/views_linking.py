"""Tournament to event linking.

An event organizer attaches their own tournaments to an event. The link drives
three things:

1. the event page's Tournaments tab lists them,
2. the tournament page carries the event's name and banner back,
3. with `shared_ticketing` on, a valid ticket for the event pays the tournament
   entry fee, so registration skips the wallet debit and the PIN.

Ownership rules: only the event creator links or unlinks, they can only link a
tournament they created, and a tournament belongs to at most one event.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from vent_auth.models import Users
from .models import Event, EventTournamentLink, Ticket, TicketTier


def _event_by_ref(ref, **extra):
    """An event by slug or by id.

    The named address is what the slug rule requires, and the numeric one still
    has to resolve because links were shared before that rule existed.
    """
    from .models import Event

    ref = str(ref)
    if ref.isdigit():
        found = Event.objects.filter(event_id=int(ref), **extra).first()
        if found:
            return found
    return Event.objects.filter(slug=ref, **extra).first()



SESSION_TIMEOUT_MINUTES = 120

# A ticket that has been refunded or cancelled does not pay for anything.
TICKET_VALID_STATUSES = ('valid', 'checked_in')


def _error(message, code, http_status, extra=None):
    return Response({'status': 'error', 'data': extra or {}, 'message': message, 'code': code},
                    status=http_status)


def _ok(data, message, http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _authenticate(request):
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


def _viewer(request):
    """Optional auth: linked lists are public, but a signed-in viewer gets their
    own ticket coverage computed."""
    user, _ = _authenticate(request)
    return user


def has_event_ticket(user, event_id):
    if not user:
        return False
    return Ticket.objects.filter(
        event_id=event_id, user=user, status__in=TICKET_VALID_STATUSES,
    ).exists()


def link_for_tournament(tournament_id):
    return (EventTournamentLink.objects
            .select_related('event')
            .filter(tournament_id=tournament_id)
            .first())


def holds_entry_ticket(user, link):
    """Whether this person holds the ticket that admits them to the tournament.

    When the organiser named an `entry_tier`, only that tier admits: a general
    admission ticket does not get somebody into the competition if the rule says
    the competitor pass does.
    """
    if not user or not link:
        return False
    tickets = Ticket.objects.filter(
        event_id=link.event_id, user=user, status__in=TICKET_VALID_STATUSES)
    if link.entry_tier_id:
        tickets = tickets.filter(tier_id=link.entry_tier_id)
    return tickets.exists()


def entry_is_covered(user, tournament):
    """True when this viewer's event ticket pays this tournament's entry fee.

    `shared_ticketing` is the old flag and still means exactly this. The newer
    `entry_mode` says the same thing more precisely, and either being set is
    enough - an organiser who set the mode should not have to also remember a
    checkbox that predates it.
    """
    link = link_for_tournament(tournament.tournament_id)
    if not link:
        return False, link
    by_ticket = (link.shared_ticketing
                 or link.entry_mode == EventTournamentLink.ENTRY_TICKET)
    if not by_ticket:
        return False, link
    return holds_entry_ticket(user, link), link


def entry_requires_ticket(tournament):
    """(required, link). Whether a ticket is the ONLY way into this tournament.

    Different from `entry_is_covered`, which asks whether a ticket happens to
    pay a fee. This asks whether somebody without one may enter at all, which
    is the organiser saying "the door price is the entry" rather than "a ticket
    saves you the entry fee".
    """
    link = link_for_tournament(tournament.tournament_id)
    if not link:
        return False, None
    return link.entry_mode == EventTournamentLink.ENTRY_TICKET, link


def serialize_linked_tournament(request, link, viewer=None):
    from vent_tournament.views import serialize_tournament_card, _card_lookups

    t = link.tournament
    counts, prizes = _card_lookups([t])
    card = serialize_tournament_card(
        t, counts.get(t.tournament_id, 0), prizes.get(t.tournament_id, 0),
    )
    card['banner_image'] = card.get('banner')
    card['shared_ticketing'] = link.shared_ticketing
    card['linked_at'] = link.created_at
    card['entry_covered_by_ticket'] = bool(
        link.shared_ticketing and has_event_ticket(viewer, link.event_id)
    )

    # How entry works here, so the register screen can say it rather than
    # discovering it when the entrant is refused.
    card['entry_mode'] = link.entry_mode
    card['entry_tier'] = ({'id': link.entry_tier_id, 'name': link.entry_tier.name}
                          if link.entry_tier_id else None)
    card['reward_from_round'] = link.reward_from_round
    card['reward_tier'] = ({'id': link.reward_tier_id, 'name': link.reward_tier.name}
                           if link.reward_tier_id else None)
    return card


def event_brand(request, event):
    """The block the tournament page renders as "part of this event"."""
    from .serializers import absolute_media_url

    return {
        'id': event.event_id,
        'event_id': event.event_id,
        'name': event.name,
        'event_type': event.event_type,
        'category': event.category,
        'location': event.location,
        'start_date': event.start_date,
        'end_date': event.end_date,
        'banner': absolute_media_url(request, event.banner, event.banner_url),
        'logo': absolute_media_url(request, event.logo),
    }


def _organizer_gate(request, event_id):
    """Returns (event, user, error_response)."""
    user, err = _authenticate(request)
    if err:
        return None, None, err
    event = _event_by_ref(event_id)
    if event is None:
        return None, None, _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id:
        return None, None, _error('Only the event organizer can manage linked tournaments.',
                                  'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    return event, user, None


@api_view(['GET'])
def event_tournaments(request, event_id):
    """Public: every tournament running inside this event."""
    event = _event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    viewer = _viewer(request)
    links = (EventTournamentLink.objects
             .filter(event=event)
             .select_related('tournament', 'tournament__tournament_game'))
    tournaments = [serialize_linked_tournament(request, link, viewer) for link in links]
    return _ok({
        'tournaments': tournaments,
        'count': len(tournaments),
        'is_organizer': bool(viewer and viewer.user_id == event.creator_id),
    }, 'Linked tournaments fetched.')


@api_view(['GET'])
def linkable_tournaments(request, event_id):
    """Organizer only: their own tournaments that are free to attach.

    Excludes drafts (nothing to show attendees yet), anything already attached to
    an event, and anything that is finished or cancelled.
    """
    event, user, err = _organizer_gate(request, event_id)
    if err:
        return err

    from vent_tournament.models import Tournament
    from vent_tournament.views import serialize_tournament_card, _card_lookups

    taken = set(EventTournamentLink.objects.values_list('tournament_id', flat=True))
    candidates = (Tournament.objects
                  .filter(tournament_creator=user, is_draft=False)
                  .exclude(tournament_id__in=taken)
                  .exclude(status__in=['completed', 'cancelled'])
                  .select_related('tournament_game')
                  .order_by('-start_date_and_time'))

    candidates = list(candidates)
    counts, prizes = _card_lookups(candidates)
    items = []
    for t in candidates:
        card = serialize_tournament_card(
            t, counts.get(t.tournament_id, 0), prizes.get(t.tournament_id, 0),
        )
        card['banner_image'] = card.get('banner')
        items.append(card)

    return _ok({'tournaments': items, 'count': len(items)}, 'Linkable tournaments fetched.')


@api_view(['POST'])
def link_tournament(request, event_id):
    """Organizer only: attach one of their tournaments to this event."""
    event, user, err = _organizer_gate(request, event_id)
    if err:
        return err

    from vent_tournament.models import Tournament

    tournament_id = request.data.get('tournament_id')
    if not tournament_id:
        return _error('tournament_id is required.', 'VALIDATION_FAILED', status.HTTP_400_BAD_REQUEST)

    tournament = Tournament.objects.filter(tournament_id=tournament_id).first()
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if tournament.tournament_creator_id != user.user_id:
        return _error('You can only link a tournament you created.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if tournament.is_draft:
        return _error('Publish the tournament before linking it to an event.',
                      'STATE_CONFLICT', status.HTTP_409_CONFLICT)

    shared = bool(request.data.get('shared_ticketing', False))

    existing = link_for_tournament(tournament.tournament_id)
    if existing:
        if existing.event_id != event.event_id:
            return _error(f'This tournament is already part of "{existing.event.name}".',
                          'ALREADY_LINKED', status.HTTP_409_CONFLICT)
        # Same event: treat the call as an update of the ticketing flag.
        if existing.shared_ticketing != shared:
            existing.shared_ticketing = shared
            existing.save(update_fields=['shared_ticketing'])
        return _ok({'tournament': serialize_linked_tournament(request, existing, user)},
                   'Link updated.')

    link = EventTournamentLink.objects.create(
        event=event, tournament=tournament, shared_ticketing=shared, linked_by=user,
    )
    return _ok({'tournament': serialize_linked_tournament(request, link, user)},
               f'{tournament.tournament_title} is now part of {event.name}.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def unlink_tournament(request, event_id):
    """Organizer only: detach a tournament from this event."""
    event, user, err = _organizer_gate(request, event_id)
    if err:
        return err

    tournament_id = request.data.get('tournament_id')
    if not tournament_id:
        return _error('tournament_id is required.', 'VALIDATION_FAILED', status.HTTP_400_BAD_REQUEST)

    link = EventTournamentLink.objects.filter(event=event, tournament_id=tournament_id).first()
    if link is None:
        return _error('That tournament is not linked to this event.',
                      'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    link.delete()
    return _ok({'tournament_id': int(tournament_id)}, 'Tournament unlinked.')


@api_view(['POST'])
def set_shared_ticketing(request, event_id, tournament_id):
    """Organizer only: turn shared ticketing on or off for one linked tournament."""
    event, user, err = _organizer_gate(request, event_id)
    if err:
        return err

    link = EventTournamentLink.objects.filter(event=event, tournament_id=tournament_id).first()
    if link is None:
        return _error('That tournament is not linked to this event.',
                      'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    raw = request.data.get('shared_ticketing')
    if raw is None:
        return _error('shared_ticketing is required.', 'VALIDATION_FAILED',
                      status.HTTP_400_BAD_REQUEST)
    shared = raw if isinstance(raw, bool) else str(raw).lower() in ('1', 'true', 'yes')

    link.shared_ticketing = shared

    fields = ['shared_ticketing']

    # How somebody gets into the tournament, and what reaching a round is worth.
    # Sent alongside the flag rather than through a second endpoint, because
    # they are one decision the organiser makes in one sitting.
    if 'entry_mode' in request.data:
        mode = str(request.data.get('entry_mode') or '').strip()
        valid = {c[0] for c in EventTournamentLink.ENTRY_CHOICES}
        if mode not in valid:
            return _error('Entry is one of: %s.' % ', '.join(sorted(valid)),
                          'VALIDATION_FAILED', status.HTTP_400_BAD_REQUEST)
        link.entry_mode = mode
        fields.append('entry_mode')

    def _tier_or_error(key):
        """A tier on THIS event, or None to clear it."""
        raw = request.data.get(key)
        if raw in (None, '', 0, '0'):
            return None, None
        tier = TicketTier.objects.filter(event=event, pk=raw).first()
        if tier is None:
            return None, _error('That ticket type is not on this event.',
                                'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        return tier, None

    if 'entry_tier' in request.data:
        tier, err2 = _tier_or_error('entry_tier')
        if err2:
            return err2
        link.entry_tier = tier
        fields.append('entry_tier')

    if 'reward_from_round' in request.data:
        raw = request.data.get('reward_from_round')
        if raw in (None, '', 0, '0'):
            link.reward_from_round = None
        else:
            try:
                link.reward_from_round = max(1, int(raw))
            except (TypeError, ValueError):
                return _error('The round has to be a number.', 'INVALID_NUMBER',
                              status.HTTP_400_BAD_REQUEST)
        fields.append('reward_from_round')

    if 'reward_tier' in request.data:
        tier, err2 = _tier_or_error('reward_tier')
        if err2:
            return err2
        link.reward_tier = tier
        fields.append('reward_tier')

    # A reward with no ticket behind it promises something that cannot be
    # handed over, so the pair is refused rather than half-stored.
    if link.reward_from_round and link.reward_tier_id is None:
        return _error('Say which ticket reaching that round earns.',
                      'VALIDATION_FAILED', status.HTTP_400_BAD_REQUEST)

    link.save(update_fields=fields)
    return _ok({'tournament': serialize_linked_tournament(request, link, user)},
               'Shared ticketing on.' if shared else 'Shared ticketing off.')

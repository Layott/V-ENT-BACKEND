"""What somebody has actually taken part in.

Found by `scripts/check-api-paths.mjs` on 7 September 2026: the two history
panels on a profile - Tournaments and Events - have been fetching
`/auth/user-activity/tournaments/` and `/auth/user-activity/events/` since they
were written, and **neither route existed**. Both answered 404, both had a
`catch` that set a "Failed to load" message, and both tabs have therefore been
empty for every account on the platform since the day they shipped.

That is the whole class the checker exists for: a wrong or missing fetch path
compiles, lints, renders, and fails silently inside a catch. Nothing about the
component looks wrong.

The panels were written first, so the SHAPE is decided by what they already
read rather than by what would be tidiest:

    tournaments   `data` is a bare list; each row `tournament_title` or `name`
    events        the same

Matching what they read is deliberate. Changing the payload to something
neater would mean changing two screens that are correct, to fix a fault that
is entirely on this side.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users

PAGE_SIZE = 100


def _error(message, code, http_status):
    return Response({'status': 'error', 'data': [], 'message': message,
                     'code': code}, status=http_status)


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    return Users.objects.filter(
        login_session_token=header.split(' ', 1)[1].strip()).first()


def _abs(request, filefield):
    if not filefield:
        return None
    try:
        return request.build_absolute_uri(filefield.url)
    except ValueError:
        return None


@api_view(['GET'])
def user_tournaments(request):
    """GET /auth/user-activity/tournaments/ - every tournament this person entered.

    Both the ones they entered alone and the ones they entered with a team,
    because from the person's side those are the same thing: they played in it.
    Splitting them would make a profile show half somebody's history with no
    sign the other half exists.
    """
    user = _viewer(request)
    if user is None:
        return _error('Sign in to see this.', 'AUTHENTICATION_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)

    from django.db.models import Q

    from vent_auth.models import TeamMembers
    from vent_tournament.models import TournamentRegistration

    my_team_ids = list(TeamMembers.objects.filter(user=user)
                       .values_list('team_id', flat=True))

    rows = (TournamentRegistration.objects
            .filter(Q(user=user) | Q(team_id__in=my_team_ids))
            .select_related('tournament', 'tournament__tournament_game', 'team')
            .order_by('-registered_at')[:PAGE_SIZE])

    out = []
    for reg in rows:
        t = reg.tournament
        if t is None:
            continue
        out.append({
            'id': t.tournament_id,
            'slug': t.slug,
            # Both names, because the panel reads `tournament_title` and falls
            # back to `name`, and a card elsewhere may read the other.
            'tournament_title': t.tournament_title,
            'name': t.tournament_title,
            'game': t.tournament_game.game_title if t.tournament_game_id else None,
            'logo': _abs(request, t.tournament_logo),
            'banner': _abs(request, t.tournament_banner),
            'start_date_and_time': t.start_date_and_time,
            'status': reg.status,
            # Which way they entered. A profile that says "you played" without
            # saying who with is missing the part people care about.
            'team': reg.team.team_name if reg.team_id else None,
            'team_slug': reg.team.slug if reg.team_id else None,
            'entered_as': 'team' if reg.team_id else 'individual',
            'registered_at': reg.registered_at,
        })
    return Response({'status': 'success', 'data': out,
                     'message': 'Tournaments you entered.'},
                    status=status.HTTP_200_OK)


@api_view(['GET'])
def user_events(request):
    """GET /auth/user-activity/events/ - every event this person has a ticket for.

    From tickets rather than from any "attending" flag: a ticket is the thing
    that actually happened, and an event somebody bought a ticket for and did
    not attend still belongs in their history.

    One row per EVENT, not per ticket. Somebody who bought four tickets to one
    convention went to one convention, and a history listing it four times
    reads as a bug.
    """
    user = _viewer(request)
    if user is None:
        return _error('Sign in to see this.', 'AUTHENTICATION_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)

    from vent_event.models import Ticket

    tickets = (Ticket.objects
               .filter(user=user)
               .exclude(status__in=('refunded', 'cancelled'))
               .select_related('event', 'event__game', 'tier')
               .order_by('-purchased_at')[:PAGE_SIZE * 4])

    seen = {}
    for ticket in tickets:
        event = ticket.event
        if event is None or event.event_id in seen:
            # The first ticket for an event is the one kept, and the list is
            # newest first, so that is the most recent purchase.
            if event is not None and event.event_id in seen:
                seen[event.event_id]['ticket_count'] += 1
            continue
        seen[event.event_id] = {
            'id': event.event_id,
            'slug': event.slug,
            'name': event.name,
            'tournament_title': event.name,     # the panel is shared
            'game': event.game.game_title if event.game_id else None,
            'logo': _abs(request, event.logo),
            'banner': _abs(request, event.banner),
            'location': event.location or None,
            'start_date': event.start_date,
            'event_date': event.event_date,
            'status': ticket.status,
            'checked_in': ticket.status == 'checked_in',
            'ticket_count': 1,
            'tier': ticket.tier.name if ticket.tier_id else None,
            'purchased_at': ticket.purchased_at,
        }

    return Response({'status': 'success', 'data': list(seen.values())[:PAGE_SIZE],
                     'message': 'Events you have tickets for.'},
                    status=status.HTTP_200_OK)

"""POST /tournament/<ref>/duplicate/ - copy your own tournament into a draft (inbox 446)."""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import lookup
from .duplicate import duplicate
from vent_auth.actors import actor_from_request as _actor_from_request


@api_view(['POST'])
def duplicate_tournament(request, tournament_id):
    """Copy a tournament its owner set up into a new draft they own.

    Only the owner: "duplicate their own tournaments". An admin who wants a copy
    of somebody else's has the console; a copy made by a stranger would put
    another organiser's work under their name.
    """
    user, auth_error = _actor_from_request(request)
    if auth_error is not None:
        return auth_error
    tournament = lookup.find(tournament_id)
    if tournament is None or getattr(tournament, 'deleted_at', None):
        return Response({'status': 'error', 'code': 'NOT_FOUND',
                         'message': 'Tournament not found', 'data': {}},
                        status=status.HTTP_404_NOT_FOUND)
    if tournament.tournament_creator_id != user.user_id:
        return Response({'status': 'error', 'code': 'ONLY_TOURNAMENT_ORGANIZER_CAN',
                         'message': 'Only the organiser can duplicate this tournament.',
                         'data': {}}, status=status.HTTP_403_FORBIDDEN)
    copy = duplicate(tournament, user)
    return Response({'status': 'success', 'code': 'DUPLICATED',
                     'message': 'Copied into a new draft.',
                     'data': {'slug': copy.slug, 'id': copy.tournament_id,
                              'title': copy.tournament_title}},
                    status=status.HTTP_201_CREATED)

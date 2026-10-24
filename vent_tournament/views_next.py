"""The two doors onto `next_actions` (P4a, design section 12).

    GET /tournament/<ref>/next-actions/   what this viewer can do next. Public.
    GET /tournament/<ref>/actions-list/   what the people running it need to
                                          settle. Whoever runs any part of it.

The first answers 200 to everybody, because a stranger is told they can
register and a signed-out visitor is told they need an account for it: that is
content, and the gate is on the action. The second is somebody else's work, so
it refuses anonymous with 401 and a viewer who runs nothing here with 403
NOT_YOURS, and then shows each helper only the kinds their permissions reach.

Both settle what time has already settled before they answer, the way the
bracket door does: a no-show whose wait ran out, a report whose confirmation
window ended. Without that the opponent is asked to confirm a score that is
already final, and the organiser is shown a match that needs nobody.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from vent_auth.actors import actor_from_request

from . import next_actions, stage_engine
from .access import permissions_of
from .views_stages import _err, _find, _ok, _viewer


def _settle_what_time_settled(tournament):
    stage_engine.sweep_no_shows(tournament)
    stage_engine.sweep_auto_confirms(tournament)


@api_view(['GET'])
@permission_classes([AllowAny])
def next_actions_for_viewer(request, tournament_id):
    """GET /tournament/<ref>/next-actions/ - what the viewer can do next."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    if tournament.is_draft:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    _settle_what_time_settled(tournament)
    return _ok({'actions': next_actions.for_viewer(_viewer(request), tournament)})


@api_view(['GET'])
def tournament_actions_list(request, tournament_id):
    """GET /tournament/<ref>/actions-list/ - what needs the organiser."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = actor_from_request(request)
    if err:
        return err
    if not permissions_of(user, tournament):
        return _err('This is not your tournament to run.', 'NOT_YOURS', status.HTTP_403_FORBIDDEN)
    _settle_what_time_settled(tournament)
    return _ok({'items': next_actions.actions_list(tournament, user)})

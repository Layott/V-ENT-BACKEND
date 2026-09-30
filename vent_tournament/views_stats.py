"""The Stats tab: leaders, every entrant's record, and head to head.

GET /tournament/<id or slug>/stats/
GET /tournament/<id or slug>/stats/head-to-head/?a=<registration id>&b=<registration id>

Public, like the tournament page they belong to: a signed-out reader gets them,
a draft answers 404 to anybody but its organiser and an admin, and a renamed
tournament answers `moved`, exactly as `view_tournament` does. Everything is
derived on read by `stats.py`; nothing here writes.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import stats
from .models import Tournament
from vent_auth import inputs


def _ok(data, message='OK'):
    return Response({'status': 'success', 'code': 'OK', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http_status):
    return Response({'status': 'error', 'code': code, 'data': None, 'message': message},
                    status=http_status)


def _readable(request, tournament_id):
    """(tournament, None) when this reader may see it, else (None, response)."""
    from vent_auth.slugs import resolve_or_redirect
    from .views import _is_creator, _may_override, _actor_from_request

    tournament, moved_to = resolve_or_redirect(
        tournament_id, entity_type='tournament', id_field='tournament_id', model=Tournament)
    if moved_to:
        return None, Response({
            'status': 'moved', 'code': 'SLUG_CHANGED',
            'message': 'This tournament has been renamed.',
            'data': {'slug': moved_to, 'url': f'/tournaments/{moved_to}'},
        }, status=status.HTTP_200_OK)
    if tournament is None:
        return None, _err('No such tournament.', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if tournament.is_draft and not (
            _is_creator(request, tournament)
            or _may_override(_actor_from_request(request)[0])):
        return None, _err('No such tournament.', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return tournament, None


@api_view(['GET'])
def tournament_stats(request, tournament_id):
    tournament, refusal = _readable(request, tournament_id)
    if refusal:
        return refusal
    return _ok(stats.compute(tournament), 'Tournament stats')


@api_view(['GET'])
def tournament_head_to_head(request, tournament_id):
    tournament, refusal = _readable(request, tournament_id)
    if refusal:
        return refusal
    try:
        first = inputs.read_int(request.query_params, 'a', required=True)
        second = inputs.read_int(request.query_params, 'b', required=True)
    except (TypeError, ValueError):
        return _err('Pick two entrants to compare.', 'H2H_PICK_TWO', status.HTTP_400_BAD_REQUEST)
    if first == second:
        return _err('Pick two different entrants.', 'H2H_SAME_ENTRANT', status.HTTP_400_BAD_REQUEST)
    known = set(tournament.registrations.filter(id__in=(first, second)).values_list('id', flat=True))
    if known != {first, second}:
        return _err('Both entrants must be in this tournament.', 'H2H_NOT_ENTRANT',
                    status.HTTP_400_BAD_REQUEST)
    return _ok(stats.head_to_head(tournament, first, second), 'Head to head')

"""Who may enter results on a tournament, and what the viewer may do.

CEO, 3 September 2026: "only those given the access to, should be able to"
input results. The organiser names scorekeepers by username here; every
result-recording view asks `access.may_record_results`; every screen asks
`/access/` and renders what it is told.

`/access/` answers 200 to everybody with every flag false for a stranger, the
same deliberate shape as `capabilities` on organisations: one code path in the
interface for organisers, scorekeepers and strangers alike, and no control
rendered live to somebody the API would refuse.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import Users

from .access import access_payload, may_manage
from .models import TournamentStaff
from .production_access import find_owner, viewer as _viewer


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _person(row):
    user = row.user
    from vent_auth.views_community import _person as shared
    try:
        described = shared(None, user)
    except Exception:
        described = {'id': user.user_id, 'user_id': user.user_id,
                     'username': user.username, 'full_name': user.full_name}
    described['role'] = row.role
    described['added_at'] = row.created_at.isoformat()
    return described


@api_view(['GET'])
@permission_classes([AllowAny])
def access(request, tournament_id):
    """GET /tournament/<ref>/access/ - what this viewer may do here."""
    tournament = find_owner('tournament', tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok(access_payload(_viewer(request), tournament), 'Access')


@api_view(['GET', 'POST'])
def staff(request, tournament_id):
    """GET the scorekeepers. POST `{"username": ...}` to add one.

    Organiser only, both ways. A scorekeeper may not add another: the person
    who decides who keeps score is the person running the tournament.
    """
    tournament = find_owner('tournament', tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not may_manage(user, tournament):
        return _err('Only the organiser can decide who enters results.',
                    'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        rows = TournamentStaff.objects.filter(tournament=tournament).select_related('user')
        return _ok({'staff': [_person(r) for r in rows]}, 'Scorekeepers')

    username = str(request.data.get('username') or '').strip().lstrip('@')
    if not username:
        return _err('Say who, by username.', 'USERNAME_REQUIRED', field='username')
    person = Users.objects.filter(username__iexact=username).first()
    if person is None:
        return _err('There is no account with that username.', 'USER_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND, field='username')
    if person.user_id == tournament.tournament_creator_id:
        return _err('That is the organiser; they can already enter results.',
                    'ALREADY_ORGANISER', field='username')
    row, made = TournamentStaff.objects.get_or_create(
        tournament=tournament, user=person,
        defaults={'role': 'scorekeeper', 'added_by': user})
    rows = TournamentStaff.objects.filter(tournament=tournament).select_related('user')
    return _ok({'staff': [_person(r) for r in rows], 'added': made},
               'Added.' if made else 'Already a scorekeeper.')


@api_view(['DELETE'])
def staff_remove(request, tournament_id, user_id):
    """DELETE /tournament/<ref>/staff/<user_id>/ - revoke at once."""
    tournament = find_owner('tournament', tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not may_manage(user, tournament):
        return _err('Only the organiser can decide who enters results.',
                    'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    TournamentStaff.objects.filter(tournament=tournament, user_id=user_id).delete()
    rows = TournamentStaff.objects.filter(tournament=tournament).select_related('user')
    return _ok({'staff': [_person(r) for r in rows]}, 'Removed.')

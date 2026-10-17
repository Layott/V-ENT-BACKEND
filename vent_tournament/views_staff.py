"""Who helps run a tournament, with what, and what the viewer may do.

CEO, 3 September 2026: "only those given the access to, should be able to"
input results. The organiser names helpers by username or email here, each with
the parts of the tournament they run (`access.PERMISSIONS`, P7: structure,
place, report, standings, caster); every door asks `access.may`; every screen
asks `/access/` and renders what it is told.

`/access/` answers 200 to everybody with every flag false for a stranger, the
same deliberate shape as `capabilities` on organisations: one code path in the
interface for organisers, scorekeepers and strangers alike, and no control
rendered live to somebody the API would refuse.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth import inputs
from vent_auth.models import Users

from .access import PermissionsError, access_payload, clean_permissions, may_manage
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


def _asked_permissions(request):
    """(permissions, error). Absent means results only, which is what every
    screen before P7 meant by naming somebody."""
    if 'permissions' not in request.data:
        return ['report'], None
    try:
        return clean_permissions(request.data.get('permissions')), None
    except PermissionsError as exc:
        message = ('Choose at least one part of the tournament for them to run.'
                   if exc.code == 'PERMISSIONS_REQUIRED'
                   else 'That is not one of the parts of a tournament a helper can run.')
        return None, _err(message, exc.code, field='permissions')


def _staff_rows(tournament):
    rows = TournamentStaff.objects.filter(tournament=tournament).select_related('user')
    return [_person(r) for r in rows]


def _person(row):
    user = row.user
    from vent_auth.views_community import _person as shared
    try:
        described = shared(None, user)
    except Exception:
        # Same KEYS as the builder, so a screen reading this fallback is not
        # handed a person with no face and no founder mark. A partial copy of
        # a shape is a copy that has already drifted.
        described = {
            'id': user.user_id, 'user_id': user.user_id,
            'username': user.username, 'full_name': user.full_name,
            'avatar': None,
            'founder_badge': bool(getattr(user, 'is_founder', False)
                                  and user.show_founder_badge),
        }
    described['permissions'] = list(row.permissions or [])
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
    """GET the helpers. POST `{"username": ..., "permissions": [...]}` to add one.

    Organiser only, both ways. A helper may not add another, whatever they
    hold: the person who decides who runs what is the person running the
    tournament.
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

    from vent_auth.models import RoleInvite
    if request.method == 'GET':
        return _ok({'staff': _staff_rows(tournament),
                    'invites': [_invite_row(i) for i in RoleInvite.objects.filter(
                        tournament=tournament, status=RoleInvite.STATUS_PENDING)
                        .order_by('-created_at')]}, 'Scorekeepers')

    # A username, or an email address that may belong to nobody yet
    # (inbox 455): then the role waits for them behind an emailed link.
    from vent_auth.invites import invitee_for, offer_role
    raw = (inputs.read_text(request.data, 'username', max_length=254)
           or inputs.read_text(request.data, 'email', max_length=254))
    if not raw:
        return _err('Say who, by username or email.', 'USERNAME_REQUIRED', field='username')
    permissions, refused = _asked_permissions(request)
    if refused is not None:
        return refused
    person, email, problem = invitee_for(raw)
    if problem:
        return _err('There is no account with that username.', 'USER_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND, field='username')
    if person is None:
        invite = offer_role(RoleInvite.KIND_TOURNAMENT, tournament, email, 'staff', user,
                            scopes=permissions)
        return _ok({'invited': email, 'awaiting_signup': True, 'invite': _invite_row(invite)},
                   'Invitation sent to %s.' % email)
    if person.user_id == tournament.tournament_creator_id:
        return _err('That is the organiser; they can already enter results.',
                    'ALREADY_ORGANISER', field='username')
    # Somebody already helping keeps what they hold: changing it is PATCH, so
    # a second press of Add never quietly takes a permission away.
    row, made = TournamentStaff.objects.get_or_create(
        tournament=tournament, user=person,
        defaults={'permissions': permissions, 'added_by': user})
    return _ok({'staff': _staff_rows(tournament), 'added': made},
               'Added.' if made else 'Already helping.')


def _invite_row(invite):
    return {'token': invite.token, 'email': invite.email, 'role': invite.role,
            'permissions': list(invite.scopes or []),
            'created_at': invite.created_at, 'claimed': invite.user_id is not None}


@api_view(['PATCH', 'DELETE'])
def staff_remove(request, tournament_id, user_id):
    """PATCH /tournament/<ref>/staff/<user_id>/ `{"permissions": [...]}` - what
    they hold now. DELETE - revoke everything at once."""
    tournament = find_owner('tournament', tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not may_manage(user, tournament):
        return _err('Only the organiser can decide who enters results.',
                    'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    if request.method == 'PATCH':
        row = TournamentStaff.objects.filter(tournament=tournament, user_id=user_id).first()
        if row is None:
            return _err('That person is not helping run this tournament.', 'STAFF_NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if 'permissions' not in request.data:
            return _err('Choose at least one part of the tournament for them to run.',
                        'PERMISSIONS_REQUIRED', field='permissions')
        permissions, refused = _asked_permissions(request)
        if refused is not None:
            return refused
        row.permissions = permissions
        row.save(update_fields=['permissions'])
        return _ok({'staff': _staff_rows(tournament)}, 'Saved.')
    TournamentStaff.objects.filter(tournament=tournament, user_id=user_id).delete()
    return _ok({'staff': _staff_rows(tournament)}, 'Removed.')

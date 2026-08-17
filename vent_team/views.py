import base64
import binascii
from datetime import timedelta

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.utils.text import slugify
from django.core.paginator import Paginator, EmptyPage
from django.core.files.base import ContentFile
from django.contrib.auth.hashers import make_password
from django.db import transaction

from .models import TeamInterests  # noqa: F401 (kept model; imported for parity)
from vent_auth.models import (
    Users, Games, Teams, TeamProfile, UserProfile,
    TeamMembers as AuthTeamMembers,
    TeamJoinRequest,
)
from .serializers import serialize_team_card, serialize_team_detail, absolute_media_url, _collect_members

SESSION_TIMEOUT_MINUTES = 120
PAGE_SIZE = 12

# Roles a member (non-owner) can hold. Owner is set only via create/transfer.
MEMBER_ROLES = {'captain', 'vice_captain', 'member', 'coach', 'manager', 'analyst'}


# -----------------------
# Helpers
# -----------------------

def _error(message, code, http_status):
    return Response(
        {'status': 'error', 'data': {}, 'message': message, 'code': code},
        status=http_status,
    )


def _authenticate(request):
    """Resolve a Bearer session token to a live user. Returns (user, error)."""
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    if not token:
        return None, _error('Bearer token is empty.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    try:
        user = Users.objects.get(login_session_token=token)
    except Users.DoesNotExist:
        return None, _error('Invalid session token.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


def _optional_user(request):
    """Resolve the caller if a valid Bearer token is present, else None (anonymous)."""
    user, error = _authenticate(request)
    return None if error else user


def _member_team_ids(user):
    """Team ids the user belongs to (unified membership table)."""
    return set(AuthTeamMembers.objects.filter(user=user).values_list('team_id', flat=True))


def _profile_map(teams):
    ids = [t.team_id for t in teams]
    return {p.team_id: p for p in TeamProfile.objects.filter(team_id__in=ids)}


def _recount_members(team):
    """Recompute number_of_members from the unified membership table + persist."""
    team.number_of_members = AuthTeamMembers.objects.filter(team=team).count()
    team.save(update_fields=['number_of_members'])


def _resolve_game(core_game):
    """Resolve a game by primary key OR case-insensitive title. None if unfound."""
    if core_game in (None, ''):
        return None
    game = None
    try:
        game = Games.objects.filter(pk=int(core_game)).first()
    except (ValueError, TypeError):
        game = None
    if not game:
        game = Games.objects.filter(game_title__iexact=str(core_game)).first()
    return game


def _decode_base64_image(data_url, name_slug):
    """Decode a base64 data-URL (or bare base64) into a ContentFile, else None."""
    if not data_url or not isinstance(data_url, str):
        return None
    b64 = data_url
    if data_url.startswith('data:'):
        try:
            _, b64 = data_url.split(',', 1)
        except ValueError:
            return None
    try:
        raw = base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    return ContentFile(raw, name=f"{name_slug or 'team'}.png")


def _paginate(request, queryset):
    paginator = Paginator(queryset, PAGE_SIZE)
    try:
        page_number = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page_number = 1
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)
    return page, paginator


# -----------------------
# READ: list teams (browse)
# -----------------------
@api_view(['GET'])
def team_list(request):
    """GET /team/get-all-teams/ (alias /team/list-teams/) - browse teams.

    Optional Bearer (needed only for tab=owned|joined). Filters: tab, game,
    region, open_to_join (yes|no), search. Paginated 12/page.
    """
    user = _optional_user(request)
    qs = Teams.objects.select_related('team_owner', 'game').all()

    tab = request.GET.get('tab')
    if tab in ('owned', 'joined', 'invited'):
        if not user:
            qs = qs.none()
        elif tab == 'owned':
            qs = qs.filter(team_owner=user)
        elif tab == 'joined':
            owned_ids = set(Teams.objects.filter(team_owner=user).values_list('team_id', flat=True))
            qs = qs.filter(team_id__in=(_member_team_ids(user) - owned_ids))
        elif tab == 'invited':
            qs = qs.none()  # team-invite model not built yet

    game = request.GET.get('game')
    if game:
        qs = qs.filter(game__game_title__iexact=game)

    region = request.GET.get('region')
    if region:
        qs = qs.filter(teamprofile__country__iexact=region)

    open_to_join = request.GET.get('open_to_join')
    if open_to_join in ('yes', 'no'):
        qs = qs.filter(allow_membership_requests=(open_to_join == 'yes'))

    search = request.GET.get('search') or request.GET.get('q')
    if search:
        qs = qs.filter(team_name__icontains=search)

    qs = qs.order_by('-creation_date', 'team_name')

    page, paginator = _paginate(request, qs)
    teams = list(page.object_list)
    profile_map = _profile_map(teams)

    return Response({
        'status': 'success',
        'data': {
            'teams': [serialize_team_card(request, t, profile_map) for t in teams],
            'page': page.number,
            'total_pages': paginator.num_pages,
            'total_count': paginator.count,
        },
        'message': 'Teams fetched successfully.',
    }, status=status.HTTP_200_OK)


# -----------------------
# READ: my teams (unblocks tournament registration picker)
# -----------------------
@api_view(['GET'])
def my_teams(request):
    """GET /team/my-teams/ - teams the caller owns OR belongs to. Bearer required."""
    user, error = _authenticate(request)
    if error:
        return error

    owned_ids = set(Teams.objects.filter(team_owner=user).values_list('team_id', flat=True))
    ids = owned_ids | _member_team_ids(user)

    teams = list(
        Teams.objects.select_related('team_owner', 'game')
        .filter(team_id__in=ids)
        .order_by('-creation_date', 'team_name')
    )
    profile_map = _profile_map(teams)

    return Response({
        'status': 'success',
        'data': {
            'teams': [serialize_team_card(request, t, profile_map) for t in teams],
            'total_count': len(teams),
        },
        'message': 'Your teams fetched successfully.',
    }, status=status.HTTP_200_OK)


# -----------------------
# READ: team detail
# -----------------------
@api_view(['GET'])
def view_team(request, team_id):
    """GET /team/view-team/<id>/ (alias /team/get-team-details/<id>/) - full detail."""
    team = Teams.objects.select_related('team_owner', 'game').filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    return Response({
        'status': 'success',
        'data': {'team': serialize_team_detail(request, team)},
        'message': 'Team fetched successfully.',
    }, status=status.HTTP_200_OK)


# =======================================================================
# WRITE endpoints
# =======================================================================

# -----------------------
# CREATE TEAM  (Part B - FE JSON contract)
# -----------------------
@api_view(['POST'])
def create_team(request):
    """POST /team/create-team/ - create a team from the FE JSON body."""
    user, error = _authenticate(request)
    if error:
        return error

    data = request.data
    name = (data.get('name') or '').strip()
    if not name:
        return _error('Team name is required.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)

    game = _resolve_game(data.get('core_game'))
    if not game:
        return _error('A valid core_game (id or title) is required.',
                      'GAME_NOT_FOUND', status.HTTP_400_BAD_REQUEST)

    if Teams.objects.filter(team_name=name).exists():
        return _error('A team with this name already exists.',
                      'TEAM_NAME_TAKEN', status.HTTP_400_BAD_REQUEST)

    bio = data.get('bio') or ''
    region = data.get('region') or ''
    open_to_join = data.get('open_to_join', True)
    social_links = data.get('social_links') or {}

    max_members = data.get('max_members')
    try:
        max_members_val = int(max_members) if max_members not in (None, '', 0) else None
    except (ValueError, TypeError):
        max_members_val = None

    with transaction.atomic():
        team = Teams.objects.create(
            team_name=name,
            game=game,
            description=bio or Teams._meta.get_field('description').default,
            allow_membership_requests=bool(open_to_join),
            max_members=max_members_val,
            password_protected=False,
            creation_date=timezone.now(),
            team_creator=user,
            team_owner=user,
            penalty_points=0,
            number_of_members=1,
        )

        logo_file = _decode_base64_image(data.get('logo_url'), f"{slugify(name)}-logo")
        if logo_file:
            team.team_logo = logo_file
        banner_file = _decode_base64_image(data.get('banner_url'), f"{slugify(name)}-banner")
        if banner_file:
            team.team_banner = banner_file
        if logo_file or banner_file:
            team.save()

        TeamProfile.objects.update_or_create(
            team=team,
            defaults={
                'country': region or None,
                'twitter_link': social_links.get('twitter') or None,
                'instagram_link': social_links.get('instagram') or None,
                'youtube_link': social_links.get('youtube') or None,
                'twitch_link': social_links.get('twitch') or None,
                'facebook_link': social_links.get('facebook') or None,
            },
        )

        AuthTeamMembers.objects.create(team=team, user=user, role='owner', is_captain=True)
        _recount_members(team)

    return Response({
        'status': 'success',
        'data': {'id': team.team_id, 'team': {'id': team.team_id}},
        'message': 'Team created successfully.',
    }, status=status.HTTP_201_CREATED)


# -----------------------
# TRANSFER OWNERSHIP  (repointed to unified membership table)
# -----------------------
@api_view(['POST'])
def transfer_ownership(request):
    """POST /team/transfer-ownership/ body {team_name, new_owner_username, prev_owner_new_role}."""
    user, error = _authenticate(request)
    if error:
        return error

    team_name = request.data.get('team_name')
    new_owner_username = request.data.get('new_owner_username')
    prev_owner_new_role = request.data.get('prev_owner_new_role')

    if not team_name or not new_owner_username or not prev_owner_new_role:
        return _error('team_name, new_owner_username, and prev_owner_new_role are required.',
                      'VALIDATION', status.HTTP_400_BAD_REQUEST)

    team = Teams.objects.filter(team_name=team_name).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('Only the team owner can transfer ownership.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)

    if prev_owner_new_role not in MEMBER_ROLES:
        return _error('Invalid role for the previous owner.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)

    new_owner = Users.objects.filter(username=new_owner_username).first()
    if not new_owner:
        return _error('New owner user not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not AuthTeamMembers.objects.filter(team=team, user=new_owner).exists():
        return _error('New owner must already be a member of the team.',
                      'NOT_MEMBER', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        team.team_owner = new_owner
        team.save(update_fields=['team_owner'])

        AuthTeamMembers.objects.update_or_create(
            team=team, user=new_owner,
            defaults={'role': 'owner', 'is_captain': True},
        )
        AuthTeamMembers.objects.update_or_create(
            team=team, user=user,
            defaults={'role': prev_owner_new_role,
                      'is_captain': prev_owner_new_role in ('owner', 'captain')},
        )

    return Response({
        'status': 'success',
        'data': {},
        'message': f'Team ownership transferred to {new_owner.username}; your new role is {prev_owner_new_role}.',
    }, status=status.HTTP_200_OK)


# -----------------------
# ASSIGN NEW ROLE  (back-compat alias of promote-member; owner-only)
# -----------------------
@api_view(['POST'])
def assign_new_role(request):
    """POST /team/assign-new-role/ body {team_name, member_username, new_role}."""
    user, error = _authenticate(request)
    if error:
        return error

    team_name = request.data.get('team_name')
    member_username = request.data.get('member_username')
    new_role = request.data.get('new_role')

    if not team_name or not member_username or not new_role:
        return _error('team_name, member_username, and new_role are required.',
                      'VALIDATION', status.HTTP_400_BAD_REQUEST)

    team = Teams.objects.filter(team_name=team_name).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('Only the team owner can assign roles.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)

    if new_role not in MEMBER_ROLES:
        return _error('Invalid role.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)

    member_entry = AuthTeamMembers.objects.filter(team=team, user__username=member_username).first()
    if not member_entry:
        return _error('Member not found in this team.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    member_entry.role = new_role
    member_entry.is_captain = new_role in ('owner', 'captain')
    member_entry.save(update_fields=['role', 'is_captain'])

    return Response({
        'status': 'success',
        'data': {},
        'message': f"{member_username}'s role updated to {new_role}.",
    }, status=status.HTTP_200_OK)


# -----------------------
# REMOVE MEMBER  (back-compat alias of kick-member)
# -----------------------
@api_view(['POST'])
def remove_member(request):
    """POST /team/remove-member/ body {team_name, member_username}."""
    user, error = _authenticate(request)
    if error:
        return error

    team_name = request.data.get('team_name')
    member_username = request.data.get('member_username')

    if not team_name or not member_username:
        return _error('team_name and member_username are required.',
                      'VALIDATION', status.HTTP_400_BAD_REQUEST)

    team = Teams.objects.filter(team_name=team_name).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    is_owner = team.team_owner_id == user.user_id
    requester_entry = AuthTeamMembers.objects.filter(team=team, user=user).first()
    requester_role = 'owner' if is_owner else (requester_entry.role if requester_entry else None)
    if requester_role is None:
        return _error('You are not a member of this team.', 'NOT_MEMBER', status.HTTP_403_FORBIDDEN)

    if member_username == user.username:
        return _error('You cannot remove yourself. Use the "leave team" action instead.',
                      'CANNOT_REMOVE_SELF', status.HTTP_400_BAD_REQUEST)

    if team.team_owner.username == member_username:
        return _error('The owner cannot be removed. Transfer ownership first.',
                      'CANNOT_REMOVE_OWNER', status.HTTP_400_BAD_REQUEST)

    target_entry = AuthTeamMembers.objects.filter(team=team, user__username=member_username).first()
    if not target_entry:
        return _error('Member not found in this team.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if requester_role in ('captain', 'vice_captain'):
        if target_entry.role in ('owner', 'captain', 'vice_captain'):
            return _error('Only the owner can remove captains or vice-captains.',
                          'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)
    elif requester_role != 'owner':
        return _error('You do not have permission to remove members.',
                      'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)

    with transaction.atomic():
        target_entry.delete()
        _recount_members(team)

    return Response({
        'status': 'success',
        'data': {'number_of_members': team.number_of_members},
        'message': f'Member {member_username} removed successfully.',
        'number_of_members': team.number_of_members,
    }, status=status.HTTP_200_OK)


# -----------------------
# REQUEST JOIN  (Part C)
# -----------------------
@api_view(['POST'])
def request_join(request, team_id):
    """POST /team/request-join/<team_id>/ body {} optional {message}."""
    user, error = _authenticate(request)
    if error:
        return error

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if team.team_owner_id == user.user_id:
        return _error('You already own this team.', 'ALREADY_MEMBER', status.HTTP_400_BAD_REQUEST)
    if AuthTeamMembers.objects.filter(team=team, user=user).exists():
        return _error('You are already a member of this team.', 'ALREADY_MEMBER', status.HTTP_400_BAD_REQUEST)
    if not team.allow_membership_requests:
        return _error('This team is not accepting join requests.', 'NOT_ACCEPTING', status.HTTP_400_BAD_REQUEST)
    if TeamJoinRequest.objects.filter(team=team, applicant=user, status='pending').exists():
        return _error('You already have a pending request for this team.',
                      'ALREADY_REQUESTED', status.HTTP_400_BAD_REQUEST)

    message = request.data.get('message') or ''
    req = TeamJoinRequest.objects.create(team=team, applicant=user, message=message)

    # Notify the team owner of the pending request - fire-and-forget.
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            team.team_owner_id, 'team',
            f'@{user.username} requested to join {team.team_name}',
            link=f'/teams/team-profile?id={team.team_id}',
            metadata={'team_id': team.team_id, 'request_id': req.id},
        )
    except Exception:
        pass

    return Response({
        'status': 'success',
        'data': {'request_id': req.id},
        'message': 'Join request sent.',
    }, status=status.HTTP_201_CREATED)


# -----------------------
# LEAVE TEAM  (Part C)
# -----------------------
@api_view(['POST'])
def leave_team(request, team_id):
    """POST /team/leave/<team_id>/ - caller leaves the team."""
    user, error = _authenticate(request)
    if error:
        return error

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if team.team_owner_id == user.user_id:
        return _error('The owner cannot leave. Transfer ownership first.',
                      'OWNER_CANNOT_LEAVE', status.HTTP_400_BAD_REQUEST)

    row = AuthTeamMembers.objects.filter(team=team, user=user).first()
    if not row:
        return _error('You are not a member of this team.', 'NOT_MEMBER', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        row.delete()
        _recount_members(team)

    return Response({
        'status': 'success',
        'data': {'number_of_members': team.number_of_members},
        'message': 'You have left the team.',
    }, status=status.HTTP_200_OK)


# -----------------------
# PROMOTE MEMBER  (Part C - owner-only; also the demote path)
# -----------------------
@api_view(['POST'])
def promote_member(request):
    """POST /team/promote-member/ body {team_id, user_id, role}."""
    user, error = _authenticate(request)
    if error:
        return error

    team_id = request.data.get('team_id')
    target_user_id = request.data.get('user_id')
    role = request.data.get('role')

    if not team_id or not target_user_id or not role:
        return _error('team_id, user_id, and role are required.',
                      'VALIDATION', status.HTTP_400_BAD_REQUEST)

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('Only the team owner can change roles.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)

    if role not in MEMBER_ROLES:
        return _error('Invalid role.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)

    row = AuthTeamMembers.objects.filter(team=team, user_id=target_user_id).first()
    if not row:
        return _error('Member not found in this team.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    row.role = role
    row.is_captain = role in ('owner', 'captain')
    row.save(update_fields=['role', 'is_captain'])

    return Response({
        'status': 'success',
        'data': {'members': _collect_members(request, team)},
        'message': 'Member role updated.',
    }, status=status.HTTP_200_OK)


# -----------------------
# KICK MEMBER  (Part C)
# -----------------------
@api_view(['POST'])
def kick_member(request):
    """POST /team/kick-member/ body {team_id, user_id}."""
    user, error = _authenticate(request)
    if error:
        return error

    team_id = request.data.get('team_id')
    target_user_id = request.data.get('user_id')

    if not team_id or not target_user_id:
        return _error('team_id and user_id are required.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    is_owner = team.team_owner_id == user.user_id
    requester_entry = AuthTeamMembers.objects.filter(team=team, user=user).first()
    requester_role = 'owner' if is_owner else (requester_entry.role if requester_entry else None)
    if requester_role not in ('owner', 'captain', 'vice_captain'):
        return _error('You do not have permission to remove members.',
                      'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)

    if str(target_user_id) == str(user.user_id):
        return _error('You cannot kick yourself. Use the "leave team" action instead.',
                      'CANNOT_REMOVE_SELF', status.HTTP_400_BAD_REQUEST)
    if str(target_user_id) == str(team.team_owner_id):
        return _error('The owner cannot be removed. Transfer ownership first.',
                      'CANNOT_REMOVE_OWNER', status.HTTP_400_BAD_REQUEST)

    target_entry = AuthTeamMembers.objects.filter(team=team, user_id=target_user_id).first()
    if not target_entry:
        return _error('Member not found in this team.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if requester_role in ('captain', 'vice_captain') and target_entry.role in ('owner', 'captain', 'vice_captain'):
        return _error('Only the owner can remove captains or vice-captains.',
                      'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)

    with transaction.atomic():
        target_entry.delete()
        _recount_members(team)

    return Response({
        'status': 'success',
        'data': {'number_of_members': team.number_of_members},
        'message': 'Member removed successfully.',
        'number_of_members': team.number_of_members,
    }, status=status.HTTP_200_OK)


# -----------------------
# JOIN REQUESTS  (Part C - owner-only list)
# -----------------------
@api_view(['GET'])
def join_requests(request, team_id):
    """GET /team/join-requests/<team_id>/ - owner-only pending requests."""
    user, error = _authenticate(request)
    if error:
        return error

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('Only the team owner can view join requests.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)

    reqs = list(
        TeamJoinRequest.objects.filter(team=team, status='pending')
        .select_related('applicant')
        .order_by('-created_at')
    )
    uids = [r.applicant_id for r in reqs]
    pic_map = {
        up.user_id: absolute_media_url(request, up.profile_picture)
        for up in UserProfile.objects.filter(user_id__in=uids)
    }

    requests_out = [{
        'id': r.id,
        'applicant': {
            'username': r.applicant.username,
            'full_name': r.applicant.full_name,
            'avatar': pic_map.get(r.applicant_id),
            'rank': None,
        },
        'message': r.message,
        'created_at': r.created_at,
    } for r in reqs]

    return Response({
        'status': 'success',
        'data': {'requests': requests_out, 'invites_sent': []},
        'message': 'Join requests fetched successfully.',
    }, status=status.HTTP_200_OK)


# -----------------------
# ACCEPT REQUEST  (Part C - owner-only)
# -----------------------
@api_view(['POST'])
def accept_request(request, request_id):
    """POST /team/accept-request/<request_id>/ - owner accepts a pending request."""
    user, error = _authenticate(request)
    if error:
        return error

    req = TeamJoinRequest.objects.select_related('team', 'applicant').filter(pk=request_id).first()
    if not req:
        return _error('Join request not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if req.team.team_owner_id != user.user_id:
        return _error('Only the team owner can accept requests.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)
    if req.status != 'pending':
        return _error('This request has already been resolved.', 'ALREADY_RESOLVED', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        AuthTeamMembers.objects.get_or_create(
            team=req.team, user=req.applicant,
            defaults={'role': 'member', 'is_captain': False},
        )
        req.status = 'accepted'
        req.resolved_at = timezone.now()
        req.save(update_fields=['status', 'resolved_at'])
        _recount_members(req.team)

    # Notify the requester they were accepted - fire-and-forget.
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            req.applicant_id, 'team',
            f'You joined {req.team.team_name}',
            link=f'/teams/team-profile?id={req.team.team_id}',
            metadata={'team_id': req.team.team_id},
        )
    except Exception:
        pass

    return Response({
        'status': 'success',
        'data': {'number_of_members': req.team.number_of_members},
        'message': 'Join request accepted.',
    }, status=status.HTTP_200_OK)


# -----------------------
# REJECT REQUEST  (Part C - owner-only)
# -----------------------
@api_view(['POST'])
def reject_request(request, request_id):
    """POST /team/reject-request/<request_id>/ - owner rejects a pending request."""
    user, error = _authenticate(request)
    if error:
        return error

    req = TeamJoinRequest.objects.select_related('team').filter(pk=request_id).first()
    if not req:
        return _error('Join request not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if req.team.team_owner_id != user.user_id:
        return _error('Only the team owner can reject requests.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)
    if req.status != 'pending':
        return _error('This request has already been resolved.', 'ALREADY_RESOLVED', status.HTTP_400_BAD_REQUEST)

    req.status = 'rejected'
    req.resolved_at = timezone.now()
    req.save(update_fields=['status', 'resolved_at'])

    return Response({
        'status': 'success',
        'data': {},
        'message': 'Join request rejected.',
    }, status=status.HTTP_200_OK)


# -----------------------
# EDIT TEAM  (contract Part C - owner-only, partial)
# -----------------------
@api_view(['PATCH'])
def edit_team(request, team_id):
    """PATCH /team/edit-team/<team_id>/ - partial update of team fields/socials."""
    user, error = _authenticate(request)
    if error:
        return error

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('Only the team owner can edit the team.', 'NOT_OWNER', status.HTTP_403_FORBIDDEN)

    data = request.data

    # Validate name/game up front so we never half-apply.
    if 'name' in data:
        new_name = (data.get('name') or '').strip()
        if new_name and new_name != team.team_name and \
                Teams.objects.filter(team_name=new_name).exclude(pk=team.pk).exists():
            return _error('A team with this name already exists.',
                          'TEAM_NAME_TAKEN', status.HTTP_400_BAD_REQUEST)
    if 'core_game' in data:
        new_game = _resolve_game(data.get('core_game'))
        if not new_game:
            return _error('A valid core_game (id or title) is required.',
                          'GAME_NOT_FOUND', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        if 'name' in data:
            new_name = (data.get('name') or '').strip()
            if new_name:
                team.team_name = new_name
        if 'core_game' in data:
            team.game = _resolve_game(data.get('core_game'))
        if 'bio' in data:
            team.description = data.get('bio') or ''
        if 'logo_url' in data:
            logo_file = _decode_base64_image(data.get('logo_url'), f"{slugify(team.team_name)}-logo")
            if logo_file:
                team.team_logo = logo_file
        if 'banner_url' in data:
            banner_file = _decode_base64_image(data.get('banner_url'), f"{slugify(team.team_name)}-banner")
            if banner_file:
                team.team_banner = banner_file
        team.save()

        if 'region' in data or 'social_links' in data:
            profile, _created = TeamProfile.objects.get_or_create(team=team)
            if 'region' in data:
                profile.country = data.get('region') or None
            if 'social_links' in data:
                sl = data.get('social_links') or {}
                field_map = {
                    'twitter': 'twitter_link',
                    'instagram': 'instagram_link',
                    'youtube': 'youtube_link',
                    'twitch': 'twitch_link',
                    'facebook': 'facebook_link',
                    'kick': 'kick_link',
                }
                for key, field in field_map.items():
                    if key in sl:
                        setattr(profile, field, sl.get(key) or None)
            profile.save()

    return Response({
        'status': 'success',
        'data': {'team': serialize_team_detail(request, team)},
        'message': 'Team updated successfully.',
    }, status=status.HTTP_200_OK)


# -----------------------
# MEMBERSHIP SETTINGS  (contract Part C - owner-only)
# -----------------------
@api_view(['PATCH'])
def membership_settings(request, team_id):
    """PATCH /team/membership-settings/<team_id>/ - owner-only membership config."""
    user, error = _authenticate(request)
    if error:
        return error

    team = Teams.objects.filter(pk=team_id).first()
    if not team:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('Only the team owner can change membership settings.',
                      'NOT_OWNER', status.HTTP_403_FORBIDDEN)

    data = request.data

    if 'max_members' in data:
        mm = data.get('max_members')
        try:
            team.max_members = int(mm) if mm not in (None, '', 0) else None
        except (ValueError, TypeError):
            team.max_members = None
    if 'open_to_join' in data:
        team.allow_membership_requests = bool(data.get('open_to_join'))
    if 'password_protected' in data:
        team.password_protected = bool(data.get('password_protected'))
    if 'password' in data:
        pw = data.get('password')
        if pw:
            team.join_password = make_password(pw)

    team.save()

    return Response({
        'status': 'success',
        'data': {},
        'message': 'Membership settings updated.',
    }, status=status.HTTP_200_OK)


# -----------------------
# Activity (team profile "Activity Tournaments" / "Activity Events" tabs)
# -----------------------

@api_view(['GET'])
def team_tournaments(request, team_id):
    """Tournaments this team has registered for, with placement when known.

    Backs GET /team/tournaments/<team_id>/ - the Activity Tournaments tab.
    """
    from vent_tournament.models import TournamentRegistration, BracketMatch

    try:
        team = Teams.objects.get(team_id=team_id)
    except Teams.DoesNotExist:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    registrations = (
        TournamentRegistration.objects
        .filter(team=team)
        .select_related('tournament', 'tournament__tournament_game')
        .order_by('-registered_at')
    )

    rows = []
    for reg in registrations:
        t = reg.tournament
        # Placement: winner of the final round means 1st; otherwise report the
        # round they went out in rather than inventing a position.
        position = 'N/A'
        finals = BracketMatch.objects.filter(tournament=t).order_by('-round_number').first()
        if finals and finals.winner_id == reg.id:
            position = '1st'
        elif BracketMatch.objects.filter(tournament=t, status='completed').filter(
            models_q_participant(reg.id)
        ).exists():
            last = (
                BracketMatch.objects
                .filter(tournament=t, status='completed')
                .filter(models_q_participant(reg.id))
                .order_by('-round_number').first()
            )
            if last:
                position = f'Round {last.round_number}'

        rows.append({
            'tournament_id': t.tournament_id,
            'id': t.tournament_id,
            'tournament_title': t.tournament_title,
            'name': t.tournament_title,
            'game': t.tournament_game.game_title if t.tournament_game else None,
            'logo': absolute_media_url(request, t.tournament_logo),
            'tournament_logo': absolute_media_url(request, t.tournament_logo),
            'status': (t.status or '').replace('_', ' ').title(),
            'position': position,
            'registration_status': reg.status,
            'start_date_and_time': t.start_date_and_time,
            'date': t.start_date_and_time,
        })

    return Response({
        'status': 'success',
        'data': {'tournaments': rows, 'count': len(rows)},
        'message': 'Team tournaments retrieved.',
    }, status=status.HTTP_200_OK)


def models_q_participant(reg_id):
    """Q() matching a bracket match where this registration played either side."""
    from django.db.models import Q
    return Q(participant_1_id=reg_id) | Q(participant_2_id=reg_id)


@api_view(['GET'])
def team_events(request, team_id):
    """Events linked to this team.

    Backs GET /team/events/<team_id>/ - the Activity Events tab. Event↔team
    linking is a Phase-2 feature (no FK exists yet), so this returns an honest
    empty list instead of 404-ing the tab.
    """
    if not Teams.objects.filter(team_id=team_id).exists():
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    return Response({
        'status': 'success',
        'data': {'events': [], 'count': 0},
        'message': 'Team events retrieved.',
    }, status=status.HTTP_200_OK)

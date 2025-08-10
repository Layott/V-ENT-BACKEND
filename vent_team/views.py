from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from .models import Teams, TeamProfile, TeamMembers
from vent_auth.models import Users, Games, GameAccount, LoginSessions
from django.shortcuts import get_object_or_404

# -----------------------
# CREATE TEAM
# -----------------------
@api_view(['POST'])
def create_team(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    # Get logged in user from session
    session = LoginSessions.objects.filter(session_token=login_session_token).first()
    if not session:
        return Response({'status': 'error', 'message': 'Invalid or expired token'}, status=status.HTTP_401_UNAUTHORIZED)

    user = session.user

    team_logo = request.FILES.get('team_logo')
    team_banner = request.FILES.get('team_banner')
    team_name = request.data.get('team_name')
    game_id = request.data.get('game')
    description = request.data.get('description', Teams._meta.get_field('description').default)
    allow_membership_requests = request.data.get('allow_membership_requests', True)

    if not team_name or not game_id:
        return Response({'status': 'error', 'message': 'Team name and game are required'}, status=status.HTTP_400_BAD_REQUEST)

    game = get_object_or_404(Games, pk=game_id)

    # Create the team
    team = Teams.objects.create(
        team_name=team_name,
        team_logo=team_logo,
        team_banner=team_banner,
        game=game,
        description=description,
        allow_membership_requests=allow_membership_requests,
        creation_date=timezone.now(),
        team_creator=user,
        team_owner=user,
        penalty_points=0,
        number_of_members=1
    )

    # Add the creator as captain in members list
    game_account = GameAccount.objects.filter(user=user, game=game).first()
    if game_account:
        TeamMembers.objects.create(team=team, member=game_account, role='captain')

    return Response({'status': 'success', 'message': 'Team created successfully', 'team_id': team.team_id}, status=status.HTTP_201_CREATED)


# -----------------------
# GET TEAM DETAILS
# -----------------------
@api_view(['GET'])
def get_team_details(request, team_id):
    team = get_object_or_404(Teams, pk=team_id)
    profile = TeamProfile.objects.filter(team=team).first()
    members = TeamMembers.objects.filter(team=team).select_related('member', 'member__user')

    members_data = [
        {
            'member_id': m.member.id,
            'username': m.member.user.username,
            'role': m.role
        }
        for m in members
    ]

    return Response({
        'team': {
            'id': team.team_id,
            'name': team.team_name,
            'logo': team.team_logo.url if team.team_logo else None,
            'banner': team.team_banner.url if team.team_banner else None,
            'description': team.description,
            'owner': team.team_owner.username,
            'game': team.game.game_name,
            'penalty_points': team.penalty_points,
            'number_of_members': team.number_of_members,
            'allow_membership_requests': team.allow_membership_requests,
            'creation_date': team.creation_date
        },
        'profile': {
            'country': profile.country if profile else None,
            'facebook': profile.facebook_link if profile else None,
            'twitter': profile.twitter_link if profile else None,
            'instagram': profile.instagram_link if profile else None,
            'youtube': profile.youtube_link if profile else None,
            'twitch': profile.twitch_link if profile else None,
            'kick': profile.kick_link if profile else None
        },
        'members': members_data
    })


# -----------------------
# TRANSFER OWNERSHIP
# -----------------------
@api_view(['POST'])
def transfer_ownership(request):
    # Auth check
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid or missing token'}, status=status.HTTP_400_BAD_REQUEST)
    login_session_token = session_token.split(" ")[1]
    session = LoginSessions.objects.filter(session_token=login_session_token).first()
    if not session:
        return Response({'status': 'error', 'message': 'Invalid or expired token'}, status=status.HTTP_401_UNAUTHORIZED)
    user = session.user

    # Get request data
    team_name = request.data.get('team_name')
    new_owner_username = request.data.get('new_owner_username')
    prev_owner_new_role = request.data.get('prev_owner_new_role')

    if not team_name or not new_owner_username or not prev_owner_new_role:
        return Response({'status': 'error', 'message': 'team_name, new_owner_username, and prev_owner_new_role are required'}, status=status.HTTP_400_BAD_REQUEST)

    # Validate team & owner
    team = get_object_or_404(Teams, team_name=team_name)
    if team.team_owner != user:
        return Response({'status': 'error', 'message': 'Only the team owner can transfer ownership'}, status=status.HTTP_403_FORBIDDEN)

    # Validate new owner
    new_owner = get_object_or_404(Users, username=new_owner_username)
    if not TeamMembers.objects.filter(team=team, member__user=new_owner).exists():
        return Response({'status': 'error', 'message': 'New owner must already be a member of the team'}, status=status.HTTP_400_BAD_REQUEST)

    # Validate role
    if prev_owner_new_role not in dict(TeamMembers.ROLE_CHOICES):
        return Response({'status': 'error', 'message': 'Invalid role for previous owner'}, status=status.HTTP_400_BAD_REQUEST)

    # Change ownership
    team.team_owner = new_owner
    team.save()

    # Update old owner's role
    old_owner_member = TeamMembers.objects.filter(team=team, member__user=user).first()
    if old_owner_member:
        old_owner_member.role = prev_owner_new_role
        old_owner_member.save()

    return Response({'status': 'success', 'message': f'Team ownership transferred to {new_owner.username}, your new role is {prev_owner_new_role}'})


# -----------------------
# ASSIGN NEW ROLE
# -----------------------
@api_view(['POST'])
def assign_new_role(request):
    # Auth check
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid or missing token'}, status=status.HTTP_400_BAD_REQUEST)
    login_session_token = session_token.split(" ")[1]
    session = LoginSessions.objects.filter(session_token=login_session_token).first()
    if not session:
        return Response({'status': 'error', 'message': 'Invalid or expired token'}, status=status.HTTP_401_UNAUTHORIZED)
    user = session.user

    # Get request data
    team_name = request.data.get('team_name')
    member_username = request.data.get('member_username')
    new_role = request.data.get('new_role')

    if not team_name or not member_username or not new_role:
        return Response({'status': 'error', 'message': 'team_name, member_username, and new_role are required'}, status=status.HTTP_400_BAD_REQUEST)

    # Validate team & owner
    team = get_object_or_404(Teams, team_name=team_name)
    if team.team_owner != user:
        return Response({'status': 'error', 'message': 'Only the team owner can assign roles'}, status=status.HTTP_403_FORBIDDEN)

    # Validate role
    if new_role not in dict(TeamMembers.ROLE_CHOICES):
        return Response({'status': 'error', 'message': 'Invalid role'}, status=status.HTTP_400_BAD_REQUEST)

    # Find member
    member_entry = TeamMembers.objects.filter(team=team, member__user__username=member_username).first()
    if not member_entry:
        return Response({'status': 'error', 'message': 'Member not found in this team'}, status=status.HTTP_404_NOT_FOUND)

    # Update role
    member_entry.role = new_role
    member_entry.save()

    return Response({'status': 'success', 'message': f"{member_username}'s role updated to {new_role}"})

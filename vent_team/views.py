from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from .models import TeamMembers
from vent_auth.models import Users, Games, GameAccount, Teams, TeamProfile
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import F

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

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)

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
    except Users.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )

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
            'game': team.game.game_title,
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

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)
    
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
    except Users.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Teams.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Team not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except TeamMembers.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Team member not found'},
            status=status.HTTP_404_NOT_FOUND
        )  
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({'status': 'success', 'message': f'Team ownership transferred to {new_owner.username}, your new role is {prev_owner_new_role}'})


# -----------------------
# ASSIGN NEW ROLE
# -----------------------
@api_view(['POST'])
def assign_new_role(request):
    # Auth check
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)

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
    
    except Users.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Teams.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Team not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except TeamMembers.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Team member not found'},
            status=status.HTTP_404_NOT_FOUND
        )  
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    return Response({'status': 'success', 'message': f"{member_username}'s role updated to {new_role}"})


# -----------------------
# REMOVE MEMBER
# -----------------------
@api_view(['POST'])
def remove_member(request):
    # --- Auth check ---
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)


        # --- Input validation ---
        team_name = request.data.get('team_name')
        member_username = request.data.get('member_username')

        if not team_name or not member_username:
            return Response({'status': 'error', 'message': 'team_name and member_username are required'}, status=status.HTTP_400_BAD_REQUEST)

        # --- Get team ---
        team = get_object_or_404(Teams, team_name=team_name)

        # --- Get requester's team role ---
        requester_entry = TeamMembers.objects.filter(team=team, member__user=user).first()
        if not requester_entry:
            return Response({'status': 'error', 'message': 'You are not a member of this team'}, status=status.HTTP_403_FORBIDDEN)

        requester_role = requester_entry.role.lower() if requester_entry.role else "member"

        # --- Prevent removing self ---
        if member_username == user.username:
            if requester_role == "owner":
                return Response({
                    'status': 'error',
                    'message': 'You cannot remove yourself as owner. Transfer ownership first, then leave the team.'
                }, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({
                    'status': 'error',
                    'message': 'You cannot remove yourself. Use the "leave team" action instead.'
                }, status=status.HTTP_400_BAD_REQUEST)

        # --- Get target member entry ---
        target_entry = TeamMembers.objects.filter(team=team, member__user__username=member_username).first()
        if not target_entry:
            return Response({'status': 'error', 'message': 'Member not found in this team'}, status=status.HTTP_404_NOT_FOUND)

        target_role = target_entry.role.lower() if target_entry.role else "member"

        # --- Permission rules ---
        if requester_role == "owner":
            pass  # Owner can remove anyone except themselves
        elif requester_role in ["captain", "vice captain"]:
            if target_role in ["owner", "captain", "vice captain"]:
                return Response({'status': 'error', 'message': 'Only the owner can remove captains or vice captains.'}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({'status': 'error', 'message': 'You do not have permission to remove members'}, status=status.HTTP_403_FORBIDDEN)

        # --- Remove member & update count ---
        with transaction.atomic():
            target_entry.delete()
            team.number_of_members = max(0, team.number_of_members - 1)
            team.save()

        # --- Updated members list ---
        updated_members = list(
            TeamMembers.objects.filter(team=team)
            .values(username=F('member__user__username'), role=F('role'))
        )

    except Users.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Teams.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Team not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except TeamMembers.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Team member not found'},
            status=status.HTTP_404_NOT_FOUND
        )  
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({
        'status': 'success',
        'message': f'Member {member_username} removed successfully',
        'number_of_members': team.number_of_members,
        'members': updated_members
    })
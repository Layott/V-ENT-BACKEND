import datetime
import logging
import random
from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    Users, UserProfile, UserInterests, UserCommunity, UserWallet,
    Games, GameAccount, FavoriteGames, Teams, TeamProfile, SocialLink,
)
from .views_helpers import send_email

logger = logging.getLogger(__name__)


@api_view(['POST'])
def change_fullname(request):
    user_id = request.data.get('user_id')
    new_fullname = request.data.get('new_fullname')

    if not user_id or not new_fullname:
        return Response({'error': 'User ID and new full name are required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            user = Users.objects.select_for_update().get(user_id=user_id)
            user.full_name = new_fullname
            user.save()
        return Response({'message': 'Full name changed successfully'}, status=status.HTTP_200_OK)
    except Users.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def change_email(request):
    user_id = request.data.get('user_id')
    new_email = request.data.get('new_email')

    if not user_id or not new_email:
        return Response({"error": "User ID and new email are required"}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=new_email).exists():
        return Response({"error": "Email already in use"}, status=status.HTTP_400_BAD_REQUEST)

    from .models import VerificationToken

    token = ''.join(random.choices('0123456789', k=6))

    VerificationToken.objects.update_or_create(
        user_email=new_email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    subject = 'Verify Email'
    message = f'''
Hi,

Your Verification Token Is: {token}

Please use it to verify your account'''

    send_email(new_email, subject, message)

    return Response({"message": "Verification token sent to email"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def verify_new_email(request):
    from .models import VerificationToken

    user_id = request.data.get('user_id')
    new_email = request.data.get('new_email')
    token = request.data.get('token')

    if not user_id or not new_email or not token:
        return Response({"error": "User ID, new email, and token are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        verification_token = VerificationToken.objects.get(user_email=new_email)

        if verification_token.token == token and verification_token.is_valid():
            with transaction.atomic():
                user = Users.objects.select_for_update().get(user_id=user_id)
                user.email = new_email
                user.save()
                verification_token.delete()

            return Response({"message": "Email changed successfully"}, status=status.HTTP_200_OK)
        else:
            return Response({"error": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)
    except VerificationToken.DoesNotExist:
        return Response({"error": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)
    except Users.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def add_date_of_birth(request):
    user_id = request.data.get('user_id')
    date_of_birth = request.data.get('date_of_birth')

    if not user_id or not date_of_birth:
        return Response({"error": "User ID and date of birth are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        date_of_birth = datetime.datetime.strptime(date_of_birth, '%Y-%m-%d').date()

        with transaction.atomic():
            user_profile = UserProfile.objects.select_for_update().get(user_id=user_id)
            user_profile.date_of_birth = date_of_birth
            user_profile.save()

        return Response({"message": "Date Of Birth Added Successfully"}, status=status.HTTP_200_OK)

    except UserProfile.DoesNotExist:
        return Response({"error": "User profile not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def add_game_account(request):
    # Session-token version (supersedes the legacy user_id version)
    session_token = request.data.get('session_token')
    game_id = request.data.get('game_id')
    game_username = request.data.get('game_username')

    if not session_token or not game_id or not game_username:
        return Response(
            {'status': 'error', 'message': 'session_token, game_id, and game_username are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    user = get_object_or_404(Users, login_session_token=session_token)

    if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=360):
        return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

    game = get_object_or_404(Games, game_id=game_id)

    if GameAccount.objects.filter(user=user, game=game).exists():
        return Response(
            {'status': 'error', 'message': 'Game account already exists for this user'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        GameAccount.objects.create(user=user, game=game, game_username=game_username)
        return Response(
            {'status': 'success', 'message': 'Game account added successfully'},
            status=status.HTTP_201_CREATED
        )
    except Exception as e:
        return Response(
            {'status': 'error', 'message': f'An unexpected error occurred: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def edit_game_account_username(request):
    user_id = request.data.get('user_id')
    game_id = request.data.get('game_id')
    new_game_username = request.data.get('new_game_username')

    if not user_id or not game_id or not new_game_username:
        return Response({"error": "User ID, game ID, and new game username are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            game_account = GameAccount.objects.select_for_update().get(user_id=user_id, game_id=game_id)
            game_account.game_username = new_game_username
            game_account.save()

        return Response({"message": "Game account username changed successfully"}, status=status.HTTP_200_OK)

    except GameAccount.DoesNotExist:
        return Response({"error": "Game account does not exist"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def create_team(request):
    user_id = request.data.get('user_id')
    team_name = request.data.get('team_name')
    creation_date = request.data.get('creation_date', timezone.now().date())
    team_privacy = request.data.get('team_privacy', 'public')
    game_id = request.data.get('game_id')

    if Teams.objects.filter(team_name=team_name).exists():
        return Response({"error": "Team name already exists"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(user_id=user_id)
        game = Games.objects.get(game_id=game_id)
    except Users.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Games.DoesNotExist:
        return Response({"error": "Game not found"}, status=status.HTTP_404_NOT_FOUND)

    team = Teams.objects.create(
        team_name=team_name,
        creation_date=creation_date,
        team_creator=user,
        team_owner=user,
        game=game,
        team_privacy=team_privacy
    )

    TeamProfile.objects.create(team=team)

    return Response({"success": "Team created successfully"}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
def choose_community(request):
    user_id = request.data.get('user_id')
    is_gamer = request.data.get('is_gamer')
    is_anime_enth = request.data.get('is_anime_enth')

    if is_gamer and is_anime_enth:
        UserCommunity.objects.create(user_id=user_id, is_gamer=is_gamer, is_anime_enth=is_anime_enth)
    elif is_gamer:
        UserCommunity.objects.create(user_id=user_id, is_gamer=is_gamer)
    elif is_anime_enth:
        UserCommunity.objects.create(user_id=user_id, is_anime_enth=is_anime_enth)
    else:
        return Response({"message": "No communities provided"}, status=status.HTTP_400_BAD_REQUEST)

    return Response({"message": "Communities processed successfully"}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_user_informations(request):
    try:
        session_token = request.headers.get('Authorization')

        if not session_token:
            return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not session_token.startswith("Bearer "):
            return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        session_token = session_token.split(" ")[1]

        user = get_object_or_404(Users, login_session_token=session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            profile = None

        interests = list(UserInterests.objects.filter(user=user).values_list('interests', flat=True))
        wallet = UserWallet.objects.filter(user=user).first()
        user_games = FavoriteGames.objects.filter(user=user).values_list('game__game_title', flat=True)
        achievements = user.achievements.values('name', 'description', 'logo')
        social_links = SocialLink.objects.filter(user=user).values('title', 'url')

        data = {
            'full_name': user.full_name,
            'username': user.username,
            'email': user.email,
            'country': user.country,
            'profile_picture': request.build_absolute_uri(profile.profile_picture.url) if profile and profile.profile_picture else None,
            'banner': request.build_absolute_uri(profile.banner.url) if profile and profile.banner else None,
            'description': profile.description if profile else None,
            'penalty_point': profile.penalty_point if profile else 0,
            'social_links': list(social_links),
            'wallet_balance': wallet.wallet_balance if wallet else 0,
            'interests': interests,
            'favorite_games': list(user_games),
            'achievements': list(achievements),
        }

        return Response(
            {'status': 'success', 'message': 'User information retrieved successfully', 'data': data},
            status=status.HTTP_200_OK
        )

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return Response({'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_user_status(request):
    email = request.query_params.get('email')

    if not email:
        return Response({'status': 'error', 'message': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email)
        return Response({
            'status': 'success',
            'is_active': user.is_active,
            'message': 'User status retrieved successfully'
        }, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': f'An error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
def update_web_and_social_links(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = Users.objects.get(login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        links = request.data.get("links")
        if not isinstance(links, dict):
            return Response({'status': 'error', 'message': 'Links should be a dictionary'}, status=status.HTTP_400_BAD_REQUEST)

        existing_titles = set(user.social_links.values_list('title', flat=True))

        for title, url in links.items():
            if not title or not url:
                continue
            SocialLink.objects.update_or_create(
                user=user,
                title=title,
                defaults={'url': url}
            )
            existing_titles.add(title)

        return Response({'status': 'success', 'message': 'Social links updated successfully'}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'Invalid session token'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def edit_favorite_games(request):
    try:
        login_session_token = request.data.get('login_session_token')
        game_ids = request.data.get('game_ids')

        if not login_session_token:
            return Response(
                {'status': 'error', 'message': 'login_session_token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(game_ids, list):
            return Response(
                {'status': 'error', 'message': 'game_ids must be a list of game IDs'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = get_object_or_404(Users, login_session_token=login_session_token)

        FavoriteGames.objects.filter(user=user).delete()

        for game_id in game_ids:
            game = get_object_or_404(Games, game_id=game_id)
            FavoriteGames.objects.create(user=user, game=game)

        return Response({'status': 'success', 'message': 'Favorite games updated successfully'}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Games.DoesNotExist:
        return Response({'status': 'error', 'message': 'One or more games not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': f'An unexpected error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def edit_profile_info(request):
    try:
        session_token = request.headers.get('Authorization')

        if not session_token:
            return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not session_token.startswith("Bearer "):
            return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        login_session_token = session_token.split(" ")[1]
        profile_pic = request.FILES.get("profile_pic")
        banner = request.FILES.get("banner")
        username = request.data.get('username')
        fullname = request.data.get('fullname')
        description = request.data.get('description')
        country = request.data.get('country')
        interests = request.data.get('interests')

        user = get_object_or_404(Users, login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=360):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            return Response({'status': 'error', 'message': 'User profile does not exist'}, status=status.HTTP_404_NOT_FOUND)

        if username and username != user.username:
            if Users.objects.filter(username=username).exists():
                return Response({'status': 'error', 'message': 'Username already taken'}, status=status.HTTP_400_BAD_REQUEST)
            user.username = username
        if fullname:
            user.full_name = fullname
        if description:
            profile.description = description
        if country:
            user.country = country
        if profile_pic:
            if not profile_pic.content_type.startswith("image/"):
                return Response({'status': 'error', 'message': 'Invalid profile picture format'}, status=status.HTTP_400_BAD_REQUEST)
            profile.profile_picture = profile_pic
        if banner:
            if not banner.content_type.startswith("image/"):
                return Response({'status': 'error', 'message': 'Invalid banner format'}, status=status.HTTP_400_BAD_REQUEST)
            profile.banner = banner

        user.save()
        profile.save()

        if interests and isinstance(interests, list):
            UserInterests.objects.filter(user=user).delete()
            interests_objects = [UserInterests(user=user, interests=interest) for interest in interests]
            UserInterests.objects.bulk_create(interests_objects)

        return Response(
            {
                'status': 'success',
                'message': 'Profile updated successfully',
                'data': {
                    'username': user.username,
                    'fullname': user.full_name,
                    'profile_pic': profile.profile_picture.url if profile.profile_picture else None,
                    'banner': profile.banner.url if profile.banner else None,
                    'description': profile.description,
                    'country': user.country,
                    'interests': interests,
                },
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return Response({'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

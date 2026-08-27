from django.http import Http404
import datetime
import logging
import random
from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import (
    Users, UserProfile, UserInterests, UserCommunity, UserWallet,
    Games, GameAccount, FavoriteGames, Teams, TeamProfile, SocialLink,
)
from . import emails
from .views_helpers import session_timeout_minutes

logger = logging.getLogger(__name__)


def _user_from_bearer(request):
    """Return (user, error_response) from a `Authorization: Bearer <token>` header.

    Standard session-token auth used by the newer profile endpoints. 120-min
    timeout, consistent with the rest of vent_auth.
    """
    session_token = request.headers.get('Authorization')
    if not session_token:
        return None, Response(
            { 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not session_token.startswith('Bearer '):
        return None, Response(
            { 'code': 'INVALID_TOKEN_FORMAT','status': 'error', 'message': 'Invalid token format'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    token = session_token.split(' ', 1)[1]
    try:
        user = Users.objects.get(login_session_token=token)
    except Users.DoesNotExist:
        return None, Response(
            { 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if (
        user.login_session_created_at is None
        or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes())
    ):
        return None, Response(
            { 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return user, None


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
        return Response({ 'code': 'FULL_NAME_CHANGED_SUCCESSFULLY','message': 'Full name changed successfully'}, status=status.HTTP_200_OK)
    except Users.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def change_email(request):
    # The account is whoever is holding the token, never a user_id posted in the
    # body. As written, this endpoint would mail a change code for any account
    # to any address, and verify_new_email would then move that account's email
    # to the attacker's - which is a password reset away from the account.
    user, err = _user_from_bearer(request)
    if err:
        return err

    new_email = (request.data.get('new_email') or '').strip().lower()
    user_id = user.user_id

    if not new_email:
        return Response({ 'code': 'NEW_EMAIL_ADDRESS_REQUIRED',"status": "error", "message": "A new email address is required"},
                        status=status.HTTP_400_BAD_REQUEST)

    if new_email == (user.email or '').strip().lower():
        return Response({ 'code': 'ALREADY_EMAIL_ADDRESS',"status": "error", "message": "That is already your email address."},
                        status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        return Response({ 'code': 'EMAIL_ALREADY_USE',"status": "error", "message": "That email is already in use."},
                        status=status.HTTP_400_BAD_REQUEST)

    from .models import VerificationToken

    token = ''.join(random.choices('0123456789', k=6))

    VerificationToken.objects.update_or_create(
        user_email=new_email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    # Greet the person by name and tell them which address the account moves
    # from, so a code arriving out of the blue is recognisable as theirs.
    owner = Users.objects.filter(user_id=user_id).first()
    emails.send_verify_new_email(
        new_email,
        name=(owner.full_name or owner.username) if owner else 'there',
        code=token,
        old_email=owner.email if owner else '',
    )

    return Response({ 'code': 'VERIFICATION_TOKEN_SENT_EMAIL',"message": "Verification token sent to email"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def verify_new_email(request):
    from .models import VerificationToken

    # Same rule as change_email: the token identifies the account.
    user, err = _user_from_bearer(request)
    if err:
        return err

    user_id = user.user_id
    new_email = (request.data.get('new_email') or '').strip().lower()
    token = request.data.get('token')

    if not new_email or not token:
        return Response({ 'code': 'NEW_EMAIL_CODE_REQUIRED',"status": "error", "message": "The new email and the code are required"},
                        status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        return Response({ 'code': 'EMAIL_ALREADY_USE',"status": "error", "message": "That email is already in use."},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        verification_token = VerificationToken.objects.get(user_email=new_email)

        from .models import VerificationToken as _VT

        if verification_token.token == token and verification_token.is_valid(_VT.RESET_CODE_MINUTES):
            with transaction.atomic():
                user = Users.objects.select_for_update().get(user_id=user_id)
                user.email = new_email
                user.save(update_fields=['email'])
                verification_token.delete()

            return Response({ 'code': 'EMAIL_CHANGED_SUCCESSFULLY',"message": "Email changed successfully"}, status=status.HTTP_200_OK)
        else:
            return Response({"error": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)
    except VerificationToken.DoesNotExist:
        return Response({"error": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)
    except Users.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
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

        return Response({ 'code': 'DATE_BIRTH_ADDED_SUCCESSFULLY',"message": "Date Of Birth Added Successfully"}, status=status.HTTP_200_OK)

    except UserProfile.DoesNotExist:
        return Response({"error": "User profile not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
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
            { 'code': 'SESSION_TOKEN_GAME_ID','status': 'error', 'message': 'session_token, game_id, and game_username are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    user = Users.objects.filter(login_session_token=session_token).first()
    if user is None:
        return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
        return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=401)

    game = get_object_or_404(Games, game_id=game_id)

    if GameAccount.objects.filter(user=user, game=game).exists():
        return Response(
            { 'code': 'GAME_ACCOUNT_ALREADY_EXISTS','status': 'error', 'message': 'Game account already exists for this user'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        GameAccount.objects.create(user=user, game=game, game_username=game_username)
        return Response(
            {'status': 'success', 'message': 'Game account added successfully'},
            status=status.HTTP_201_CREATED
        )
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
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

        return Response({ 'code': 'GAME_ACCOUNT_USERNAME_CHANGED',"message": "Game account username changed successfully"}, status=status.HTTP_200_OK)

    except GameAccount.DoesNotExist:
        return Response({"error": "Game account does not exist"}, status=status.HTTP_404_NOT_FOUND)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
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
        return Response({ 'code': 'NO_COMMUNITIES_PROVIDED',"message": "No communities provided"}, status=status.HTTP_400_BAD_REQUEST)

    return Response({ 'code': 'COMMUNITIES_PROCESSED_SUCCESSFULLY',"message": "Communities processed successfully"}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_user_informations(request):
    try:
        session_token = request.headers.get('Authorization')

        if not session_token:
            return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not session_token.startswith("Bearer "):
            return Response({ 'code': 'INVALID_TOKEN_FORMAT','status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        session_token = session_token.split(" ")[1]

        user = Users.objects.filter(login_session_token=session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=401)

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
            # A founder looking at their own profile saw no badge, because this
            # payload never carried the flag - only the public profile view did.
            # So the mark appeared to everybody except the person who earned it,
            # which is how it was reported. Same expression as the public view
            # and the community payloads; keep the three in step.
            'founder_badge': bool(getattr(user, 'is_founder', False)
                                  and user.show_founder_badge),
            'country': user.country,
            # The city, so a profile can read "Lagos, Nigeria". Both halves are
            # set by the daily location refresh at sign-in.
            'state': user.state,
            'profile_picture': request.build_absolute_uri(profile.profile_picture.url) if profile and profile.profile_picture else None,
            'banner': request.build_absolute_uri(profile.banner.url) if profile and profile.banner else None,
            'description': profile.description if profile else None,
            'penalty_point': profile.penalty_point if profile else 0,
            'social_links': list(social_links),
            'wallet_balance': wallet.wallet_balance if wallet else 0,
            'interests': interests,
            'favorite_games': _favorite_games_payload(user, request),
            'gaming_accounts': _gaming_accounts_payload(user),
            'achievements': list(achievements),
        }

        return Response(
            {'status': 'success', 'message': 'User information retrieved successfully', 'data': data},
            status=status.HTTP_200_OK
        )

    except Users.DoesNotExist:
        return Response({ 'code': 'USER_NOT_FOUND','status': 'error', 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return Response({ 'code': 'UNEXPECTED_ERROR_OCCURRED_PLEASE','status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_user_status(request):
    email = request.query_params.get('email')

    if not email:
        return Response({ 'code': 'EMAIL_REQUIRED','status': 'error', 'message': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email)
        return Response({
            'status': 'success',
            'is_active': user.is_active,
            'message': 'User status retrieved successfully'
        }, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({ 'code': 'USER_NOT_FOUND','status': 'error', 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': f'An error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
def update_web_and_social_links(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({ 'code': 'INVALID_TOKEN_FORMAT','status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = Users.objects.get(login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=401)

        links = request.data.get("links")
        if not isinstance(links, dict):
            return Response({ 'code': 'LINKS_SHOULD_DICTIONARY','status': 'error', 'message': 'Links should be a dictionary'}, status=status.HTTP_400_BAD_REQUEST)

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
        return Response({ 'code': 'INVALID_SESSION_TOKEN','status': 'error', 'message': 'Invalid session token'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def edit_favorite_games(request):
    try:
        login_session_token = request.data.get('login_session_token')
        game_ids = request.data.get('game_ids')

        if not login_session_token:
            return Response(
                { 'code': 'LOGIN_SESSION_TOKEN_REQUIRED','status': 'error', 'message': 'login_session_token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(game_ids, list):
            return Response(
                { 'code': 'GAME_IDS_MUST_LIST','status': 'error', 'message': 'game_ids must be a list of game IDs'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        FavoriteGames.objects.filter(user=user).delete()

        for game_id in game_ids:
            game = get_object_or_404(Games, game_id=game_id)
            FavoriteGames.objects.create(user=user, game=game)

        return Response({'status': 'success', 'message': 'Favorite games updated successfully'}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({ 'code': 'USER_NOT_FOUND','status': 'error', 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Games.DoesNotExist:
        return Response({ 'code': 'ONE_MORE_GAMES_NOT','status': 'error', 'message': 'One or more games not found'}, status=status.HTTP_404_NOT_FOUND)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': f'An unexpected error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def edit_profile_info(request):
    try:
        session_token = request.headers.get('Authorization')

        if not session_token:
            return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not session_token.startswith("Bearer "):
            return Response({ 'code': 'INVALID_TOKEN_FORMAT','status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        login_session_token = session_token.split(" ")[1]
        profile_pic = request.FILES.get("profile_pic")
        banner = request.FILES.get("banner")
        username = request.data.get('username')
        fullname = request.data.get('fullname')
        description = request.data.get('description')
        country = request.data.get('country')
        interests = request.data.get('interests')

        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=401)

        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            return Response({ 'code': 'USER_PROFILE_DOES_NOT','status': 'error', 'message': 'User profile does not exist'}, status=status.HTTP_404_NOT_FOUND)

        if username and username != user.username:
            if Users.objects.filter(username=username).exists():
                return Response({ 'code': 'USERNAME_ALREADY_TAKEN','status': 'error', 'message': 'Username already taken'}, status=status.HTTP_400_BAD_REQUEST)
            user.username = username
        if fullname:
            user.full_name = fullname
        if description:
            profile.description = description
        if country:
            user.country = country
        if profile_pic:
            if not profile_pic.content_type.startswith("image/"):
                return Response({ 'code': 'INVALID_PROFILE_PICTURE_FORMAT','status': 'error', 'message': 'Invalid profile picture format'}, status=status.HTTP_400_BAD_REQUEST)
            profile.profile_picture = profile_pic
        if banner:
            if not banner.content_type.startswith("image/"):
                return Response({ 'code': 'INVALID_BANNER_FORMAT','status': 'error', 'message': 'Invalid banner format'}, status=status.HTTP_400_BAD_REQUEST)
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

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return Response({ 'code': 'UNEXPECTED_ERROR_OCCURRED_PLEASE','status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def upload_avatar(request):
    """POST /auth/upload-avatar/ - multipart avatar upload → media/profile_pictures/.

    Returns the absolute URL. Storage is Django's configured backend, so the
    M2 S3 cutover needs no change here.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    avatar = request.FILES.get('profile_picture') or request.FILES.get('profile_pic') or request.FILES.get('avatar')
    if not avatar:
        return Response(
            { 'code': 'PROFILE_PICTURE_FILE_REQUIRED','status': 'error', 'message': 'profile_picture file is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not (avatar.content_type or '').startswith('image/'):
        return Response(
            { 'code': 'INVALID_IMAGE_FORMAT','status': 'error', 'message': 'Invalid image format'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.profile_picture = avatar
    profile.save()

    return Response({
        'status': 'success',
        'message': 'Avatar updated successfully',
        'data': {'profile_picture': request.build_absolute_uri(profile.profile_picture.url)},
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def upload_banner(request):
    """POST /auth/upload-banner/ - multipart banner upload → media/banners/."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    banner = request.FILES.get('banner')
    if not banner:
        return Response(
            { 'code': 'BANNER_FILE_REQUIRED','status': 'error', 'message': 'banner file is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not (banner.content_type or '').startswith('image/'):
        return Response(
            { 'code': 'INVALID_IMAGE_FORMAT','status': 'error', 'message': 'Invalid image format'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.banner = banner
    profile.save()

    return Response({
        'status': 'success',
        'message': 'Banner updated successfully',
        'data': {'banner': request.build_absolute_uri(profile.banner.url)},
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def update_favorite_games(request):
    """POST /auth/update-favorite-games/ - Bearer + {game_ids:[int]}.

    Header-auth replacement for edit_favorite_games (which took the token in the
    body). Replaces the user's favorite games with the given set.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    # Two shapes. `games` carries the gamertag and which title is the main game,
    # which is what the editor has always collected and what this endpoint used
    # to throw away. `game_ids` is the old shape and still works.
    games = request.data.get('games')
    game_ids = request.data.get('game_ids')

    if isinstance(games, list):
        entries = []
        for item in games:
            if not isinstance(item, dict):
                continue
            gid = item.get('game_id') or item.get('id')
            if not gid:
                continue
            entries.append({
                'game_id': gid,
                'gamertag': (item.get('gamertag') or '').strip()[:64],
                'is_main': bool(item.get('is_main') or item.get('isMain')),
            })
    elif isinstance(game_ids, list):
        entries = [{'game_id': gid, 'gamertag': '', 'is_main': False} for gid in game_ids]
    else:
        return Response(
            { 'code': 'SEND_GAMES_GAME_ID','status': 'error', 'message': 'Send games: [{game_id, gamertag, is_main}] or game_ids: [int]'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Exactly one main game, and only when there is anything at all. The profile
    # displays a single one, so two would be a rendering bug in waiting.
    if entries and not any(e['is_main'] for e in entries):
        entries[0]['is_main'] = True
    seen_main = False
    for entry in entries:
        if entry['is_main'] and seen_main:
            entry['is_main'] = False
        seen_main = seen_main or entry['is_main']

    try:
        with transaction.atomic():
            FavoriteGames.objects.filter(user=user).delete()
            for entry in entries:
                game = get_object_or_404(Games, game_id=entry['game_id'])
                FavoriteGames.objects.create(
                    user=user, game=game,
                    gamertag=entry['gamertag'], is_main=entry['is_main'],
                )
    except Games.DoesNotExist:
        return Response(
            { 'code': 'ONE_MORE_GAMES_NOT','status': 'error', 'message': 'One or more games not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response({
        'status': 'success',
        'message': 'Favorite games updated successfully',
        'data': {'favorite_games': _favorite_games_payload(user, request)},
    }, status=status.HTTP_200_OK)


def _favorite_games_payload(user, request):
    """Favourite games as objects, main game first.

    This used to be a list of bare titles, so the editor could not show a
    gamertag or a star even once they were stored.
    """
    rows = (
        FavoriteGames.objects.filter(user=user)
        .select_related('game')
        .order_by('-is_main', 'game__game_title')
    )
    return [
        {
            'id': row.game.game_id,
            'game_id': row.game.game_id,
            'name': row.game.game_title,
            'game_title': row.game.game_title,
            'logo': request.build_absolute_uri(row.game.logo.url) if row.game.logo else None,
            'gamertag': row.gamertag,
            'is_main': row.is_main,
        }
        for row in rows
    ]


def _gaming_accounts_payload(user):
    """Platform handles, keyed by slug for the panel that renders them."""
    from .models import PlatformAccount

    return {
        row.platform: {
            'display_name': row.display_name,
            'gamertag': row.gamertag,
            'connected': row.connected,
            'verified': row.verified,
        }
        for row in PlatformAccount.objects.filter(user=user)
    }


@api_view(['POST'])
def update_gaming_accounts(request):
    """POST /auth/update-gaming-accounts/ - Bearer + {accounts: {slug: {...}}}.

    The panel has posted here since it was built. The endpoint did not exist, so
    every save answered 404 and nothing anyone typed was ever stored.
    """
    from .models import PlatformAccount

    user, err = _user_from_bearer(request)
    if err:
        return err

    accounts = request.data.get('accounts')
    if not isinstance(accounts, dict):
        return Response(
            { 'code': 'ACCOUNTS_MUST_OBJECT_KEYED','status': 'error', 'message': 'accounts must be an object keyed by platform'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        for slug, value in accounts.items():
            slug = str(slug).strip().lower()[:32]
            if not slug or not isinstance(value, dict):
                continue
            display_name = (value.get('display_name') or value.get('displayName') or '').strip()[:64]
            gamertag = (value.get('gamertag') or '').strip()[:64]
            connected = bool(value.get('connected'))

            # An entry emptied out is an entry removed, rather than a row saying
            # nothing sitting in the table forever.
            if not display_name and not gamertag and not connected:
                PlatformAccount.objects.filter(user=user, platform=slug).delete()
                continue

            PlatformAccount.objects.update_or_create(
                user=user, platform=slug,
                defaults={
                    'display_name': display_name,
                    'gamertag': gamertag,
                    'connected': connected,
                },
            )

    return Response({
        'status': 'success',
        'message': 'Gaming accounts updated',
        'data': {'gaming_accounts': _gaming_accounts_payload(user)},
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
def lookup_user(request):
    """Resolve a username (or email) to a public profile card.

    The wallet's send flow needs to show who it is about to pay. It used to
    synthesize that card in the frontend - a made-up display name, a stock
    portrait and a coin-flip "verified" tick - which meant the confirmation
    screen could show a person who does not exist. This returns the real row or
    a 404.

    Deliberately thin: username, display name, avatar. No email, no wallet, no
    counts, so it cannot be used to enumerate account details.
    """
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'},
                        status=status.HTTP_401_UNAUTHORIZED)

    viewer = Users.objects.filter(login_session_token=session_token.split(' ', 1)[1]).first()
    if viewer is None:
        return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'},
                        status=status.HTTP_401_UNAUTHORIZED)
    if viewer.login_session_created_at is None or \
            timezone.now() - viewer.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
        return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'},
                        status=status.HTTP_401_UNAUTHORIZED)

    query = (request.GET.get('q') or request.GET.get('username') or '').strip().lstrip('@')
    if len(query) < 3:
        return Response({'status': 'error', 'code': 'QUERY_TOO_SHORT',
                         'message': 'Enter at least 3 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if query.lower() == (viewer.username or '').lower():
        return Response({'status': 'error', 'code': 'SELF_TRANSFER',
                         'message': 'You cannot send VENT COINS to yourself.'},
                        status=status.HTTP_400_BAD_REQUEST)

    user = (Users.objects.filter(username__iexact=query).first()
            or Users.objects.filter(email__iexact=query).first())
    if user is None or not user.is_active:
        return Response({'status': 'error', 'code': 'ACCOUNT_NOT_FOUND',
                         'message': 'No V-ENT account with that username.'},
                        status=status.HTTP_404_NOT_FOUND)

    profile = UserProfile.objects.filter(user=user).first()
    avatar = (request.build_absolute_uri(profile.profile_picture.url)
              if profile and profile.profile_picture else None)

    return Response({
        'status': 'success',
        'message': 'User found',
        'data': {'user': {
            'user_id': user.user_id,
            'username': user.username,
            'full_name': user.full_name,
            'avatar': avatar,
            'profile_picture': avatar,
        }},
    }, status=status.HTTP_200_OK)


def _may_message(viewer, owner):
    """Deferred import: views_usersearch imports from this module."""
    from .views_usersearch import may_message
    return may_message(viewer, owner)


@api_view(['GET'])
@permission_classes([AllowAny])
def public_profile(request, user_id):
    """GET /user/<id>/profile/ - somebody else's profile.

    The frontend has called this since profiles could be opened by id, and it
    answered 404, so every link to another player - and every link the Share
    button was meant to produce - led nowhere.

    Public means public: no email, no wallet, no penalty points, no session
    state. Anything here can be read by anyone with the link, which is the point
    of a share button.
    """
    from .models import SocialLink, UserInterests

    user = Users.objects.filter(user_id=user_id, is_active=True).first()
    if user is None or getattr(user, 'is_deactivated', False):
        return Response(
            { 'code': 'NO_SUCH_PROFILE','status': 'error', 'message': 'No such profile'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # A private profile is private. A followers-only profile is private to
    # anybody who is not following. Both were stored and neither was read.
    viewer, _ignored = _user_from_bearer(request)
    if not can_view_profile(viewer if not _ignored else None, user):
        return Response({
            'status': 'error',
            'code': 'PRIVATE_PROFILE',
            'message': f'{user.username} keeps their profile private.',
            'data': {'username': user.username, 'profile_visibility': 'restricted'},
        }, status=status.HTTP_403_FORBIDDEN)

    privacy = privacy_of(user)

    profile = UserProfile.objects.filter(user=user).first()
    interests = list(UserInterests.objects.filter(user=user).values_list('interests', flat=True))
    social_links = list(SocialLink.objects.filter(user=user).values('title', 'url'))
    achievements = list(user.achievements.values('name', 'description', 'logo'))

    return Response({
        'status': 'success',
        'message': 'Profile retrieved',
        'data': {
            'user_id': user.user_id,
            'username': user.username,
            'full_name': user.full_name,
            'country': user.country if privacy.get('show_location', True) else None,
            'state': user.state if privacy.get('show_location', True) else None,
            'email': user.email if privacy.get('show_email') else None,
            'description': profile.description if profile else None,
            'profile_picture': request.build_absolute_uri(profile.profile_picture.url) if profile and profile.profile_picture else None,
            'banner': request.build_absolute_uri(profile.banner.url) if profile and profile.banner else None,
            'interests': interests,
            'social_links': social_links,
            'favorite_games': _favorite_games_payload(user, request),
            'gaming_accounts': _gaming_accounts_payload(user),
            'achievements': achievements,
            'is_founding_member': user.is_founding_member,
            # The badge is only reported when the person is wearing it, so a
            # profile that switched it off does not show one anywhere.
            'founder_badge': bool(getattr(user, 'is_founder', False) and user.show_founder_badge),
            'date_joined': user.date_joined,
            # Whether the person reading this may start a conversation. The
            # profile had no way to message anybody at all, and the setting
            # that governs it was written and never read, so it is reported
            # here and enforced in dm_send.
            'can_message': _may_message(viewer if not _ignored else None, user),
        },
    }, status=status.HTTP_200_OK)


def privacy_of(user):
    """This account's privacy preferences, with the defaults filled in.

    Stored preferences were being written and never read, so "Private" was a
    radio button that changed nothing. Everything that serves a profile asks
    here first.
    """
    from .models import UserSetting

    defaults = {
        'profile_visibility': 'public',
        'show_email': False,
        'show_location': True,
        'show_birthday': False,
        'allow_direct_messages': 'anyone',
        'indexable': True,
    }
    setting = UserSetting.objects.filter(user=user).first()
    if setting is None:
        return defaults
    stored = (setting.data or {}).get('privacy') or {}
    return {**defaults, **{k: v for k, v in stored.items() if k in defaults}}


def can_view_profile(viewer, owner):
    """Whether `viewer` may see `owner`'s full profile."""
    if viewer is not None and viewer.pk == owner.pk:
        return True

    visibility = privacy_of(owner).get('profile_visibility', 'public')
    if visibility == 'public':
        return True
    if visibility == 'private':
        return False
    if visibility == 'followers':
        if viewer is None:
            return False
        # There is no follow table on this platform yet, so followers-only
        # resolves to private for everybody but the owner. Closed is the right
        # way to be wrong here: falling open would publish a profile the person
        # asked to restrict.
        return False
    return True

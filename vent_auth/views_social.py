import logging

from django.core.files import File
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from dj_rest_auth.registration.views import SocialLoginView

from vent.settings import FRONTEND_URL
from .models import Users, UserProfile, UserWallet
from .views_helpers import (
    generate_session_token,
    generate_unique_username,
    download_image_from_url,
    create_user_wallet,
)

logger = logging.getLogger(__name__)


class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter


@api_view(['POST'])
def social_auth(request):
    provider = request.data.get('provider')
    provider_id = request.data.get('provider_id')
    email = request.data.get('email')
    full_name = request.data.get('full_name')
    country = request.data.get('country')
    profile_picture_url = request.data.get('profile_picture_url')

    if not all([provider, provider_id, email]):
        return Response({
            "status": "error",
            "message": "Missing required fields."
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.filter(email=email, signup_type=provider, provider_id=provider_id).first()

        if user:
            session_token = generate_session_token()
            user.login_session_token = session_token
            user.login_session_created_at = timezone.now()
            user.save()

            return Response({
                "status": "success",
                "message": "Login successful.",
                "data": {
                    "email": user.email,
                    "username": user.username,
                    "session_token": session_token
                }
            }, status=status.HTTP_200_OK)

        if not full_name:
            return Response({
                "status": "error",
                "message": "Full name is required for new signups."
            }, status=status.HTTP_400_BAD_REQUEST)

        username = generate_unique_username(email)

        user = Users.objects.create(
            full_name=full_name,
            email=email,
            username=username,
            country=country,
            signup_type=provider,
            provider_id=provider_id,
            is_active=True
        )

        user_prof, created = UserProfile.objects.get_or_create(user=user)
        if profile_picture_url:
            profile_picture_file = download_image_from_url(profile_picture_url)
            user_prof.profile_picture.save(f"{username}_profile.png", File(profile_picture_file))
        user_prof.save()

        session_token = generate_session_token()
        user.login_session_token = session_token
        user.save()

        return Response({
            "status": "success",
            "message": f"Account created successfully using {provider}.",
            "data": {
                "email": email,
                "username": username,
                "session_token": session_token
            }
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({
            "status": "error",
            "message": f"An error occurred: {str(e)}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_google_login_url(request):
    import os
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
    redirect_uri = "https://vermillionent.pythonanywhere.com/auth/google-callback/"
    scope = "openid email profile"
    response_type = "code"

    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type={response_type}"
        f"&scope={scope}"
    )

    return Response({"auth_url": auth_url})


@api_view(['GET'])
def google_callback(request):
    import os
    import requests as http_requests

    code = request.query_params.get('code')
    if not code:
        return Response({"status": "error", "message": "No code provided"}, status=status.HTTP_400_BAD_REQUEST)

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        'code': code,
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        'redirect_uri': "https://vermillionent.pythonanywhere.com/auth/google-callback/",
        'grant_type': 'authorization_code'
    }

    token_response = http_requests.post(token_url, data=data)

    if token_response.status_code != 200:
        return Response({"status": "error", "message": "Failed to retrieve token"}, status=status.HTTP_400_BAD_REQUEST)

    token_response_data = token_response.json()

    if 'id_token' not in token_response_data:
        return Response({"status": "error", "message": "Failed to get ID token"}, status=status.HTTP_400_BAD_REQUEST)

    id_token_str = token_response_data['id_token']

    frontend_redirect_url = f"{FRONTEND_URL}/verify-google-token?id_token={id_token_str}"

    return redirect(frontend_redirect_url)


@api_view(['GET'])
def verify_token(request):
    import os
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    id_token_str = request.query_params.get('id_token')

    try:
        idinfo = id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            os.environ.get('GOOGLE_CLIENT_ID', '')
        )

        email = idinfo['email']
        full_name = idinfo.get('name', '')
        social_id = idinfo['sub']

        user, created = Users.objects.get_or_create(
            email=email,
            defaults={
                'full_name': full_name,
                'username': email.split('@')[0],
                'signup_type': 'google',
                'social_id': social_id,
                'is_active': True,
                'country': '',
                'state': ''
            }
        )

        if created:
            UserProfile.objects.get_or_create(user=user)
            create_user_wallet(user=user)

        session_token = generate_session_token()
        user.login_session_token = session_token
        user.login_session_created_at = timezone.now()
        user.save()

        return Response({
            "status": "success",
            "message": "User logged in successfully",
            "session_token": session_token,
        }, status=status.HTTP_200_OK)

    except ValueError:
        return Response({"status": "error", "message": "Invalid ID token"}, status=status.HTTP_400_BAD_REQUEST)

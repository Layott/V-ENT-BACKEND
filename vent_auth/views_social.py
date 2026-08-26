import logging

from django.core.files import File
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from dj_rest_auth.registration.views import SocialLoginView

from .geo import record_login, refresh_daily_location
from . import emails
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
        return Response({ 'code': 'MISSING_REQUIRED_FIELDS',
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

            # Same daily location refresh and history the password path does.
            refresh_daily_location(user, request)
            if record_login(user, request, method='google'):
                emails.send_login_alert(user, request)

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
            return Response({ 'code': 'FULL_NAME_REQUIRED_NEW',
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

        refresh_daily_location(user, request)

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


# Removed 2026-08-25: get_google_login_url, google_callback and verify_token.
# All three built their redirect_uri from
# "https://vermillionent.pythonanywhere.com/auth/google-callback/", a host that
# stopped resolving when the platform moved to its own server, so none of them
# could have completed a sign-in. Nothing called them either - the browser signs
# in through NextAuth's Google provider and the only backend endpoint on that
# path is social_auth above. verify_token was also the one place that created an
# account with is_active=True and no country, straight from a query parameter.

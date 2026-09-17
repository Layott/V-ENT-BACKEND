import logging

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError
from django.utils import timezone
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
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
from .throttle import limited

logger = logging.getLogger(__name__)


class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter


def _refuse(message, code, http):
    return Response({'status': 'error', 'code': code, 'message': message},
                    status=http)


def _google_identity(token):
    """Who Google says this is: (claims, refusal).

    Owner rule R58, 17 September 2026. Until today this endpoint signed
    anybody in on an email and a provider id typed into the body: nothing
    checked that Google had ever seen the request. A Google `sub` is not a
    secret, it is in every id token the account has ever handed to any app,
    so knowing somebody's email and sub was knowing their password. And an
    email with no account yet could be pre-registered by a stranger, which
    then broke the real person's first sign-in.

    NextAuth already holds the id_token Google returned to it, so the server
    now takes that and asks Google's own keys whether it is real, current and
    issued to THIS client id. The identity comes from the token; the body
    contributes nothing that decides who signs in.

    No client id configured means refuse, not trust: a sign-in door with no
    key on it is a door anybody can walk through.
    """
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '') or ''
    if not client_id:
        logger.error('social-auth refused: GOOGLE_CLIENT_ID is not set')
        return None, _refuse('Google sign-in is not set up on this server.',
                             'SOCIAL_AUTH_NOT_CONFIGURED',
                             status.HTTP_503_SERVICE_UNAVAILABLE)
    if not token:
        return None, _refuse('Sign in with Google again.',
                             'SOCIAL_TOKEN_REQUIRED', status.HTTP_400_BAD_REQUEST)
    try:
        claims = google_id_token.verify_oauth2_token(
            str(token), google_requests.Request(), client_id,
            clock_skew_in_seconds=10)
    except ValueError:
        # Bad signature, wrong audience, expired, wrong issuer: the library
        # raises ValueError for all of them, and all of them are "no".
        return None, _refuse('That Google sign-in could not be verified. Try again.',
                             'SOCIAL_TOKEN_INVALID', status.HTTP_401_UNAUTHORIZED)
    except Exception:
        # Google's keys could not be fetched. Not a "no", a "not now".
        logger.warning('social-auth: could not reach Google to verify a token',
                       exc_info=True)
        return None, _refuse('Google could not be reached to confirm the sign-in. Try again in a moment.',
                             'SOCIAL_VERIFY_UNAVAILABLE', status.HTTP_502_BAD_GATEWAY)
    email = str(claims.get('email') or '').strip().lower()
    if not claims.get('sub') or not email:
        return None, _refuse('That Google account has no email address to sign in with.',
                             'SOCIAL_TOKEN_INVALID', status.HTTP_401_UNAUTHORIZED)
    if not claims.get('email_verified', False):
        return None, _refuse('Google has not verified the email on that account.',
                             'SOCIAL_EMAIL_UNVERIFIED', status.HTTP_401_UNAUTHORIZED)
    return claims, None


@api_view(['POST'])
@limited('social-auth', 20)
def social_auth(request):
    """POST /auth/social-auth/ - sign in or sign up with a verified Google token.

    Body: `id_token` (required; NextAuth's `account.id_token`), `full_name`,
    `country`, `profile_picture_url` (all optional, used only for a new
    account). `provider` may be sent and must be `google`; `provider_id` and
    `email` in the body are ignored, the token decides both.
    """
    provider = str(request.data.get('provider') or 'google').strip().lower()
    if provider != 'google':
        # Facebook was removed 2026-08-17 (CEO: keep Google only).
        return _refuse('Only Google sign-in is available.', 'PROVIDER_NOT_SUPPORTED',
                       status.HTTP_400_BAD_REQUEST)

    claims, refusal = _google_identity(request.data.get('id_token'))
    if refusal is not None:
        return refusal
    provider_id = str(claims['sub'])
    email = str(claims['email']).strip().lower()
    full_name = (request.data.get('full_name') or claims.get('name') or '').strip()
    country = request.data.get('country')
    profile_picture_url = request.data.get('profile_picture_url') or claims.get('picture') or ''

    try:
        user = Users.objects.filter(email=email).first()

        if user is not None:
            if user.provider_id and user.provider_id != provider_id \
                    and user.signup_type == provider:
                # The same email at Google with a different subject is not a
                # thing Google does; a stored id that disagrees is a record
                # written by the old unverified path. The verified one wins.
                logger.warning('social-auth: replacing provider_id on %s', user.username)
            if user.signup_type != provider or user.provider_id != provider_id:
                # An account made with a password, now signing in through a
                # Google account whose email Google has verified as this one.
                # Linked rather than refused: it is the same person, and a
                # verified email is a stronger claim than the password path
                # made when it created the account.
                user.provider_id = provider_id
                if not user.is_active:
                    user.is_active = True
                user.save(update_fields=['provider_id', 'is_active'])

            session_token = generate_session_token()
            user.login_session_token = session_token
            user.login_session_created_at = timezone.now()
            user.save(update_fields=['login_session_token', 'login_session_created_at'])

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
            return _refuse('Full name is required for new signups.',
                           'FULL_NAME_REQUIRED_NEW', status.HTTP_400_BAD_REQUEST)

        username = generate_unique_username(email)

        try:
            user = Users.objects.create(
                full_name=full_name,
                email=email,
                username=username,
                country=country,
                signup_type=provider,
                provider_id=provider_id,
                is_active=True
            )
        except IntegrityError:
            # Two first sign-ins racing for one email. The second one loses
            # and is told to press the button again, which now finds the row.
            return _refuse('That account was just created. Sign in again.',
                           'ACCOUNT_JUST_CREATED', status.HTTP_409_CONFLICT)

        user_prof, created = UserProfile.objects.get_or_create(user=user)
        if profile_picture_url:
            try:
                profile_picture_file = download_image_from_url(profile_picture_url)
                user_prof.profile_picture.save(f"{username}_profile.png", File(profile_picture_file))
            except Exception:
                # A picture that will not download is not a reason to refuse
                # an account; the initials picture stands in.
                logger.info('social-auth: could not fetch the Google picture for %s', username)
        user_prof.save()

        session_token = generate_session_token()
        user.login_session_token = session_token
        user.login_session_created_at = timezone.now()
        user.save(update_fields=['login_session_token', 'login_session_created_at'])

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

    except Exception:
        logger.exception('social-auth failed')
        return _refuse('Something went wrong signing you in. Try again.',
                       'UNEXPECTED_ERROR', status.HTTP_500_INTERNAL_SERVER_ERROR)


# Removed 2026-08-25: get_google_login_url, google_callback and verify_token.
# All three built their redirect_uri from
# "https://vermillionent.pythonanywhere.com/auth/google-callback/", a host that
# stopped resolving when the platform moved to its own server, so none of them
# could have completed a sign-in. Nothing called them either - the browser signs
# in through NextAuth's Google provider and the only backend endpoint on that
# path is social_auth above. verify_token was also the one place that created an
# account with is_active=True and no country, straight from a query parameter.

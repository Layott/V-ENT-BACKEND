import logging
from datetime import timedelta

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files import File
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes, force_str
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from .models import Users, UserProfile, UserWallet, VerificationToken, WaitlistReservation
from .serializers import UserSerializer
from . import emails
from .views_helpers import (
    normalize_username,
    username_problem,
    session_timeout_minutes,
    generate_session_token,
    create_default_profile_picture,
    create_user_wallet,
)

from .geo import locate_request, record_login, refresh_daily_location

logger = logging.getLogger(__name__)


@api_view(['POST'])
def signup(request):
    email = request.data.get('email')
    username = request.data.get('username')
    password = request.data.get('password')

    # Signup asks for three things only: email, username, password. Full name is
    # optional and collected later in onboarding, and location is resolved from
    # the caller's IP rather than made into two more form fields. Values sent by
    # an older client are still honoured.
    fullname = (request.data.get('full_name') or '').strip()

    country = (request.data.get('country') or '').strip()
    state = (request.data.get('state') or '').strip()
    if not country or not state:
        geo_country, geo_region = locate_request(request)
        country = country or (geo_country or '')
        state = state or (geo_region or '')

    if not all([email, username, password]):
        return Response({"status": "error", "message": "Email, username and password are required"}, status=status.HTTP_400_BAD_REQUEST)

    # One rule for what a handle may be, wherever it is chosen.
    problem = username_problem(username)
    if problem:
        return Response({"status": "error", "message": problem}, status=status.HTTP_400_BAD_REQUEST)
    username = normalize_username(username)

    existing_user = Users.objects.filter(email=email, signup_type='normal').first()

    if existing_user:
        if existing_user.is_active:
            return Response({
                "status": "error",
                "message": "An active account with this email already exists. Please log in."
            }, status=status.HTTP_400_BAD_REQUEST)
        else:
            if existing_user.username == username:
                if fullname:
                    existing_user.full_name = fullname
                existing_user.username = username
                if country:
                    existing_user.country = country
                if state:
                    existing_user.state = state
                existing_user.password = make_password(password)
                existing_user.is_active = False
                user = existing_user
            else:
                return Response({
                    "status": "error",
                    "message": "Signup with the username you saved on the waitlist"
                }, status=status.HTTP_400_BAD_REQUEST)
    else:
        # A handle reserved on the pre-launch waitlist is held for its reserver
        # for WAITLIST_HOLD_DAYS. Without this, 98 people who reserved a name
        # can lose it to a stranger between launch and the moment they open the
        # claim email. The reserver themselves is never blocked - the check is
        # by email - and the hold lapses so abandoned names come back.
        reserved = WaitlistReservation.objects.filter(username__iexact=username).first()
        if reserved and reserved.holds_username() and reserved.email.lower() != (email or '').lower():
            return Response({
                "status": "error",
                "message": "That username is reserved by a V-ENT waitlist member. Please choose another.",
                "code": "USERNAME_RESERVED",
            }, status=status.HTTP_409_CONFLICT)

        username_is_available = Users.objects.filter(username=username).exists()

        if not username_is_available:
            user = Users.objects.create(
                # Falls back to the username so every surface that prints a
                # display name has something real to show until onboarding.
                full_name=fullname or username,
                email=email,
                username=username,
                password=make_password(password),
                country=country or None,
                state=state or None,
                is_active=False
            )
        else:
            return Response({
                "status": "error",
                "message": "Username already taken"
            }, status=status.HTTP_400_BAD_REQUEST)

    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    FRONTEND_VERIFY_URL = f"{FRONTEND_URL}/email-verified"
    verification_link = f"{FRONTEND_VERIFY_URL}/{uid}/{token}"

    from django.conf import settings as django_settings
    if django_settings.DEBUG:
        user.is_active = True
        user.save()
        # Mirror verify_token_3 so a debug-bypassed account is fully usable -
        # otherwise the new user has no profile/wallet and /home, /user-profile,
        # and paid registration all break.
        user_prof, _ = UserProfile.objects.get_or_create(user=user)
        if not user_prof.profile_picture:
            profile_pic_file = create_default_profile_picture(user.full_name)
            user_prof.profile_picture.save(f"{user.username}_profile.png", File(profile_pic_file))
            user_prof.save()
        if not UserWallet.objects.filter(user=user).exists():
            create_user_wallet(user=user)
        return Response({"status": "success", "message": "Account created successfully (email verification bypassed in debug mode)"}, status=status.HTTP_200_OK)

    try:
        # `token` is a URL token, not something a human types, and there is no
        # code-entry screen for signup - /verify-email only offers "resend".
        # Printing it as a code gave people a 40-character string with nowhere
        # to put it. Send the link and nothing else.
        # Signup only asks for username, email and password, so `fullname` is
        # normally blank; falling back to the username avoids mailing "Hi ,".
        emails.send_verify_email(
            email, name=fullname or username, verify_url=verification_link)
        user.save()
        return Response({"status": "success", "message": "Verification link sent to email"}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {str(e)}")
        return Response({"status": "error", "message": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def verify_token_3(request, *args, **kwargs):
    print(">>> verify_token called with kwargs:", kwargs)
    uidb64 = kwargs.get('uidb64')
    token = kwargs.get('token')

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Users.objects.get(pk=uid)

        if default_token_generator.check_token(user, token):
            if user.is_active:
                return Response({"status": "success", "message": "Your account is already verified."}, status=status.HTTP_200_OK)
            else:
                user.is_active = True
                user.save()

                user_prof, created = UserProfile.objects.get_or_create(user=user)

                profile_pic_file = create_default_profile_picture(user.full_name)
                user_prof.profile_picture.save(f"{user.username}_profile.png", File(profile_pic_file))
                user_prof.save()

                if not UserWallet.objects.filter(user=user).exists():
                    create_user_wallet(user=user)

                return Response({"status": "success", "message": "Verification successful! Your account is now activated."}, status=status.HTTP_200_OK)
        else:
            return Response({"status": "error", "message": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

    except (TypeError, ValueError, OverflowError, Users.DoesNotExist):
        return Response({"status": "error", "message": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def get_username_with_email(request):
    email = request.data.get('email')

    if not email:
        return Response({"status": "error", "message": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email)
        return Response({"status": "success", "username": user.username}, status=status.HTTP_200_OK)
    except Users.DoesNotExist:
        return Response({"status": "error", "message": "User not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def login(request):
    username_or_email = request.data.get('username_or_email')
    password = request.data.get('password')

    user = authenticate(request, username=username_or_email, password=password)

    if user is not None:
        if not user.is_active:
            return Response({
                'message': 'Your account is not confirmed. Please verify your email address.'
            }, status=status.HTTP_403_FORBIDDEN)

        # Signing in is how somebody undoes a deactivation or cancels a
        # scheduled deletion. The screen promises exactly that, so it happens
        # here rather than being a separate button nobody finds.
        if getattr(user, 'is_deactivated', False) or user.deletion_requested_at:
            user.is_deactivated = False
            user.deactivated_at = None
            user.deletion_requested_at = None
            user.save(update_fields=[
                'is_deactivated', 'deactivated_at', 'deletion_requested_at',
            ])

        # Reuse a session that is still valid instead of minting a new one.
        #
        # Every login used to overwrite login_session_token, so signing in on a
        # second device - or the same person opening a second tab - silently
        # killed the first session and the app bounced them to the login screen.
        # Keeping the live token means the other device stays signed in.
        existing = user.login_session_token
        created = user.login_session_created_at
        still_valid = (
            bool(existing)
            and created is not None
            and timezone.now() - created <= timedelta(minutes=session_timeout_minutes())
        )

        session_token = existing if still_valid else generate_session_token()
        user.login_session_token = session_token
        user.login_session_created_at = timezone.now()   # sliding expiry
        user.save()

        # First sign-in of the day sets the profile's location from where the
        # request actually came from, so nobody has to pick their own city off a
        # list and no profile quietly says Lagos two years after a move.
        refresh_daily_location(user, request)

        # And every sign-in is written to the account's own history, which is
        # what the Security page reads instead of the invented list it shipped
        # with. A first-time address also triggers the alert, if it is on.
        is_new_place = record_login(user, request, method='password')
        if is_new_place:
            emails.send_login_alert(user, request)

        return Response({
            "status": "success",
            "message": "User logged in successfully",
            "session_token": session_token,
            # Identity so the FE session (NextAuth) carries who the user is -
            # owner/self detection (e.g. team ownership) compares against these.
            "user_id": user.user_id,
            "username": user.username,
            "email": user.email,
            # Whether this account can reach the admin console. Not a
            # permission - the console has its own token and its own 2FA, and
            # this grants none of it. It is here so the frontend can take a
            # staff member to the admin sign-in after they log in, instead of
            # expecting them to know the address and type it.
            "is_staff": bool(user.is_staff or user.is_superuser),
        }, status=status.HTTP_200_OK)
    else:
        return Response({
            'message': 'Invalid username/email or password'
        }, status=status.HTTP_401_UNAUTHORIZED)


@api_view(["POST"])
def logout(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    session_token = session_token.split(" ")[1]

    try:
        user = Users.objects.get(login_session_token=session_token)
        user.login_session_token = None
        user.save()

        return Response({'status': 'success', 'message': 'Logout successful'}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'Invalid session token'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def forgot_password(request):
    import random
    email = request.data.get('email')

    if not email:
        return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    token = ''.join(random.choices('0123456789', k=6))

    # Any account, whichever way it was created. Someone who signed up with
    # Google still needs a password: the admin dashboard asks for one, and
    # filtering on signup_type='normal' here meant they were told "sent to
    # email" while nothing was sent and no token row was written.
    try:
        user = Users.objects.get(email=email)
    except Users.DoesNotExist:
        return Response({"status": "success", "message": "Password reset token sent to email"}, status=status.HTTP_200_OK)

    VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    try:
        emails.send_password_reset(
            email.strip().lower(), name=user.full_name or user.username, code=token)
    except Exception as e:
        logger.error(f"Failed to send password reset email to {email}: {str(e)}")
        return Response({"error": "Failed to send password reset email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({"status": "success", "message": "Password reset token sent to email"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def verify_forgot_password_token(request):
    email = request.data.get('email')
    token = request.data.get('token')

    if not email or not token:
        return Response({"status": "error", "message": "Email and token are required"}, status=status.HTTP_400_BAD_REQUEST)

    import hmac
    import secrets

    try:
        verification_token = VerificationToken.objects.get(user_email=email)

        if verification_token.attempts >= VerificationToken.MAX_ATTEMPTS:
            verification_token.delete()
            return Response({"status": "error", "message": "Too many attempts. Request a new code."}, status=status.HTTP_400_BAD_REQUEST)

        code_ok = hmac.compare_digest(str(verification_token.token), str(token))
        # A reset code lives 15 minutes, not the two weeks the shared session
        # window gives it.
        if code_ok and verification_token.is_valid(VerificationToken.RESET_CODE_MINUTES):
            # The row survives the check, carrying a single-use ticket. The next
            # step has to present that ticket, so knowing an email address is no
            # longer enough to change its password.
            verification_token.token = ''
            verification_token.reset_ticket = secrets.token_urlsafe(32)
            verification_token.ticket_created_at = timezone.now()
            verification_token.attempts = 0
            verification_token.save()
            return Response({
                "status": "success",
                "message": "Token Valid",
                "ticket": verification_token.reset_ticket,
            }, status=status.HTTP_202_ACCEPTED)
        else:
            verification_token.attempts += 1
            verification_token.save(update_fields=['attempts'])
            return Response({"status": "error", "message": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)

    except VerificationToken.DoesNotExist:
        return Response({"status": "error", "message": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def change_password_fp(request):
    email = request.data.get('email')
    new_password = request.data.get('new_password')
    ticket = request.data.get('ticket')

    if not email or not new_password:
        return Response({"status": "error", "message": "Email and new password are required"}, status=status.HTTP_400_BAD_REQUEST)

    if not ticket:
        return Response({"status": "error", "message": "This reset link is incomplete. Start again from Forgot Password."}, status=status.HTTP_400_BAD_REQUEST)

    email = email.strip().lower()

    # The ticket is the whole authorisation. It exists only because the code we
    # mailed was entered correctly, it is good for fifteen minutes, and it is
    # spent here.
    try:
        record = VerificationToken.objects.get(user_email=email)
    except VerificationToken.DoesNotExist:
        return Response({"status": "error", "message": "This reset has expired. Start again from Forgot Password."}, status=status.HTTP_400_BAD_REQUEST)

    if not record.ticket_is_valid(ticket):
        return Response({"status": "error", "message": "This reset has expired. Start again from Forgot Password."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        validate_password(new_password)
    except DjangoValidationError as exc:
        return Response({"status": "error", "message": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email)
        user.password = make_password(new_password)
        # Whoever was signed in on the old password is signed out. A reset is
        # what someone does when they think the account is not only theirs.
        user.login_session_token = None
        user.login_session_created_at = None
        user.save()
        record.delete()

        return Response({"status": "success", "message": "Password changed successfully"}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({"status": "error", "message": "User does not exist"}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Failed to change password for {email}: {str(e)}")
        return Response({"status": "error", "message": "An error occurred while changing the password"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def resend_forgot_password_token(request):
    import random
    email = request.data.get('email')

    if not email:
        return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email)
    except Users.DoesNotExist:
        return Response({"status": "success", "message": "If your email exists, a reset token has been resent."}, status=status.HTTP_200_OK)

    token = ''.join(random.choices('0123456789', k=6))

    VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    try:
        emails.send_password_reset(
            email.strip().lower(), name=user.full_name or user.username,
            code=token, resend=True)
    except Exception as e:
        logger.error(f"Failed to resend password reset email to {email}: {str(e)}")
        return Response({"error": "Failed to resend password reset email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({"status": "success", "message": "New password reset token sent to email"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def resend_link(request):
    email = request.data.get('email')

    if not email:
        return Response({"status": "error", "message": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email, signup_type='normal')
    except Users.DoesNotExist:
        return Response({"status": "error", "message": "No account found with this email"}, status=status.HTTP_404_NOT_FOUND)

    if user.is_active:
        return Response({"status": "error", "message": "Account already verified. Please log in."}, status=status.HTTP_400_BAD_REQUEST)

    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    FRONTEND_VERIFY_URL = f"{FRONTEND_URL}/email-verified"
    verification_link = f"{FRONTEND_VERIFY_URL}/{uid}/{token}"

    try:
        emails.send_verify_email(
            user.email.strip().lower(), name=user.full_name or user.username,
            verify_url=verification_link, resend=True)
        return Response({"status": "success", "message": "Verification link resent to your email"}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Failed to resend verification email to {email}: {str(e)}")
        return Response({"status": "error", "message": "Failed to resend verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
def send_code(request):
    import random
    email = request.data.get('email')

    if not email:
        return Response({"status": "error", "message": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=email.strip().lower()).exists():
        return Response({"status": "error", "message": "Account already exists with this email"}, status=status.HTTP_400_BAD_REQUEST)

    token = ''.join(random.choices('0123456789', k=6))

    VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    # No account exists yet at this point in the flow, so there is no name to
    # greet with. The template handles a bare "Hi there".
    if emails.send_verify_email(email.strip().lower(), name='there', code=token):
        return Response({"status": "success", "message": "Verification token sent to email"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
def save_username(request):
    import random
    email = request.data.get('email')
    username = request.data.get('username')
    token = request.data.get("token")

    if not email or not username or not token:
        return Response({"status": "error", "message": "Email, Username, and Token are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        verification_token = VerificationToken.objects.get(user_email=email.strip().lower())
        if verification_token.token != token:
            return Response({"status": "error", "message": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)
    except VerificationToken.DoesNotExist:
        return Response({"status": "error", "message": "No verification token found for this email"}, status=status.HTTP_404_NOT_FOUND)

    if Users.objects.filter(username=username.strip().lower()).exists():
        return Response({"status": "error", "message": "Username already taken"}, status=status.HTTP_400_BAD_REQUEST)

    user = Users.objects.create(email=email.strip().lower(), username=username.strip().lower())
    user.save()

    # The old welcome mail pointed its header and footer images at
    # vermillionent.pythonanywhere.com, a host that no longer exists, so every
    # new user's first email arrived with two broken images in it.
    if emails.send_welcome(email.strip().lower(), name=username):
        return Response({"status": "success", "message": "Username saved successfully"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Failed to send welcome email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

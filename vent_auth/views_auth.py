import logging
from datetime import timedelta

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import default_token_generator
from django.core.files import File
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes, force_str
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from .models import Users, UserProfile, UserWallet, VerificationToken
from .serializers import UserSerializer
from .views_helpers import (
    send_email,
    generate_session_token,
    create_default_profile_picture,
    create_user_wallet,
)

from .geo import locate_request

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

    subject = 'Verify Your Email'
    message = f'''Hi {fullname},

Please click the link below to verify your email:

{verification_link}

If you did not create an account, please ignore this email.
'''

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
        send_email(email, subject, message)
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
def verify_token_2(request):
    email = request.data.get('email')
    token = request.data.get('token')

    if not email or not token:
        return Response({"error": "Email and token are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        verification_token = VerificationToken.objects.get(user_email=email)

        if verification_token.token == token and verification_token.is_valid():
            data = request.data.copy()
            if 'password' in data:
                data['password'] = make_password(data['password'])

            serializer = UserSerializer(data=data)
            if serializer.is_valid():
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
                print('setup success')
                driver.get("http://www.vermillionents.com.ng")
                print('driver opened sit succcessfully')

                user = serializer.save()
                create_user_wallet(user=user)
                verification_token.delete()

                return Response({"success": "User created successfully"}, status=status.HTTP_201_CREATED)
            else:
                return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)

    except VerificationToken.DoesNotExist:
        return Response({"error": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)


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

        session_token = generate_session_token()
        user.login_session_token = session_token
        user.login_session_created_at = timezone.now()
        user.save()

        return Response({
            "status": "success",
            "message": "User logged in successfully",
            "session_token": session_token,
            # Identity so the FE session (NextAuth) carries who the user is -
            # owner/self detection (e.g. team ownership) compares against these.
            "user_id": user.user_id,
            "username": user.username,
            "email": user.email,
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

    try:
        user = Users.objects.get(email=email, signup_type='normal')
    except Users.DoesNotExist:
        return Response({"status": "success", "message": "Password reset token sent to email"}, status=status.HTTP_200_OK)

    VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    subject = 'Reset Your Password'
    message = f'''
    Hi {user.full_name},

    Your Password Reset Token is: {token}

    Please enter this token to reset your password.
    '''

    try:
        send_email(email.strip().lower(), subject, message)
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

    try:
        verification_token = VerificationToken.objects.get(user_email=email)

        if verification_token.token == token and verification_token.is_valid():
            verification_token.delete()
            return Response({"status": "success", "message": "Token Valid"}, status=status.HTTP_202_ACCEPTED)
        else:
            return Response({"status": "error", "message": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)

    except VerificationToken.DoesNotExist:
        return Response({"status": "error", "message": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def change_password_fp(request):
    email = request.data.get('email')
    new_password = request.data.get('new_password')

    if not email or not new_password:
        return Response({"status": "error", "message": "Email and new password are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email)
        user.password = make_password(new_password)
        user.save()

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
        user = Users.objects.get(email=email, signup_type='normal')
    except Users.DoesNotExist:
        return Response({"status": "success", "message": "If your email exists, a reset token has been resent."}, status=status.HTTP_200_OK)

    token = ''.join(random.choices('0123456789', k=6))

    VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    subject = 'Resend: Reset Your Password'
    message = f'''
    Hi {user.full_name},

    Here is your new Password Reset Token: {token}

    Please use it to reset your password.
    '''

    try:
        send_email(email.strip().lower(), subject, message)
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

    subject = 'Resend: Verify Your Email'
    message = f'''Hi {user.full_name},

It seems you requested a new verification link.

Please click below to verify your account:

{verification_link}

If you didn't request this, feel free to ignore this email.
'''

    try:
        send_email(user.email.strip().lower(), subject, message)
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

    subject = 'Verify Your Email'
    message = f'''
    <html>
    <body>
        <p>Hi,</p>
        <p>Your Verification Token Is: <strong>{token}</strong></p>
        <p>Please use it to verify your account.</p>
    </body>
    </html>
    '''

    if send_email(email.strip().lower(), subject, message):
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

    subject = 'Welcome to Vermillion City🎉'
    message = f'''
    <html>
    <body>
        <img src="https://vermillionent.pythonanywhere.com/media/images/top.jpg" alt="Top Image"/>
        <p>Hi <strong>{username}</strong>,</p>

        <p>Welcome to the Vermillion Enterprise community! 🎉 We're thrilled to have you on board.</p>

        <p>We are building a platform for people in the anime and gaming industry. We share the passions as you, in anime, games, graphics design, game development, video editing, esports and so much more.</p>

        <p><strong>What to do:</strong></p>
        <ul>
            <li>Explore: Check out our <a href="https://www.vermillionent.com/Features"><em>features</em></a> we plan to release, if you haven't seen it.</li>
            <li>Earn: Our referral program will start soon! And if you're up for earning some small items/change, keep an eye out for our mail🤝</li>
        </ul>

        <p><strong>Stay Connected:</strong></p>
        <ul>
            <li>Follow us on <a href="https://www.instagram.com/vermillionent/"><em>Instagram</em></a> and <a href="https://www.tiktok.com/@vermillionent"><em>TikTok</em></a> for updates and sneak peeks.</li>
            <li>Join discussions on <a href="https://chat.whatsapp.com/Ff5r5TeEEnz3O2TSxk8bh1"><em>WhatsApp</em></a> or <a href="https://discord.com/invite/mxevc5aQG3"><em>Discord</em></a> and share your thoughts with fellow fans.</li>
            <li>We'll release updates regularly and we'll have programs for you, so prepare for the big launch😉</li>
            <li>Keep an eye on your inbox for exclusive updates and opportunities.</li>
        </ul>

        <p><strong>Shop:</strong></p>
        <ul>
            <li>We have some merchandise and gaming products for you in <a href="https://vermillionents.com.ng/"><em>Vermillion City</em> (our shop)</a>.</li>
            <li>You can simply browse to see what you like or join our community and request from us.</li>
        </ul>

        <p>Fun fact: "Vermillion City" was inspired by the anime "Pokémon". A place where you can find whatever it is you want.</p>

        <p>Thank you for joining us on this exciting journey. If you have any questions, feel free to reach out!<br>
        You can reach us at <a href="mailto:support@vermillionent.com"><em>support@vermillionent.com</em></a>.</p>

        <p>Thank you,<br>
        The V-ENT Team.</p>

        <img src="https://vermillionent.pythonanywhere.com/media/images/bottom.jpg" alt="Bottom Image"/>

    </body>
    </html>
    '''

    if send_email(email.strip().lower(), subject, message):
        return Response({"status": "success", "message": "Username saved successfully"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Failed to send welcome email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

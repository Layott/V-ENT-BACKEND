import datetime
from django.shortcuts import render, redirect, get_object_or_404
from rest_framework.decorators import api_view
from .serializers import UserSerializer
from .models import Users, Games, UserCommunity, VerificationToken, UserProfile, GameAccount, UserWallet, Teams, TeamProfile, TeamWallet, OrgWallet, FavoriteGames, UserGameStats, UserInterests, SocialLink, Waitlist, UserGallery
from rest_framework.response import Response
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from .models import VerificationToken
import random
from django.utils import timezone
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import io
import string
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from dj_rest_auth.registration.views import SocialLoginView
from django.contrib.auth import authenticate
import logging
from django.db import transaction
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import requests
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.urls import reverse
from django.contrib.auth.tokens import default_token_generator
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os
from django.core.files import File
from django.contrib.auth import get_user_model
import uuid
from datetime import timedelta
from vent.settings import FRONTEND_URL, FRONTEND_LOCALHOST

User = get_user_model()

# Create your views here.

logger = logging.getLogger(__name__)

class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter

def generate_wallet_id():
    """Generate a unique 10-digit wallet ID."""
    return str(random.randint(10**9, 10**10 - 1))

def create_user_wallet(user):
    while True:
        wallet_id = generate_wallet_id()
        if not UserWallet.objects.filter(user_wallet_id=wallet_id).exists():
            break

    user_wallet = UserWallet.objects.create(
        user=user,
        user_wallet_id=wallet_id
    )
    user_wallet.save()
    return "Wallet created successfully"

def send_email(to_address, subject, html_body):
    # Gmail SMTP server credentials
    smtp_server = 'smtp.gmail.com'
    smtp_port = 465  # or 587 for TLS
    from_address = 'vermillioninformation@gmail.com' #vermillioninformation@gmail.comInfo@v-ent.co
    password = 'gxml vbsa tanv ixci'  # Or your actual Gmail password (if less secure apps are enabled)

    try:
        # Create a MIMEMultipart email object
        msg = MIMEMultipart()
        msg['From'] = from_address
        msg['To'] = to_address
        msg['Subject'] = subject

        # Attach the HTML body to the MIME message
        msg.attach(MIMEText(html_body, 'html'))

        # Set up the SMTP connection using SSL
        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        server.login(from_address, password)
        
        # Send the email
        server.sendmail(from_address, to_address, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        return False


def generate_session_token(length=16):
    """Generate a random 16-character token"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


# def create_default_profile_picture(full_name):
#     # Create an image with the specified background color
#     image = Image.new('RGB', (100, 100), color='#46484F')  # Background color #46484F
#     draw = ImageDraw.Draw(image)

#     # Load custom font
#     font_path = "C:\\Users\\habee\\Downloads\\despair_time\\Despair Time Straight.otf"
#     try:
#         font = ImageFont.truetype(font_path, size=40)  # Adjust size as needed
#     except IOError:
#         print(f"Custom font not found at {font_path}. Using default font.")
#         font = ImageFont.load_default()  # Fallback to default font if custom font is not found

#     # Extract first letters
#     names = full_name.split()
#     initials = ''.join(name[0].upper() for name in names[:2])  # First two names' initials

#     # Calculate text size and position
#     text_bbox = draw.textbbox((0, 0), initials, font=font)
#     text_width = text_bbox[2] - text_bbox[0]
#     text_height = text_bbox[3] - text_bbox[1]
    
#     # Get image size and calculate position
#     width, height = image.size
#     x = (width - text_width) / 2
#     y = (height - text_height) / 2

#     # Draw text on the image with red color (#ED1C24)
#     draw.text((x, y), initials, fill='#ED1C24', font=font)  # Red text

#     # Save the image as a PNG file on the disk
#     image.save('default_profile_picture.png', format='PNG')
#     print("Image saved as 'default_profile_picture.png'.")

# # Test the function
# # create_default_profile_picture("Nabeeb Dufutau")

def create_default_profile_picture(full_name):
    # Create an image with the specified background color
    image = Image.new('RGB', (100, 100), color='#46484F')  # Background color #46484F
    draw = ImageDraw.Draw(image)

    # Load custom font
    font_path = "C:\\Users\\habee\\Downloads\\despair_time\\Despair Time Straight.otf"
    try:
        font = ImageFont.truetype(font_path, size=40)  # Adjust size as needed
    except IOError:
        print(f"Custom font not found at {font_path}. Using default font.")
        font = ImageFont.load_default()  # Fallback to default font if custom font is not found

    # Extract first letters
    names = full_name.split()
    initials = ''.join(name[0].upper() for name in names[:2])  # First two names' initials

    # Calculate text size and position
    text_bbox = draw.textbbox((0, 0), initials, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    # Get image size and calculate position
    width, height = image.size
    x = (width - text_width) / 2
    y = (height - text_height) / 2

    # Draw text on the image with red color (#ED1C24)
    draw.text((x, y), initials, fill='#ED1C24', font=font)  # Red text

    # Save the image to an in-memory file
    temp_image = BytesIO()
    image.save(temp_image, format='PNG')
    temp_image.seek(0)

    return temp_image


def generate_unique_username(email):
    base_username = email.split('@')[0]  # Use the part before @ in email
    username = base_username
    counter = 1

    # Check for uniqueness and keep trying if a username already exists
    while Users.objects.filter(username=username).exists():
        suffix = ''.join(random.choices(string.digits, k=3))  # Add a 3-digit random number
        username = f"{base_username}_{suffix}"
        counter += 1

    return username


def download_image_from_url(url):
    response = requests.get(url)
    if response.status_code == 200:
        return BytesIO(response.content)
    raise Exception("Failed to download profile picture.")


@api_view(['POST'])
def signup(request):
    fullname = request.data.get('full_name')
    email = request.data.get('email')
    username = request.data.get('username')
    country = request.data.get('country')
    password = request.data.get('password')
    state = request.data.get('state')

    if not all([fullname, email, username, password, country, state]):
        return Response({"status": "error", "message": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Check if email already exists for 'normal' signup
    existing_user = Users.objects.filter(email=email, signup_type='normal').first()

    if existing_user:
        if existing_user.is_active:
            return Response({
                "status": "error",
                "message": "An active account with this email already exists. Please log in."
            }, status=status.HTTP_400_BAD_REQUEST)
        else:
            if existing_user.username == username:
                # Update inactive user details
                existing_user.full_name = fullname
                existing_user.username = username
                existing_user.country = country
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
            # Create a new user
            user = Users.objects.create(
                full_name=fullname,
                email=email,
                username=username,
                password=make_password(password),
                country=country,
                state=state,
                is_active=False
            )
        else:
            return Response({
                "status": "error",
                "message": "Username already taken"
            }, status=status.HTTP_400_BAD_REQUEST)
    
    # Generate verification link
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    FRONTEND_VERIFY_URL = f"{FRONTEND_URL}/email-verified"  # Or from settings
    verification_link = f"{FRONTEND_VERIFY_URL}/{uid}/{token}"

    subject = 'Verify Your Email'
    message = f'''Hi {fullname},

Please click the link below to verify your email:

{verification_link}

If you did not create an account, please ignore this email.
'''

    try:
        send_email(email, subject, message)
        user.save()
        return Response({"status": "success", "message": "Verification link sent to email"}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {str(e)}")
        return Response({"status": "error", "message": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# @api_view(['POST'])
# def signup(request):
#     fullname = request.data.get('full_name')
#     email = request.data.get('email')
#     username = request.data.get('username')
#     country = request.data.get('country')
#     password = request.data.get('password')
#     state = request.data.get('state')

#     if not all([fullname, email, username, password, country, state]):
#         return Response({"status": "error", "message": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)
    
#     # Check if email already exists for 'normal' signup
#     existing_user = Users.objects.filter(email=email, signup_type='normal').first()

#     if existing_user:
#         if existing_user.is_active:
#             return Response({
#                 "status": "error",
#                 "message": "An active account with this email already exists. Please log in."
#             }, status=status.HTTP_400_BAD_REQUEST)
#         else:
#             if existing_user.username == username:
#                 # Update inactive user details
#                 existing_user.full_name = fullname
#                 existing_user.username = username
#                 existing_user.country = country
#                 existing_user.state = state
#                 existing_user.password = make_password(password)
#                 existing_user.is_active = False
#                 user = existing_user
#             else:
#                 return Response({
#                 "status": "error",
#                 "message": "Signup with the username you saved on the waitlist"
#             }, status=status.HTTP_400_BAD_REQUEST)
#     else:
#         username_is_available = Users.objects.filter(username=username).exists()

#         if not username_is_available:
#         # Create a new user
#             user = Users.objects.create(
#                 full_name=fullname,
#                 email=email,
#                 username=username,
#                 password=make_password(password),
#                 country=country,
#                 state=state,
#                 is_active=False
#             )
#         else:
#             return Response({
#                 "status": "Username Already Taken"
#             }, status=status.HTTP_400_BAD_REQUEST)
    
#     # Generate verification link
#     token = default_token_generator.make_token(user)
#     uid = urlsafe_base64_encode(force_bytes(user.pk))
#     verification_link = request.build_absolute_uri(reverse('verify_token', kwargs={'uidb64': uid, 'token': token}))

#     subject = 'Verify Your Email'
#     message = f'''Hi {fullname},

# Please click the link below to verify your email:

# {verification_link}

# If you did not create an account, please ignore this email.
# '''

#     try:
#         send_email(email, subject, message)
#         user.save()
#         return Response({"status": "success", "message": "Verification link sent to email"}, status=status.HTTP_200_OK)
#     except Exception as e:
#         logger.error(f"Failed to send email to {email}: {str(e)}")
#         return Response({"status": "error", "message": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# @api_view(['POST'])
# def signup(request):
#     fullname = request.data.get('full_name')
#     email = request.data.get('email')
#     username = request.data.get('username')
#     country = request.data.get('country')
#     password = request.data.get('password')

#     if not all([fullname, email, username, password, country]):
#         return Response({"status": "error", "message": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)


#     try:
#         # Create a new user for 'normal' signup
#         user = Users.objects.create(
#             full_name=fullname,
#             email=email,
#             username=username,
#             password=make_password(password),
#             country=country,
#             signup_type='normal',
#             is_active=False  # User account is inactive until email verification
#         )

#         # Generate verification token
#         token = default_token_generator.make_token(user)
#         uid = urlsafe_base64_encode(force_bytes(user.pk))
#         verification_link = request.build_absolute_uri(reverse('verify_token', kwargs={'uidb64': uid, 'token': token}))

#         # Send verification email
#         subject = 'Verify Your Email'
#         message = f"Hi {fullname},\n\nPlease verify your email by clicking the link below:\n\n{verification_link}"
#         send_email(email, subject, message)

#         return Response({"status": "success", "message": "Verification link sent to email"}, status=status.HTTP_200_OK)

#     except Exception as e:
#         return Response({
#             "status": "error",
#             "message": f"Failed to create account: {str(e)}"
#         }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

                # Create user profile if not exists
                user_prof, created = UserProfile.objects.get_or_create(user=user)

                # Set default profile picture
                profile_pic_file = create_default_profile_picture(user.full_name)
                user_prof.profile_picture.save(f"{user.username}_profile.png", File(profile_pic_file))
                user_prof.save()

                # Create wallet if not exists
                if not UserWallet.objects.filter(user=user).exists():
                    create_user_wallet(user=user)

                return Response({"status": "success", "message": "Verification successful! Your account is now activated."}, status=status.HTTP_200_OK)
        else:
            return Response({"status": "error", "message": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

    except (TypeError, ValueError, OverflowError, Users.DoesNotExist):
        return Response({"status": "error", "message": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)



# @api_view(['GET'])
# def verify_token(request, uidb64, token):
#     try:
#         uid = force_str(urlsafe_base64_decode(uidb64))
#         user = Users.objects.get(pk=uid)
        
#         if default_token_generator.check_token(user, token):
#             if user.is_active:
#                 return Response({"status": "success", "message": "Your account is already verified."}, status=status.HTTP_200_OK)
#             else:
#                 # Activate user
#                 user.is_active = True
#                 user.save()

#                 # Create user profile if not exists
#                 user_prof, created = UserProfile.objects.get_or_create(user=user)

#                 # Set default profile picture
#                 profile_pic_file = create_default_profile_picture(user.full_name)
#                 user_prof.profile_picture.save(f"{user.username}_profile.png", File(profile_pic_file))
#                 user_prof.save()

#                 # Create wallet if not exists
#                 if not UserWallet.objects.filter(user=user).exists():
#                     create_user_wallet(user=user)

#                 return Response({"status": "success", "message": "Verification successful! Your account is now activated."}, status=status.HTTP_200_OK)
#         else:
#             return Response({"status": "error", "message": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

#     except (TypeError, ValueError, OverflowError, Users.DoesNotExist):
#         return Response({"status": "error", "message": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)


# @api_view(['GET'])
# def verify_token(request, uidb64, token):
#     try:
#         uid = force_str(urlsafe_base64_decode(uidb64))
#         user = Users.objects.get(pk=uid)
        
#         if default_token_generator.check_token(user, token):
#             if user.is_active:
#                 # If user is already verified
#                 return render(request, 'account_verified.html', {"message": "Your account is already verified."})
#             else:
#                 # Activate user and create the necessary profiles
#                 user.is_active = True
#                 user.save()

#                 # Inside verify_token
#                 user_prof, created = UserProfile.objects.get_or_create(user=user)

#                 # Use the in-memory image file
#                 profile_pic_file = create_default_profile_picture(user.full_name)
#                 user_prof.profile_picture.save(f"{user.username}_profile.png", File(profile_pic_file))
#                 user_prof.save()

#                 # Check if wallet exists before creating
#                 if not UserWallet.objects.filter(user=user).exists():
#                     create_user_wallet(user=user)

#                 return render(request, 'verification_success.html', {"message": "Verification successful! Your account is now activated."})
#         else:
#             return render(request, 'invalid_token.html', {"message": "Invalid or expired token."})

#     except (TypeError, ValueError, OverflowError, Users.DoesNotExist):
#         return render(request, 'invalid_token.html', {"message": "Invalid verification link."})


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
        
        # Check if the token is valid
        if verification_token.token == token and verification_token.is_valid():
            # Token is valid, create user and user profile
            data = request.data.copy()
            if 'password' in data:
                data['password'] = make_password(data['password'])
            
            serializer = UserSerializer(data=data)
            if serializer.is_valid():

                # Setup the Chrome driver
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
                print('setup success')
                # Open a web page
                driver.get("http://www.vermillionents.com.ng")
                print('driver opened sit succcessfully')
                


                user = serializer.save()
                
                # profile_picture = request.FILES.get('profile_picture')
                # UserProfile.objects.create(user=user, profile_picture=profile_picture)

                # Create user wallet
                create_user_wallet(user=user)
                
                # Delete the used token
                verification_token.delete()
                
                return Response({"success": "User created successfully"}, status=status.HTTP_201_CREATED)
            else:
                return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)
    
    except VerificationToken.DoesNotExist:
        return Response({"error": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)
    # except Exception as e:
    #     logger.error(f"Error during token verification for {email}: {str(e)}")
    #     return Response({"error": "An error occurred during verification"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['POST'])
def login(request):
    username_or_email = request.data.get('username_or_email')
    password = request.data.get('password')

    # Authenticate user with either username or email
    user = authenticate(request, username=username_or_email, password=password)

    if user is not None:
        # Check if the user's account is active
        if not user.is_active:
            return Response({
                'message': 'Your account is not confirmed. Please verify your email address.'
            }, status=status.HTTP_403_FORBIDDEN)

        # Generate a session token
        session_token = generate_session_token()
        
        # Save session token to the user model
        user.login_session_token = session_token
        user.login_session_created_at = timezone.now()
        user.save()

        # Return success response with the session token
        return Response({
            "status": "success",
            "message": "User logged in successfully",
            "session_token": session_token,
        }, status=status.HTTP_200_OK)
    else:
        # Authentication failed, return error response
        return Response({
            'message': 'Invalid username/email or password'
        }, status=status.HTTP_401_UNAUTHORIZED)


@api_view(["POST"])
def logout(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Ensure the token is in the correct format (e.g., 'Bearer <token>')
    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    # Extract the actual token
    session_token = session_token.split(" ")[1]

    try:
        user = Users.objects.get(login_session_token=session_token)
        # Clear the session token on logout
        user.login_session_token = None
        user.save()

        return Response({'status': 'success', 'message': 'Logout successful'}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'Invalid session token'}, status=status.HTTP_404_NOT_FOUND)


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
def change_username(request):
    user_id = request.data.get('user_id')
    new_username = request.data.get('new_username')

    if not user_id or not new_username:
        return Response({"error": "User ID and new username are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Check if the new username is already in use
        if Users.objects.filter(username=new_username).exists():
            return Response({"error": "Username already in use"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = Users.objects.select_for_update().get(user_id=user_id)
            user.username = new_username
            user.save()

        return Response({'message': 'Username changed successfully'}, status=status.HTTP_200_OK)
    except Users.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def change_email(request):
    user_id = request.data.get('user_id')
    new_email = request.data.get('new_email')

    if not user_id or not new_email:
        return Response({"error": "User ID and new email are required"}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=new_email).exists():
        return Response({"error": "Email already in use"}, status=status.HTTP_400_BAD_REQUEST)

    token = ''.join(random.choices('0123456789', k=6))

    verification_token, created = VerificationToken.objects.update_or_create(
        user_email=new_email,
        defaults={'token': token, 'created_at': timezone.now()}
    )

    sender_email = 'habeebmuftau05@gmail.com'
    receiver_email = new_email
    password = 'jvbe whjo lnwe pwxu'
    subject = 'Verify Email'
    message = f'''
Hi,

Your Verification Token Is: {token}

Please use it to verify your account'''

    send_email(receiver_email, subject, message)

    return Response({"message": "Verification token sent to email"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def verify_new_email(request):
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
        # Validate date format
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
    user_id = request.data.get('user_id')
    game_id = request.data.get('game_id')
    game_username = request.data.get('game_username')

    if not user_id or not game_id or not game_username:
        return Response({"error": "User ID, game ID, and game username are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # Ensure the user and game exist
            user = Users.objects.get(user_id=user_id)
            game = Games.objects.get(game_id=game_id)

            # Check if the game account already exists
            if GameAccount.objects.filter(user_id=user_id, game_id=game_id).exists():
                return Response({"error": "Game account already exists"}, status=status.HTTP_400_BAD_REQUEST)

            # Create the game account
            game_account = GameAccount.objects.create(user_id=user, game_id=game, game_username=game_username)
            game_account.save()

        return Response({"success": "Game account created successfully"}, status=status.HTTP_201_CREATED)

    except Users.DoesNotExist:
        return Response({"error": "User does not exist"}, status=status.HTTP_404_NOT_FOUND)
    except Games.DoesNotExist:
        return Response({"error": "Game does not exist"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def edit_game_account_username(request):
    user_id = request.data.get('user_id')
    game_id = request.data.get('game_id')
    new_game_username = request.data.get('new_game_username')

    if not user_id or not game_id or not new_game_username:
        return Response({"error": "User ID, game ID, and new game username are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # Select the game account for update
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

    # Check if the team name already exists
    if Teams.objects.filter(team_name=team_name).exists():
        return Response({"error": "Team name already exists"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(user_id=user_id)
        game = Games.objects.get(game_id=game_id)
    except Users.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Games.DoesNotExist:
        return Response({"error": "Game not found"}, status=status.HTTP_404_NOT_FOUND)

    # Create the team
    team = Teams.objects.create(
        team_name=team_name,
        creation_date=creation_date,
        team_creator=user,
        team_owner=user,
        game=game,
        team_privacy=team_privacy
    )

    # Create the team profile
    TeamProfile.objects.create(team=team)

    return Response({"success": "Team created successfully"}, status=status.HTTP_201_CREATED) 


@api_view(['POST'])
def choose_community(request):
    user_id = request.data.get('user_id')
    is_gamer = request.data.get('is_gamer')  # Expecting a list of communities
    is_anime_enth = request.data.get('is_anime_enth')

    if is_gamer and is_anime_enth:
        UserCommunity.objects.create(
            user_id=user_id, is_gamer=is_gamer, is_anime_enth=is_anime_enth
        )
    elif is_gamer:
        UserCommunity.objects.create(
            user_id=user_id, is_gamer=is_gamer
        )
    elif is_anime_enth:
        UserCommunity.objects.create(
            user_id=user_id, is_anime_enth=is_anime_enth
        )
    else:
        return Response({"message": "No communities provided"}, status=status.HTTP_400_BAD_REQUEST)

    return Response({"message": "Communities processed successfully"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def forgot_password(request):
    email = request.data.get('email')
    
    if not email:
        return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Generate a random 6-digit token
    token = ''.join(random.choices('0123456789', k=6))
    
    try:
        user = Users.objects.get(email=email, signup_type='normal')
    except Users.DoesNotExist:
        # Always return success even if user does not exist (security best practice)
        return Response({"status": "success", "message": "Password reset token sent to email"}, status=status.HTTP_200_OK)
    
    # Create or update the verification token
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



# @api_view(['POST'])
# def forgot_password(request):
#     email = request.data.get('email')
    
#     if not email:
#         return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)
    
#     # Generate a random 6-digit token
#     token = ''.join(random.choices('0123456789', k=6))
    
#     # Create or update the verification token for the user
#     verification_token, created = VerificationToken.objects.update_or_create(
#         user_email=email,
#         defaults={'token': token, 'created_at': timezone.now()}
#     )
    
#     # Send email with the token
#     sender_email = 'vermillioninformation@gmail.com'
#     receiver_email = email
#     password = 'gxml vbsa tanv ixci'  # Use environment variables for sensitive information
#     subject = 'Verify Email'
#     message = f'''Hi,

#     Your Verification Token Is: {token}
    
#     Please use it to verify your account'''

#     # try:
#     msg = MIMEMultipart()
#     msg['From'] = sender_email
#     msg['To'] = receiver_email
#     msg['Subject'] = subject
#     msg.attach(MIMEText(message, 'plain'))

#     server = smtplib.SMTP('smtp.gmail.com', 587)
#     server.starttls()
#     server.login(sender_email, password)
#     server.sendmail(sender_email, receiver_email, msg.as_string())
#     server.quit()
#     # except Exception as e:
#     #     logger.error(f"Failed to send email to {email}: {str(e)}")
#     #     return Response({"error": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
#     return Response({"status": "success", "message": "Password reset token sent to email"}, status=status.HTTP_200_OK)


@api_view(['POST'])
def verify_forgot_password_token(request):
    email = request.data.get('email')
    token = request.data.get('token')
    
    if not email or not token:
        return Response({"status": "error", "message": "Email and token are required"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        verification_token = VerificationToken.objects.get(user_email=email)
        
        # Check if the token is valid
        if verification_token.token == token and verification_token.is_valid():
            # Token is valid, delete the verification token
            verification_token.delete()

            return Response({"status": "success", "message": "Token Valid"}, status=status.HTTP_202_ACCEPTED)
        else:
            return Response({"status": "error", "message": "Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)
    
    except VerificationToken.DoesNotExist:
        return Response({"status": "error", "message": "Token does not exist"}, status=status.HTTP_400_BAD_REQUEST)
    # except Exception as e:
    #     logger.error(f"Error during token verification for {email}: {str(e)}")
    #     return Response({"error": "An error occurred during verification"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

        # Notification.objects.create(
        #     user=user,
        #     title="Password Changed",
        #     message="Your password has been changed successfully.",
        #     notif_type='password',
        # )
        
        return Response({"status": "success", "message": "Password changed successfully"}, status=status.HTTP_200_OK)
    
    except Users.DoesNotExist:
        return Response({"status": "error", "message": "User does not exist"}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Failed to change password for {email}: {str(e)}")
        return Response({"status":"error", "message": "An error occurred while changing the password"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def resend_forgot_password_token(request):
    email = request.data.get('email')

    if not email:
        return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = Users.objects.get(email=email, signup_type='normal')
    except Users.DoesNotExist:
        return Response({"status": "success", "message": "If your email exists, a reset token has been resent."}, status=status.HTTP_200_OK)

    # Generate a new token
    token = ''.join(random.choices('0123456789', k=6))
    
    # Update or create the token
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



@api_view(["POST"])
def send_funds(request):
    # Extract data from the request
    sender_id = request.data.get('sender_id')
    receiver_id = request.data.get('receiver_id')
    recipient_type = request.data.get('recipient_type')  # 'user', 'team', or 'org'
    wallet_pin = request.data.get('wallet_pin')
    amount = request.data.get('amount')
    
    # Basic validation
    if not all([sender_id, receiver_id, recipient_type, wallet_pin, amount]):
        return Response({'error': 'All fields are required'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Fetch sender wallet
        sender_wallet = UserWallet.objects.get(user_id=sender_id)
        
        # Determine the recipient and fetch their wallet
        if recipient_type == 'user':
            recipient_wallet = UserWallet.objects.get(user_wallet_id=receiver_id)
        elif recipient_type == 'team':
            recipient_wallet = TeamWallet.objects.get(team_wallet_id=receiver_id)
        elif recipient_type == 'org':
            recipient_wallet = OrgWallet.objects.get(org_wallet_id=receiver_id)
        else:
            return Response({'error': 'Invalid recipient type'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify wallet pin
        if (recipient_type == 'user' and sender_wallet.user_wallet_pin != wallet_pin) or \
           (recipient_type in ['team', 'org'] and sender_wallet.wallet_pin != wallet_pin):
            return Response({'error': 'Invalid wallet pin'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if sender has enough balance
        if sender_wallet.wallet_balance < amount:
            return Response({'error': 'Insufficient funds'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Perform the transfer
        sender_wallet.wallet_balance -= amount
        recipient_wallet.wallet_balance += amount
        
        # Save updated wallet balances
        sender_wallet.save()
        recipient_wallet.save()
        
        return Response({'success': 'Transfer successful'}, status=status.HTTP_200_OK)

    except ObjectDoesNotExist:
        return Response({'error': 'Sender or recipient wallet not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ZOHO_ACCOUNT_ID = "6378693000000008002"
# CLIENT_ID = "1000.HXKB69X855U3R1OJ17FS35X1PHJ06G"
# CLIENT_SECRET = "3556a22929c0ba8ee509428ad3c1ced705591601be"
# REFRESH_TOKEN = "1000.584f1f10cc49eca17cb751b8f838e28b.d2f5874af7d73b767c21912eaa917daa"
# TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
# redirect_uri = "http://vermillionent.pythonanywhere.com/"
# SCOPE = "ZohoMail.messages.ALL"
# auth_url = f"https://accounts.zoho.com/oauth/v2/auth?response_type=code&client_id={CLIENT_ID}&scope={SCOPE}&redirect_uri={redirect_uri}&access_type=offline"


# def get_access_token_from_refresh_token():
#     data = {
#         "refresh_token": REFRESH_TOKEN,  # Your stored refresh token
#         "client_id": CLIENT_ID,
#         "client_secret": CLIENT_SECRET,
#         "redirect_uri": redirect_uri,
#         "grant_type": "refresh_token"
#     }
#     response = requests.post(TOKEN_URL, data=data)
#     print(response.json())
#     if response.status_code == 200:
#         return response.json().get("access_token")
#     else:
#         logger.error(f"Failed to refresh access token: {response.status_code} - {response.json()}")
#         return None


# def send_zoho_email(receiver_email, subject, html_content):
#     # Obtain the access token using the refresh token
#     access_token = get_access_token_from_refresh_token()

#     if not access_token:
#         logger.error("Failed to retrieve access token.")
#         return False

#     # Send the email using the Zoho Mail API
#     url = f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages"

#     headers = {
#         "Authorization": f"Zoho-oauthtoken {access_token}",
#         "Content-Type": "application/json"
#     }

#     data = {
#         "fromAddress": "info@vermillionent.com",  # Replace with your sender email
#         "toAddress": receiver_email,
#         "subject": subject,
#         "content": html_content
#     }

#     response = requests.post(url, json=data, headers=headers)

#     if response.status_code == 200:
#         return True
#     else:
#         logger.error(f"Failed to send email to {receiver_email}: {response.status_code} - {response.json()}")
#         return False





@api_view(["POST"])
def send_code(request):
    email = request.data.get('email')

    if not email:
        return Response({"status": "error", "message": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=email.strip().lower()).exists():
        return Response({"status": "error", "message": "Account already exists with this email"}, status=status.HTTP_400_BAD_REQUEST)

    token = ''.join(random.choices('0123456789', k=6))

    verification_token, created = VerificationToken.objects.update_or_create(
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
    

@api_view(["POST"])
def admin_login(request):
    password = request.data.get("password")

    if not password:
        return Response({"status": "error", "message": "Password is required"}, status=status.HTTP_400_BAD_REQUEST)

    if password == "ventontop1234":
        return Response({"status": "success", "message": "Admin Login Successful"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Invalid password"}, status=status.HTTP_401_UNAUTHORIZED)
    

@api_view(["GET"])
def get_all_username_and_email(request):
    users = Users.objects.all().values("username", "email")  # Fetch only username and email fields

    return Response({"status": "success", "data": list(users)}, status=status.HTTP_200_OK)


@api_view(["GET"])
def get_number_of_all_users(request):
    user_count = Users.objects.count()  # Get the total number of users

    return Response({"status": "success", "total_users": user_count}, status=status.HTTP_200_OK)


@api_view(["POST"])
def check_username_availability(request):
    username = request.data.get("username")
    
    if not username:
        return Response({"status": "error", "message": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Check if the username exists
    exists = Users.objects.filter(username=username).exists()

    if exists:
        return Response({"status": "success", "message": "Username exists"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Username does not exist"}, status=status.HTTP_404_NOT_FOUND)
    

@api_view(['GET'])
def get_user_informations(request):
    try:
        session_token = request.headers.get('Authorization')

        if not session_token:
            return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

        # Ensure the token is in the correct format (e.g., 'Bearer <token>')
        if not session_token.startswith("Bearer "):
            return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        # Extract the actual token
        session_token = session_token.split(" ")[1]

        # Fetch user object based on the session token
        user = get_object_or_404(Users, login_session_token=session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=10):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        # Get user profile
        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            profile = None

        # Get user interests
        interests = UserInterests.objects.filter(user=user).values_list('interests', flat=True)
        interests = list(interests)

        # Get wallet balance
        wallet = UserWallet.objects.filter(user=user).first()

        # Get user's favorite games
        user_games = FavoriteGames.objects.filter(user=user).values_list('game__game_title', flat=True)

        # Get user achievements
        achievements = user.achievements.values('name', 'description', 'logo')

        # Get user social links
        social_links = SocialLink.objects.filter(user=user).values('title', 'url')

        # Construct response data
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
        return Response(
            {'status': 'error', 'message': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error: {str(e)}")
        return Response(
            {'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def add_game_account(request):
    # Get data from the request
    session_token = request.data.get('session_token')
    game_id = request.data.get('game_id')
    game_username = request.data.get('game_username')

    # Validate inputs
    if not session_token or not game_id or not game_username:
        return Response(
            {'status': 'error', 'message': 'session_token, game_id, and game_username are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Fetch the user based on session token
    user = get_object_or_404(Users, login_session_token=session_token)

    if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=360):
        return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

    # Fetch the game object
    game = get_object_or_404(Games, game_id=game_id)

    # Check if the game account already exists for the user
    if GameAccount.objects.filter(user=user, game=game).exists():
        return Response(
            {'status': 'error', 'message': 'Game account already exists for this user'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Create the game account
    try:
        GameAccount.objects.create(
            user=user,
            game=game,
            game_username=game_username
        )
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
def edit_profile_info(request):
    try:
        session_token = request.headers.get('Authorization')

        if not session_token:
            return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

        # Ensure the token is in the correct format (e.g., 'Bearer <token>')
        if not session_token.startswith("Bearer "):
            return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        # Extract the actual token
        login_session_token = session_token.split(" ")[1]
        profile_pic = request.FILES.get("profile_pic")
        banner = request.FILES.get("banner")
        username = request.data.get('username')
        fullname = request.data.get('fullname')
        description = request.data.get('description')
        country = request.data.get('country')
        interests = request.data.get('interests')

        # Fetch user
        user = get_object_or_404(Users, login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=360):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)


        # Fetch user profile
        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            return Response(
                {'status': 'error', 'message': 'User profile does not exist'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Update user and profile fields
        if username and username != user.username:
            if Users.objects.filter(username=username).exists():
                return Response(
                    {'status': 'error', 'message': 'Username already taken'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            user.username = username
        if fullname:
            user.full_name = fullname
        if description:
            profile.description = description
        if country:
            user.country = country
        if profile_pic:
            if not profile_pic.content_type.startswith("image/"):
                return Response(
                    {'status': 'error', 'message': 'Invalid profile picture format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            profile.profile_picture = profile_pic
        if banner:
            if not banner.content_type.startswith("image/"):
                return Response(
                    {'status': 'error', 'message': 'Invalid banner format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            profile.banner = banner

        # Save user and profile
        user.save()
        profile.save()

        # Update interests
        if interests and isinstance(interests, list):
            UserInterests.objects.filter(user=user).delete()
            interests_objects = [UserInterests(user=user, interests=interest) for interest in interests]
            UserInterests.objects.bulk_create(interests_objects)

        # Return updated data
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
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error: {str(e)}")
        return Response(
            {'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )



@api_view(['GET'])
def get_user_status(request):
    email = request.query_params.get('email')

    if not email:
        return Response({'status': 'error', 'message': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Fetch user using the email
        user = Users.objects.get(email=email)

        # Return the is_active status
        return Response({
            'status': 'success',
            'is_active': user.is_active,
            'message': 'User status retrieved successfully'
        }, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({
            'status': 'error', 
            'message': 'User not found'}, 
            status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': f'An error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def add_email_to_waitlist(request):
    email = request.data.get("email")
    
    if not email:
        return Response({"status": "error", "message": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
    
    # Check if email already exists
    if Waitlist.objects.filter(email=email).exists():
        return Response({"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)
    
    if Users.objects.filter(email=email).exists():
        return Response({"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)
    
    # Add email to waitlist
    waitlist_entry = Waitlist.objects.create(email=email)

    subject = "Welcome to Vermillion City"
    email_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome to Vermillion Enterprise!</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f9f9f9;
            margin: 0;
            padding: 0;
        }
        .container {
            width: 90%;
            max-width: 600px;
            margin: 20px auto;
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
            overflow: hidden;
        }
        .content {
            padding: 20px;
        }
        h2 {
            color: #ff4747;
        }
        a {
            color: #ff4747;
            text-decoration: none;
        }
        .banner img {
            width: 100%;
            display: block;
        }
    </style>
</head>
<body>
    <div class="banner">
        <img src="https://vermillionent.pythonanywhere.com/media/images/top.jpg" alt="Top Image">
    </div>
    <div class="container">
        <div class="content">
            <p>Hello there!!</p>
            <p>Welcome to the Vermillion Enterprise community! 🎉 We're thrilled to have you on board.</p>
            <p>We're building a platform tailored for the anime and gaming industry—just like you, we’re passionate about anime, games, graphic design, game development, video editing, esports, and so much more.</p>
            <h2>What to Do:</h2>
            <ul>
                <li><strong>Explore</strong>: Check out the features we plan to release (it’s on the landing page, if you haven’t seen it yet!).</li>
                <li><strong>Earn</strong>: Our referral program is coming soon! If you’re up for earning some fun rewards or extra cash, keep an eye on your inbox. 🤝</li>
            </ul>
            <h2>Stay Connected:</h2>
            <ul>
                <li><strong>Socials</strong>: Follow us on <a href="https://www.instagram.com/v_ent.co/profilecard/?igsh=MWJ2N3NweWJkbm5vaw==" target="_blank">Instagram</a> and <a href="https://www.tiktok.com/@v_ent.co?_t=8rUxaio9v1z&_r=1" target="_blank">TikTok</a> for updates and sneak peeks.</li>
                <li><strong>Join the Conversation</strong>: Engage in discussions on <a href="https://chat.whatsapp.com/J3a6pPSmHLeF0TbuLX14MV" target="_blank">WhatsApp</a> or <a href="https://discord.gg/DdkuNGFMH7" target="_blank">Discord</a> with fellow fans.</li>
                <li><strong>Events & Updates</strong>: Regular updates and programs are on the way, so stay tuned for our big launch! 😉</li>
            </ul>
            <h2>Shop:</h2>
            <p>Check out <a href="https://vermillionents.com.ng/" target="_blank"><strong>Vermillion City</strong></a>, our shop for exclusive merchandise and gaming products. Browse your favorites or join the community to request custom items.</p>
            <p><em>Fun fact:</em> <strong>"Vermillion City"</strong> was inspired by Pokémon, a place where you can find whatever you’re looking for!</p>
            <p>Thank you for joining us on this exciting journey. If you have any questions, feel free to reach out at <a href="mailto:support@vermillionent.com">support@vermillionent.com</a>.</p>
        </div>
    </div>
    <div class="banner">
        <img src="https://vermillionent.pythonanywhere.com/media/images/bottom.jpg" alt="Bottom Image">
    </div>
</body>
</html>
"""

    send_email(email, subject, email_content)
    return Response({"status": "success", "message": "Email added to waitlist successfully."}, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def update_web_and_social_links(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Ensure the token is in the correct format (e.g., 'Bearer <token>')
    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    # Extract the actual token
    login_session_token = session_token.split(" ")[1]

    try:
        # Get the user based on the session token
        user = Users.objects.get(login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=10):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)


        # Get the links from the request data
        links = request.data.get("links")
        if not isinstance(links, dict):
            return Response({'status': 'error', 'message': 'Links should be a dictionary'}, status=status.HTTP_400_BAD_REQUEST)

        # Fetch existing titles for the user
        existing_titles = set(user.social_links.values_list('title', flat=True))

        # Validate and update links
        for title, url in links.items():
            # Ensure title and url are valid
            if not title or not url:
                continue

            # Check if a duplicate title is being added
            if title in existing_titles:
                return Response({'status': 'error', 'message': f"Duplicate title found: {title}"}, status=status.HTTP_400_BAD_REQUEST)

            # Update if exists, otherwise create a new record
            social_link, created = SocialLink.objects.update_or_create(
                user=user,
                title=title,
                defaults={'url': url}
            )
            # Add the title to existing_titles to prevent further duplicates
            existing_titles.add(title)

        return Response({'status': 'success', 'message': 'Social links updated successfully'}, status=status.HTTP_200_OK)

    except Users.DoesNotExist:
        return Response({'status': 'error', 'message': 'Invalid session token'}, status=status.HTTP_404_NOT_FOUND)
    

@api_view(['POST'])
def social_auth(request):
    provider = request.data.get('provider')  # google or facebook
    provider_id = request.data.get('provider_id')  # unique ID from provider
    email = request.data.get('email')
    full_name = request.data.get('full_name')
    country = request.data.get('country')
    profile_picture_url = request.data.get('profile_picture_url')

    # Validate required fields
    if not all([provider, provider_id, email]):
        return Response({
            "status": "error",
            "message": "Missing required fields."
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Check if user already exists
        user = Users.objects.filter(email=email, signup_type=provider, provider_id=provider_id).first()

        if user:
            # Social Login
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

        # If user doesn't exist, proceed to Social Signup
        if not full_name:
            return Response({
                "status": "error",
                "message": "Full name is required for new signups."
            }, status=status.HTTP_400_BAD_REQUEST)

        # Generate a unique username
        username = generate_unique_username(email)

        # Create the user
        user = Users.objects.create(
            full_name=full_name,
            email=email,
            username=username,
            country=country,
            signup_type=provider,
            provider_id=provider_id,
            is_active=True  # No email verification for social signups
        )

        # Create user profile and save profile picture
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


@api_view(['POST'])
def edit_favorite_games(request):
    try:
        # Get login_session_token and games list from the request
        login_session_token = request.data.get('login_session_token')
        game_ids = request.data.get('game_ids')  # Expected to be a list of game IDs

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

        # Fetch user by session token
        user = get_object_or_404(Users, login_session_token=login_session_token)

        # Clear existing favorite games for the user
        FavoriteGames.objects.filter(user=user).delete()

        # Add new favorite games
        for game_id in game_ids:
            game = get_object_or_404(Games, game_id=game_id)
            FavoriteGames.objects.create(user=user, game=game)

        return Response(
            {'status': 'success', 'message': 'Favorite games updated successfully'},
            status=status.HTTP_200_OK
        )

    except Users.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Games.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'One or more games not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {'status': 'error', 'message': f'An unexpected error occurred: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


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

    # Generate new token and uid
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    FRONTEND_VERIFY_URL = f"{FRONTEND_URL}/email-verified"  # Use your actual frontend URL
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


@api_view(['GET'])
def get_google_login_url(request):
    client_id = "33973954184-679jm6l22j2di387c7ra8uktmp5mg335.apps.googleusercontent.com"
    redirect_uri = "https://vermillionent.pythonanywhere.com/auth/google-callback/"  # Your API endpoint
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
    code = request.query_params.get('code')
    if not code:
        return Response({"status": "error", "message": "No code provided"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Exchange code for tokens
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        'code': code,
        'client_id': "33973954184-679jm6l22j2di387c7ra8uktmp5mg335.apps.googleusercontent.com",
        'client_secret': "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",
        'redirect_uri': "https://vermillionent.pythonanywhere.com/auth/google-callback/",
        'grant_type': 'authorization_code'
    }

    token_response = requests.post(token_url, data=data)
    
    if token_response.status_code != 200:
        return Response({"status": "error", "message": "Failed to retrieve token"}, status=status.HTTP_400_BAD_REQUEST)

    token_response_data = token_response.json()

    if 'id_token' not in token_response_data:
        return Response({"status": "error", "message": "Failed to get ID token"}, status=status.HTTP_400_BAD_REQUEST)

    id_token_str = token_response_data['id_token']

    # Redirect user to frontend with id_token
    frontend_redirect_url = f"{FRONTEND_URL}/verify-google-token?id_token={id_token_str}"

    return redirect(frontend_redirect_url)


@api_view(['GET'])
def verify_token(request):
    id_token_str = request.query_params.get('id_token')

    # Verify the id_token
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    try:
        idinfo = id_token.verify_oauth2_token(id_token_str, google_requests.Request(), "33973954184-679jm6l22j2di387c7ra8uktmp5mg335.apps.googleusercontent.com")

        # Extract user info
        email = idinfo['email']
        full_name = idinfo.get('name', '')
        social_id = idinfo['sub']

        # Now check if user exists
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

        # 👇 Generate your own session token
        # Generate a session token
        session_token = generate_session_token()
        
        # Save session token to the user model
        user.login_session_token = session_token
        user.login_session_created_at = timezone.now()
        user.save()

        # return session token
        return Response({
            "status": "success",
            "message": "User logged in successfully",
            "session_token": session_token,
        }, status=status.HTTP_200_OK)

    except ValueError:
        return Response({"status": "error", "message": "Invalid ID token"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def upload_images(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=10):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        images = request.FILES.getlist('images')
        if not images:
            return Response({'status': 'error', 'message': 'No images provided'}, status=status.HTTP_400_BAD_REQUEST)

        current_image_count = UserGallery.objects.filter(user=user).count()
        total_after_upload = current_image_count + len(images)

        if total_after_upload > 5:
            remaining_slots = max(0, 5 - current_image_count)
            return Response({
                'status': 'error',
                'message': f'Upload limit exceeded. You can only upload {remaining_slots} more image(s).'
            }, status=status.HTTP_400_BAD_REQUEST)

        for image in images:
            UserGallery.objects.create(user=user, image=image)

        return Response({'status': 'success', 'message': 'Images uploaded successfully'}, status=status.HTTP_200_OK)

    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error: {str(e)}")
        return Response({'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_user_gallery(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=10):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        gallery_items = UserGallery.objects.filter(user=user)

        # Build full URLs
        gallery_data = [
            {
                'image': request.build_absolute_uri(item.image.url),
                'date_added': item.date_added,
                'image_id': item.id
            }
            for item in gallery_items
        ]

        return Response({'status': 'success', 'data': gallery_data}, status=status.HTTP_200_OK)

    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error: {str(e)}")
        return Response({'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def delete_gallery_image(request):
    session_token = request.headers.get('Authorization')
    image_id = request.data.get('image_id')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(" ")[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=360):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        gallery_item = get_object_or_404(UserGallery, id=image_id, user=user)
        gallery_item.delete()

        return Response({'status': 'success', 'message': 'Image deleted successfully'}, status=status.HTTP_200_OK)

    except UserGallery.DoesNotExist:
        return Response({'status': 'error', 'message': 'Image not found'}, status=status.HTTP_404_NOT_FOUND)
    
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error: {str(e)}")
        return Response({'status': 'error', 'message': 'An unexpected error occurred. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
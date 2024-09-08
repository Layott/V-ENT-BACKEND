import datetime
from django.shortcuts import render, redirect
from rest_framework.decorators import api_view
from .serializers import UserSerializer
from .models import Users, Games, UserCommunity, VerificationToken, UserProfile, GameAccount, UserWallet, Teams, TeamProfile, TeamWallet, OrgWallet
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

# Create your views here.

logger = logging.getLogger(__name__)

class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter

def create_user_wallet(user):
    user_wallet = UserWallet.objects.create(user=user)
    user_wallet.save()
    return "Wallet created successfully"

def send_email(to_address, subject, html_body):
    # Gmail SMTP server credentials
    smtp_server = 'smtp.gmail.com'
    smtp_port = 465  # or 587 for TLS
    from_address = 'vermillioninformation@gmail.com'
    password = 'rglb ssfs xhip psma'  # Or your actual Gmail password (if less secure apps are enabled)

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



@api_view(['POST'])
def signup(request):
    fullname = request.data.get('full_name')
    email = request.data.get('email')
    username = request.data.get('username')
    country = request.data.get('country')
    password = request.data.get('password')

    if not all([fullname, email, username, password, country]):
        return Response({"status": "error", "message": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)
    

    try:
        user = Users.objects.get(email=email)
        # Update the existing user with new details
        user.full_name = fullname
        user.username = username
        user.country = country
        user.password = make_password(password)
        user.save()
    except Users.DoesNotExist:
        # Create a new user if it doesn't exist
        user = Users.objects.create(
            full_name=fullname,
            email=email,
            username=username,
            password=make_password(password),
            country=country
        )


    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    verification_link = request.build_absolute_uri(reverse('verify_token', kwargs={'uidb64': uid, 'token': token}))

    subject = 'Verify Your Email'
    message = f'''Hi,

Please click the link below to verify your email:

{verification_link}

If you did not create an account, please ignore this email.
'''

    try:
        send_email(email, subject, message)
        return Response({"status": "success", "message": "Verification link sent to email"}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {str(e)}")
        return Response({"status": "error", "message": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def verify_token(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Users.objects.get(pk=uid)
        
        if default_token_generator.check_token(user, token):
            if user.is_active:
                # If user is already verified
                return render(request, 'account_verified.html', {"message": "Your account is already verified."})
            else:
                # Activate user and create the necessary profiles
                user.is_active = True
                user.save()

                UserProfile.objects.create(user=user)
                create_user_wallet(user=user)

                return render(request, 'verification_success.html', {"message": "Verification successful! Your account is now activated."})
        else:
            return render(request, 'invalid_token.html', {"message": "Invalid or expired token."})

    except (TypeError, ValueError, OverflowError, Users.DoesNotExist):
        return render(request, 'invalid_token.html', {"message": "Invalid verification link."})


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
        # User is authenticated, return success response
        return Response({'message': 'Login successful'}, status=status.HTTP_200_OK)
    else:
        # Authentication failed, return error response
        return Response({'message': 'Invalid username/email or password'}, status=status.HTTP_401_UNAUTHORIZED)


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

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(message, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(sender_email, password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()

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
    
    # Create or update the verification token for the user
    verification_token, created = VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )
    
    # Send email with the token
    sender_email = 'habeebmuftau05@gmail.com'
    receiver_email = email
    password = 'jvbe whjo lnwe pwxu'  # Use environment variables for sensitive information
    subject = 'Verify Email'
    message = f'''Hi,

    Your Verification Token Is: {token}
    
    Please use it to verify your account'''

    # try:
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(message, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(sender_email, password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()
    # except Exception as e:
    #     logger.error(f"Failed to send email to {email}: {str(e)}")
    #     return Response({"error": "Failed to send verification email"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response({"status": "success", "message": "Password reset token sent to email"}, status=status.HTTP_200_OK)


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
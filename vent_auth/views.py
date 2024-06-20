from django.shortcuts import render
from rest_framework.decorators import api_view
from .serializers import UserSerializer
from .models import Users, UserCommunity, VerificationToken, UserProfile
from rest_framework.response import Response
from django.contrib.auth.hashers import make_password, check_password
from rest_framework import status
from django.core.mail import send_mail
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

# Create your views here.


class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter



@api_view(['POST'])
def signup(request):
    email = request.data.get('email')
    
    # Generate a random 6-digit token
    token = ''.join(random.choices('0123456789', k=6))
    
    # Create or update the verification token for the user
    verification_token, created = VerificationToken.objects.update_or_create(
        user_email=email,
        defaults={'token': token, 'created_at': timezone.now()}
    )
    
    # Send email with the token
    # send_mail(
    #     'Your Verification Token',
    #     f'Your verification token is {token}. Please use it to verify your account.',
    #     'habeebmuftau05@gmail.com',  # Replace with your actual "from" email address
    #     [email],
    #     fail_silently=False,
    # )


    sender_email = 'habeebmuftau05@gmail.com'
    receiver_email = email
    password = 'jvbe whjo lnwe pwxu'
    subject = 'Verify Email'
    message = f'''Hi,

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
def verify_token(request):
    email = request.data.get('email')
    token = request.data.get('token')
    
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
                user = serializer.save()
                
                profile_picture = request.FILES.get('profile_picture')
                UserProfile.objects.create(user_id=user, profile_picture=profile_picture)
                
                # Delete the used token
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

    # Authenticate user with either username or email
    user = authenticate(request, username=username_or_email, password=password)
    
    if user is not None:
        # User is authenticated, return success response
        return Response({'message': 'Login successful'}, status=status.HTTP_200_OK)
    else:
        # Authentication failed, return error response
        return Response({'message': 'Invalid username/email or password'}, status=status.HTTP_401_UNAUTHORIZED)



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
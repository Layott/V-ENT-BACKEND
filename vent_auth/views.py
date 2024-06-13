from django.shortcuts import render
from rest_framework.decorators import api_view
from .serializers import UserSerializer
from .models import Users, UserCommunity

# Create your views here.


@api_view(['POST'])
def signup(request):
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()

        return Response({"success": "User Created Successful"}, status=status.HTTP_201_CREATED)
    return Response({"error": "Sign Up error"}, status=status.HTTP_400_BAD_REQUEST)


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
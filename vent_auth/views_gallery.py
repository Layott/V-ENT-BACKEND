import logging
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, UserGallery


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

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
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

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

        gallery_items = UserGallery.objects.filter(user=user)

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

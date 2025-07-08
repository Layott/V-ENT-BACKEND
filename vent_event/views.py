from django.utils import timezone
import uuid
from django.shortcuts import render
from imports import api_view, Response, get_object_or_404, datetime, status
from .models import Event, Sponsor, SocialLink
from vent_auth.models import Games, Users
from datetime import timedelta


# Create your views here.

# @api_view(['POST'])
# def create_event(request):
#     try:
#         session_token = request.headers.get('Authorization')

#         if not session_token:
#             return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

#         # Ensure the token is in the correct format (e.g., 'Bearer <token>')
#         if not session_token.startswith("Bearer "):
#             return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

#         # Extract the actual token
#         login_session_token = session_token.split(" ")[1]
#         name = request.data.get('name')
#         event_type = request.data.get('event_type')
#         desc = request.data.get('desc')
#         entry_fee = request.data.get('entry_fee')
#         reg_start_date = request.data.get('reg_start_date')
#         reg_end_date = request.data.get('reg_end_date')
#         event_date = request.data.get('event_date')
#         start_time = request.data.get('start_time')
#         end_time = request.data.get('end_time')
#         logo = request.FILES.get('logo')  # Event logo
#         banner = request.FILES.get('banner')  # Event banner
#         game_id = request.data.get('game_id')
#         game = get_object_or_404(Games, pk=game_id)

#         # Validate required fields
#         if not all([name, session_token, event_type, desc, entry_fee, event_date, start_time, end_time]):
#             return Response({'status': 'error', 'message': 'All fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

#         # Check for event type (physical, virtual, hybrid) and get location or event_link accordingly
#         location = request.data.get('location') if event_type in ['physical', 'hybrid'] else None
#         event_link = request.data.get('event_link') if event_type in ['virtual', 'hybrid'] else None

#         # Validate that location or event link is provided as needed
#         if event_type == 'physical' and not location:
#             return Response({'status': 'error', 'message': 'Location is required for physical events.'}, status=status.HTTP_400_BAD_REQUEST)
#         if event_type == 'virtual' and not event_link:
#             return Response({'status': 'error', 'message': 'Event link is required for virtual events.'}, status=status.HTTP_400_BAD_REQUEST)

#         sponsors = request.data.get('sponsors', [])
#         sponsor_logos = request.data.get('sponsor_logos', [])
#         social_links = request.data.get('social_links', [])
#         social_urls = request.data.get('social_urls', [])


#         # Get the event creator
#         creator = get_object_or_404(Users, login_session_token=login_session_token)

#         # Create the event
#         event = Event.objects.create(
#             name=name,
#             creator=creator,
#             event_type=event_type,
#             desc=desc,
#             entry_fee=entry_fee,
#             reg_start_date=reg_start_date,
#             reg_end_date=reg_end_date,
#             event_date=event_date,
#             start_time=start_time,
#             end_time=end_time,
#             location=location,
#             event_link=event_link,
#             logo=logo,  # Set the event logo
#             banner=banner  # Set the event banner
#         )

#         # Create sponsors with logos if provided
#         if sponsors and sponsor_logos:
#             for sponsor_name, logo in zip(sponsors, sponsor_logos):
#                 Sponsor.objects.create(event=event, name=sponsor_name, logo=logo)

#         # Create social links if provided
#         if social_links and social_urls:
#             for platform, url in zip(social_links, social_urls):
#                 SocialLink.objects.create(event=event, platform=platform, url=url)

#         return Response({'status': 'success', 'message': 'Event created successfully.'}, status=status.HTTP_201_CREATED)

#     except Exception as e:
#         return Response({'status': 'error', 'message': f'Error creating event: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def create_event(request):
    session_token = request.headers.get('Authorization')

    if not session_token or not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Authorization required'}, status=status.HTTP_400_BAD_REQUEST)

    token = session_token.split(" ")[1]
    user = get_object_or_404(Users, login_session_token=token)

    if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
        return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

    try:
        game_title = request.data.get('game_title')
        if not game_title:
            return Response({'status': 'error', 'message': 'Game title is required'}, status=status.HTTP_400_BAD_REQUEST)

        game = get_object_or_404(Games, game_title__iexact=game_title.strip())

        event = Event.objects.create(
            name=request.data['name'],
            desc=request.data['desc'],
            creator=user,
            event_type=request.data['event_type'],
            entry_fee=request.data.get('entry_fee', 0),
            reg_start_date=request.data['reg_start_date'],
            reg_end_date=request.data['reg_end_date'],
            event_date=request.data['event_date'],
            start_time=request.data['start_time'],
            end_time=request.data['end_time'],
            location=request.data['location'],
            event_link=request.data.get('event_link'),
            logo=request.FILES.get('logo'),
            banner=request.FILES.get('banner'),
            game=game
        )

        return Response({'status': 'success', 'message': 'Event created successfully'}, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_400_BAD_REQUEST)



@api_view(['GET'])
def get_all_events(request):
    session_token = request.headers.get('Authorization')

    if not session_token or not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Authorization header required'}, status=status.HTTP_400_BAD_REQUEST)

    token = session_token.split(" ")[1]
    user = get_object_or_404(Users, login_session_token=token)

    if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
        return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)

    today = timezone.now().date()

    # Featured = top 5 by interaction
    featured_events = Event.objects.order_by('-interaction_count')[:5]

    # Upcoming = event_date >= today
    upcoming_events = Event.objects.filter(event_date__gte=today).order_by('event_date')[:5]

    # Group all by game
    all_events = Event.objects.select_related('game')
    events_by_game = {}
    for event in all_events:
        game_name = event.game.game_title if event.game else "Unknown Game"
        if game_name not in events_by_game:
            events_by_game[game_name] = []
        events_by_game[game_name].append(event)

    # Serializer helper
    def serialize(event):
        return {
            "event_id": event.event_id,
            "name": event.name,
            "creator": event.creator.username,
            "event_type": event.event_type,
            "desc": event.desc,
            "entry_fee": str(event.entry_fee),
            "reg_start_date": event.reg_start_date,
            "reg_end_date": event.reg_end_date,
            "event_date": event.event_date,
            "start_time": str(event.start_time),
            "end_time": str(event.end_time),
            "location": event.location,
            "event_link": event.event_link,
            "logo": request.build_absolute_uri(event.logo.url) if event.logo else None,
            "banner": request.build_absolute_uri(event.banner.url) if event.banner else None,
            "interaction_count": event.interaction_count,
            "game": event.game.game_title if event.game else None,
        }

    return Response({
        "status": "success",
        "data": {
            "featured": [serialize(e) for e in featured_events],
            "upcoming": [serialize(e) for e in upcoming_events],
            "by_game": {
                game: [serialize(e) for e in evts]
                for game, evts in events_by_game.items()
            }
        }
    }, status=status.HTTP_200_OK)

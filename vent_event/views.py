from django.shortcuts import render
from imports import api_view, Response, get_object_or_404, datetime, status
from .models import Event, Sponsor, SocialLink
from vent_auth.models import Users
# Create your views here.

@api_view(['POST'])
def create_event(request):
    try:
        name = request.data.get('name')
        session_token = request.data.get('session_token')
        event_type = request.data.get('event_type')
        desc = request.data.get('desc')
        entry_fee = request.data.get('entry_fee')
        reg_start_date = request.data.get('reg_start_date')
        reg_end_date = request.data.get('reg_end_date')
        event_date = request.data.get('event_date')
        start_time = request.data.get('start_time')
        end_time = request.data.get('end_time')
        logo = request.FILES.get('logo')  # Event logo
        banner = request.FILES.get('banner')  # Event banner

        # Validate required fields
        if not all([name, session_token, event_type, desc, entry_fee, event_date, start_time, end_time]):
            return Response({'status': 'error', 'message': 'All fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Check for event type (physical, virtual, hybrid) and get location or event_link accordingly
        location = request.data.get('location') if event_type in ['physical', 'hybrid'] else None
        event_link = request.data.get('event_link') if event_type in ['virtual', 'hybrid'] else None

        # Validate that location or event link is provided as needed
        if event_type == 'physical' and not location:
            return Response({'status': 'error', 'message': 'Location is required for physical events.'}, status=status.HTTP_400_BAD_REQUEST)
        if event_type == 'virtual' and not event_link:
            return Response({'status': 'error', 'message': 'Event link is required for virtual events.'}, status=status.HTTP_400_BAD_REQUEST)

        sponsors = request.data.get('sponsors', [])
        sponsor_logos = request.data.get('sponsor_logos', [])
        social_links = request.data.get('social_links', [])
        social_urls = request.data.get('social_urls', [])


        # Get the event creator
        creator = get_object_or_404(Users, session_token=session_token)

        # Create the event
        event = Event.objects.create(
            name=name,
            creator=creator,
            event_type=event_type,
            desc=desc,
            entry_fee=entry_fee,
            reg_start_date=reg_start_date,
            reg_end_date=reg_end_date,
            event_date=event_date,
            start_time=start_time,
            end_time=end_time,
            location=location,
            event_link=event_link,
            logo=logo,  # Set the event logo
            banner=banner  # Set the event banner
        )

        # Create sponsors with logos if provided
        if sponsors and sponsor_logos:
            for sponsor_name, logo in zip(sponsors, sponsor_logos):
                Sponsor.objects.create(event=event, name=sponsor_name, logo=logo)

        # Create social links if provided
        if social_links and social_urls:
            for platform, url in zip(social_links, social_urls):
                SocialLink.objects.create(event=event, platform=platform, url=url)

        return Response({'status': 'success', 'message': 'Event created successfully.'}, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({'status': 'error', 'message': f'Error creating event: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def get_all_events(request):
    session_token = request.headers.get('Authorization')

    if not session_token:
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Ensure the token is in the correct format (e.g., 'Bearer <token>')
    if not session_token.startswith("Bearer "):
        return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    # Extract the actual token
    session_token = session_token.split(" ")[1]

    # Fetch the user associated with the session token
    user = get_object_or_404(Users, login_session_token=session_token)

    # Retrieve all events with logos and banners
    events = Event.objects.all().values(
        'event_id',
        'name',
        'creator__username',  # Include the username of the event creator
        'event_type',
        'desc',
        'entry_fee',
        'reg_start_date',
        'reg_end_date',
        'event_date',
        'start_time',
        'end_time',
        'location',
        'event_link',
        'logo',  # Logo field
        'banner'  # Banner field
    )

    # Construct the URLs for logos and banners
    for event in events:
        if event['logo']:
            event['logo'] = request.build_absolute_uri(event['logo'])
        if event['banner']:
            event['banner'] = request.build_absolute_uri(event['banner'])

    return Response(
        {'status': 'success', 'data': list(events)},
        status=status.HTTP_200_OK
    )
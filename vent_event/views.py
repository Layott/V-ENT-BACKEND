import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.paginator import Paginator, EmptyPage
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from vent_auth.models import Games, Users
from .models import Event, TicketTier, Sponsor, SocialLink, VendorInvite
from .serializers import serialize_event_card, serialize_event_detail

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_MINUTES = 120
PAGE_SIZE = 12
VALID_EVENT_TYPES = {'physical', 'virtual', 'hybrid'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message, code, http_status, field_errors=None):
    """Build the canonical error envelope: {status, data, message, code}."""
    data = {}
    if field_errors:
        data['field_errors'] = field_errors
    return Response(
        {'status': 'error', 'data': data, 'message': message, 'code': code},
        status=http_status,
    )


def _authenticate(request):
    """Resolve the Bearer session token to a live user.

    Returns (user, None) on success or (None, error_response) on failure.
    """
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _error(
            'Authorization header with a Bearer token is required.',
            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED,
        )

    token = header.split(' ', 1)[1].strip()
    if not token:
        return None, _error('Bearer token is empty.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)

    try:
        user = Users.objects.get(login_session_token=token)
    except Users.DoesNotExist:
        return None, _error('Invalid session token.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)

    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED', status.HTTP_401_UNAUTHORIZED)

    return user, None


def _parse_datetime(value):
    """Parse an ISO / datetime-local string into an aware datetime, or None."""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if settings.USE_TZ and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _as_list(value):
    """Accept a native list (JSON body) or a JSON-encoded string (multipart)."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _as_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@api_view(['POST'])
def create_event(request):
    """POST /event/create-event/ - create an event.

    Canonical contract (see events-map.md). Accepts JSON or multipart. Banner may
    be provided as an uploaded file (`banner`) or an external URL (`banner_url`).
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    data = request.data
    field_errors = {}

    name = (data.get('name') or '').strip()
    if not name:
        field_errors['name'] = ['Event name is required.']
    elif len(name) < 4:
        field_errors['name'] = ['Event name must be at least 4 characters.']
    elif len(name) > 40:
        field_errors['name'] = ['Event name must be at most 40 characters.']

    event_type = (data.get('event_type') or '').strip().lower()
    if not event_type:
        field_errors['event_type'] = ['Event type is required.']
    elif event_type not in VALID_EVENT_TYPES:
        field_errors['event_type'] = ['Event type must be one of: physical, virtual, hybrid.']

    description = data.get('description')
    if description is None:
        description = data.get('desc')  # tolerate the legacy field name
    description = (description or '').strip()
    if not description:
        field_errors['description'] = ['Description is required.']

    start_date = _parse_datetime(data.get('start_date'))
    if data.get('start_date') and start_date is None:
        field_errors['start_date'] = ['Invalid start date format. Use ISO 8601.']
    elif not data.get('start_date'):
        field_errors['start_date'] = ['Start date is required.']

    end_date = _parse_datetime(data.get('end_date'))
    if data.get('end_date') and end_date is None:
        field_errors['end_date'] = ['Invalid end date format. Use ISO 8601.']
    elif not data.get('end_date'):
        field_errors['end_date'] = ['End date is required.']

    if start_date and end_date and end_date < start_date:
        field_errors['end_date'] = ['End date must be on or after the start date.']

    location = (data.get('location') or '').strip() or None
    virtual_link = (data.get('virtual_link') or data.get('event_link') or '').strip() or None

    if event_type in ('physical', 'hybrid') and not location:
        field_errors['location'] = ['Location is required for physical and hybrid events.']
    if event_type in ('virtual', 'hybrid') and not virtual_link:
        field_errors['virtual_link'] = ['Virtual link is required for virtual and hybrid events.']

    if field_errors:
        return _error('Validation failed.', 'VALIDATION_FAILED',
                      status.HTTP_400_BAD_REQUEST, field_errors=field_errors)

    # Optional fields
    category = (data.get('category') or '').strip() or None

    entry_fee = data.get('entry_fee', 0)
    try:
        entry_fee = entry_fee if entry_fee not in ('', None) else 0
        entry_fee = float(entry_fee)
    except (ValueError, TypeError):
        entry_fee = 0

    capacity = data.get('capacity')
    try:
        capacity = int(capacity) if capacity not in ('', None) else None
        if capacity is not None and capacity < 1:
            capacity = None
    except (ValueError, TypeError):
        capacity = None

    banner_url = (data.get('banner_url') or '').strip() or None

    # Optional game lookup (events do not require a game).
    game = None
    game_title = data.get('game_title') or data.get('game')
    game_id = data.get('game_id')
    if game_id:
        game = Games.objects.filter(game_id=game_id).first()
    elif game_title and str(game_title).strip():
        game = Games.objects.filter(game_title__iexact=str(game_title).strip()).first()

    try:
        with transaction.atomic():
            event = Event.objects.create(
                name=name,
                creator=user,
                game=game,
                event_type=event_type,
                category=category,
                desc=description,
                entry_fee=entry_fee,
                start_date=start_date,
                end_date=end_date,
                # Derive legacy split fields so admin / older readers stay consistent.
                event_date=start_date.date() if start_date else None,
                start_time=start_date.time() if start_date else None,
                end_time=end_date.time() if end_date else None,
                reg_start_date=timezone.now(),
                reg_end_date=start_date,
                location=location,
                event_link=virtual_link,
                capacity=capacity,
                banner=request.FILES.get('banner'),
                banner_url=banner_url,
                logo=request.FILES.get('logo'),
            )

            for tier in _as_list(data.get('ticket_types')):
                if not isinstance(tier, dict):
                    continue
                tier_name = (tier.get('name') or '').strip()
                if not tier_name:
                    continue
                try:
                    price = float(tier.get('price') or 0)
                except (ValueError, TypeError):
                    price = 0
                try:
                    quantity = int(tier.get('quantity') or 0)
                except (ValueError, TypeError):
                    quantity = 0
                TicketTier.objects.create(
                    event=event, name=tier_name, price=price,
                    quantity=max(quantity, 0), perks=(tier.get('perks') or '').strip(),
                )

            for sponsor in _as_list(data.get('sponsors')):
                if not isinstance(sponsor, dict):
                    continue
                sponsor_name = (sponsor.get('name') or '').strip()
                if not sponsor_name:
                    continue
                Sponsor.objects.create(
                    event=event, name=sponsor_name,
                    logo_url=(sponsor.get('logo_url') or '').strip() or None,
                )

            for vendor in _as_list(data.get('vendor_invites')):
                if not isinstance(vendor, dict):
                    continue
                vendor_name = (vendor.get('name') or '').strip()
                if not vendor_name:
                    continue
                VendorInvite.objects.create(
                    event=event, name=vendor_name,
                    email=(vendor.get('email') or '').strip() or None,
                    booth=(vendor.get('booth') or '').strip(),
                )

            for platform, url in _as_dict(data.get('social_links')).items():
                if url and str(url).strip():
                    SocialLink.objects.create(event=event, platform=platform, url=str(url).strip())

        return Response({
            'status': 'success',
            'data': {
                'event_id': event.event_id,
                'slug': event.slug,
                'event': serialize_event_detail(request, event),
            },
            'message': 'Event created successfully.',
        }, status=status.HTTP_201_CREATED)

    except Exception:
        logger.exception('create_event failed for user %s', getattr(user, 'username', None))
        return _error('Could not create the event. Please try again.',
                      'INTERNAL_ERROR', status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@api_view(['GET'])
def get_all_events(request):
    """GET /event/get-all-events/ - public listing with filters + pagination.

    Query params: type, category, q (search), from, to (YYYY-MM-DD), page.
    Returns the filtered page under `events` plus discovery sections
    (`featured`, `upcoming`, `by_game`) computed from the full active set.
    """
    base = (
        Event.objects.filter(is_active=True)
        .select_related('game', 'creator')
        .prefetch_related('ticket_tiers')
    )

    filtered = base
    event_type = request.GET.get('type')
    if event_type:
        filtered = filtered.filter(event_type__iexact=event_type)

    category = request.GET.get('category')
    if category:
        filtered = filtered.filter(category__iexact=category)

    search = request.GET.get('q') or request.GET.get('search')
    if search:
        filtered = filtered.filter(
            Q(name__icontains=search) | Q(desc__icontains=search) | Q(location__icontains=search)
        )

    date_from = parse_date(request.GET.get('from') or '')
    if date_from:
        filtered = filtered.filter(start_date__date__gte=date_from)

    date_to = parse_date(request.GET.get('to') or '')
    if date_to:
        filtered = filtered.filter(start_date__date__lte=date_to)

    filtered = filtered.order_by(F('start_date').desc(nulls_last=True))

    paginator = Paginator(filtered, PAGE_SIZE)
    try:
        page_number = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page_number = 1
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        page = paginator.page(paginator.num_pages) if paginator.num_pages else None

    events = list(page.object_list) if page else []

    # Discovery sections (independent of the active filters).
    now = timezone.now()
    featured = list(base.order_by('-interaction_count')[:5])
    upcoming = list(base.filter(start_date__gte=now).order_by('start_date')[:5])

    by_game = {}
    for event in base:
        game_name = event.game.game_title if event.game else 'Other'
        by_game.setdefault(game_name, []).append(serialize_event_card(request, event))

    return Response({
        'status': 'success',
        'data': {
            'events': [serialize_event_card(request, e) for e in events],
            'featured': [serialize_event_card(request, e) for e in featured],
            'upcoming': [serialize_event_card(request, e) for e in upcoming],
            'by_game': by_game,
            'page': page.number if page else 1,
            'total_pages': paginator.num_pages,
            'total_count': paginator.count,
        },
        'message': 'Events fetched successfully.',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@api_view(['GET'])
def view_event(request, event_id):
    """GET /event/view-event/<id or slug>/ - full event detail (public)."""
    from vent_auth.slugs import lookup_kwargs

    try:
        event = (
            Event.objects
            .select_related('game', 'creator')
            .prefetch_related('ticket_tiers', 'sponsors', 'social_links', 'vendor_invites')
            .get(is_active=True, **lookup_kwargs(event_id, id_field='event_id'))
        )
    except Event.DoesNotExist:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    Event.objects.filter(pk=event.pk).update(interaction_count=F('interaction_count') + 1)
    event.interaction_count += 1

    return Response({
        'status': 'success',
        'data': {'event': serialize_event_detail(request, event)},
        'message': 'Event fetched successfully.',
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
def event_vendors(request, event_id):
    """GET /event/<id>/vendors/ - vendor list for an event.

    Returns invited vendors gracefully (empty list, never an error) until the
    Phase 2 vendor-shop system is built.
    """
    try:
        event = Event.objects.prefetch_related('vendor_invites').get(event_id=event_id, is_active=True)
    except Event.DoesNotExist:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    vendors = [
        {
            'id': v.id,
            'name': v.name,
            'booth': v.booth or None,
            'category': None,   # populated when the vendor-shop system lands
            'logo': None,
        }
        for v in event.vendor_invites.all()
    ]

    return Response({
        'status': 'success',
        'data': {'vendors': vendors},
        'message': 'Vendors fetched successfully.',
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def buy_ticket(request, event_id):
    """POST /event/<id>/buy-ticket/ - PHASE 2 PLACEHOLDER.

    Real ticket purchasing (wallet deduction, QR issuance) is part of the Events
    phase and lives outside this app's M1 scope. This endpoint exists only so the
    FE receives a clean JSON envelope instead of an HTML 404 when the buy flow is
    triggered. It never touches the wallet.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    if not Event.objects.filter(event_id=event_id, is_active=True).exists():
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    return _error(
        'Ticket purchasing launches with the Events phase. This feature is not available yet.',
        'FEATURE_NOT_AVAILABLE', status.HTTP_503_SERVICE_UNAVAILABLE,
    )

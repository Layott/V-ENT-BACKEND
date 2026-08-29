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
from .models import Event, TicketTier, Sponsor, SponsorLink, SocialLink, VendorInvite
from .serializers import serialize_event_card, serialize_event_detail


def _event_by_ref(ref, **extra):
    """An event by slug or by id.

    The named address is what the slug rule requires, and the numeric one still
    has to resolve because links were shared before that rule existed.
    """
    from .models import Event

    ref = str(ref)
    if ref.isdigit():
        found = Event.objects.filter(event_id=int(ref), **extra).first()
        if found:
            return found
    return Event.objects.filter(slug=ref, **extra).first()



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

    # Which edition of that game, for an annual title. Ignored when it does not
    # belong to the game that was chosen, because an edition of a different
    # game is somebody's stale form rather than an instruction.
    series = None
    series_id = data.get('series_id')
    if series_id and game is not None:
        from vent_auth.models import GameSeries

        series = GameSeries.objects.filter(series_id=series_id, game=game).first()

    try:
        with transaction.atomic():
            event = Event.objects.create(
                name=name,
                creator=user,
                game=game,
                series=series,
                currency=(str(data.get('currency') or 'NGN').upper()[:3]),
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
                # A tier may be for one day of a multi-day event. Anything
                # outside the event's own dates is dropped rather than stored,
                # because a ticket for a day the event does not run is a door
                # nobody can walk through.
                day = parse_date((tier.get('day') or '').strip()) if tier.get('day') else None
                if day and event.start_date and event.end_date:
                    if not (event.start_date.date() <= day <= event.end_date.date()):
                        day = None

                TicketTier.objects.create(
                    event=event, name=tier_name, price=price,
                    quantity=max(quantity, 0), perks=(tier.get('perks') or '').strip(),
                    day=day, day_label=(tier.get('day_label') or '').strip(),
                )

            # Sponsors and partners are the same list with a different `kind`,
            # so one loop handles both and neither can drift from the other.
            supporters = list(_as_list(data.get('sponsors')))
            supporters += [
                dict(row, kind='partner') if isinstance(row, dict) else row
                for row in _as_list(data.get('partners'))
            ]

            for index, sponsor in enumerate(supporters):
                if not isinstance(sponsor, dict):
                    continue
                sponsor_name = (sponsor.get('name') or '').strip()
                if not sponsor_name:
                    continue

                kind = sponsor.get('kind')
                kind = kind if kind in ('sponsor', 'partner') else 'sponsor'

                # The wizard uploads a file per supporter, keyed by its position
                # in the combined list, because a row has no id until this loop
                # runs. logo_url stays accepted for anything still sending one.
                created = Sponsor.objects.create(
                    event=event, name=sponsor_name, kind=kind,
                    logo=request.FILES.get('sponsor_logo_%s' % index),
                    logo_url=(sponsor.get('logo_url') or '').strip() or None,
                    website=(sponsor.get('website') or '').strip() or None,
                    sort_order=index,
                )

                for platform, url in _as_dict(sponsor.get('links')).items():
                    url = (url or '').strip()
                    if url:
                        SponsorLink.objects.create(
                            sponsor=created, platform=platform, url=url)

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
    from vent_auth.slugs import resolve_or_redirect

    event, moved_to = resolve_or_redirect(
        event_id, entity_type='event', id_field='event_id', model=Event,
        queryset=(
            Event.objects
            .select_related('game', 'creator')
            .prefetch_related('ticket_tiers', 'sponsors', 'sponsors__links', 'social_links', 'vendor_invites')
            .filter(is_active=True)
        ),
    )
    if moved_to:
        # Renamed. A ticket emailed under the old name still has to open.
        return Response({
            'status': 'moved',
            'code': 'SLUG_CHANGED',
            'message': 'This event has been renamed.',
            'data': {'slug': moved_to, 'url': f'/events/{moved_to}'},
        }, status=status.HTTP_200_OK)
    if event is None:
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

@api_view(['PUT'])
def edit_event(request, event_id):
    """PUT /event/edit-event/{id}/ - correct an event.

    The organiser edits their own; an admin holding `manage_events` may correct
    somebody else's, which is written to the audit log. Partial: a field that is
    not sent is not touched, so a screen showing five fields cannot blank the
    other twenty by omission.
    """
    from vent_auth.actors import actor_from_request, may_override

    user, auth_error = actor_from_request(request)
    if auth_error is not None:
        return auth_error

    event = _event_by_ref(event_id)
    if event is None:
        from django.http import Http404

        raise Http404('No event matches %s' % event_id)

    is_owner = event.creator_id == user.user_id
    acting_as_admin = (not is_owner) and may_override(user, 'manage_events')
    if not is_owner and not acting_as_admin:
        return _error('Only the event organizer can edit this event.',
                      'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)

    data = request.data
    updated = []

    text_fields = [
        'name', 'desc', 'event_type', 'category', 'location', 'event_link',
        'banner_url',
    ]
    for field in text_fields:
        value = data.get(field)
        if value is not None:
            setattr(event, field, value)
            updated.append(field)

    # Numbers, guarded: a capacity of "soon" must not reach the column.
    for field in ('entry_fee', 'capacity'):
        value = data.get(field)
        if value in (None, ''):
            continue
        try:
            setattr(event, field, int(float(value)) if field == 'capacity' else value)
        except (TypeError, ValueError):
            return _error('%s must be a number.' % field, 'INVALID_NUMBER',
                          status.HTTP_400_BAD_REQUEST)
        updated.append(field)

    # How many tickets one email address may hold. Nullable on purpose, and an
    # empty string means "no limit" rather than "unchanged", because turning
    # the limit OFF is a thing the organiser has to be able to express. The
    # loop above skips empty values, which is why this one is separate.
    if 'max_tickets_per_email' in data:
        raw = data.get('max_tickets_per_email')
        if raw in (None, '', 0, '0'):
            event.max_tickets_per_email = None
        else:
            try:
                limit = int(raw)
            except (TypeError, ValueError):
                return _error('The limit per email must be a number.',
                              'INVALID_NUMBER', status.HTTP_400_BAD_REQUEST)
            if limit < 1:
                return _error('A limit of less than one ticket would sell '
                              'nothing at all.', 'INVALID_NUMBER',
                              status.HTTP_400_BAD_REQUEST)
            event.max_tickets_per_email = limit
        updated.append('max_tickets_per_email')

    for field in ('start_date', 'end_date'):
        value = data.get(field)
        if value in (None, ''):
            continue
        parsed = parse_datetime(value) if isinstance(value, str) else value
        if parsed is None:
            return _error('%s must be a date and time.' % field, 'INVALID_DATETIME',
                          status.HTTP_400_BAD_REQUEST)
        # A browser's datetime-local field sends "2026-07-26T23:30" with no zone,
        # and the stored dates are timezone aware. Comparing the two raises, so
        # the naive one is read as local time before it goes anywhere near a
        # comparison or the column.
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        setattr(event, field, parsed)
        updated.append(field)

    # An event that ends before it starts is the one ordering mistake worth
    # catching here, because nothing downstream can make sense of it.
    if event.start_date and event.end_date and event.end_date < event.start_date:
        return _error('The event cannot end before it starts.',
                      'END_BEFORE_START', status.HTTP_400_BAD_REQUEST)

    if 'is_active' in data:
        event.is_active = str(data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if request.FILES.get('logo'):
        event.logo = request.FILES['logo']
        updated.append('logo')
    if request.FILES.get('banner'):
        event.banner = request.FILES['banner']
        updated.append('banner')

    if not updated:
        return _error('Nothing to change.', 'NO_FIELDS_TO_UPDATE',
                      status.HTTP_400_BAD_REQUEST)

    # save() adds `slug` itself when the name changed, so a rename keeps every
    # link ever shared instead of silently dropping the new slug.
    event.save(update_fields=updated + ['last_updated'])

    if acting_as_admin:
        # An organiser who finds their venue changed deserves to be able to find
        # out who changed it.
        from vent_auth.models import AdminAction

        AdminAction.objects.create(
            admin=user,
            action_type='edit_event',
            target_model='Event',
            target_id=str(event.event_id),
            metadata={'updated_fields': updated, 'owner_id': event.creator_id},
        )

    return Response({
        'status': 'success',
        'message': 'Event updated.',
        'data': {
            'event': serialize_event_detail(request, event),
            'updated_fields': updated,
            'edited_as_admin': acting_as_admin,
        },
    }, status=status.HTTP_200_OK)

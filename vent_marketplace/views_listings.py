"""Listings: making one, finding one, and looking at one.

Everything here is behind `switch.gated` at the urlconf, so none of it answers
while Vermillion City is closed. That is applied once per route rather than as a
decorator on each view, because a rule remembered at twenty sites holds at
nineteen.
"""
from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth import premium
from vent_auth.actors import actor_from_request, may_override
from vent_auth.slugs import lookup_kwargs, resolve_or_redirect

from . import catalogue, listings as listing_rules
from .models import Listing, ListingMedia, Review


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _find(reference):
    if not reference:
        return None
    return Listing.objects.filter(
        **lookup_kwargs(reference, id_field='listing_id')).first()


def _person(user):
    """One description of a person, so a card never builds its own."""
    if user is None:
        return None
    profile = user.userprofile_set.order_by('profile_id').first()
    picture = getattr(profile, 'profile_picture', None)
    return {
        'username': user.username,
        'name': user.full_name or user.username,
        'avatar': picture.url if picture else None,
    }


def _seller_record(user):
    """What a buyer needs to decide whether to trust somebody."""
    from .models import Purchase

    reviews = Review.objects.filter(seller=user).aggregate(
        count=Count('id'), average=Avg('rating'))
    return {
        **(_person(user) or {}),
        'sales': Purchase.objects.filter(seller=user, status='released').count(),
        'reviews': reviews['count'] or 0,
        'rating': round(reviews['average'], 1) if reviews['average'] else None,
    }


def _row(listing, *, full=False):
    data = {
        'listing_id': listing.listing_id,
        'slug': listing.slug,
        'kind': listing.kind,
        'category': listing.category,
        'title': listing.title,
        'price': listing.price,
        'price_kind': listing.price_kind,
        'quantity': listing.quantity,
        'status': listing.status,
        'bidding': listing.bidding,
        'bids_close_at': listing.bids_close_at,
        'expires_at': listing.expires_at,
        'tags': listing.tags or [],
        'seller': _person(listing.seller),
        'created_at': listing.created_at,
        'cover': None,
        # Hoisting is the premium promotion the spec names. A listing that is
        # hoisted says so, because an advert that does not admit it is one is
        # the thing people resent.
        'hoisted': bool(listing.hoisted_until
                        and listing.hoisted_until > timezone.now()),
    }
    cover = listing.media.filter(portfolio=False).first()
    if cover and cover.file:
        data['cover'] = cover.file.url

    if full:
        data.update({
            'description': listing.description,
            'duration_minutes': listing.duration_minutes,
            'available_from': listing.available_from,
            'available_to': listing.available_to,
            'experience': listing.experience,
            'delivery': listing.delivery,
            'location': listing.location,
            'offered': listing.offered,
            'wanted': listing.wanted,
            'trade_value': listing.trade_value,
            'condition': listing.condition,
            'payment_methods': listing.payment_methods,
            'response_time_hours': listing.response_time_hours,
            'discount_code': listing.discount_code,
            'discount_percent': listing.discount_percent,
            'seller_record': _seller_record(listing.seller),
            'media': [
                {'id': m.id, 'url': m.file.url if m.file else None,
                 'caption': m.caption, 'portfolio': m.portfolio}
                for m in listing.media.all()
            ],
            'reviews': [
                {'rating': r.rating, 'body': r.body, 'at': r.created_at,
                 'by': _person(r.reviewer)}
                for r in listing.reviews.select_related('reviewer')[:20]
            ],
        })
    return data


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def listing_catalogue(request):
    """GET /marketplace/catalogue/ - what a form may ask, from the table that checks it."""
    return _ok(catalogue.catalogue(), 'Marketplace catalogue')


@api_view(['GET'])
@permission_classes([AllowAny])
def browse(request):
    """GET /marketplace/listings/ - search and filter.

    The spec's filters: category, game, price range, seller rating. Rating is
    applied last, in Python, because it is an aggregate over another table and
    filtering on it in SQL would mean a join that makes every other filter
    slower for the one filter almost nobody sets.
    """
    rows = (Listing.objects.filter(status='active')
            .select_related('seller')
            .prefetch_related('media'))

    search = (request.GET.get('q') or '').strip()
    if search:
        rows = rows.filter(Q(title__icontains=search)
                           | Q(description__icontains=search)
                           | Q(offered__icontains=search)
                           | Q(wanted__icontains=search))

    for field in ('kind', 'category'):
        value = (request.GET.get(field) or '').strip()
        if value:
            rows = rows.filter(**{field: value})

    game = (request.GET.get('game') or '').strip()
    if game:
        rows = rows.filter(game__game_title__iexact=game)

    org = (request.GET.get('organization') or '').strip()
    if org:
        rows = rows.filter(organization__slug=org)

    tournament = (request.GET.get('tournament') or '').strip()
    if tournament:
        rows = rows.filter(tournament__slug=tournament)

    for field, lookup in (('min_price', 'price__gte'), ('max_price', 'price__lte')):
        raw = (request.GET.get(field) or '').strip()
        if raw.isdigit():
            rows = rows.filter(**{lookup: int(raw)})

    # Hoisted first, then newest. That is what the seller paid for, and it is
    # the whole of what they paid for: no other ranking is quietly bought.
    rows = rows.order_by('-hoisted_until', '-created_at')[:200]

    out = [_row(listing) for listing in rows]

    min_rating = (request.GET.get('min_rating') or '').strip()
    if min_rating:
        try:
            floor = float(min_rating)
            keep = []
            for row, listing in zip(out, rows):
                record = _seller_record(listing.seller)
                if (record['rating'] or 0) >= floor:
                    row['seller_record'] = record
                    keep.append(row)
            out = keep
        except ValueError:
            pass

    return _ok({'listings': out, 'count': len(out)}, 'Listings')


@api_view(['GET'])
@permission_classes([AllowAny])
def listing_detail(request, reference):
    """GET /marketplace/listings/<ref>/ - one listing in full.

    A listing that was renamed keeps every address it has ever had. The move is
    reported in the envelope with 200 rather than as a 301, because `fetch()`
    follows a redirect transparently and would chase a frontend path against
    the API host.
    """
    listing, moved_to = resolve_or_redirect(
        reference, entity_type='listing', id_field='listing_id', model=Listing)
    if moved_to:
        return Response({
            'status': 'moved',
            'data': {'slug': moved_to,
                     'url': '/marketplace/listing/%s' % moved_to},
            'message': 'This listing was renamed.',
        })
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    # A view is a view by somebody who is not the seller. Counting the seller
    # refreshing their own page makes the premium analytics a lie.
    user, _err_response = actor_from_request(request)
    if user is None or user.user_id != listing.seller_id:
        Listing.objects.filter(pk=listing.pk).update(views=listing.views + 1)

    return _ok({'listing': _row(listing, full=True)}, 'Listing')


@api_view(['GET'])
@permission_classes([AllowAny])
def seller_detail(request, username):
    """GET /marketplace/sellers/<username>/ - somebody's record and what they list."""
    from vent_auth.models import Users

    seller = Users.objects.filter(username=username).first()
    if seller is None:
        return _err('No such seller.', 'SELLER_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    rows = Listing.objects.filter(seller=seller, status='active')
    return _ok({
        'seller': _seller_record(seller),
        'listings': [_row(listing) for listing in rows],
    }, 'Seller')


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

@api_view(['POST'])
def create_listing(request):
    """POST /marketplace/listings/ - make one."""
    user, err = actor_from_request(request)
    if err:
        return err

    try:
        cleaned = listing_rules.clean(request.data, seller=user)
    except listing_rules.ListingError as exc:
        return _err(str(exc), 'VALIDATION_FAILED',
                    field=getattr(exc, 'field', None))

    bid_days = cleaned.pop('bid_days', None)
    publish = bool(request.data.get('publish'))

    if publish:
        allowed, limit = listing_rules.may_publish(user)
        if not allowed:
            return _err(
                'A free account keeps %s listing live at a time. Pause the '
                'other one, or ask about premium.' % limit,
                'LISTING_LIMIT', status.HTTP_409_CONFLICT)

    listing = Listing(seller=user, status='active' if publish else 'draft',
                      **cleaned)
    if bid_days:
        listing.bids_close_at = timezone.now() + timezone.timedelta(days=bid_days)
    listing.save()
    return _ok({'listing': _row(listing, full=True)}, 'Listing saved.',
               status.HTTP_201_CREATED)


def _mine(user, listing):
    # `ban_users` is the moderation permission: taking down somebody else's
    # listing is a moderation act, not a financial one.
    return (listing.seller_id == user.user_id
            or may_override(user, 'ban_users'))


@api_view(['PUT', 'PATCH'])
def edit_listing(request, reference):
    """PUT /marketplace/listings/<ref>/ - change one."""
    user, err = actor_from_request(request)
    if err:
        return err
    listing = _find(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _mine(user, listing):
        return _err('This is not your listing to change.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    # Once money is held against it, the terms are what somebody agreed to.
    if listing.purchases.filter(status__in=('held', 'disputed')).exists():
        return _err('Somebody has paid for this and is waiting. It cannot be '
                    'changed until that is settled.', 'PURCHASE_PENDING',
                    status.HTTP_409_CONFLICT)

    # An edit starts from what is STORED and takes the request on top. Starting
    # from the request alone means changing a title refuses for want of a price
    # nobody was asked to resend, which is exactly what the first version did
    # and what the rename test caught.
    merged = {
        'kind': listing.kind,
        'category': listing.category,
        'title': listing.title,
        'description': listing.description,
        'tags': listing.tags or [],
    }
    for field in catalogue.fields_for(listing.kind):
        value = getattr(listing, field, None)
        if value not in (None, ''):
            merged[field] = value
    if listing.discount_code:
        merged['discount_code'] = listing.discount_code
        merged['discount_percent'] = listing.discount_percent
    merged.update({k: v for k, v in request.data.items()})

    try:
        cleaned = listing_rules.clean(merged, seller=user)
    except listing_rules.ListingError as exc:
        return _err(str(exc), 'VALIDATION_FAILED',
                    field=getattr(exc, 'field', None))
    cleaned.pop('bid_days', None)

    for field, value in cleaned.items():
        setattr(listing, field, value)
    listing.save()
    return _ok({'listing': _row(listing, full=True)}, 'Listing saved.')


@api_view(['POST'])
def set_status(request, reference):
    """POST /marketplace/listings/<ref>/status/ - publish, pause or take down."""
    user, err = actor_from_request(request)
    if err:
        return err
    listing = _find(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _mine(user, listing):
        return _err('This is not your listing to change.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    wanted = str(request.data.get('status') or '').strip()
    if wanted not in ('active', 'paused', 'removed'):
        return _err('Live, paused, or taken down.', 'VALIDATION_FAILED',
                    field='status')

    if wanted == 'active' and listing.status != 'active':
        allowed, limit = listing_rules.may_publish(user)
        if not allowed:
            return _err(
                'A free account keeps %s listing live at a time.' % limit,
                'LISTING_LIMIT', status.HTTP_409_CONFLICT)

    listing.status = wanted
    listing.save(update_fields=['status'])
    return _ok({'listing': _row(listing)}, 'Listing updated.')


@api_view(['POST'])
def add_media(request, reference):
    """POST /marketplace/listings/<ref>/media/ - a picture or a video.

    `portfolio=true` is the premium half: the spec puts "samples of previous
    work" behind premium and ordinary product images in front of it.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    listing = _find(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _mine(user, listing):
        return _err('This is not your listing.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    upload = request.FILES.get('file')
    if upload is None:
        return _err('Choose a file.', 'VALIDATION_FAILED', field='file')

    portfolio = str(request.data.get('portfolio') or '').lower() in ('1', 'true', 'yes')
    if portfolio and not premium.has_premium(user):
        return Response(premium.refuse('media_export'),
                        status=status.HTTP_402_PAYMENT_REQUIRED)

    media = ListingMedia.objects.create(
        listing=listing, file=upload, portfolio=portfolio,
        caption=str(request.data.get('caption') or '')[:140],
        order=listing.media.count())
    return _ok({'id': media.id, 'url': media.file.url,
                'portfolio': media.portfolio}, 'Added.',
               status.HTTP_201_CREATED)


@api_view(['GET'])
def my_listings(request):
    """GET /marketplace/mine/ - the seller's own dashboard.

    Analytics - views, inquiries, completions - is one of the premium lines, so
    the numbers are only filled in for an account that may see them. The rows
    themselves are always theirs to read.
    """
    user, err = actor_from_request(request)
    if err:
        return err

    rows = Listing.objects.filter(seller=user).prefetch_related('media')
    may_see = premium.has_premium(user)
    out = []
    for listing in rows:
        row = _row(listing)
        row['analytics'] = ({
            'views': listing.views,
            'inquiries': listing.inquiries,
            'completed': listing.completed,
        } if may_see else None)
        out.append(row)

    allowed, limit = listing_rules.may_publish(user)
    return _ok({
        'listings': out,
        'can_publish': allowed,
        'active_limit': limit,
        'has_premium': may_see,
    }, 'Your listings')

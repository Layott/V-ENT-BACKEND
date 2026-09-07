"""Running a stall: your stalls, your products, your orders.

CEO, 7 September 2026: "run the vendor UI properly. build all screens."

The stall itself was built yesterday and `tools/endpoint-callers.py` then said
what nobody had noticed: **nothing on the site called any of it**. Somebody who
bought a pitch got a stall they could not stock, because the only way to add a
product was a POST nobody could make from a screen.

Three things were missing on this side before the screens could exist:

    my stalls        a vendor had no way to find their own
    editing          a product could be created and never changed or removed
    fulfilment       an order could be collected, and never sent anywhere

Delivery is the one worth explaining. `collect` and `deliver` are chosen by the
BUYER at checkout, because that decides whether an address is needed at all,
and asking everybody for a postal address when most people are walking ten
metres to a table is how a checkout loses people. The address lives on the
ORDER rather than being read off the buyer's profile: somebody may want a
parcel sent to an office, to a friend, or to the venue, and quietly using a
profile address is how a package goes to the wrong place with nobody having
typed anything wrong.

`sent` and `delivered` are separate states because a stallholder can only ever
know the first. Collapsing them would make them assert something they cannot
see.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Vendor, VendorOrder, VendorProduct
from .views_tickets import _authenticate, _error, _ngn_to_coins, _ok

PAGE_SIZE = 100


def _abs(request, filefield):
    if not filefield:
        return None
    try:
        return request.build_absolute_uri(filefield.url)
    except ValueError:
        return None


def _my_stall(user, ref):
    """A stall this person owns, by slug or key, or None.

    Ownership is checked HERE rather than by the caller, so no endpoint in this
    file can forget it. Every one of them changes something.
    """
    qs = Vendor.objects.select_related('event').filter(owner=user)
    found = qs.filter(slug=str(ref)).first()
    if found is None and str(ref).isdigit():
        found = qs.filter(id=int(ref)).first()
    return found


def _product_row(request, p):
    return {
        'id': p.id,
        'name': p.name,
        'description': p.description,
        'price_ngn': float(p.price),
        'price_vc': int(_ngn_to_coins(p.price)),
        'image': _abs(request, p.image),
        'stock': p.stock,
        'sold': p.sold,
        'in_stock': p.stock > 0,
        'is_active': p.is_active,
        'variants': p.variants or [],
        'can_deliver': p.can_deliver,
    }


def _order_row(request, o):
    return {
        'id': o.id,
        'code': o.code,
        'status': o.status,
        'total_vc': o.total_vc,
        'created_at': o.created_at,
        'collected_at': o.collected_at,
        'fulfilment': o.fulfilment,
        'buyer': o.buyer.username if o.buyer_id else '',
        'buyer_name': (o.buyer.full_name or o.buyer.username) if o.buyer_id else '',
        'items': [
            {
                'id': i.id,
                'product': i.product.name if i.product_id else '',
                'variant': i.variant or None,
                'quantity': i.quantity,
                'unit_vc': i.unit_vc,
            }
            for i in o.items.select_related('product')
        ],
        # Only when it is being delivered. Sending an empty address block on
        # every collection order invites a screen to draw an empty "Deliver to"
        # panel on orders nobody is delivering.
        'delivery': ({
            'name': o.delivery_name,
            'phone': o.delivery_phone,
            'address': o.delivery_address,
            'note': o.delivery_note or None,
            'fee_vc': o.delivery_fee_vc,
            'tracking': o.tracking or None,
            'sent_at': o.sent_at,
            'delivered_at': o.delivered_at,
        } if o.fulfilment == 'deliver' else None),
    }


@api_view(['GET'])
def my_stalls(request):
    """GET /event/my-stalls/ - the stalls this person runs.

    A vendor had no way to find their own stall at all. They bought a pitch,
    got a confirmation, and then there was no address on the platform that
    listed it.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    rows = []
    for v in Vendor.objects.select_related('event').filter(owner=user):
        rows.append({
            'id': v.id,
            'slug': v.slug,
            'name': v.name,
            'status': v.status,
            'category': v.category or None,
            'description': v.description,
            'logo': _abs(request, v.logo),
            'banner': _abs(request, v.banner),
            'product_count': v.products.filter(is_active=True).count(),
            # What the stallholder actually wants to know when they open this.
            'open_orders': v.orders.filter(
                status__in=('paid', 'ready', 'sent')).count(),
            'event': {
                'id': v.event.event_id,
                'slug': v.event.slug,
                'name': v.event.name,
                'start_date': v.event.start_date,
            } if v.event_id else None,
        })
    return _ok({'stalls': rows, 'count': len(rows)}, 'Your stalls.')


@api_view(['GET', 'PATCH'])
def my_stall_detail(request, vendor_id):
    """GET or PATCH one of my stalls: its name, description, category, images."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    stall = _my_stall(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    if request.method == 'PATCH':
        fields = []
        for key in ('name', 'description', 'category'):
            if key in request.data:
                setattr(stall, key, (request.data.get(key) or '').strip())
                fields.append(key)
        # Opening and closing the stall. `approved` and `live` both trade;
        # `closed` is the stallholder saying they have stopped for the day, and
        # they must be able to undo it.
        if 'status' in request.data:
            wanted = str(request.data.get('status') or '').strip()
            if wanted not in ('live', 'closed'):
                return _error('A stall can be live or closed.',
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            if stall.status == 'pending' and wanted == 'live':
                return _error('The organiser has not approved this stall yet.',
                              'NOT_APPROVED', status.HTTP_409_CONFLICT)
            stall.status = wanted
            fields.append('status')
        for key, f in (('logo', 'logo'), ('banner', 'banner')):
            if key in request.FILES:
                setattr(stall, f, request.FILES[key])
                fields.append(f)
        if fields:
            stall.save(update_fields=fields)

    return _ok({
        'stall': {
            'id': stall.id, 'slug': stall.slug, 'name': stall.name,
            'status': stall.status, 'category': stall.category or None,
            'description': stall.description,
            'logo': _abs(request, stall.logo),
            'banner': _abs(request, stall.banner),
        },
        'products': [_product_row(request, p) for p in stall.products.all()],
    }, 'Your stall.')


@api_view(['PATCH', 'DELETE'])
def my_product(request, vendor_id, product_id):
    """Change or remove one product.

    A product could be created and never edited: no price change, no restock,
    no taking something off the table when it ran out. That is most of running
    a stall.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    stall = _my_stall(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    product = VendorProduct.objects.filter(id=product_id, vendor=stall).first()
    if product is None:
        return _error('That product is not on this stall.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        if product.sold:
            # Hidden rather than deleted: past orders point at it, and deleting
            # the row would leave somebody's receipt naming nothing.
            product.is_active = False
            product.save(update_fields=['is_active'])
            return _ok({'hidden': True},
                       'Taken off the stall. Past orders still show it.')
        product.delete()
        return _ok({'deleted': True}, 'Removed.')

    fields = []
    for key in ('name', 'description'):
        if key in request.data:
            setattr(product, key, (request.data.get(key) or '').strip())
            fields.append(key)
    for key, caster, label in (('price', float, 'price'),
                               ('stock', int, 'number in stock')):
        if key in request.data:
            try:
                value = caster(request.data.get(key))
            except (TypeError, ValueError):
                return _error('The %s has to be a number.' % label,
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            if value < 0:
                return _error('The %s cannot be negative.' % label,
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            setattr(product, key, value)
            fields.append(key)
    if 'is_active' in request.data:
        product.is_active = bool(request.data.get('is_active'))
        fields.append('is_active')
    if 'can_deliver' in request.data:
        product.can_deliver = bool(request.data.get('can_deliver'))
        fields.append('can_deliver')
    if 'variants' in request.data:
        raw = request.data.get('variants')
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(',')]
        if not isinstance(raw, list):
            return _error('Choices have to be a list, or a comma separated line.',
                          'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
        product.variants = [str(x).strip()[:60] for x in raw if str(x).strip()][:20]
        fields.append('variants')
    if 'image' in request.FILES:
        product.image = request.FILES['image']
        fields.append('image')

    if fields:
        product.save(update_fields=fields)
    return _ok({'product': _product_row(request, product)}, 'Saved.')


@api_view(['GET'])
def my_stall_orders(request, vendor_id):
    """Every order on one of my stalls, newest first."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    stall = _my_stall(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    wanted = request.GET.get('status')
    orders = stall.orders.select_related('buyer').prefetch_related('items__product')
    if wanted:
        orders = orders.filter(status=wanted)

    rows = [_order_row(request, o) for o in orders[:PAGE_SIZE]]
    return _ok({
        'orders': rows,
        'count': len(rows),
        # The stallholder's own summary, so the screen does not have to count
        # and get a different answer from the one the list shows.
        'open': stall.orders.filter(status__in=('paid', 'ready', 'sent')).count(),
        'to_deliver': stall.orders.filter(fulfilment='deliver',
                                          status__in=('paid', 'ready')).count(),
    }, 'Orders on your stall.')


# What a stallholder may move an order to, and from where. Written as a table
# rather than as branches, because "can I mark this collected" is a question
# with one answer and three screens will otherwise each decide it differently.
NEXT_STATUS = {
    'ready': ('paid',),
    'collected': ('paid', 'ready'),
    'sent': ('paid', 'ready'),
    'delivered': ('sent',),
    'cancelled': ('paid', 'ready'),
}


@api_view(['POST'])
def my_order_status(request, vendor_id, code):
    """Move one order along: ready, collected, sent, delivered, cancelled."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    stall = _my_stall(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    wanted = str(request.data.get('status') or '').strip()
    if wanted not in NEXT_STATUS:
        return _error('That is not something an order can become.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        order = VendorOrder.objects.select_for_update().filter(
            code=code, vendor=stall).first()
        if order is None:
            return _error('No such order on this stall.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)

        if order.status == wanted:
            # Not an error. Two taps on a slow connection is one intention, and
            # a red toast for the second is a lie about what happened.
            return _ok({'order': _order_row(request, order)}, 'Already there.')

        if order.status not in NEXT_STATUS[wanted]:
            return _error(
                'An order that is %s cannot become %s.' % (order.status, wanted),
                'BAD_TRANSITION', status.HTTP_409_CONFLICT)

        if wanted == 'sent' and order.fulfilment != 'deliver':
            return _error('This order is being collected from the stall, not '
                          'delivered.', 'NOT_A_DELIVERY',
                          status.HTTP_409_CONFLICT)

        fields = ['status']
        order.status = wanted
        if wanted == 'collected':
            order.collected_at = timezone.now()
            fields.append('collected_at')
        if wanted == 'sent':
            order.sent_at = timezone.now()
            fields.append('sent_at')
            tracking = (request.data.get('tracking') or '').strip()
            if tracking:
                order.tracking = tracking[:80]
                fields.append('tracking')
        if wanted == 'delivered':
            order.delivered_at = timezone.now()
            fields.append('delivered_at')
        order.save(update_fields=fields)

    return _ok({'order': _order_row(request, order)}, 'Order updated.')

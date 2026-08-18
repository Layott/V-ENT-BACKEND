"""Vendor shops - stalls at an event, their products, and wallet-paid orders.

Same money discipline as ticketing: wallet row locked, PIN verified, stock and
debit written in one transaction, a Transaction row for the ledger.
"""
import secrets

from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from vent_auth.models import UserWallet, Transaction
from .models import Event, Vendor, VendorProduct, VendorOrder, VendorOrderItem
from .views_tickets import _authenticate, _error, _ok, _ngn_to_coins, CODE_ALPHABET


def _new_order_code():
    while True:
        code = 'VS-' + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not VendorOrder.objects.filter(code=code).exists():
            return code


def _abs(request, filefield):
    if not filefield:
        return None
    try:
        return request.build_absolute_uri(filefield.url)
    except ValueError:
        return None


def serialize_product(request, p):
    return {
        'id': p.id,
        'name': p.name,
        'description': p.description,
        'price_ngn': float(p.price),
        'price': _ngn_to_coins(p.price),      # VC - what the storefront renders
        'price_vc': _ngn_to_coins(p.price),
        'image': _abs(request, p.image),
        'stock': p.stock,
        'sold': p.sold,
        'in_stock': p.stock > 0,
        'is_active': p.is_active,
    }


def serialize_vendor(request, v, include_products=False):
    data = {
        'id': v.id,
        'event_id': v.event_id,
        'name': v.name,
        'category': v.category or None,
        'description': v.description,
        'booth': v.booth or None,
        'booth_number': v.booth or None,
        'logo': _abs(request, v.logo),
        'banner': _abs(request, v.banner),
        'status': v.status,
        'owner': v.owner.username if v.owner else None,
        'product_count': v.products.filter(is_active=True).count(),
    }
    if include_products:
        data['products'] = [
            serialize_product(request, p) for p in v.products.filter(is_active=True)
        ]
    return data


# ---------------------------------------------------------------------------
# GET /event/<event_id>/vendors/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def event_vendors(request, event_id):
    event = Event.objects.filter(event_id=event_id, is_active=True).first()
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    vendors = event.vendors.exclude(status='closed').prefetch_related('products')
    return _ok(
        {
            'event_id': event.event_id,
            'event_name': event.name,
            # Products included: the shop page browses across every stall's
            # catalogue, not just the stall list.
            'vendors': [serialize_vendor(request, v, include_products=True) for v in vendors],
            'count': vendors.count(),
        },
        'Vendors retrieved.',
    )


# ---------------------------------------------------------------------------
# GET /event/<event_id>/vendor/<vendor_id>/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def vendor_detail(request, event_id, vendor_id):
    vendor = Vendor.objects.filter(id=vendor_id, event_id=event_id).prefetch_related('products').first()
    if vendor is None:
        return _error('Vendor not found for this event.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    return _ok(
        {'vendor': serialize_vendor(request, vendor, include_products=True)},
        'Vendor retrieved.',
    )


# ---------------------------------------------------------------------------
# POST /event/<event_id>/vendors/create/   - organizer only
# ---------------------------------------------------------------------------

@api_view(['POST'])
def create_vendor(request, event_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event = Event.objects.filter(event_id=event_id).first()
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id:
        return _error('Only the event organizer can add vendors.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    name = (request.data.get('name') or '').strip()
    if not name:
        return _error('A stall name is required.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    owner = None
    owner_username = (request.data.get('owner_username') or '').strip()
    if owner_username:
        from vent_auth.models import Users
        owner = Users.objects.filter(username=owner_username).first()
        if owner is None:
            return _error(f'No user called @{owner_username}.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    vendor = Vendor.objects.create(
        event=event,
        owner=owner,
        name=name[:120],
        category=(request.data.get('category') or '').strip()[:60],
        description=(request.data.get('description') or '').strip(),
        booth=(request.data.get('booth') or '').strip()[:40],
        status=request.data.get('status') if request.data.get('status') in dict(Vendor.STATUS_CHOICES) else 'approved',
    )
    return Response(
        {'status': 'success', 'data': {'vendor': serialize_vendor(request, vendor)},
         'message': f'{vendor.name} added to {event.name}.'},
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# POST /event/vendor/<vendor_id>/products/  - vendor owner or event organizer
# ---------------------------------------------------------------------------

@api_view(['POST'])
def create_product(request, vendor_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    vendor = Vendor.objects.select_related('event').filter(id=vendor_id).first()
    if vendor is None:
        return _error('Vendor not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if vendor.owner_id != user.user_id and vendor.event.creator_id != user.user_id:
        return _error('Only the stall owner or the event organizer can add products.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    name = (request.data.get('name') or '').strip()
    if not name:
        return _error('A product name is required.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    try:
        price = float(request.data.get('price', request.data.get('price_ngn', 0)) or 0)
        stock = int(request.data.get('stock', 0) or 0)
    except (TypeError, ValueError):
        return _error('Price and stock must be numbers.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if price < 0 or stock < 0:
        return _error('Price and stock cannot be negative.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    product = VendorProduct.objects.create(
        vendor=vendor,
        name=name[:140],
        description=(request.data.get('description') or '').strip(),
        price=price,
        stock=stock,
    )
    return Response(
        {'status': 'success', 'data': {'product': serialize_product(request, product)},
         'message': f'{product.name} listed.'},
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# POST /event/vendor/<vendor_id>/order/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def create_order(request, vendor_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    vendor = Vendor.objects.filter(id=vendor_id).first()
    if vendor is None:
        return _error('Vendor not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if vendor.status == 'closed':
        return _error(f'{vendor.name} is closed.', 'VENDOR_CLOSED', status.HTTP_409_CONFLICT)

    items = request.data.get('items') or []
    pin = request.data.get('pin')
    if not isinstance(items, list) or not items:
        return _error('Add at least one item to your order.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        priced = []
        total_vc = 0
        for raw in items:
            if not isinstance(raw, dict):
                return _error('Malformed order item.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            try:
                qty = int(raw.get('quantity', raw.get('qty', 1)))
            except (TypeError, ValueError):
                return _error('Quantity must be a number.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            if qty < 1:
                return _error('Quantity must be at least 1.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

            product = VendorProduct.objects.select_for_update().filter(
                id=raw.get('product_id') or raw.get('id'), vendor=vendor, is_active=True
            ).first()
            if product is None:
                return _error('One of those products is no longer available.',
                              'NOT_FOUND', status.HTTP_404_NOT_FOUND)
            if product.stock < qty:
                return _error(f'Only {product.stock} × {product.name} left.',
                              'INSUFFICIENT_STOCK', status.HTTP_409_CONFLICT)

            unit_vc = _ngn_to_coins(product.price)
            total_vc += unit_vc * qty
            priced.append((product, qty, unit_vc))

        wallet = UserWallet.objects.select_for_update().filter(user=user).first()
        if wallet is None:
            return _error('No wallet found for this account.', 'NO_WALLET', status.HTTP_400_BAD_REQUEST)

        if total_vc > 0:
            if not wallet.pin_hash:
                return _error('Set a wallet PIN before buying.', 'PIN_REQUIRED', status.HTTP_400_BAD_REQUEST)
            if not pin or not check_password(str(pin), wallet.pin_hash):
                return _error('Incorrect wallet PIN.', 'INVALID_PIN', status.HTTP_400_BAD_REQUEST)
            if wallet.wallet_balance < total_vc:
                return _error(
                    f'You need {total_vc} VC - your balance is {wallet.wallet_balance} VC.',
                    'INSUFFICIENT_BALANCE', status.HTTP_400_BAD_REQUEST,
                )
            wallet.wallet_balance -= total_vc
            wallet.save(update_fields=['wallet_balance'])
            Transaction.objects.create(
                wallet=wallet, type='deduction', amount=-total_vc,
                description=f'Order at {vendor.name}', status='completed',
            )

        order = VendorOrder.objects.create(
            vendor=vendor, buyer=user, code=_new_order_code(), total_vc=total_vc,
        )
        for product, qty, unit_vc in priced:
            VendorOrderItem.objects.create(order=order, product=product, quantity=qty, unit_vc=unit_vc)
            VendorProduct.objects.filter(id=product.id).update(
                stock=F('stock') - qty, sold=F('sold') + qty,
            )

    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            user=user, category='event',
            title=f'Order {order.code} confirmed',
            body=f'{vendor.name} · {total_vc} VC',
            link='/events/vendor-shop',
            metadata={'vendor_id': vendor.id, 'order_code': order.code},
        )
        if vendor.owner_id and vendor.owner_id != user.user_id:
            create_notification(
                user=vendor.owner, category='event',
                title=f'New order at {vendor.name}',
                body=f'{order.code} · {total_vc} VC',
                link='/events/vendor-shop',
                metadata={'vendor_id': vendor.id, 'order_code': order.code},
            )
    except Exception:
        pass

    return Response(
        {'status': 'success',
         'data': {'order': serialize_order(request, order), 'wallet_balance': wallet.wallet_balance,
                  'new_balance': wallet.wallet_balance},
         'message': f'Order {order.code} placed.'},
        status=status.HTTP_201_CREATED,
    )


def serialize_order(request, order):
    return {
        'id': order.id,
        'code': order.code,
        'status': order.status,
        'total_vc': order.total_vc,
        'created_at': order.created_at,
        'collected_at': order.collected_at,
        'vendor': {'id': order.vendor_id, 'name': order.vendor.name, 'booth': order.vendor.booth or None},
        'buyer': order.buyer.username,
        'items': [
            {
                'product_id': i.product_id,
                'name': i.product.name,
                'quantity': i.quantity,
                'unit_vc': i.unit_vc,
                'line_vc': i.unit_vc * i.quantity,
            }
            for i in order.items.select_related('product')
        ],
    }


# ---------------------------------------------------------------------------
# GET /event/vendor-orders/  - the buyer's own orders
# ---------------------------------------------------------------------------

@api_view(['GET'])
def my_vendor_orders(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    orders = (
        VendorOrder.objects.filter(buyer=user)
        .select_related('vendor')
        .prefetch_related('items__product')
    )
    return _ok(
        {'orders': [serialize_order(request, o) for o in orders], 'count': orders.count()},
        'Orders retrieved.',
    )


# ---------------------------------------------------------------------------
# GET  /event/vendor/<vendor_id>/orders/          - stall owner
# POST /event/vendor/order/<code>/collect/        - stall owner marks collected
# ---------------------------------------------------------------------------

@api_view(['GET'])
def vendor_orders(request, vendor_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    vendor = Vendor.objects.select_related('event').filter(id=vendor_id).first()
    if vendor is None:
        return _error('Vendor not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if vendor.owner_id != user.user_id and vendor.event.creator_id != user.user_id:
        return _error('Only the stall owner or the event organizer can see these orders.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    orders = vendor.orders.select_related('vendor', 'buyer').prefetch_related('items__product')
    return _ok(
        {
            'orders': [serialize_order(request, o) for o in orders],
            'count': orders.count(),
            'revenue_vc': sum(o.total_vc for o in orders if o.status != 'cancelled'),
        },
        'Vendor orders retrieved.',
    )


@api_view(['POST'])
def collect_order(request, code):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    order = VendorOrder.objects.select_related('vendor', 'vendor__event').filter(code=code.upper()).first()
    if order is None:
        return _error('No order with that code.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if order.vendor.owner_id != user.user_id and order.vendor.event.creator_id != user.user_id:
        return _error('Only the stall owner can mark an order collected.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if order.status == 'collected':
        return _error('That order was already collected.', 'ALREADY_COLLECTED', status.HTTP_409_CONFLICT)
    if order.status == 'cancelled':
        return _error('That order was cancelled.', 'INVALID_ORDER', status.HTTP_409_CONFLICT)

    order.status = 'collected'
    order.collected_at = timezone.now()
    order.save(update_fields=['status', 'collected_at'])

    return _ok({'order': serialize_order(request, order)}, f'Order {order.code} collected.')

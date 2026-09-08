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


def serialize_product(request, p, vendor=None):
    """One item on a stall.

    `vendor` is passed in wherever the caller already holds it, because
    `p.vendor` on a prefetched product list is one query per product and these
    lists are drawn on the storefront.
    """
    stall = vendor if vendor is not None else (p.vendor if p.vendor_id else None)
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
        # What the buyer has to choose, and whether it can be posted.
        'variants': p.variants or [],
        'can_deliver': p.can_deliver,
        # Which stall sells it. The storefront draws a "see everything from
        # this stall" link off `vendor_slug`, and neither this nor `vendor_id`
        # was in the payload - so that link read `vendor=undefined` and opened
        # nothing. A product without its stall is not a product anybody can buy.
        'vendor_id': p.vendor_id,
        'vendor_slug': stall.slug if stall else None,
        'vendor_name': stall.name if stall else None,
    }


def serialize_vendor(request, v, include_products=False):
    data = {
        'id': v.id,
        'event_id': v.event_id,
        'name': v.name,
        'slug': v.slug,
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
            serialize_product(request, p, vendor=v)
            for p in v.products.filter(is_active=True)
        ]
    return data


# ---------------------------------------------------------------------------
# GET /event/<event_id>/vendors/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def event_vendors(request, event_id):
    event = _event_by_ref(event_id, is_active=True)
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
def _vendor_by_ref(ref, **extra):
    """A stall, by slug or by primary key.

    Stalls were addressed by primary key everywhere. The address is the slug
    now, and the key still resolves so every link already shared - an order
    confirmation, a message to a stallholder - keeps opening the right stall.

    The slug is tried FIRST here, unlike the admin user resolver: a stall slug
    is never all digits, so there is no ambiguity to break, and trying the slug
    first means the common case is one query.
    """
    ref = str(ref)
    qs = Vendor.objects.select_related('event')
    found = qs.filter(slug=ref, **extra).first()
    if found is None and ref.isdigit():
        found = qs.filter(id=int(ref), **extra).first()
    return found


# GET /event/<event_id>/vendor/<vendor_id>/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def vendor_detail(request, event_id, vendor_id):
    vendor = _vendor_by_ref(vendor_id, event_id=event_id)
    if vendor is not None:
        vendor = Vendor.objects.filter(pk=vendor.pk).prefetch_related('products').first()
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

    event = _event_by_ref(event_id)
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

    vendor = _vendor_by_ref(vendor_id)
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

class Refused(Exception):
    """A refusal raised from inside `transaction.atomic()`.

    `return` inside an atomic block COMMITS. Every refusal after the wallet
    debit in `create_order` therefore kept the money: the buyer was charged,
    the response said "Nothing has been taken from your wallet", and it was not
    true. Caught by a delivery test on 7 September 2026 - the buyer was 2 VC
    down after being told the order was refused.

    Raising instead of returning makes every refusal roll back BY
    CONSTRUCTION, rather than by whoever writes the next check remembering to
    put it above the debit.
    """

    def __init__(self, message, code, http_status=status.HTTP_400_BAD_REQUEST,
                 extra=None):
        super().__init__(message)
        self.response = _error(message, code, http_status, extra)


def _refuse(message, code, http_status=status.HTTP_400_BAD_REQUEST, extra=None):
    raise Refused(message, code, http_status, extra)


@api_view(['POST'])
def create_order(request, vendor_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    # ONE guard for the whole function. Three refusals sat above the old,
    # narrower try - vendor missing, stall closed, empty basket - and raised
    # into nothing, which turned three clean 400s into 500s. A guard that
    # covers only part of a function is a guard somebody will step outside.
    try:

        vendor = _vendor_by_ref(vendor_id)
        if vendor is None:
            _refuse('Vendor not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if vendor.status == 'closed':
            _refuse(f'{vendor.name} is closed.', 'VENDOR_CLOSED', status.HTTP_409_CONFLICT)

        items = request.data.get('items') or []
        pin = request.data.get('pin')
        if not isinstance(items, list) or not items:
            _refuse('Add at least one item to your order.', 'VALIDATION_ERROR',
                    status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            priced = []
            total_vc = 0
            for raw in items:
                if not isinstance(raw, dict):
                    _refuse('Malformed order item.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
                try:
                    qty = int(raw.get('quantity', raw.get('qty', 1)))
                except (TypeError, ValueError):
                    _refuse('Quantity must be a number.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
                if qty < 1:
                    _refuse('Quantity must be at least 1.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

                product = VendorProduct.objects.select_for_update().filter(
                    id=raw.get('product_id') or raw.get('id'), vendor=vendor, is_active=True
                ).first()
                if product is None:
                    _refuse('One of those products is no longer available.',
                                  'NOT_FOUND', status.HTTP_404_NOT_FOUND)
                if product.stock < qty:
                    _refuse(f'Only {product.stock} × {product.name} left.',
                                  'INSUFFICIENT_STOCK', status.HTTP_409_CONFLICT)

                # Which choice they asked for. Checked against what the product
                # actually offers, so an order cannot carry a size the stall does
                # not sell - the stallholder reads this while packing and has no
                # way to query it.
                variant = str(raw.get('variant') or '').strip()
                offered = product.variants or []
                if offered and not variant:
                    _refuse('Choose an option for %s.' % product.name,
                                  'VARIANT_REQUIRED', status.HTTP_400_BAD_REQUEST)
                if variant and variant not in offered:
                    _refuse('%s does not come in %s.' % (product.name, variant),
                                  'VARIANT_UNKNOWN', status.HTTP_400_BAD_REQUEST)

                unit_vc = _ngn_to_coins(product.price)
                total_vc += unit_vc * qty
                priced.append((product, qty, unit_vc, variant))

            wallet = UserWallet.objects.select_for_update().filter(user=user).first()
            if wallet is None:
                _refuse('No wallet found for this account.', 'NO_WALLET', status.HTTP_400_BAD_REQUEST)

            # Taking your own stock costs nothing.
            #
            # The stall owner buying from their own stall was charged and, since
            # there is nobody to pay, the coins went nowhere - the same money
            # destruction as the missing credit below, in the one case where it is
            # hardest to notice. The order is still recorded and stock still moves,
            # because a shirt off the table is a shirt off the table.
            own_stall = bool(vendor.owner_id) and vendor.owner_id == user.user_id
            if own_stall:
                total_vc = 0

            if total_vc > 0:
                if not wallet.pin_hash:
                    _refuse('Set a wallet PIN before buying.', 'PIN_REQUIRED', status.HTTP_400_BAD_REQUEST)
                if not pin or not check_password(str(pin), wallet.pin_hash):
                    _refuse('Incorrect wallet PIN.', 'INVALID_PIN', status.HTTP_400_BAD_REQUEST)
                if wallet.wallet_balance < total_vc:
                    _refuse(
                        f'You need {total_vc} VC - your balance is {wallet.wallet_balance} VC.',
                        'INSUFFICIENT_BALANCE', status.HTTP_400_BAD_REQUEST,
                    )
                wallet.wallet_balance -= total_vc
                wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=wallet, type='deduction', amount=-total_vc,
                    description=f'Order at {vendor.name}', status='completed',
                )

                # Pay the stall.
                #
                # This was missing entirely: the buyer was debited, a row was
                # written for them, and the money reached nobody. Every purchase
                # destroyed it. Nothing raised, because the order still succeeded
                # and the buyer's side of the books balanced on its own.
                #
                # Inside the same transaction as the debit, and under the same lock
                # discipline, so a crash between the two cannot take the money
                # without delivering it. Skipped when the buyer owns the stall -
                # moving coins from a wallet to itself is a no-op that would leave
                # two rows implying a sale to yourself.
                # `own_stall` has already zeroed the total, so reaching here means
                # a real buyer and a real seller.
                if vendor.owner_id:
                    seller_wallet = UserWallet.objects.select_for_update().filter(
                        user_id=vendor.owner_id).first()
                    if seller_wallet is None:
                        # Refusing is the honest answer. Taking the money and
                        # working out where to put it later is how a platform ends
                        # up owing somebody an amount nobody recorded.
                        _refuse(
                            f'{vendor.name} cannot be paid right now. Nothing has '
                            'been taken from your wallet.',
                            'SELLER_HAS_NO_WALLET', status.HTTP_409_CONFLICT)
                    seller_wallet.wallet_balance += total_vc
                    seller_wallet.save(update_fields=['wallet_balance'])
                    Transaction.objects.create(
                        wallet=seller_wallet, type='receive', amount=total_vc,
                        description=f'Sale at {vendor.name}', status='completed',
                    )

            # How they want it. `collect` unless they said otherwise, because most
            # people at an event are walking to the table.
            fulfilment = 'deliver' if request.data.get('fulfilment') == 'deliver' else 'collect'
            delivery = request.data.get('delivery') or {}
            if fulfilment == 'deliver':
                if not isinstance(delivery, dict):
                    _refuse('Delivery details are missing.', 'VALIDATION_ERROR',
                                  status.HTTP_400_BAD_REQUEST)
                missing = [k for k in ('name', 'phone', 'address')
                           if not str(delivery.get(k) or '').strip()]
                if missing:
                    # Named, so the screen can point at the empty box rather than
                    # saying "something is wrong".
                    _refuse('A delivery needs a name, a phone number and an '
                                  'address.', 'DELIVERY_DETAILS_REQUIRED',
                                  status.HTTP_400_BAD_REQUEST, {'missing': missing})
                undeliverable = [p.name for p, _, _, _ in priced if not p.can_deliver]
                if undeliverable:
                    # A plate of hot food cannot be posted, and finding that out
                    # after paying is worse than being told now.
                    _refuse('%s cannot be delivered.' % ', '.join(undeliverable),
                                  'NOT_DELIVERABLE', status.HTTP_409_CONFLICT)

            order = VendorOrder.objects.create(
                vendor=vendor, buyer=user, code=_new_order_code(), total_vc=total_vc,
                fulfilment=fulfilment,
                delivery_name=str(delivery.get('name') or '')[:120] if fulfilment == 'deliver' else '',
                delivery_phone=str(delivery.get('phone') or '')[:40] if fulfilment == 'deliver' else '',
                delivery_address=str(delivery.get('address') or '') if fulfilment == 'deliver' else '',
                delivery_note=str(delivery.get('note') or '')[:200] if fulfilment == 'deliver' else '',
            )
            for product, qty, unit_vc, variant in priced:
                VendorOrderItem.objects.create(order=order, product=product,
                                               quantity=qty, unit_vc=unit_vc,
                                               variant=variant)
                VendorProduct.objects.filter(id=product.id).update(
                    stock=F('stock') - qty, sold=F('sold') + qty,
                )

    except Refused as refusal:
        # Everything the transaction did is unwound, so a refusal after the
        # debit really does leave the money where it was.
        return refusal.response

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

    vendor = _vendor_by_ref(vendor_id)
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

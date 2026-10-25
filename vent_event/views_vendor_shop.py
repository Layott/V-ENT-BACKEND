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
from .views_vendors import read_variants
from vent_auth import uploads
from vent_auth import coins
from vent_auth import inputs

PAGE_SIZE = 100


def _abs(request, filefield):
    if not filefield:
        return None
    try:
        return request.build_absolute_uri(filefield.url)
    except ValueError:
        return None


def _refund_cancelled(order, stall):
    from django.db.models import F
    from vent_auth.models import Transaction, UserWallet
    from . import ledger as _ledger
    from .models import VendorProduct
    if order.vendor_paid_vc > 0 and stall.owner_id:
        seller = UserWallet.objects.select_for_update().filter(user_id=stall.owner_id).first()
        if seller is None or seller.wallet_balance < order.vendor_paid_vc:
            return _error('Cancelling refunds the buyer from your wallet, and it holds %s VC '
                          'of the %s VC this order paid you.'
                          % (coins.label(seller.wallet_balance if seller else 0), order.vendor_paid_vc),
                          'CANNOT_REFUND', status.HTTP_409_CONFLICT)
        seller.wallet_balance -= order.vendor_paid_vc
        seller.save(update_fields=['wallet_balance'])
        Transaction.objects.create(wallet=seller, type='refund', amount=-order.vendor_paid_vc,
                                   description='Order %s cancelled' % order.code, status='completed')
    if order.total_vc > 0:
        buyer = UserWallet.objects.select_for_update().filter(user_id=order.buyer_id).first()
        if buyer is not None:
            buyer.wallet_balance += order.total_vc
            buyer.save(update_fields=['wallet_balance'])
            Transaction.objects.create(wallet=buyer, type='refund', amount=order.total_vc,
                                       description='Order %s cancelled at %s' % (order.code, stall.name),
                                       status='completed')
    # The part of this order's take that was still waiting under a coin.
    unit = _ledger.ngn_per_coin()
    remainder = _ledger._ngn(order.vendor_ngn) - order.vendor_paid_vc * unit
    if remainder > 0:
        stall.carry_ngn = max(_ledger._ngn(0), _ledger._ngn(stall.carry_ngn) - remainder)
        stall.save(update_fields=['carry_ngn'])
    for item in order.items.all():
        VendorProduct.objects.filter(pk=item.product_id).update(
            stock=F('stock') + item.quantity, sold=F('sold') - item.quantity)
    return None


def _price(src, key):
    return inputs.read_float(src, key, maximum=99_999_999, required=True)


def _stock(src, key):
    return inputs.read_int(src, key, maximum=2_000_000_000, required=True)


def _my_stall(user, ref):
    """A stall this person owns, by slug or key, or None.

    Ownership is checked HERE rather than by the caller, so no endpoint in this
    file can forget it. Every one of them changes something.
    """
    from . import shop as _shop
    return _shop.stall_by_ref(user, ref, roles=('owner',))


def _stall_i_work(user, ref):
    """A stall this person owns or staffs (inbox 553), for the doors staff may
    use: the orders, and marking them ready or collected."""
    from . import shop as _shop
    return _shop.stall_by_ref(user, ref, roles=('owner', 'staff'))


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
        'category': p.category or None,
        'discount_kind': p.discount_kind or None,
        'discount_value': float(p.discount_value),
        'sale_price_ngn': float(p.sale_price),
        'sale_price_vc': int(_ngn_to_coins(p.sale_price)),
        'low_stock_at': p.low_stock_at,
        'low_stock': bool(p.low_stock_at) and p.stock <= p.low_stock_at,
    }


def _order_row(request, o, here=None):
    return {
        'id': o.id,
        'code': o.code,
        'status': o.status,
        'total_vc': o.total_vc,
        'fee_ngn': float(o.fee_ngn),
        'fee_bearer': o.fee_bearer,
        'buyer_fee_vc': o.buyer_fee_vc,
        'vendor_ngn': float(o.vendor_ngn),
        'vendor_paid_vc': o.vendor_paid_vc,
        'created_at': o.created_at,
        'collected_at': o.collected_at,
        'fulfilment': o.fulfilment,
        'buyer': o.buyer.username if o.buyer_id else '',
        'buyer_name': (o.buyer.full_name or o.buyer.username) if o.buyer_id else '',
        # Whether the buyer is checked in at the event right now (inbox 553),
        # worked out once per list by the caller and passed in as a set.
        'buyer_here': bool(here is not None and o.buyer_id in here),
        'promo_code': o.promo.code if o.promo_id else None,
        'discount_ngn': float(o.discount_ngn),
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

    from django.db.models import Q
    from . import shop as _shop
    rows = []
    mine = (Vendor.objects.select_related('event')
            .filter(Q(owner=user) | Q(staff__user=user)).distinct())
    for v in mine:
        rows.append({
            'role': _shop.stall_role(user, v),
            'kind': v.kind,
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
        for key, most in (('name', 120), ('description', inputs.LONGEST_TEXT), ('category', 60)):
            if key in request.data:
                setattr(stall, key, inputs.read_text(request.data, key, max_length=most))
                fields.append(key)
        # Opening and closing the stall. `approved` and `live` both trade;
        # `closed` is the stallholder saying they have stopped for the day, and
        # they must be able to undo it.
        if 'fee_bearer' in request.data:
            # The same choice the organiser has on tickets. Applies to orders
            # from now on; what has sold keeps the numbers it sold under.
            wanted = inputs.read_text(request.data, 'fee_bearer', max_length=20)
            if wanted not in ('vendor', 'buyer'):
                return _error('Who pays the fee is the stallholder or the buyer.',
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            stall.fee_bearer = wanted
            fields.append('fee_bearer')
        if 'status' in request.data:
            wanted = inputs.read_text(request.data, 'status', max_length=20)
            if wanted not in ('live', 'closed'):
                return _error('A stall can be live or closed.',
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            if stall.status == 'pending' and wanted == 'live':
                return _error('The organiser has not approved this stall yet.',
                              'NOT_APPROVED', status.HTTP_409_CONFLICT)
            stall.status = wanted
            fields.append('status')
        if 'refund_policy' in request.data:
            wanted = inputs.read_text(request.data, 'refund_policy', max_length=20)
            if wanted not in dict(Vendor.REFUND_CHOICES):
                return _error('Refunds are none, until the event starts, or until collected.',
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            stall.refund_policy = wanted
            fields.append('refund_policy')
        if 'pickup_location' in request.data:
            stall.pickup_location = inputs.read_text(request.data, 'pickup_location', max_length=160)
            fields.append('pickup_location')
        if 'pickup_opens_at' in request.data or 'pickup_closes_at' in request.data:
            from . import shop as _shop
            def _when(key, current):
                # Absent keeps what is saved; an empty value clears it, which
                # is what emptying the field on the form means.
                if key not in request.data:
                    return current
                if not str(request.data.get(key) or '').strip():
                    return None
                return inputs.read_datetime(request.data, key)
            try:
                opens = _when('pickup_opens_at', stall.pickup_opens_at)
                closes = _when('pickup_closes_at', stall.pickup_closes_at)
            except (TypeError, ValueError):
                return _error('That is not a date and time.', 'VALIDATION_ERROR',
                              status.HTTP_400_BAD_REQUEST)
            why = _shop.window_refusal(stall, opens, closes)
            if why:
                return _error(why[1], why[0], status.HTTP_400_BAD_REQUEST)
            stall.pickup_opens_at, stall.pickup_closes_at = opens, closes
            fields += ['pickup_opens_at', 'pickup_closes_at']
        refused = uploads.files_refusal(request, ('logo', 'banner'))
        if refused:
            return refused
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
            # Who bears the platform's fee on this stall, the rule, and the
            # money so far; the stall page draws its choice from this.
            'fee_bearer': stall.fee_bearer,
            'fee_pct': _fee_rule()[0],
            'fee_flat_ngn': float(_fee_rule()[1]),
            **_stall_money(stall),
            'kind': stall.kind,
            'refund_policy': stall.refund_policy,
            'pickup_location': stall.pickup_location or None,
            'pickup_opens_at': stall.pickup_opens_at,
            'pickup_closes_at': stall.pickup_closes_at,
            'staff': [{'username': m.user.username,
                       'name': m.user.full_name or m.user.username}
                      for m in stall.staff.select_related('user')],
        },
        'products': [_product_row(request, p) for p in stall.products.all()],
    }, 'Your stall.')


def _fee_rule():
    from . import ledger as _ledger
    return _ledger.platform_fee()


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
    for key, most in (('name', 140), ('description', inputs.LONGEST_TEXT)):
        if key in request.data:
            setattr(product, key, inputs.read_text(request.data, key, max_length=most))
            fields.append(key)
    # NaN and infinity used to pass float() and every `< 0` check below; the
    # column holds 10 digits, the stock a positive 32 bit number.
    for key, caster, label in (('price', _price, 'price'),
                               ('stock', _stock, 'number in stock')):
        if key in request.data:
            try:
                value = caster(request.data, key)
            except (TypeError, ValueError):
                return _error('The %s has to be a number.' % label,
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            if value < 0:
                return _error('The %s cannot be negative.' % label,
                              'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
            if key == 'price':
                from .pricing import refuse_if_not_whole
                refused = refuse_if_not_whole(value, field='price')
                if refused is not None:
                    return refused
            setattr(product, key, value)
            fields.append(key)
    if 'is_active' in request.data:
        product.is_active = inputs.read_bool(request.data, 'is_active')
        fields.append('is_active')
    if 'can_deliver' in request.data:
        product.can_deliver = inputs.read_bool(request.data, 'can_deliver')
        fields.append('can_deliver')
    if 'variants' in request.data:
        product.variants, why = read_variants(request.data.get('variants'))
        if why:
            return _error(why, 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
        fields.append('variants')
    from . import shop as _shop
    extras, problem = _shop.read_product_extras(request.data)
    if problem:
        return _error(problem[1], problem[0], status.HTTP_400_BAD_REQUEST)
    for key, value in extras.items():
        setattr(product, key, value)
        fields.append(key)
    if stall.kind == Vendor.KIND_ORGANISER and product.can_deliver:
        product.can_deliver = False
        fields.append('can_deliver')
    if product.low_stock_alerted_at and product.stock > product.low_stock_at:
        product.low_stock_alerted_at = None
        fields.append('low_stock_alerted_at')
    refused = uploads.files_refusal(request, 'image')
    if refused:
        return refused
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

    stall = _stall_i_work(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    wanted = inputs.read_text(request.GET, 'status', max_length=20)
    orders = stall.orders.select_related('buyer', 'promo').prefetch_related('items__product')
    if wanted:
        orders = orders.filter(status=wanted)

    from . import shop as _shop
    page = list(orders[:PAGE_SIZE])
    here = _shop.buyers_here(stall.event_id, {o.buyer_id for o in page if o.buyer_id})
    rows = [_order_row(request, o, here) for o in page]
    owner = _shop.stall_role(user, stall) == 'owner'
    if not owner:
        # Staff hand orders over; what the stall earns stays the owner's
        # (inbox 553), in the payload as well as on the screen.
        for row in rows:
            for key in ('fee_ngn', 'fee_bearer', 'buyer_fee_vc', 'vendor_ngn', 'vendor_paid_vc',
                        'discount_ngn'):
                row.pop(key, None)
    return _ok({
        'role': 'owner' if owner else 'staff',
        'orders': rows,
        'count': len(rows),
        # The stallholder's own summary, so the screen does not have to count
        # and get a different answer from the one the list shows.
        'open': stall.orders.filter(status__in=('paid', 'ready', 'sent')).count(),
        'to_deliver': stall.orders.filter(fulfilment='deliver',
                                          status__in=('paid', 'ready')).count(),
        # The money, in naira, over every order that was not cancelled: what
        # the items came to, what the platform took, what the stall kept, the
        # whole coins paid into the wallet so far, and what waits under a coin.
        **(_stall_money(stall) if owner else {}),
    }, 'Orders on your stall.')


def _stall_money(stall):
    live = stall.orders.exclude(status='cancelled')
    return {
        'items_ngn': float(sum((o.items_ngn for o in live), 0)),
        'fees_ngn': float(sum((o.fee_ngn for o in live), 0)),
        'kept_ngn': float(sum((o.vendor_ngn for o in live), 0)),
        'paid_vc': sum(o.vendor_paid_vc for o in live),
        'carry_ngn': float(stall.carry_ngn),
        'fee_bearer': stall.fee_bearer,
    }


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

    stall = _stall_i_work(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    wanted = inputs.read_text(request.data, 'status', max_length=20)
    if wanted not in NEXT_STATUS:
        return _error('That is not something an order can become.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    from . import shop as _shop
    if wanted == 'cancelled' and _shop.stall_role(user, stall) != 'owner':
        return _error('Only the owner can cancel an order, because cancelling refunds it.',
                      'OWNER_ONLY', status.HTTP_403_FORBIDDEN)
    if wanted == 'collected':
        closed = _shop.pickup_refusal(stall)
        if closed:
            return _error(closed[1], closed[0], status.HTTP_409_CONFLICT)

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
        if wanted == 'cancelled':
            # Until 13 September a cancel kept the buyer's coins and the stock
            # stayed sold. The buyer gets back everything they paid; the stall
            # gives back the coins it was paid for this order, and its carry
            # loses the rest; the stock goes back on the table.
            refused = _refund_cancelled(order, stall)
            if refused is not None:
                return refused
        if wanted == 'collected':
            order.collected_at = timezone.now()
            fields.append('collected_at')
        if wanted == 'sent':
            order.sent_at = timezone.now()
            fields.append('sent_at')
            tracking = inputs.read_text(request.data, 'tracking', max_length=inputs.LONGEST_TEXT)
            if tracking:
                order.tracking = tracking[:80]
                fields.append('tracking')
        if wanted == 'delivered':
            order.delivered_at = timezone.now()
            fields.append('delivered_at')
        order.save(update_fields=fields)

    return _ok({'order': _order_row(request, order)}, 'Order updated.')


# ---------------------------------------------------------------------------
# The people running a stall with its owner (inbox 553)
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def my_stall_staff(request, vendor_id):
    """GET the staff of my stall; POST a username to add them."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error
    stall = _my_stall(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    from vent_auth.models import Users
    from .models import VendorStaff
    if request.method == 'POST':
        name = inputs.read_text(request.data, 'username', max_length=128).lstrip('@')
        person = Users.objects.filter(username__iexact=name).first() if name else None
        if person is None:
            return _error('Nobody has that username.', 'USER_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        if stall.owner_id == person.user_id:
            return _error('That is the owner of the stall.', 'ALREADY_OWNER',
                          status.HTTP_409_CONFLICT)
        VendorStaff.objects.get_or_create(vendor=stall, user=person,
                                          defaults={'added_by': user})
    rows = [{'username': m.user.username, 'name': m.user.full_name or m.user.username,
             'added_at': m.created_at}
            for m in stall.staff.select_related('user')]
    return _ok({'staff': rows, 'count': len(rows)},
               'Added.' if request.method == 'POST' else 'The people running this stall.')


@api_view(['DELETE'])
def my_stall_staff_member(request, vendor_id, username):
    """Remove somebody from my stall's staff."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error
    stall = _my_stall(user, vendor_id)
    if stall is None:
        return _error('That is not one of your stalls.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    removed, _ = stall.staff.filter(user__username__iexact=str(username).lstrip('@')).delete()
    if not removed:
        return _error('That person is not on this stall.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    return _ok({'removed': True}, 'Removed.')


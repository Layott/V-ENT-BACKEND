"""Selling a pitch at an event, and buying one.

CEO, 7 September 2026: "event owners should be able to sell vendor slots to
other people, they can list prices for vendor slots with their rules and
conditions and other users should be able to buy and use the site to run the
shop or they can invite people too."

Two doors into one room. A stall is claimed either by BUYING a slot or by being
INVITED to one, and after that both are the same thing: somebody running a shop
with stock, images and orders. Everything downstream is deliberately unable to
tell which way a vendor came in, because building the two routes apart is how
the invited vendor and the paying vendor end up with different products and one
of them stops being maintained.

    GET    /event/<event>/slots/            what is for sale, and what is left
    POST   /event/<event>/slots/            list a slot          (organiser)
    PATCH  /event/<event>/slots/<id>/       edit one             (organiser)
    DELETE /event/<event>/slots/<id>/       withdraw one         (organiser)
    POST   /event/<event>/slots/<id>/buy/   buy it               (anybody)

The money follows `create_order` in `views_vendors.py` exactly: both wallets
locked with `select_for_update`, the PIN checked against the stored hash, the
debit and the credit inside one transaction, and a refusal rather than taking
money that cannot be delivered. That last rule is why the seller's missing
wallet is a 409 and not a shrug - a platform that takes the money and works out
where to put it later ends up owing somebody an amount nobody recorded.
"""
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import Transaction, UserWallet

from .models import Event, Vendor, VendorSlot, VendorSlotPurchase
from .views_promos import _actor_for_event, event_by_ref
from .views_tickets import _authenticate, _error, _ngn_to_coins, _ok


def _slot_row(slot, viewer=None):
    """One slot, as a listing card.

    `remaining` and `is_sold_out` are both sent. A screen that has to work out
    "sold out" from two numbers is a screen that will get it wrong somewhere,
    and a sold-out slot has to SAY so rather than vanish - somebody who was
    told about it needs to know what happened to it.
    """
    return {
        'id': slot.id,
        'name': slot.name,
        'description': slot.description,
        'category': slot.category or None,
        'price_ngn': float(slot.price_ngn),
        'price_vc': int(_ngn_to_coins(slot.price_ngn)),
        'quantity': slot.quantity,
        'sold': slot.sold,
        'remaining': slot.remaining,
        'is_sold_out': slot.is_sold_out,
        'rules': slot.rules,
        'rules_version': slot.rules_version,
        'requires_approval': slot.requires_approval,
        'is_active': slot.is_active,
        # Whether the person reading this already holds one. Without it the
        # page offers Buy to somebody who cannot buy again, and the refusal
        # arrives after they have entered their PIN.
        'already_mine': bool(
            viewer and VendorSlotPurchase.objects.filter(
                slot=slot, buyer=viewer).exists()),
    }


def _viewer(request):
    """The signed-in account, or None. Never an error: the list is public."""
    from vent_auth.models import Users

    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    return Users.objects.filter(
        login_session_token=header.split(' ', 1)[1].strip()).first()


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def event_slots(request, event_id):
    """What pitches are for sale at this event, or list a new one."""
    event = event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        viewer = _viewer(request)
        # Inactive slots are hidden from everybody but the organiser: a
        # withdrawn pitch is not an offer, but the organiser still has to see
        # it to put it back.
        qs = VendorSlot.objects.filter(event=event)
        organiser, err = _actor_for_event(request, event)
        if err is not None:
            qs = qs.filter(is_active=True)
        return _ok({
            'slots': [_slot_row(s, viewer) for s in qs],
            'can_manage': err is None,
        }, 'Vendor slots.')

    organiser, err = _actor_for_event(request, event)
    if err is not None:
        return err

    name = (request.data.get('name') or '').strip()
    if not name:
        return _error('Give the slot a name, so a buyer knows what they are '
                      'getting.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    try:
        quantity = int(request.data.get('quantity', 1))
        price_ngn = float(request.data.get('price_ngn', 0))
    except (TypeError, ValueError):
        return _error('The price and the number available have to be numbers.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if quantity < 1:
        return _error('There has to be at least one to sell.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if price_ngn < 0:
        return _error('A price cannot be negative.', 'VALIDATION_ERROR',
                      status.HTTP_400_BAD_REQUEST)

    slot = VendorSlot.objects.create(
        event=event, name=name,
        description=(request.data.get('description') or '').strip(),
        category=(request.data.get('category') or '').strip(),
        price_ngn=price_ngn, quantity=quantity,
        rules=(request.data.get('rules') or '').strip(),
        requires_approval=bool(request.data.get('requires_approval', True)),
    )
    return Response({'status': 'success', 'data': {'slot': _slot_row(slot)},
                     'message': f'{slot.name} is on sale.'},
                    status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def event_slot_detail(request, event_id, slot_id):
    """Edit or withdraw one slot."""
    event = event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    organiser, err = _actor_for_event(request, event)
    if err is not None:
        return err

    slot = VendorSlot.objects.filter(id=slot_id, event=event).first()
    if slot is None:
        return _error('That slot does not exist.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        if slot.sold:
            # Withdrawn rather than deleted, because somebody has paid for one
            # and their stall points at it. Deleting the row would take their
            # record of what they agreed to with it.
            slot.is_active = False
            slot.save(update_fields=['is_active'])
            return _ok({'slot': _slot_row(slot)},
                       'Taken off sale. The stalls already sold are untouched.')
        slot.delete()
        return _ok({'deleted': True}, 'Slot removed.')

    fields = []
    for key in ('name', 'description', 'category', 'rules'):
        if key in request.data:
            setattr(slot, key, (request.data.get(key) or '').strip())
            fields.append(key)
    if 'requires_approval' in request.data:
        slot.requires_approval = bool(request.data.get('requires_approval'))
        fields.append('requires_approval')
    if 'is_active' in request.data:
        slot.is_active = bool(request.data.get('is_active'))
        fields.append('is_active')
    if 'price_ngn' in request.data:
        try:
            slot.price_ngn = float(request.data.get('price_ngn'))
        except (TypeError, ValueError):
            return _error('The price has to be a number.', 'VALIDATION_ERROR',
                          status.HTTP_400_BAD_REQUEST)
        fields.append('price_ngn')
    if 'quantity' in request.data:
        try:
            quantity = int(request.data.get('quantity'))
        except (TypeError, ValueError):
            return _error('The number available has to be a number.',
                          'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
        if quantity < slot.sold:
            # Reducing below what is already sold would make `remaining`
            # negative and, worse, imply somebody's stall does not exist.
            return _error(
                f'{slot.sold} of these have been sold, so you cannot set it '
                f'below {slot.sold}.', 'BELOW_SOLD', status.HTTP_409_CONFLICT)
        slot.quantity = quantity
        fields.append('quantity')

    if fields:
        slot.save(update_fields=fields)
    return _ok({'slot': _slot_row(slot)}, 'Saved.')


@api_view(['POST'])
def buy_slot(request, event_id, slot_id):
    """Buy a pitch, and become a stall.

    One purchase makes exactly one `Vendor` owned by the buyer. When the
    organiser asked for approval it starts `pending` and they can trade the
    moment it is approved; when they did not, it is live at once.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event = event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    accepted = request.data.get('accept_rules')
    pin = request.data.get('pin')
    stall_name = (request.data.get('stall_name') or '').strip()

    with transaction.atomic():
        slot = VendorSlot.objects.select_for_update().filter(
            id=slot_id, event=event).first()
        if slot is None:
            return _error('That slot does not exist.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        if not slot.is_active:
            return _error(f'{slot.name} is no longer on sale.',
                          'SLOT_WITHDRAWN', status.HTTP_409_CONFLICT)

        # Locked and re-read, so two people buying the last pitch at the same
        # moment cannot both get it. Checking `remaining` before the lock is
        # the classic way to sell one thing twice.
        if slot.is_sold_out:
            return _error(f'{slot.name} is sold out.', 'SOLD_OUT',
                          status.HTTP_409_CONFLICT)

        if VendorSlotPurchase.objects.filter(slot__event=event, buyer=user).exists():
            return _error('You already have a stall at this event.',
                          'ALREADY_A_VENDOR', status.HTTP_409_CONFLICT)

        # The rules are AGREED, not merely displayed. An organiser who wrote
        # conditions is relying on them having been read.
        if slot.rules and not accepted:
            return _error('Read and accept the conditions before buying.',
                          'RULES_NOT_ACCEPTED', status.HTTP_400_BAD_REQUEST)

        price_vc = int(_ngn_to_coins(slot.price_ngn))

        if price_vc > 0:
            wallet = UserWallet.objects.select_for_update().filter(user=user).first()
            if wallet is None:
                return _error('No wallet found for this account.', 'NO_WALLET',
                              status.HTTP_400_BAD_REQUEST)
            if not wallet.pin_hash:
                return _error('Set a wallet PIN before buying.', 'PIN_REQUIRED',
                              status.HTTP_400_BAD_REQUEST)
            if not pin or not check_password(str(pin), wallet.pin_hash):
                return _error('Incorrect wallet PIN.', 'INVALID_PIN',
                              status.HTTP_400_BAD_REQUEST)
            if wallet.wallet_balance < price_vc:
                return _error(
                    f'You need {price_vc} VC - your balance is '
                    f'{wallet.wallet_balance} VC.',
                    'INSUFFICIENT_BALANCE', status.HTTP_400_BAD_REQUEST)

            # Pay the organiser. Refused rather than taken when there is
            # nowhere to put it, for the same reason as a vendor order.
            seller_wallet = None
            if event.creator_id and event.creator_id != user.user_id:
                seller_wallet = UserWallet.objects.select_for_update().filter(
                    user_id=event.creator_id).first()
                if seller_wallet is None:
                    return _error(
                        'This organiser cannot be paid right now. Nothing has '
                        'been taken from your wallet.',
                        'SELLER_HAS_NO_WALLET', status.HTTP_409_CONFLICT)

            wallet.wallet_balance -= price_vc
            wallet.save(update_fields=['wallet_balance'])
            Transaction.objects.create(
                wallet=wallet, type='deduction', amount=-price_vc,
                description=f'{slot.name} at {event.name}', status='completed')

            if seller_wallet is not None:
                seller_wallet.wallet_balance += price_vc
                seller_wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=seller_wallet, type='receive', amount=price_vc,
                    description=f'{slot.name} sold at {event.name}',
                    status='completed')

        # The stall itself. Named by the buyer if they said, so a shop opens
        # with the buyer's own name on it rather than the pitch's.
        vendor = Vendor.objects.create(
            event=event,
            owner=user,
            name=stall_name or f'{user.username} at {event.name}'[:120],
            category=slot.category,
            status='pending' if slot.requires_approval else 'approved',
        )

        VendorSlotPurchase.objects.create(
            slot=slot, buyer=user, vendor=vendor,
            price_vc=price_vc, price_ngn=slot.price_ngn,
            # Word for word as it stood today. Pointing at the organiser's live
            # text would let them edit it afterwards and change what somebody
            # already agreed to.
            rules_accepted=slot.rules,
            rules_version_accepted=slot.rules_version,
            accepted_at=timezone.now() if slot.rules else None,
        )

        VendorSlot.objects.filter(id=slot.id).update(sold=F('sold') + 1)
        slot.refresh_from_db()

    from .views_vendors import serialize_vendor
    return Response({
        'status': 'success',
        'data': {'vendor': serialize_vendor(request, vendor),
                 'slot': _slot_row(slot, user)},
        'message': ('Your stall is set up. The organiser will approve it '
                    'shortly.' if slot.requires_approval
                    else 'Your stall is set up. Start adding what you sell.'),
    }, status=status.HTTP_201_CREATED)

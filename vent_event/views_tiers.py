"""An organiser managing the ticket tiers on an event that already exists.

Tiers could only be created inside the event creation wizard. After that the
event was fixed: no adding a VIP tier once the standard ones sold, no correcting
a price typed wrong, no raising an allocation when a tier sold out and there was
still room in the venue. The read endpoint existed and nothing could write.

Three rules the whole file is built around.

**`sold` is never writable.** It is the count of tickets that exist, and a
number that says how many were sold has to come from the tickets, not from a
form. An organiser who could edit it could make a sold-out tier look open and
oversell the room.

**An allocation cannot go below what is already sold.** Twenty people hold a
ticket; setting the tier to ten does not un-sell ten of them, it just makes
every number on the page a lie.

**A tier with tickets sold against it is retired, not deleted.** Deleting it
would cascade to the tickets, which are what somebody holds at the door.
"""
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import Event, TicketTier
from .views_tickets import serialize_tier


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _event_and_permission(request, event_id):
    """The event, and whether this caller may change its tiers."""
    user, err = actor_from_request(request)
    if err:
        return None, None, err

    from .views import _event_by_ref
    event = _event_by_ref(event_id)
    if event is None:
        return None, None, _err('Event not found.', 'NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)

    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return None, None, _err(
            'Only the event organizer can change its tickets.',
            'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)

    return event, user, None


def _read_price(raw, current=None):
    if raw in (None, ''):
        return current, None
    try:
        price = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None, _err('The price has to be a number.', 'INVALID_NUMBER',
                          field='price')
    if price < 0:
        return None, _err('A price cannot be negative.', 'INVALID_NUMBER',
                          field='price')
    return price, None


def _read_quantity(raw, current=None):
    if raw in (None, ''):
        return current, None
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        return None, _err('How many has to be a number.', 'INVALID_NUMBER',
                          field='quantity')
    if quantity < 0:
        return None, _err('How many cannot be negative.', 'INVALID_NUMBER',
                          field='quantity')
    return quantity, None


@api_view(['GET', 'POST'])
def create_tier(request, event_id):
    """POST /event/<id>/tiers/ - add a ticket type to an event that exists.

    The case this is for: an event sold out its standard tier and the organiser
    wants to open a VIP one. Until now that meant creating the event again.
    """
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    if request.method == 'GET':
        # The organiser's own list, including types hidden behind an access
        # code. The public endpoint filters those out, which is the whole point
        # of a code - but it left an organiser unable to see, price or retire a
        # tier they had created.
        return _ok({'tiers': [serialize_tier(t) for t in event.ticket_tiers.all()]},
                   'Ticket types')

    name = str(request.data.get('name') or '').strip()
    if not name:
        return _err('A ticket type needs a name.', 'VALIDATION_FAILED', field='name')
    if event.ticket_tiers.filter(name__iexact=name).exists():
        return _err('This event already has a ticket type with that name.',
                    'TIER_EXISTS', status.HTTP_409_CONFLICT, field='name')

    price, err = _read_price(request.data.get('price'), Decimal('0'))
    if err:
        return err
    quantity, err = _read_quantity(request.data.get('quantity'), 0)
    if err:
        return err

    perks = request.data.get('perks')
    if isinstance(perks, list):
        perks = ', '.join(str(p).strip() for p in perks if str(p).strip())

    tier = TicketTier.objects.create(
        event=event,
        name=name[:60],
        price=price,
        quantity=quantity,
        perks=str(perks or '')[:255],
        day=request.data.get('day') or None,
        day_label=str(request.data.get('day_label') or '')[:60],
    )
    return _ok({'tier': serialize_tier(tier),
                'tiers': [serialize_tier(t) for t in event.ticket_tiers.all()]},
               'Ticket type added.', status.HTTP_201_CREATED)


@api_view(['PATCH'])
def update_tier(request, event_id, tier_id):
    """PATCH /event/<id>/tiers/<tid>/ - correct a price, or open more.

    Partial: a field that is not sent is not touched.
    """
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    tier = event.ticket_tiers.filter(pk=tier_id).first()
    if tier is None:
        return _err('No such ticket type on this event.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    updated = []

    if 'name' in request.data:
        name = str(request.data.get('name') or '').strip()
        if not name:
            return _err('A ticket type needs a name.', 'VALIDATION_FAILED',
                        field='name')
        if event.ticket_tiers.filter(name__iexact=name).exclude(pk=tier.pk).exists():
            return _err('This event already has a ticket type with that name.',
                        'TIER_EXISTS', status.HTTP_409_CONFLICT, field='name')
        tier.name = name[:60]
        updated.append('name')

    if 'price' in request.data:
        price, err = _read_price(request.data.get('price'))
        if err:
            return err
        # Changing a price does not change what anybody already paid. Their
        # ticket records its own price, which is the only honest way to read a
        # receipt from before the change.
        tier.price = price
        updated.append('price')

    if 'quantity' in request.data:
        quantity, err = _read_quantity(request.data.get('quantity'))
        if err:
            return err
        if quantity < tier.sold:
            return _err(
                '%s tickets have already been sold on this type, so the '
                'allocation cannot go below that.' % tier.sold,
                'BELOW_SOLD', field='quantity')
        tier.quantity = quantity
        updated.append('quantity')

    if 'perks' in request.data:
        perks = request.data.get('perks')
        if isinstance(perks, list):
            perks = ', '.join(str(p).strip() for p in perks if str(p).strip())
        tier.perks = str(perks or '')[:255]
        updated.append('perks')

    for field in ('day', 'day_label'):
        if field in request.data:
            value = request.data.get(field) or ('' if field == 'day_label' else None)
            setattr(tier, field, value)
            updated.append(field)

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    tier.save(update_fields=updated)
    return _ok({'tier': serialize_tier(tier),
                'tiers': [serialize_tier(t) for t in event.ticket_tiers.all()]},
               'Ticket type updated.')


@api_view(['DELETE'])
def delete_tier(request, event_id, tier_id):
    """DELETE /event/<id>/tiers/<tid>/ - remove a type nobody has bought.

    Once anybody holds a ticket on it, it is not removable: deleting it cascades
    to the tickets, which are the thing somebody shows at the door. Set the
    allocation down to what is sold instead and it stops selling.
    """
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    tier = event.ticket_tiers.filter(pk=tier_id).first()
    if tier is None:
        return _err('No such ticket type on this event.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    sold = tier.tickets.count() if hasattr(tier, 'tickets') else tier.sold
    if sold:
        return _err(
            '%s tickets have been sold on this type, so it cannot be removed. '
            'Set how many are available down to %s and it stops selling.'
            % (sold, sold),
            'TIER_HAS_TICKETS', status.HTTP_409_CONFLICT)

    tier.delete()
    return _ok({'tiers': [serialize_tier(t) for t in event.ticket_tiers.all()]},
               'Ticket type removed.')


# ---------------------------------------------------------------------------
# What the organiser asks a buyer for
# ---------------------------------------------------------------------------

@api_view(['GET', 'PUT'])
def manage_checkout_fields(request, event_id):
    """GET /event/<id>/checkout-fields/manage/ - what is asked for.
       PUT /event/<id>/checkout-fields/manage/ - replace the list, in order.

    Email is not in this list and cannot be. It is always collected and always
    required, because a ticket with no way to reach the holder is not a ticket.
    """
    from . import checkout
    from .models import EventCheckoutField

    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    def rows():
        return [
            {
                'id': f.id, 'label': f.label, 'kind': f.kind,
                'help_text': f.help_text, 'required': f.required,
                'options': f.options, 'per_ticket': f.per_ticket,
                'order': f.order,
            }
            for f in event.checkout_fields.all()
        ]

    if request.method == 'GET':
        return _ok({'fields': rows(), 'catalogue': checkout.catalogue()},
                   'Checkout fields')

    raw = request.data.get('fields')
    if not isinstance(raw, list):
        return _err('Send the fields as a list, in the order they are asked.',
                    'VALIDATION_FAILED', field='fields')

    cleaned = []
    for index, item in enumerate(raw):
        try:
            cleaned.append(checkout.clean_field(item))
        except checkout.CheckoutError as exc:
            return _err(str(exc), 'VALIDATION_FAILED',
                        field=getattr(exc, 'field', None))

    # Matched by label rather than deleted and recreated, so an answer already
    # given against a field keeps pointing at it. Answers are stored by field
    # id; recreating the rows would orphan every one of them.
    existing = {f.label.lower(): f for f in event.checkout_fields.all()}
    keep = set()
    for order, data in enumerate(cleaned):
        field = existing.get(data['label'].lower()) or EventCheckoutField(event=event)
        field.label = data['label']
        field.kind = data['kind']
        field.help_text = data['help_text']
        field.required = data['required']
        field.options = data['options']
        field.per_ticket = data['per_ticket']
        field.order = order
        field.save()
        keep.add(field.pk)

    event.checkout_fields.exclude(pk__in=keep).delete()
    return _ok({'fields': rows()}, 'Saved.')

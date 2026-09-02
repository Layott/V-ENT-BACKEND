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
from datetime import date
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


def _read_email_limit(raw, current=None):
    """The per-type email limit. Empty means the type sets no rule of its own.

    Distinct from zero, which nothing should mean here: a type nobody may buy
    is a type with no allocation, not a type with a limit of none.
    """
    if raw in (None, ''):
        return None, None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return None, _err('That has to be a number.', 'INVALID_NUMBER',
                          field='max_tickets_per_email')
    if limit < 1:
        return None, _err('A limit is at least one ticket. Leave it empty for '
                          'no limit of its own.', 'INVALID_NUMBER',
                          field='max_tickets_per_email')
    return limit, None


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
        # The event's own days go with the list, because a type's date can only
        # be corrected against the days the event actually runs. Without this
        # the console could show a type reading "Day 2" with no date and offer
        # no way to give it one, which is exactly what it did.
        from .views_limits import event_days
        from . import availability

        tiers = list(event.ticket_tiers.all())

        # The venue ceiling, which is a SECOND limit on top of each type's own
        # quantity and silently wins when it is lower.
        #
        # An organiser set two types of 5000, saw "186 of 5000 sold" on this
        # screen, and could not understand why the public page said the event
        # was sold out. The event's capacity was 400, set once in the creation
        # wizard, and appeared nowhere on this console at all. Two ceilings and
        # only one of them visible is not a number an organiser can reason
        # about, so both are sent and the screen says when they disagree.
        capacity = int(event.capacity) if event.capacity else None
        mode = getattr(event, 'capacity_mode', 'per_day')

        # Whether the types promise more than the venue holds depends entirely
        # on what the capacity counts.
        #
        # Under per_day, two days of 5000 against a 5000-seat venue is exactly
        # right: different people in the same chairs. Adding them to 10000 and
        # calling that an error told an organiser their correct setup was
        # broken, which is worse than saying nothing.
        #
        # So per_day compares each DAY's types against the capacity, and only
        # total compares the sum.
        by_day = {}
        for t in tiers:
            by_day.setdefault(t.day, 0)
            by_day[t.day] += int(t.quantity or 0)
        offered = sum(int(t.quantity or 0) for t in tiers)

        if capacity is None:
            over = False
            worst = offered
        elif mode == 'per_day' and any(d is not None for d in by_day):
            # A type with no day is a full pass and lands on every day, so it
            # counts towards each day's total rather than as a day of its own.
            passes = by_day.get(None, 0)
            worst = max((n + passes) for d, n in by_day.items() if d is not None)
            over = worst > capacity
        else:
            worst = offered
            over = offered > capacity

        return _ok({
            'tiers': [serialize_tier(t) for t in tiers],
            'days': [{'day': row['day'].isoformat(), 'n': row['n']}
                     for row in event_days(event)],
            'capacity': {
                'capacity': capacity,
                'mode': mode,
                'sold': availability.sold_on_event(event),
                'held': availability.held_on_event(event),
                'room': availability.event_room(event),
                'offered_by_tiers': offered,
                # The figure the ceiling is actually compared against: the
                # busiest single day under per_day, the whole lot under total.
                'offered_worst_day': worst,
                'day_count': len([d for d in by_day if d is not None]),
                # True only when the types really do promise more than the
                # venue will take, judged the way this event counts.
                'over_capacity': over,
            },
        }, 'Ticket types')

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

    email_limit, err = _read_email_limit(request.data.get('max_tickets_per_email'))
    if err:
        return err

    tier = TicketTier.objects.create(
        event=event,
        name=name[:60],
        price=price,
        quantity=quantity,
        perks=str(perks or '')[:255],
        day=request.data.get('day') or None,
        day_label=str(request.data.get('day_label') or '')[:60],
        max_tickets_per_email=email_limit,
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

    # Which influencer's audience this type is for. Cleared with an empty
    # value, because taking a tier back off a creator is a thing organisers do.
    if 'unlocked_by' in request.data:
        raw = request.data.get('unlocked_by')
        if raw in (None, '', 0, '0'):
            tier.unlocked_by = None
        else:
            from .models import EventReferral
            referral = EventReferral.objects.filter(
                event=tier.event, pk=raw).first()
            if referral is None:
                return _err('That influencer is not on this event.',
                            'NOT_FOUND', status.HTTP_404_NOT_FOUND,
                            field='unlocked_by')
            tier.unlocked_by = referral
        updated.append('unlocked_by')

    if 'max_tickets_per_email' in request.data:
        raw = request.data.get('max_tickets_per_email')
        if raw in (None, ''):
            tier.max_tickets_per_email = None
        else:
            limit, err = _read_email_limit(raw)
            if err:
                return err
            tier.max_tickets_per_email = limit
        updated.append('max_tickets_per_email')

    # The date a type admits on. Parsed rather than assigned raw: a DateField
    # given a string writes correctly, because Django coerces on the way to the
    # database, but the instance in memory keeps the string - and the very next
    # line serialises it and calls `.isoformat()` on a str.
    #
    # That is a 500 AFTER a successful write, which is the worst shape a bug can
    # take: the change lands, the caller is told it failed, and they try again.
    # It happened live, and the retries landed on a different type, so an event
    # ended up with two ticket types pointed at the same day.
    if 'day' in request.data:
        raw = request.data.get('day')
        if raw in (None, ''):
            tier.day = None
        else:
            try:
                tier.day = date.fromisoformat(str(raw)[:10])
            except ValueError:
                return _err('That is not a date.', 'INVALID_DATE', field='day')
        updated.append('day')

    if 'day_label' in request.data:
        tier.day_label = str(request.data.get('day_label') or '')[:60]
        updated.append('day_label')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    tier.save(update_fields=updated)
    # Read back before serialising. Every field is now the type the column says
    # it is, whatever was assigned above, so a serializer that calls a date
    # method on a date cannot be handed a string.
    tier.refresh_from_db()
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

"""Holds, and the money an event has taken.

Two organiser-only surfaces that had no endpoint at all.

**Holds.** Every real event has a guest list, press, the venue's own allocation
and the artist's family. Without them an organiser buys their own tickets, which
corrupts the sales figures they then show a sponsor.

**Money.** What was sold, what came back, and what is owed. There was no
per-event view of any of it, which makes settling with a venue or a sponsor a
manual count.
"""
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from . import availability
from .models import Ticket, TicketHold


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _organiser(request, event_id):
    user, err = actor_from_request(request)
    if err:
        return None, None, err

    from .views import _event_by_ref
    event = _event_by_ref(event_id)
    if event is None:
        return None, None, _err('Event not found.', 'NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return None, None, _err('Only the event organizer can do that.',
                                'ONLY_EVENT_ORGANIZER_CAN',
                                status.HTTP_403_FORBIDDEN)
    return event, user, None


def _hold_row(hold):
    return {
        'id': hold.id,
        'name': hold.name,
        'kind': hold.kind,
        'quantity': hold.quantity,
        'issued': hold.issued,
        'outstanding': hold.outstanding,
        'tier': hold.tier_id,
        'tier_name': hold.tier.name if hold.tier_id else None,
        'note': hold.note,
        'released': bool(hold.released_at),
        'created_at': hold.created_at,
    }


# ---------------------------------------------------------------------------
# Holds
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def holds(request, event_id):
    """GET  /event/<id>/holds/ - what is held back and why.
       POST /event/<id>/holds/ - hold some tickets.
    """
    event, user, err = _organiser(request, event_id)
    if err:
        return err

    if request.method == 'GET':
        rows = event.holds.select_related('tier').all()
        return _ok({
            'holds': [_hold_row(h) for h in rows],
            'availability': availability.snapshot(event),
        }, 'Holds')

    name = str(request.data.get('name') or '').strip()
    if not name:
        return _err('A hold needs a name, so anybody reading it knows who it is '
                    'for.', 'VALIDATION_FAILED', field='name')

    try:
        quantity = int(request.data.get('quantity') or 0)
    except (TypeError, ValueError):
        return _err('How many has to be a number.', 'INVALID_NUMBER',
                    field='quantity')
    if quantity < 1:
        return _err('Hold at least one ticket.', 'VALIDATION_FAILED',
                    field='quantity')

    tier = None
    tier_id = request.data.get('tier')
    if tier_id not in ('', None):
        tier = event.ticket_tiers.filter(pk=tier_id).first()
        if tier is None:
            return _err('That ticket type does not belong to this event.',
                        'VALIDATION_FAILED', field='tier')

    # A hold cannot take tickets that are not there. Held tickets are not sold,
    # but they are equally unavailable, so the check is against what is actually
    # sellable rather than against the raw allocation.
    spare = (availability.available(tier) if tier is not None
             else availability.event_room(event))
    if spare is not None and quantity > spare:
        return _err(
            'Only %s ticket(s) are available to hold.' % spare,
            'NOT_ENOUGH_TO_HOLD', status.HTTP_409_CONFLICT, field='quantity')

    hold = TicketHold.objects.create(
        event=event, tier=tier, name=name[:80],
        kind=str(request.data.get('kind') or 'guest')[:20],
        quantity=quantity,
        note=str(request.data.get('note') or '')[:200],
        created_by=user,
    )
    return _ok({'hold': _hold_row(hold),
                'availability': availability.snapshot(event)},
               'Held %s ticket(s).' % quantity, status.HTTP_201_CREATED)


@api_view(['POST'])
def release_hold(request, event_id, hold_id):
    """POST /event/<id>/holds/<hid>/release/ - put them back on sale.

    Only what has not been issued comes back. Tickets already given to somebody
    are theirs, and a release that un-issued them would take a ticket off
    somebody who is holding it.
    """
    event, _user, err = _organiser(request, event_id)
    if err:
        return err

    hold = event.holds.filter(pk=hold_id).first()
    if hold is None:
        return _err('No such hold on this event.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if hold.released_at:
        return _err('That hold has already been released.', 'ALREADY_RELEASED',
                    status.HTTP_409_CONFLICT)

    returned = hold.outstanding
    hold.released_at = timezone.now()
    hold.save(update_fields=['released_at'])
    return _ok({'hold': _hold_row(hold),
                'returned': returned,
                'availability': availability.snapshot(event)},
               '%s ticket(s) back on sale.' % returned)


@api_view(['POST'])
def issue_hold(request, event_id, hold_id):
    """POST /event/<id>/holds/<hid>/issue/ - turn held tickets into real ones.

    The guest list arriving. Each name becomes a ticket with a code, at no
    charge, which is what makes it different from the organiser buying their own
    and then having to explain the revenue.
    """
    event, user, err = _organiser(request, event_id)
    if err:
        return err

    hold = event.holds.filter(pk=hold_id).first()
    if hold is None:
        return _err('No such hold on this event.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if hold.released_at:
        return _err('That hold has been released, so there is nothing to issue.',
                    'ALREADY_RELEASED', status.HTTP_409_CONFLICT)

    names = request.data.get('names') or []
    if isinstance(names, str):
        names = [names]
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return _err('Name who the tickets are for.', 'VALIDATION_FAILED',
                    field='names')
    if len(names) > hold.outstanding:
        return _err(
            'Only %s ticket(s) are still held. Asked for %s.'
            % (hold.outstanding, len(names)),
            'NOT_ENOUGH_HELD', status.HTTP_409_CONFLICT, field='names')

    tier = hold.tier or event.ticket_tiers.order_by('price').first()
    if tier is None:
        return _err('This event has no ticket types, so there is nothing to '
                    'issue against.', 'NO_TIER')

    from .views_tickets import _new_code

    issued = []
    for name in names:
        ticket = Ticket.objects.create(
            event=event, tier=tier, user=user,
            code=_new_code(),
            price_vc=0, price_ngn=0,
            attendee_name=name[:120],
        )
        issued.append({'code': ticket.code, 'name': name})

    hold.issued += len(names)
    hold.save(update_fields=['issued'])

    return _ok({'hold': _hold_row(hold), 'issued': issued,
                'availability': availability.snapshot(event)},
               'Issued %s ticket(s).' % len(names), status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

@api_view(['GET'])
def event_money(request, event_id):
    """GET /event/<id>/money/ - what this event has taken, and what came back.

    There was no per-event view of any of it, which makes settling with a venue
    or a sponsor a manual count of rows.

    Everything is computed from the tickets, so it reconciles by construction:
    what is owed is what was taken minus what went back, and none of the three
    is stored anywhere that could drift from the others.
    """
    event, _user, err = _organiser(request, event_id)
    if err:
        return err

    tickets = event.tickets.select_related('tier')

    def totals(rows):
        agg = rows.aggregate(vc=Sum('price_vc'), ngn=Sum('price_ngn'))
        return {
            'count': rows.count(),
            'vc': int(agg['vc'] or 0),
            'ngn': float(agg['ngn'] or 0),
        }

    live = tickets.filter(status__in=('valid', 'checked_in'))
    back = tickets.filter(status__in=('refunded', 'cancelled'))

    taken = totals(live)
    returned = totals(back)

    by_tier = []
    for tier in event.ticket_tiers.all():
        rows = tickets.filter(tier=tier, status__in=('valid', 'checked_in'))
        by_tier.append(dict(totals(rows), id=tier.id, name=tier.name,
                            price_ngn=float(tier.price)))

    return _ok({
        'taken': taken,
        'returned': returned,
        # What is owed to the organiser: what was taken, less what went back.
        # Free tickets and issued guest-list ones are counted in `taken['count']`
        # and contribute nothing to the money, which is the honest reading of
        # both numbers.
        'owed': {
            'vc': taken['vc'] - returned['vc'],
            'ngn': round(taken['ngn'] - returned['ngn'], 2),
        },
        'checked_in': tickets.filter(status='checked_in').count(),
        'free_tickets': live.filter(price_vc=0).count(),
        'by_tier': by_tier,
        'availability': availability.snapshot(event),
    }, 'Event money')

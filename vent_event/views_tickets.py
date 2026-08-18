"""Event ticketing - tiers, purchase, my tickets, organizer check-in.

Money path mirrors tournament registration exactly: the wallet row is locked with
select_for_update, the PIN is verified against the stored hash, the debit and the
ticket rows are written in one transaction, and a Transaction row records it.
Tier prices are set in NGN; the charge is in VENT COINS at the platform rate, and
both are stored on the ticket so a later rate change never rewrites history.
"""
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from vent_auth.models import Users, UserWallet, Transaction
from .models import Event, TicketTier, Ticket

SESSION_TIMEOUT_MINUTES = 120
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no look-alikes
MAX_PER_PURCHASE = 10


def _error(message, code, http_status, extra=None):
    body = {'status': 'error', 'data': extra or {}, 'message': message, 'code': code}
    return Response(body, status=http_status)


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message}, status=status.HTTP_200_OK)


def _authenticate(request):
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


def _coins_per_100_ngn():
    # Single source of truth: the wallet app owns the rate.
    from vent_auth.views_wallet import COINS_PER_100_NGN
    return COINS_PER_100_NGN


def _ngn_to_coins(amount_ngn):
    return int((float(amount_ngn) * _coins_per_100_ngn()) // 100)


def _new_code():
    while True:
        code = 'VT-' + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not Ticket.objects.filter(code=code).exists():
            return code


def serialize_tier(tier):
    remaining = max(int(tier.quantity) - int(tier.sold), 0)
    return {
        'id': tier.id,
        'name': tier.name,
        'price_ngn': float(tier.price),
        'price_vc': _ngn_to_coins(tier.price),
        'price': _ngn_to_coins(tier.price),   # VC - what the buy modal renders
        'quantity': tier.quantity,
        'sold': tier.sold,
        'remaining': remaining,
        'sold_out': remaining == 0,
        'perks': [p.strip() for p in (tier.perks or '').split(',') if p.strip()],
    }


def serialize_ticket(ticket):
    event = ticket.event
    return {
        'id': ticket.id,
        'code': ticket.code,
        'status': ticket.status,
        'price_vc': ticket.price_vc,
        'price_ngn': float(ticket.price_ngn),
        'purchased_at': ticket.purchased_at,
        'checked_in_at': ticket.checked_in_at,
        'tier': {'id': ticket.tier_id, 'name': ticket.tier.name},
        'attendee_name': ticket.attendee_name,
        'attendee_email': ticket.attendee_email,
        'attendee_phone': ticket.attendee_phone,
        'event': {
            'id': event.event_id,
            'event_id': event.event_id,
            'name': event.name,
            'event_type': event.event_type,
            'location': event.location,
            'event_link': event.event_link,
            # start_date/end_date are canonical; the split date+time columns are
            # legacy and drift, so derive from the canonical pair when present.
            'start_date': event.start_date,
            'end_date': event.end_date,
            'event_date': (event.start_date.date() if event.start_date else event.event_date),
            'start_time': (event.start_date.time() if event.start_date else event.start_time),
            'end_time': (event.end_date.time() if event.end_date else event.end_time),
        },
    }


# ---------------------------------------------------------------------------
# GET /event/<id>/ticket-types/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def ticket_types(request, event_id):
    event = Event.objects.filter(event_id=event_id, is_active=True).first()
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    tiers = event.ticket_tiers.all().order_by('price')
    return _ok(
        {
            'event_id': event.event_id,
            'event_name': event.name,
            'entry_fee_ngn': float(event.entry_fee or 0),
            'tiers': [serialize_tier(t) for t in tiers],
            'ticket_types': [serialize_tier(t) for t in tiers],  # alias
            'count': tiers.count(),
        },
        'Ticket tiers retrieved.',
    )


# ---------------------------------------------------------------------------
# POST /event/<id>/buy-ticket/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def buy_ticket(request, event_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event = Event.objects.filter(event_id=event_id, is_active=True).first()
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    # Doors close when the event ends: no selling a ticket to something over.
    # Walk-ups stay open while it is running, even past the registration cutoff.
    now = timezone.now()
    started = bool(event.start_date and now >= event.start_date)
    if event.end_date and now > event.end_date:
        return _error('This event has ended.', 'STATE_CONFLICT', status.HTTP_409_CONFLICT)
    if not started and event.reg_end_date and now > event.reg_end_date:
        return _error('Ticket sales have closed for this event.',
                      'STATE_CONFLICT', status.HTTP_409_CONFLICT)

    tier_id = request.data.get('tier_id')
    pin = request.data.get('pin')
    attendees = request.data.get('attendees') or []
    if not isinstance(attendees, list):
        attendees = []
    raw_qty = request.data.get('quantity', request.data.get('qty', len(attendees) or 1))
    try:
        quantity = int(raw_qty)
    except (TypeError, ValueError):
        return _error('Quantity must be a number.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    if not tier_id:
        return _error('Pick a ticket type.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if quantity < 1 or quantity > MAX_PER_PURCHASE:
        return _error(f'Choose between 1 and {MAX_PER_PURCHASE} tickets.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        tier = TicketTier.objects.select_for_update().filter(id=tier_id, event=event).first()
        if tier is None:
            return _error('That ticket type is not available for this event.',
                          'NOT_FOUND', status.HTTP_404_NOT_FOUND)

        remaining = int(tier.quantity) - int(tier.sold)
        if remaining <= 0:
            return _error(f'{tier.name} is sold out.', 'SOLD_OUT', status.HTTP_409_CONFLICT)
        if quantity > remaining:
            return _error(f'Only {remaining} {tier.name} ticket(s) left.',
                          'INSUFFICIENT_STOCK', status.HTTP_409_CONFLICT)

        unit_vc = _ngn_to_coins(tier.price)
        total_vc = unit_vc * quantity

        wallet = UserWallet.objects.select_for_update().filter(user=user).first()
        if wallet is None:
            return _error('No wallet found for this account.', 'NO_WALLET', status.HTTP_400_BAD_REQUEST)

        if total_vc > 0:
            if not wallet.pin_hash:
                return _error('Set a wallet PIN before buying tickets.',
                              'PIN_REQUIRED', status.HTTP_400_BAD_REQUEST)
            if not pin or not check_password(str(pin), wallet.pin_hash):
                return _error('Incorrect wallet PIN.', 'INVALID_PIN', status.HTTP_400_BAD_REQUEST)
            if wallet.wallet_balance < total_vc:
                return _error(
                    f'You need {total_vc} VC for this purchase - your balance is {wallet.wallet_balance} VC.',
                    'INSUFFICIENT_BALANCE', status.HTTP_400_BAD_REQUEST,
                )
            wallet.wallet_balance -= total_vc
            wallet.save(update_fields=['wallet_balance'])

            Transaction.objects.create(
                wallet=wallet,
                type='deduction',
                amount=-total_vc,
                description=f'{quantity}x {tier.name} - {event.name}',
                status='completed',
            )

        tickets = []
        for i in range(quantity):
            who = attendees[i] if i < len(attendees) and isinstance(attendees[i], dict) else {}
            tickets.append(Ticket.objects.create(
                event=event, tier=tier, user=user, code=_new_code(),
                price_vc=unit_vc, price_ngn=tier.price,
                # No name given means the buyer is the attendee, which is what
                # the door needs to see on the pass.
                attendee_name=((who.get('name') or '').strip()
                               or user.full_name or user.username)[:120],
                attendee_email=((who.get('email') or '').strip() or user.email or '')[:254],
                attendee_phone=(who.get('phone') or '').strip()[:40],
            ))

        TicketTier.objects.filter(id=tier.id).update(sold=F('sold') + quantity)

    # Fire-and-forget: a failed notification must never undo a paid purchase.
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            user=user,
            category='event',
            title=f'{quantity} ticket(s) for {event.name}',
            body=f'{tier.name} · {total_vc} VC',
            link='/events/my-tickets',
            metadata={'event_id': event.event_id, 'tier': tier.name},
        )
    except Exception:
        pass

    return Response({
        'status': 'success',
        'data': {
            'tickets': [serialize_ticket(t) for t in tickets],
            'quantity': quantity,
            'total_vc': total_vc,
            'wallet_balance': UserWallet.objects.get(user=user).wallet_balance,
            'new_balance': UserWallet.objects.get(user=user).wallet_balance,
        },
        'message': f'{quantity} ticket(s) booked for {event.name}.',
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /event/my-tickets/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def my_tickets(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    wanted = (request.GET.get('status') or '').strip()
    qs = Ticket.objects.filter(user=user).select_related('event', 'tier')
    if wanted in {'valid', 'checked_in', 'refunded', 'cancelled'}:
        qs = qs.filter(status=wanted)

    tickets = [serialize_ticket(t) for t in qs]
    today = timezone.now().date()
    upcoming = [t for t in tickets if t['event']['event_date'] and t['event']['event_date'] >= today]
    past = [t for t in tickets if not (t['event']['event_date'] and t['event']['event_date'] >= today)]

    return _ok(
        {'tickets': tickets, 'upcoming': upcoming, 'past': past, 'count': len(tickets)},
        'Tickets retrieved.',
    )


# ---------------------------------------------------------------------------
# POST /event/ticket/<code>/check-in/  - organizer only
# ---------------------------------------------------------------------------

@api_view(['POST'])
def check_in_ticket(request, code):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    ticket = Ticket.objects.select_related('event', 'tier', 'user').filter(code=code.upper()).first()
    if ticket is None:
        return _error('No ticket with that code.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if ticket.event.creator_id != user.user_id:
        return _error('Only the event organizer can check tickets in.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if ticket.status == 'checked_in':
        return _error(
            f'Already checked in at {timezone.localtime(ticket.checked_in_at).strftime("%H:%M")}.',
            'ALREADY_CHECKED_IN', status.HTTP_409_CONFLICT,
            extra={'ticket': serialize_ticket(ticket)},
        )
    if ticket.status != 'valid':
        return _error(f'This ticket is {ticket.status}.', 'INVALID_TICKET', status.HTTP_409_CONFLICT)

    ticket.status = 'checked_in'
    ticket.checked_in_at = timezone.now()
    ticket.checked_in_by = user
    ticket.save(update_fields=['status', 'checked_in_at', 'checked_in_by'])

    return _ok({'ticket': serialize_ticket(ticket), 'holder': ticket.user.username},
               f'{ticket.user.username} checked in.')


# ---------------------------------------------------------------------------
# GET /event/<id>/attendees/ - organizer only
# ---------------------------------------------------------------------------

@api_view(['GET'])
def event_attendees(request, event_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event = Event.objects.filter(event_id=event_id).first()
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id:
        return _error('Only the event organizer can see the attendee list.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    tickets = Ticket.objects.filter(event=event).select_related('tier', 'user')
    rows = [
        {
            'code': t.code,
            'username': t.user.username,
            'full_name': t.user.full_name,
            'attendee_name': t.attendee_name or t.user.full_name or t.user.username,
            'attendee_email': t.attendee_email,
            'tier': t.tier.name,
            'status': t.status,
            'purchased_at': t.purchased_at,
            'checked_in_at': t.checked_in_at,
        }
        for t in tickets
    ]
    return _ok(
        {
            'attendees': rows,
            'count': len(rows),
            'checked_in': sum(1 for r in rows if r['status'] == 'checked_in'),
        },
        'Attendees retrieved.',
    )

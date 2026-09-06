"""Event ticketing - tiers, purchase, my tickets, organizer check-in.

Money path mirrors tournament registration exactly: the wallet row is locked with
select_for_update, the PIN is verified against the stored hash, the debit and the
ticket rows are written in one transaction, and a Transaction row records it.
Tier prices are set in NGN; the charge is in VENT COINS at the platform rate, and
both are stored on the ticket so a later rate change never rewrites history.
"""
import secrets
from datetime import datetime, timedelta

from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from vent_auth.models import Users, UserWallet, Transaction
from . import checkout
from .models import Event, TicketTier, Ticket


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



SESSION_TIMEOUT_MINUTES = 120
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no look-alikes
MAX_PER_PURCHASE = 10


def _error(message, code, http_status, extra=None, field=None):
    body = {'status': 'error', 'data': extra or {}, 'message': message, 'code': code}
    # Which field was refused, so the form can point at it rather than making
    # somebody hunt the page for what they missed. Same shape the guest
    # checkout answers with, because it is the same form.
    if field is not None:
        body['field'] = field
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


def _ngn_to_coins(amount_ngn):
    # Single source of truth: the wallet app owns the rate.
    from vent_auth.views_wallet import NGN_PER_COIN
    return int(float(amount_ngn) // NGN_PER_COIN)


def _new_code():
    while True:
        code = 'VT-' + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not Ticket.objects.filter(code=code).exists():
            return code


def serialize_tier(tier):
    # `remaining` is what somebody can ACTUALLY buy, which is the lower of this
    # type's own room and the room left in the venue.
    #
    # It used to be `quantity - sold` alone, and that is how a page came to
    # advertise "4814 remaining" on a type while the checkout answered "This
    # event is sold out": two tiers of 5000 against a venue capacity of 400,
    # 300 sold and 100 held. Both numbers were correct about different
    # questions, and the one the buyer was shown was not the one that decided
    # whether they could pay. That is a false advertisement of stock, which
    # costs a sale and the trust with it.
    #
    # `availability.available()` was already the single answer to this and this
    # function simply was not asking it. Availability is one function.
    from . import availability

    remaining = availability.available(tier)
    # The type's own room, kept separately: an organiser looking at the console
    # still needs to see 186 of 5000 sold rather than a number the venue
    # ceiling has already flattened.
    tier_only = availability.tier_available(tier)
    room = availability.event_room(tier.event, getattr(tier, 'day', None))

    return {
        'id': tier.id,
        'name': tier.name,
        'price_ngn': float(tier.price),
        'price_vc': _ngn_to_coins(tier.price),
        'price': _ngn_to_coins(tier.price),   # VC - what the buy modal renders
        'quantity': tier.quantity,
        'sold': tier.sold,
        'remaining': remaining,
        'tier_remaining': tier_only,
        # Why it cannot be bought, so a screen can say "the venue is full"
        # rather than "sold out" next to a type that plainly has thousands
        # left. A bare contradiction reads as a broken site.
        'unavailable_reason': (
            None if remaining > 0
            else 'venue_full' if (room is not None and room <= 0)
            else 'tier_sold_out'),
        'sold_out': remaining == 0,
        'perks': [p.strip() for p in (tier.perks or '').split(',') if p.strip()],
        # Which day this ticket is for. A multi-day event sells "Day 1" and
        # "Day 2" side by side, and two cards differing only in a number tell
        # a buyer nothing about which date they are actually buying. Stored
        # since the wizard wrote it and never sent, so no screen could show it.
        'day': tier.day.isoformat() if tier.day else None,
        'day_label': tier.day_label or '',
        # How many of this type one address may hold. Null means this type sets
        # no rule of its own; the day's and the event's still apply, so the
        # screen says "no limit of its own" rather than "no limit".
        'max_tickets_per_email': tier.max_tickets_per_email,
        # What the price does, so the buy screen can say "12 left at this price"
        # and the console can edit it rather than render an empty box.
        'early_bird_quantity': tier.early_bird_quantity,
        'early_bird_price': float(tier.early_bird_price) if tier.early_bird_price is not None else None,
        'group_min': tier.group_min,
        'group_price': float(tier.group_price) if tier.group_price is not None else None,
        # The code itself is never sent. Whether one exists is not a secret;
        # what it is, is.
        'is_hidden': tier.is_hidden,
        # Which influencer's audience this type belongs to, if any. The name
        # only: their code is the key and is never published.
        'unlocked_by': ({'id': tier.unlocked_by_id, 'name': tier.unlocked_by.name}
                        if tier.unlocked_by_id else None),
        'price_now_ngn': float(tier.price_for(1)),
    }


def _holder(ticket):
    """Who this ticket is for, whether or not they have an account.

    A guest has no username, so anything that reached for one would 500 on the
    first guest through the door.
    """
    return (ticket.attendee_name
            or (ticket.user.username if ticket.user_id else '')
            or ticket.attendee_email
            or ticket.code)


def serialize_ticket(ticket):
    event = ticket.event
    return {
        'id': ticket.id,
        'code': ticket.code,
        'status': ticket.status,
        'checked_in_gate': ticket.checked_in_gate,
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
    event = _event_by_ref(event_id, is_active=True)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    tiers = list(event.ticket_tiers.all().order_by('price'))

    # A type with an access code is not listed until somebody types it. That is
    # the whole feature: a members' presale that appears in the public list is
    # not a presale.
    #
    # The same code can be an influencer's referral: a type locked to them is
    # invisible until somebody arrives with their link or their code, which is
    # the whole point of giving a creator something their audience alone can
    # buy.
    code = str(request.GET.get('code') or '').strip()
    visible = [tier for tier in tiers if tier.opened_by(code)]
    unlocked = [t for t in visible if t.is_hidden]

    return _ok(
        {
            'event_id': event.event_id,
            'event_name': event.name,
            'entry_fee_ngn': float(event.entry_fee or 0),
            'tiers': [serialize_tier(t) for t in visible],
            'ticket_types': [serialize_tier(t) for t in visible],  # alias
            'count': len(visible),
            # So the buy screen can say "code accepted" rather than leaving
            # somebody wondering whether anything happened.
            'unlocked': [t.name for t in unlocked],
            'hidden_count': len(tiers) - len(visible),
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

    event = _event_by_ref(event_id, is_active=True)
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

        # One function decides what is sellable, because three rules in three
        # files could disagree about the same number - and one of them, the
        # venue's capacity, was not enforced at all.
        #
        # It accounts for the type's own allocation, the venue's ceiling (a
        # SECOND ceiling, and the lower one wins), and everything held back:
        # guest list, press, venue, an influencer's allocation.
        from . import availability

        remaining = availability.tier_available(tier)
        if remaining <= 0:
            return _error(f'{tier.name} is sold out.', 'SOLD_OUT', status.HTTP_409_CONFLICT)
        if quantity > remaining:
            return _error(f'Only {remaining} {tier.name} ticket(s) left.',
                          'INSUFFICIENT_STOCK', status.HTTP_409_CONFLICT)

        # Somebody holding a waitlist offer is buying into the room their own
        # offer is holding open, so the ceiling does not apply to them. Without
        # this the offer is unusable: the event is sold out by definition, which
        # is why they are in the queue.
        from .views_waitlist import expire_stale_offers
        expire_stale_offers(event)
        offer = event.waitlist.filter(
            user=user, status='offered',
            offer_expires_at__gt=timezone.now()).first()

        # Same day as the type being bought, so this and the listing
        # answer the same question. They disagreed once already.
        room = availability.event_room(event, getattr(tier, 'day', None))
        if room is not None and offer is None:
            if room <= 0:
                return _error('This event is sold out.', 'EVENT_FULL',
                              status.HTTP_409_CONFLICT)
            if quantity > room:
                return _error(
                    f'Only {room} ticket(s) left for this event.',
                    'EVENT_FULL', status.HTTP_409_CONFLICT)

        # The access code, checked at the purchase and not only at the listing.
        # A hidden type that can be bought by anybody who guesses its id is not
        # hidden.
        if tier.is_hidden:
            given = str(request.data.get('code') or '').strip()
            if not tier.opened_by(given):
                return _error('That ticket type needs an access code.',
                              'CODE_REQUIRED', status.HTTP_403_FORBIDDEN)

        # How many one address may hold. The same rule as the guest checkout,
        # because it is a property of the event and not of the door somebody
        # came through. A signed-in buyer's address is their account's.
        buyer_email = (str(request.data.get('email') or '').strip()
                       or (user.email or ''))
        ok, refusal = checkout.room_for_email(event, buyer_email, quantity,
                                              tier=tier)
        if not ok:
            from .views_guest import _email_limit_or_error
            # One refusal, written once. A signed-in buyer and a guest are
            # refused by the same three rules for the same reasons, and two
            # copies of that sentence is how the two checkouts start
            # disagreeing about what the organiser set.
            return _email_limit_or_error(event, buyer_email, quantity,
                                         tier=tier)

        # What the organiser asked for. A signed-in buyer answers exactly the
        # same questions a guest does: the fields belong to the event, not to
        # the checkout somebody happened to arrive through. Skipping them here
        # would give the door a shirt size for half the queue and nothing for
        # the other half, which is the same feature built for half the product.
        #
        # Checked before any money moves. Refusing after the wallet is debited
        # means a refund for a question that could have been asked first.
        try:
            order_answers = checkout.collect(event, request.data.get('answers'))
            per_person = [
                checkout.collect(
                    event,
                    (attendees[i] or {}).get('answers')
                    if i < len(attendees) and isinstance(attendees[i], dict) else {},
                    per_ticket_index=i)
                for i in range(quantity)
            ]
        except checkout.CheckoutError as exc:
            return _error(str(exc), 'FIELD_REQUIRED',
                          status.HTTP_400_BAD_REQUEST, field=exc.field)

        # Early bird and group rates. `price_for` decides, so the two cannot
        # drift between the screen that shows a price and the code that charges
        # one.
        unit_ngn = tier.price_for(quantity)
        unit_vc = _ngn_to_coins(unit_ngn)
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
            mine = {**order_answers, **per_person[i]}
            tickets.append(Ticket.objects.create(
                event=event, tier=tier, user=user, code=_new_code(),
                price_vc=unit_vc, price_ngn=unit_ngn,
                # No name given means the buyer is the attendee, which is what
                # the door needs to see on the pass.
                attendee_name=((who.get('name') or '').strip()
                               or user.full_name or user.username)[:120],
                attendee_email=((who.get('email') or '').strip() or user.email or '')[:254],
                attendee_phone=((who.get('phone') or '').strip()[:40]
                                or checkout.phone_from(event, mine)),
                answers=mine,
            ))

        TicketTier.objects.filter(id=tier.id).update(sold=F('sold') + quantity)

        # Credit the influencer link this buyer came through, in the same
        # transaction as the purchase. A signed-in buyer reaches checkout the
        # same way a guest does, so the same `ref` the page has been holding
        # since they arrived is sent here too. An unknown or switched-off code
        # credits nobody and is never a reason to refuse the sale.
        from . import referrals as _refs
        _refs.attribute(tickets, _refs.resolve(event, request.data.get('ref')))

        # The offer is spent, inside the same transaction as the purchase.
        # Leaving it standing would let one person in the queue buy every
        # ticket that comes back.
        if offer is not None:
            offer.status = 'taken'
            offer.resolved_at = timezone.now()
            offer.save(update_fields=['status', 'resolved_at'])

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
        # One email per ticket: each carries its own code and admits one person,
        # and a buyer booking for friends needs to forward them individually.
        from vent_auth import emails
        for ticket in tickets:
            emails.send_ticket_purchased(ticket)
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

    # Anything bought as a guest at this account's own address becomes theirs
    # before the list is built. `claim_for` used to run only at email
    # verification, which happens once and usually before the ticket exists, so
    # a member who checked out as a guest was told they had no tickets while
    # holding one. The update matches nothing on the common path and costs a
    # single statement.
    try:
        from .views_guest import claim_for
        claim_for(user)
    except Exception:
        pass

    wanted = (request.GET.get('status') or '').strip()
    qs = Ticket.objects.filter(user=user).select_related('event', 'tier')
    if wanted in {'valid', 'checked_in', 'refunded', 'cancelled'}:
        qs = qs.filter(status=wanted)

    tickets = [serialize_ticket(t) for t in qs]
    today = timezone.now().date()
    upcoming = [t for t in tickets if t['event']['event_date'] and t['event']['event_date'] >= today]
    past = [t for t in tickets if not (t['event']['event_date'] and t['event']['event_date'] >= today)]

    # The counts under the filters, computed from the same rows the list is
    # built from rather than from a second query that can disagree with it.
    counts = {'all': len(tickets), 'active': 0, 'used': 0, 'refunded': 0}
    for t in tickets:
        if t['status'] == 'valid':
            counts['active'] += 1
        elif t['status'] == 'checked_in':
            counts['used'] += 1
        elif t['status'] in ('refunded', 'cancelled'):
            counts['refunded'] += 1

    return _ok(
        {'tickets': tickets, 'upcoming': upcoming, 'past': past,
         'count': len(tickets), 'counts': counts},
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

    # The creator, somebody they put on the door, or the organisation's own
    # people. EventManager has had a `door` role since it was written, "check
    # tickets in, nothing else", and this path never consulted it, so in
    # practice one person scanned or the organiser handed over their account.
    from .permissions import may_work_the_door
    if not may_work_the_door(user, ticket.event):
        return _error('Only the event organizer or their door staff can check '
                      'tickets in.', 'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    gate = str(request.data.get('gate') or '').strip()[:60]

    # WHICH DAY this door is admitting for.
    #
    # CEO, 4 September 2026: "maybe there should also be different scanners for
    # different days. so that people dont come and show day 2 tickets on day one
    # and its work because tehre is just one scanner."
    #
    # `TicketTier.day` has carried the date since the tier was written, and
    # nothing had ever read it at the door, so a Saturday ticket opened Friday's
    # gate. A tier with no day admits on any day, which is what a single day
    # event and a full run pass both want, so the check only ever narrows.
    #
    # The scanner sends the day; without one, today at the venue is meant. The
    # API decides it either way, because a door that enforces this only in the
    # browser is a door anybody can walk through with a second browser.
    scan_day, day_error = _scan_day(request)
    if day_error:
        return day_error

    tier_day = ticket.tier.day if ticket.tier_id else None
    if tier_day and scan_day and tier_day != scan_day:
        return _error(
            'This ticket is for another day.', 'WRONG_DAY',
            status.HTTP_409_CONFLICT,
            extra={
                'ticket': serialize_ticket(ticket),
                # The frontend translates by code and builds the sentence from
                # these, because a date formatted in Python is a date in the
                # server's language.
                'ticket_day': tier_day.isoformat(),
                'ticket_day_label': ticket.tier.day_label or '',
                'scanning_day': scan_day.isoformat(),
            },
        )

    if ticket.status == 'checked_in':
        # WHEN, WHERE and WHO. "Already scanned" sends a steward to a
        # supervisor; this lets them decide at the gate.
        when = timezone.localtime(ticket.checked_in_at).strftime('%H:%M') \
            if ticket.checked_in_at else 'an unrecorded time'
        where = ticket.checked_in_gate or ''
        who = ticket.checked_in_by.username if ticket.checked_in_by_id else ''

        detail = 'Already checked in at %s' % when
        if where:
            detail += ' on %s' % where
        if who:
            detail += ' by %s' % who
        detail += '.'

        return _error(
            detail,
            'ALREADY_CHECKED_IN', status.HTTP_409_CONFLICT,
            extra={
                'ticket': serialize_ticket(ticket),
                'first_used': {
                    'at': ticket.checked_in_at,
                    'gate': where,
                    'by': who,
                    'holder': _holder(ticket),
                    'attendee_name': ticket.attendee_name,
                },
            },
        )
    if ticket.status != 'valid':
        return _error(f'This ticket is {ticket.status}.', 'INVALID_TICKET', status.HTTP_409_CONFLICT)

    ticket.status = 'checked_in'
    ticket.checked_in_at = timezone.now()
    ticket.checked_in_by = user
    ticket.checked_in_gate = gate
    ticket.save(update_fields=['status', 'checked_in_at', 'checked_in_by',
                               'checked_in_gate'])

    return _ok({'ticket': serialize_ticket(ticket),
                'holder': _holder(ticket),
                'attendee_name': ticket.attendee_name,
                'gate': gate},
               '%s checked in.' % _holder(ticket))


# ---------------------------------------------------------------------------
# GET /event/<id>/attendees/ - organizer only
# ---------------------------------------------------------------------------

def _scan_day(request):
    """The date this door is admitting for, or an error.

    Explicit beats implicit: a steward may open Saturday's door on Friday night
    to test it, and a scanner pinned to a date is the only way that is possible.
    Absent, it is today, which is what a door standing open right now means.
    """
    raw = str(request.data.get('day') or '').strip()
    if not raw:
        return timezone.localdate(), None
    try:
        return datetime.strptime(raw[:10], '%Y-%m-%d').date(), None
    except ValueError:
        return None, _error('That day could not be read.', 'INVALID_DAY',
                            status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def event_attendees(request, event_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    # The named address (/events/naija-anime-con/attendees) passes the slug
    # through, and this route was int-only, so every slug URL answered 404.
    event = (Event.objects.filter(event_id=int(event_id)).first()
             if str(event_id).isdigit()
             else Event.objects.filter(slug=str(event_id)).first())
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    # The creator, somebody on the door, or the organisation's own people. The
    # scanner downloads this list before the gates open, so a steward who
    # cannot load it cannot scan at all, which is the same fault the check-in
    # path had until tonight.
    from .permissions import may_work_the_door
    if not may_work_the_door(user, event):
        return _error('Only the event organizer or their door staff can see the '
                      'attendee list.', 'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    tickets = (Ticket.objects.filter(event=event)
               .select_related('tier', 'user', 'checked_in_by'))

    # ONLY WHAT CHANGED, when the caller says what it already has.
    #
    # CEO, 6 September 2026: "it took too long for the listto load on the
    # people managing the event", and separately that every event page should
    # keep itself current without anybody reloading it. Those two pull against
    # each other: this payload was 648KB, and re-fetching it on a timer would
    # starve the very connection the door needs. So a refresh asks for the
    # delta, and the full download happens once.
    #
    # A bad timestamp is ignored rather than refused. A door that stops
    # answering because a clock is odd is worse than one that sends a little
    # too much.
    since_raw = str(request.query_params.get('since') or '').strip()
    since = None
    if since_raw:
        parsed = parse_datetime(since_raw)
        if parsed is not None:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            since = parsed
            tickets = tickets.filter(updated_at__gt=since)

    # The answers are described per row, turning field ids into the organiser's
    # own labels, and on a list this size that is most of the cost. A door
    # refreshing every few seconds does not need them; the full list on first
    # load does. Asking for `lean` is asking for the door's version.
    lean = str(request.query_params.get('lean') or '').lower() in ('1', 'true', 'yes')

    # The stamp to send back next time. Taken BEFORE the rows are built so a
    # ticket bought while this response is being assembled is not skipped: it
    # will simply arrive again in the next delta, which is the safe direction
    # to be wrong in.
    asked_at = timezone.now()

    rows = [
        {
            'code': t.code,
            'username': t.user.username if t.user_id else '',
            'guest': not t.user_id,
            'full_name': t.user.full_name if t.user_id else '',
            'attendee_name': (t.attendee_name
                              or (t.user.full_name or t.user.username if t.user_id else '')
                              or t.attendee_email),
            'attendee_email': t.attendee_email,
            # The number, on the row rather than only inside the answers. A
            # steward ringing a no-show at the gate should not have to read a
            # labelled list to find it.
            'attendee_phone': t.attendee_phone,
            'tier': t.tier.name,
            # Which day this ticket admits on, so a scanner with no network can
            # still refuse a ticket for another day. Null means the whole run.
            'tier_day': t.tier.day.isoformat() if t.tier.day else None,
            'tier_day_label': t.tier.day_label or '',
            'status': t.status,
            'purchased_at': t.purchased_at,
            'checked_in_at': t.checked_in_at,
            # Where and by whom, so an offline scanner can tell a steward more
            # than "already scanned". Without these the duplicate warning has
            # only half of what it exists to say.
            'checked_in_gate': t.checked_in_gate,
            # What the organiser asked at checkout, with labels rather than
            # field ids: a door list showing {"7": "Large"} helps nobody.
            'answers': [] if lean else checkout.describe(event, t.answers),
            'checked_in_by': t.checked_in_by.username if t.checked_in_by_id else '',
            # Whether they admitted themselves, named the same way everywhere.
            'self_check_in': t.checked_in_gate == 'self',
        }
        for t in tickets
    ]

    # The totals are counted in the database, never off `rows`.
    #
    # They used to be `len(rows)` and a sum over them, which was right only
    # while the response carried every ticket. The moment a delta returns four
    # changed rows, counting those four would tell an organiser that four
    # people bought tickets and none came. The headcount is the number this
    # whole change exists to make trustworthy, so it does not get to be a
    # by-product of what happened to be sent.
    everyone = Ticket.objects.filter(event=event)
    return _ok(
        {
            'attendees': rows,
            'count': everyone.count(),
            'checked_in': everyone.filter(status='checked_in').count(),
            'returned': len(rows),
            # Hand this back as `since` on the next request and only what has
            # moved since comes down.
            'asked_at': asked_at,
            'delta': since is not None,
        },
        'Attendees retrieved.',
    )

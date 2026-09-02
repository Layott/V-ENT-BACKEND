"""Buying a ticket without an account.

CEO: "Hope people can get tickets without having to create accounts on the
website, but they'll need to submit emails and Maybe full name and number. Or
better still, the organizer decides what fields he wants to be collected."

Somebody buying a ticket to a one-off event should not have to make an account
to do it. A platform that insists loses the sale rather than gaining a member,
and the member it would have gained never comes back anyway.

Three paths, and the difference between them is only how the money moves:

  free      the ticket is issued immediately
  paid      Paystack, because a guest has no wallet to deduct from
  verify    Paystack calls back, or the browser returns, and the ticket issues

The signed-in wallet path in `views_tickets.buy_ticket` is untouched. A member
with coins should keep spending them, and running both through one endpoint
would mean one function holding two payment models and a branch on every line.
"""
import os
import secrets

import requests as http_requests
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import availability, checkout
from .models import Event, Ticket, TicketTier

PAYSTACK_BASE = 'https://api.paystack.co'
MAX_PER_PURCHASE = 10


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None,
         data=None):
    body = {'status': 'error', 'data': data or {}, 'message': message,
            'code': code}
    if field is not None:
        body['field'] = field
    return Response(body, status=http_status)


def _event(event_id):
    from .views import _event_by_ref
    return _event_by_ref(event_id, is_active=True)


def _referral_from(request, event):
    """The influencer link this order came through, if the page sent one.

    The page holds it because the buyer arrived through `?ref=CODE` and may
    have taken several minutes and several screens to reach checkout. An
    unknown or switched-off code resolves to None and the sale simply is not
    credited; it is never a reason to refuse somebody's money.
    """
    from . import referrals as _refs
    return _refs.resolve(event, request.data.get('ref'))


def _refs_resolve(event, code):
    from . import referrals as _refs
    return _refs.resolve(event, code)


def _paystack_headers():
    return {
        'Authorization': 'Bearer %s' % os.environ.get('PAYSTACK_SECRET_KEY', ''),
        'Content-Type': 'application/json',
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def checkout_fields(request, event_id):
    """GET /event/<id>/checkout-fields/ - what this organiser asks for.

    Public, because the buy form needs it before anybody has done anything.
    """
    event = _event(event_id)
    if event is None:
        return _err('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    return _ok({
        'fields': [
            {
                'id': f.id,
                'label': f.label,
                'kind': f.kind,
                'help_text': f.help_text,
                'required': f.required,
                'options': f.options,
                'per_ticket': f.per_ticket,
            }
            for f in event.checkout_fields.all()
        ],
        # Said explicitly rather than left for the form to assume. Email is
        # always collected and cannot be switched off.
        'email_required': True,
        'guest_checkout': True,
        # So the guest form can cap its own quantity box instead of letting
        # somebody pick four and be refused after filling everything in.
        'max_tickets_per_email': event.max_tickets_per_email,
    }, 'Checkout fields')


def _validate_order(event, request):
    """Everything about the order that is true before any money moves."""
    tier = event.ticket_tiers.filter(pk=request.data.get('tier_id')).first()
    if tier is None:
        return None, None, None, None, _err(
            'Pick a ticket type.', 'VALIDATION_ERROR', field='tier_id')

    # A guest is held to the same lock as anybody else. Two gates that check a
    # code differently is how one of them ends up letting people through.
    if tier.is_hidden:
        given = str(request.data.get('code') or '').strip()
        if not tier.opened_by(given):
            return None, None, None, None, _err(
                'That ticket type needs an access code.', 'CODE_REQUIRED',
                status.HTTP_403_FORBIDDEN)

    try:
        quantity = int(request.data.get('quantity') or 1)
    except (TypeError, ValueError):
        return None, None, None, None, _err(
            'How many has to be a number.', 'VALIDATION_ERROR', field='quantity')
    if quantity < 1 or quantity > MAX_PER_PURCHASE:
        return None, None, None, None, _err(
            'Between 1 and %s tickets.' % MAX_PER_PURCHASE, 'VALIDATION_ERROR',
            field='quantity')

    try:
        email = checkout.clean_email(request.data.get('email'))
    except checkout.CheckoutError as exc:
        return None, None, None, None, _err(str(exc), 'VALIDATION_ERROR',
                                            field='email')

    # One answer set per ticket, plus one for the order. A jersey size is per
    # person; a company name on the receipt is not, and asking it six times is
    # how somebody abandons a basket.
    attendees = request.data.get('attendees') or []
    if not isinstance(attendees, list):
        attendees = []
    while len(attendees) < quantity:
        attendees.append({})

    try:
        order_answers = checkout.collect(event, request.data.get('answers'))
        per_person = [
            checkout.collect(event, (attendees[i] or {}).get('answers'),
                             per_ticket_index=i)
            for i in range(quantity)
        ]
    except checkout.CheckoutError as exc:
        return None, None, None, None, _err(str(exc), 'FIELD_REQUIRED',
                                            field=getattr(exc, 'field', None))

    return tier, quantity, email, (order_answers, per_person, attendees), None


def _email_limit_or_error(event, email, quantity, tier=None):
    """Whether this address may hold this many, under every rule that applies.

    Checked against what the address ALREADY holds rather than against this
    request, because the case the organiser is guarding against is somebody
    refreshing the page and typing the same address again.

    Three rules can refuse it: the ticket type's own, the day's, and the
    event-wide one. The refusal names which, because "you already have a VIP"
    and "you already have two tickets for Saturday" send somebody to different
    next steps, and "you have reached a limit" sends them to neither.
    """
    ok, refusal = checkout.room_for_email(event, email, quantity, tier=tier)
    if ok:
        return None

    # A code per scope rather than one code and three sentences. A sentence
    # built here cannot be translated; a code with the numbers beside it can.
    #
    # Six codes, not three, because "you already hold two of these" and "nobody
    # may take three of these" are different sentences and a translation cannot
    # be both. The second happens when somebody asks for more in one go than
    # the rule allows, so they hold none and are still refused - and a
    # translation reading "you already have 0" would be nonsense.
    scope = refusal['scope']
    already, limit, name = refusal['already'], refusal['limit'], refusal['name']
    held = bool(already)
    codes = {
        ('tier', True): 'EMAIL_LIMIT_TIER',
        ('tier', False): 'EMAIL_LIMIT_TIER_MAX',
        ('day', True): 'EMAIL_LIMIT_DAY',
        ('day', False): 'EMAIL_LIMIT_DAY_MAX',
        ('event', True): 'EMAIL_LIMIT_REACHED',
        ('event', False): 'EMAIL_LIMIT',
    }

    if scope == 'tier':
        message = (
            'That email address already has %s %s ticket(s), and the organiser '
            'allows %s.' % (already, name, limit) if held else
            'The organiser allows %s %s ticket(s) per email address.'
            % (limit, name))
    elif scope == 'day':
        message = (
            'That email address already has %s ticket(s) for %s, and the '
            'organiser allows %s that day.' % (already, name, limit) if held else
            'The organiser allows %s ticket(s) per email address on %s.'
            % (limit, name))
    else:
        message = (
            'That email address already has %s ticket(s) for this event, and '
            'the organiser allows %s.' % (already, limit) if held else
            'The organiser allows %s ticket(s) per email address.' % limit)

    return _err(message, codes[(scope, held)], status.HTTP_409_CONFLICT,
                field='email',
                data={'scope': scope, 'already': already, 'limit': limit,
                      'name': name})


def _room_or_error(event, tier, quantity):
    remaining = availability.tier_available(tier)
    if remaining <= 0:
        return _err('%s is sold out.' % tier.name, 'SOLD_OUT',
                    status.HTTP_409_CONFLICT)
    if quantity > remaining:
        return _err('Only %s %s ticket(s) left.' % (remaining, tier.name),
                    'INSUFFICIENT_STOCK', status.HTTP_409_CONFLICT)

    # On the day this type admits, not across the whole engagement. A venue
    # that holds 400 holds 400 on each day of a two-day event. Guest checkout
    # and the signed-in one have to ask the identical question or a buyer is
    # refused by one and served by the other.
    room = availability.event_room(event, getattr(tier, 'day', None))
    if room is not None and room <= 0:
        return _err('This event is sold out.', 'EVENT_FULL',
                    status.HTTP_409_CONFLICT)
    if room is not None and quantity > room:
        return _err('Only %s ticket(s) left for this event.' % room,
                    'EVENT_FULL', status.HTTP_409_CONFLICT)
    return None


def _buyer(request):
    """The signed-in person, if there is one.

    The guest checkout is called by signed-out visitors and by signed-in ones -
    it is the only checkout an event with a card price has. It set `user=None`
    unconditionally, so a member who bought through it ended up with a ticket
    attached to nobody, and "My tickets" told them they had none. That is the
    exact report from the CEO on 31 August.
    """
    try:
        from vent_auth.views_community import _optional_user
        return _optional_user(request)
    except Exception:
        return None


def _issue(event, tier, quantity, email, answers, unit_ngn, unit_vc, reference='',
           referral=None, buyer=None):
    """Turn a paid-for or free order into tickets."""
    from .views_tickets import _new_code

    order_answers, per_person, attendees = answers
    tickets = []
    with transaction.atomic():
        for index in range(quantity):
            who = attendees[index] or {}
            mine = {**order_answers, **per_person[index]}
            tickets.append(Ticket.objects.create(
                event=event, tier=tier, user=buyer,
                code=_new_code(),
                price_vc=unit_vc, price_ngn=unit_ngn,
                attendee_name=str(who.get('name') or '').strip()[:120],
                attendee_email=str(who.get('email') or email).strip()[:254],
                attendee_phone=(str(who.get('phone') or '').strip()[:40]
                                or checkout.phone_from(event, mine)),
                answers=mine,
                payment_reference=reference,
            ))
        TicketTier.objects.filter(pk=tier.pk).update(sold=tier.sold + quantity)
        # Inside the same transaction as the issue, so an influencer link is
        # credited if and only if the tickets it is being credited for exist.
        from . import referrals as _refs
        _refs.attribute(tickets, referral)
    return tickets


def _ticket_row(ticket):
    return {
        'code': ticket.code,
        'attendee_name': ticket.attendee_name,
        'attendee_email': ticket.attendee_email,
        'tier': ticket.tier.name,
        'status': ticket.status,
    }


@api_view(['POST'])
@permission_classes([AllowAny])
def guest_buy(request, event_id):
    """POST /event/<id>/guest-buy/ - buy without an account.

    A free ticket is issued here and now. A paid one goes to Paystack, because a
    guest has no wallet to deduct from, and the ticket is issued when the
    payment is confirmed rather than when it is started.
    """
    event = _event(event_id)
    if event is None:
        return _err('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    now = timezone.now()
    if event.end_date and now > event.end_date:
        return _err('This event has ended.', 'STATE_CONFLICT',
                    status.HTTP_409_CONFLICT)
    started = bool(event.start_date and now >= event.start_date)
    if not started and event.reg_end_date and now > event.reg_end_date:
        return _err('Ticket sales have closed for this event.',
                    'STATE_CONFLICT', status.HTTP_409_CONFLICT)

    tier, quantity, email, answers, err = _validate_order(event, request)
    if err:
        return err

    err = _room_or_error(event, tier, quantity)
    if err:
        return err

    # Before the gateway, so nobody is sent to pay for a ticket that will be
    # refused on the way back.
    err = _email_limit_or_error(event, email, quantity, tier=tier)
    if err:
        return err

    unit_ngn = tier.price_for(quantity)
    total_ngn = unit_ngn * quantity

    from .views_tickets import _ngn_to_coins
    unit_vc = _ngn_to_coins(unit_ngn)

    # ------------------------------------------------------------------ free
    if total_ngn <= 0:
        with transaction.atomic():
            # Checked again inside the transaction that writes. The check above
            # catches somebody retyping their address; this catches two
            # requests arriving at once, which is what a double-tapped button
            # on a slow connection actually looks like.
            err = _email_limit_or_error(event, email, quantity, tier=tier)
            if err:
                return err
            tickets = _issue(event, tier, quantity, email, answers, unit_ngn, unit_vc,
                             buyer=_buyer(request),
                             referral=_referral_from(request, event))
        _send_them(tickets)
        return _ok({
            'tickets': [_ticket_row(t) for t in tickets],
            'paid': False,
            'email': email,
        }, 'Your ticket is on its way to %s.' % email,
            status.HTTP_201_CREATED)

    # ------------------------------------------------------------------ paid
    if not os.environ.get('PAYSTACK_SECRET_KEY'):
        return _err('Card payment is not set up for this platform yet, so only '
                    'free tickets can be bought without an account.',
                    'PAYMENT_UNAVAILABLE', status.HTTP_503_SERVICE_UNAVAILABLE)

    reference = 'vt-%s' % secrets.token_hex(10)
    # Everything needed to issue the tickets, held against the reference. Kept
    # in metadata rather than in a pending row because a payment nobody
    # completes should leave nothing behind to clean up.
    payload = {
        'email': email,
        'amount': int(total_ngn * 100),          # Paystack works in kobo
        'reference': reference,
        'callback_url': request.data.get('callback_url') or '',
        'metadata': {
            'event_id': event.event_id,
            'tier_id': tier.id,
            'quantity': quantity,
            'answers': answers[0],
            'attendees': answers[2],
            # The influencer link, carried through the card payment. Paystack
            # hands the metadata back at verification, which is a different
            # request minutes later on a different device as often as not, and
            # the link is the whole reason that sale exists.
            'ref': (_referral_from(request, event).code
                    if _referral_from(request, event) else ''),
        },
    }

    try:
        response = http_requests.post(
            '%s/transaction/initialize' % PAYSTACK_BASE,
            json=payload, headers=_paystack_headers(), timeout=10)
        response.raise_for_status()
        body = response.json()
    except http_requests.RequestException as exc:
        return _err('The payment gateway could not be reached: %s' % exc,
                    'PAYMENT_GATEWAY', status.HTTP_502_BAD_GATEWAY)

    if not body.get('status'):
        return _err(body.get('message') or 'The payment could not be started.',
                    'PAYMENT_GATEWAY', status.HTTP_502_BAD_GATEWAY)

    return _ok({
        'authorization_url': body['data']['authorization_url'],
        'reference': reference,
        'paid': True,
        'amount_ngn': float(total_ngn),
        'email': email,
    }, 'Continue to payment.')


@api_view(['POST'])
@permission_classes([AllowAny])
def guest_verify(request):
    """POST /event/guest-verify/ - confirm a payment and issue the tickets.

    The tickets are created HERE, not when the payment was started. A payment
    that is abandoned halfway leaves nothing behind, and a ticket that exists
    before the money does is a ticket somebody can screenshot.

    Idempotent: the browser returning and Paystack calling back are two arrivals
    for one payment, and issuing twice would put two people through one door.
    """
    reference = str(request.data.get('reference') or '').strip()
    if not reference:
        return _err('Send the payment reference.', 'VALIDATION_ERROR',
                    field='reference')

    existing = list(Ticket.objects.filter(payment_reference=reference))
    if existing:
        return _ok({'tickets': [_ticket_row(t) for t in existing],
                    'already_issued': True},
                   'Your tickets are ready.')

    try:
        response = http_requests.get(
            '%s/transaction/verify/%s' % (PAYSTACK_BASE, reference),
            headers=_paystack_headers(), timeout=10)
        response.raise_for_status()
        body = response.json()
    except http_requests.RequestException as exc:
        return _err('The payment gateway could not be reached: %s' % exc,
                    'PAYMENT_GATEWAY', status.HTTP_502_BAD_GATEWAY)

    data = (body or {}).get('data') or {}
    if not body.get('status') or data.get('status') != 'success':
        return _err('That payment has not gone through.', 'PAYMENT_NOT_COMPLETE',
                    status.HTTP_402_PAYMENT_REQUIRED)

    meta = data.get('metadata') or {}
    event = Event.objects.filter(pk=meta.get('event_id')).first()
    tier = TicketTier.objects.filter(pk=meta.get('tier_id'),
                                     event=event).first() if event else None
    if event is None or tier is None:
        return _err('That payment does not match an event we can find.',
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    quantity = int(meta.get('quantity') or 1)
    email = str(data.get('customer', {}).get('email') or meta.get('email') or '')
    attendees = meta.get('attendees') or [{} for _ in range(quantity)]
    while len(attendees) < quantity:
        attendees.append({})

    per_person = []
    for index in range(quantity):
        try:
            per_person.append(checkout.collect(
                event, (attendees[index] or {}).get('answers'),
                per_ticket_index=index))
        except checkout.CheckoutError:
            # The money is taken. Refusing the ticket now over a field would
            # leave somebody paid-up and empty-handed, so what was given is
            # kept and the organiser can chase the rest.
            per_person.append({})

    unit_ngn = tier.price_for(quantity)
    from .views_tickets import _ngn_to_coins
    tickets = _issue(event, tier, quantity, email,
                     (meta.get('answers') or {}, per_person, attendees),
                     unit_ngn, _ngn_to_coins(unit_ngn), reference=reference,
                     referral=_refs_resolve(event, meta.get('ref')),
                     buyer=_buyer(request))
    _send_them(tickets)

    return _ok({'tickets': [_ticket_row(t) for t in tickets],
                'already_issued': False},
               'Paid. Your tickets are on their way to %s.' % email,
               status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def guest_lookup(request):
    """POST /event/guest-lookup/ - find a ticket again without an account.

    Email AND code, both. The code alone is the credential on the ticket, and
    email alone would let anybody type an address and read somebody's booking -
    which is the same shape of leak as an enumerable id.
    """
    email = str(request.data.get('email') or '').strip().lower()
    code = str(request.data.get('code') or '').strip().upper()
    if not email or not code:
        return _err('Both the email address and the ticket code are needed.',
                    'VALIDATION_ERROR')

    ticket = Ticket.objects.select_related('event', 'tier').filter(
        code=code, attendee_email__iexact=email).first()
    if ticket is None:
        # One message for "no such code" and "wrong email", deliberately.
        # Separate messages would say which half was right.
        return _err('No ticket found for that code and email address.',
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    from .views_tickets import serialize_ticket
    return _ok({'ticket': serialize_ticket(ticket)}, 'Your ticket')


def _send_them(tickets):
    """Email each ticket. A failure here must never undo a paid purchase."""
    try:
        from vent_auth import emails
        for ticket in tickets:
            emails.send_ticket_purchased(ticket)
    except Exception:
        pass


def claim_for(user):
    """Attach any guest tickets bought with this account's email address.

    Called when somebody signs up or verifies their email. Buying as a guest and
    then making an account should not leave the tickets stranded in an inbox,
    and asking somebody to forward themselves a code is not a flow.
    """
    if not user or not user.email:
        return 0
    return Ticket.objects.filter(
        user__isnull=True, attendee_email__iexact=user.email
    ).update(user=user)

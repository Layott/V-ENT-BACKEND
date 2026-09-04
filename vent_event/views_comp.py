"""Tickets an organiser hands out, by typing an email address.

CEO, 29 August 2026: "event creators should be able to send tickets to people
also, by just inputting their emails".

Every event has a list: the sponsor's people, the press, the two friends of the
venue manager, the artist's plus one. Until now the only way to get one of those
people through the door was to sell them a ticket for nothing, which meant they
had to make an account and check out.

The decisions:

**It is a real ticket.** Same model, same code, same scanner, same door list.
Not a name on a separate list the steward has to cross-reference at the gate,
which is exactly where a guest list goes wrong.

**Stock is decremented like any other sale.** A comped ticket occupies a seat.
An organiser who gives away thirty and then sells to capacity has oversold the
room, and the room is the thing that is finite.

**Sending twice to the same address does not issue twice.** Organisers paste a
list, notice a typo, fix it and paste again. Issuing a second ticket for every
address in the list is the obvious failure and the expensive one, because each
extra ticket is a seat.

**Every address is checked before any ticket is issued.** A list of forty with
one typo issues nothing and names the bad one, rather than issuing thirty-nine
and leaving the organiser to work out which.
"""

from django.db import transaction
from django.db.models import F
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from .models import Event, EventManager, Ticket, TicketTier

#: One request should not be able to empty a room by accident.
MAX_PER_REQUEST = 100


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message})


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _event(event_id):
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


def _may_manage(user, event):
    # This one admitted anybody named on the event, door staff included, which
    # was wider than every other screen. One rule, in permissions.py: running
    # the competition is running the event.
    from .permissions import may_run_event
    return may_run_event(user, event)


def _looks_like_an_address(value):
    """Enough of a check to catch a typo, and no more.

    A stricter pattern rejects addresses that work. The real check is whether
    the mail arrives, and that is reported per address afterwards.
    """
    value = str(value or '').strip()
    if '@' not in value or ' ' in value:
        return False
    local, _, domain = value.rpartition('@')
    return bool(local) and '.' in domain and not domain.startswith('.') \
        and not domain.endswith('.')


def _new_code():
    from .views_tickets import _new_code as generate
    return generate()


@api_view(['POST'])
def comp_tickets(request, event_id):
    """Issue tickets to a list of email addresses."""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _error('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(user, event):
        return _error('Only the event organizer can send tickets.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    tier = TicketTier.objects.filter(
        event=event, pk=request.data.get('tier_id')).first()
    if tier is None:
        return _error('Pick which type of ticket to send.', 'VALIDATION_ERROR')

    # Accept a list, or the block of text somebody pasted out of a spreadsheet.
    raw = request.data.get('emails')
    if isinstance(raw, str):
        raw = [part for chunk in raw.replace(';', ',').replace('\n', ',').split(',')
               for part in [chunk.strip()] if part]
    if not isinstance(raw, list) or not raw:
        return _error('Add at least one email address.', 'VALIDATION_ERROR')

    # Every address checked before any ticket exists. Issuing most of a list and
    # failing on one leaves the organiser to work out which.
    addresses = []
    for value in raw:
        address = str(value or '').strip().lower()
        if not address:
            continue
        if not _looks_like_an_address(address):
            return _error('%s does not look like an email address.' % address,
                          'BAD_EMAIL', extra={'email': address})
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        return _error('Add at least one email address.', 'VALIDATION_ERROR')
    if len(addresses) > MAX_PER_REQUEST:
        return _error('Send to %d addresses at a time.' % MAX_PER_REQUEST,
                      'TOO_MANY')

    note = str(request.data.get('note') or '').strip()[:280]

    issued = []
    skipped = []

    with transaction.atomic():
        locked = TicketTier.objects.select_for_update().get(pk=tier.pk)

        # Somebody who already holds a ticket of this type is not sent another.
        # Organisers paste a list twice; a seat is not something to hand out
        # again because a request arrived again.
        already = set(
            Ticket.objects.filter(event=event, tier=locked,
                                  status__in=['valid', 'checked_in'])
            .values_list('attendee_email', flat=True))
        already = {a.lower() for a in already if a}

        wanted = [a for a in addresses if a not in already]
        skipped = [a for a in addresses if a in already]

        left = max(locked.quantity - locked.sold, 0)
        if len(wanted) > left:
            return _error(
                'There are %d of that type left, and you asked for %d.'
                % (left, len(wanted)), 'NOT_ENOUGH_LEFT',
                extra={'remaining': left, 'wanted': len(wanted)})

        for address in wanted:
            # Attached to an account where one already exists, so it appears in
            # My Tickets rather than only in an inbox.
            holder = Users.objects.filter(email__iexact=address).first()
            ticket = Ticket.objects.create(
                event=event, tier=locked, user=holder, code=_new_code(),
                price_vc=0, price_ngn=0,
                attendee_name=(holder.full_name or holder.username) if holder else '',
                attendee_email=address,
                answers={'comped_by': user.username, 'note': note} if note
                else {'comped_by': user.username},
            )
            issued.append(ticket)

        if issued:
            TicketTier.objects.filter(pk=locked.pk).update(
                sold=F('sold') + len(issued))

    # After the transaction: a mail server being slow must not hold a lock, and
    # a mail that fails must not undo a ticket that exists.
    delivered = []
    failed = []
    for ticket in issued:
        try:
            from vent_auth import emails
            emails.send_ticket_purchased(ticket)
            delivered.append(ticket.attendee_email)
        except Exception:                                   # noqa: BLE001
            failed.append(ticket.attendee_email)

    for ticket in issued:
        if ticket.user_id is None:
            continue
        try:
            from vent_auth.views_notifications import create_notification
            create_notification(
                user=ticket.user, category='event',
                title='You have a ticket to %s' % event.name,
                body=note or 'The organiser sent you one.',
                link='/events/my-tickets',
                metadata={'ticket_code': ticket.code},
            )
        except Exception:                                   # noqa: BLE001
            pass

    return _ok({
        'issued': [{'email': t.attendee_email, 'code': t.code} for t in issued],
        'issued_count': len(issued),
        # Named rather than counted: an organiser who pasted a list twice wants
        # to know which addresses already had one.
        'skipped_already_had_one': skipped,
        'emailed': delivered,
        # A ticket that exists but whose mail bounced is the case an organiser
        # has to know about, because the person is expecting it.
        'not_emailed': failed,
    }, 'Tickets sent.')

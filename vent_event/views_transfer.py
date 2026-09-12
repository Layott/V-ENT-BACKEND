"""Giving a ticket to somebody else.

CEO, 7 September 2026, from the ticketing research: give a ticket to somebody
else, the code reissues, the door sees the new holder.

## The three things that make this different from editing a name

1. **The code reissues.** A ticket that keeps its code after being given away
   means the old holder still has something that opens the gate. Two people,
   one seat, and the one turned away has done nothing wrong. The new code is
   generated and the old one stops working in the same transaction.

2. **The old code still ANSWERS.** Dead is not the same as unknown. Somebody
   arriving with a screenshot of the old code gets "this was transferred to
   Ada on Tuesday", not "no such ticket", because the second sentence makes a
   steward think the person is lying.

3. **A checked-in ticket cannot be transferred.** The seat is already occupied.
   Moving it afterwards would mean the attendance figures name somebody who was
   not there, which is the number the organiser makes next year's decisions on.

## Who may do it

The holder, and the organiser. The organiser is not a courtesy: somebody who
bought four tickets and lost their phone cannot reach the transfer screen, and
the organiser is who they will ask at the door. Both routes write the same row,
so the trail does not depend on who did it.

Nothing here moves money. A transfer is a gift; reselling is a different
feature with a different set of decisions in it, and conflating the two is how
a platform ends up brokering a resale market it never agreed to run.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from .models import Event, Ticket, TicketTransfer


def _error(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _may_transfer(user, ticket):
    """The holder, or the organiser. Both write the same row."""
    if user is None:
        return False
    if ticket.user_id and ticket.user_id == user.user_id:
        return True
    if ticket.attendee_email and user.email \
            and ticket.attendee_email.lower() == user.email.lower():
        return True
    from .permissions import may_run_event
    return may_run_event(user, ticket.event)


@api_view(['POST'])
def transfer_ticket(request, code):
    """`{to: 'email or @username', name, note}` -> the ticket, with a new code."""
    viewer = _viewer(request)
    if viewer is None:
        return _error('Sign in to transfer a ticket.', 'UNAUTHORIZED',
                      status.HTTP_401_UNAUTHORIZED)

    with transaction.atomic():
        ticket = (Ticket.objects.select_for_update()
                  .select_related('event', 'tier', 'user')
                  .filter(code=str(code).strip().upper()).first())
        if ticket is None:
            return _error('No ticket with that code.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        if not _may_transfer(viewer, ticket):
            return _error('Only the ticket holder or the organiser can transfer '
                          'this ticket.', 'NOT_HOLDER',
                          status.HTTP_403_FORBIDDEN)

        if ticket.status == 'checked_in' or ticket.checked_in_at:
            return _error('This ticket has already been used to get in, so it '
                          'cannot be given to anybody else.', 'ALREADY_USED',
                          status.HTTP_409_CONFLICT)
        if ticket.status in ('cancelled', 'refunded'):
            return _error('This ticket is no longer valid.', 'NOT_VALID',
                          status.HTTP_409_CONFLICT)

        # A ticket cannot be given away after the event is over. Nothing breaks
        # if it is, but the row would say somebody holds a ticket to a thing
        # that has happened, which is not true of anybody.
        from django.utils import timezone
        if ticket.event.end_date and timezone.now() > ticket.event.end_date:
            return _error('This event has already happened.', 'STATE_CONFLICT',
                          status.HTTP_409_CONFLICT)

        # Who it is going to. The same resolver the invitations use, so an
        # email address that belongs to nobody is a person to be reached
        # rather than an error.
        from vent_auth.invites import invitee_for
        to_user, to_email, problem = invitee_for(request.data.get('to'))
        if problem:
            return _error(problem, 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if not to_email:
            return _error('That account has no email address on it, so the '
                          'ticket cannot be sent anywhere.', 'NO_EMAIL')

        if to_user is not None and to_user.user_id == ticket.user_id:
            return _error('That is already the person holding this ticket.',
                          'NO_CHANGE', status.HTTP_409_CONFLICT)

        # How many one address may hold, checked here as at any other door in.
        # A transfer that walks past the organiser's own limit is a way to buy
        # eight tickets on a four-ticket limit using two accounts.
        from . import checkout
        ok, _refusal = checkout.room_for_email(ticket.event, to_email, 1,
                                               tier=ticket.tier)
        if not ok:
            from .views_guest import _email_limit_or_error
            return _email_limit_or_error(ticket.event, to_email, 1,
                                         tier=ticket.tier)

        from .views_tickets import _new_code
        old_code = ticket.code
        new_code = _new_code()

        record = TicketTransfer.objects.create(
            ticket=ticket,
            from_user=ticket.user, to_user=to_user,
            from_email=ticket.attendee_email or '',
            to_email=to_email,
            from_name=ticket.attendee_name or '',
            to_name=(str(request.data.get('name') or '').strip()
                     or (to_user.full_name if to_user else '')
                     or (to_user.username if to_user else ''))[:120],
            old_code=old_code, new_code=new_code,
            note=str(request.data.get('note') or '')[:200],
        )

        ticket.code = new_code
        ticket.user = to_user
        ticket.attendee_email = to_email
        ticket.attendee_name = record.to_name or ticket.attendee_name
        # The new holder's own phone is not known. Clearing it beats keeping
        # the previous holder's, which would put one person's number against
        # another person's name on the door list.
        ticket.attendee_phone = ''
        ticket.save(update_fields=['code', 'user', 'attendee_email',
                                   'attendee_name', 'attendee_phone'])

    # Fire and forget: a failed email must never undo a completed transfer.
    try:
        from vent_auth import emails
        emails.send_ticket_purchased(ticket)
    except Exception:
        pass

    from .views_tickets import serialize_ticket
    return _ok({
        'ticket': serialize_ticket(ticket),
        'old_code': record.old_code,
        'new_code': record.new_code,
        'to': record.to_email,
    }, 'The ticket is now theirs, and it has a new code.')


@api_view(['GET'])
def transfer_history(request, code):
    """Where a code went, including one that no longer opens anything.

    Dead is not the same as unknown. A steward handed a screenshot of an old
    code needs "transferred to Ada on Tuesday", because "no such ticket" makes
    them think the person in front of them is lying.

    Door staff and the organiser. Not public: the row names two people and
    their email addresses.
    """
    viewer = _viewer(request)
    if viewer is None:
        return _error('Sign in to look this up.', 'UNAUTHORIZED',
                      status.HTTP_401_UNAUTHORIZED)

    asked = str(code).strip().upper()
    ticket = Ticket.objects.select_related('event').filter(code=asked).first()
    if ticket is None:
        record = TicketTransfer.objects.select_related(
            'ticket', 'ticket__event').filter(old_code=asked).first()
        if record is None:
            return _error('No ticket with that code.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        ticket = record.ticket

    from .permissions import may_work_the_door
    if not may_work_the_door(viewer, ticket.event) \
            and not _may_transfer(viewer, ticket):
        return _error('Only the organiser or their door staff can look this '
                      'up.', 'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    rows = [{
        'at': t.at.isoformat(),
        'from_name': t.from_name, 'from_email': t.from_email,
        'to_name': t.to_name, 'to_email': t.to_email,
        'old_code': t.old_code, 'new_code': t.new_code,
        'note': t.note,
    } for t in ticket.transfers.all()]

    return _ok({
        'code_asked': asked,
        'current_code': ticket.code,
        # The one thing a steward at a gate actually needs from this.
        'still_valid': asked == ticket.code,
        'holder_name': ticket.attendee_name,
        'transfers': rows,
    })

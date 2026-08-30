"""The console's view of an event: its real numbers, its tickets, and its post.

CEO, 30 August 2026: "For admin section we should be able to fully manage events
also and tickets and sese the full details about what was sent out by tournament
organizers and event managers also."

Three things it could not do before. It could list events and edit their fields,
and that was all: no way to see an event's actual numbers, no way to touch a
single ticket, and no way at all to see what an organiser had sent to the people
holding them.

The last one is the reason this exists. An organiser can email every ticket
holder, hand out free tickets, invite vendors, schedule reminders and invite
teams, and until now none of it was visible from the console. When somebody
writes in about a message they received, or a ticket they were given, or a
reminder that went out at the wrong time, support had nothing to look at.

Two rules run through all of it:

**Nothing here rewrites history.** Voiding a ticket sets its status and returns
the seat; it does not delete the row, because the person holding it turned up
and the door needs to know why they were turned away. An announcement is never
editable, because the recipients already have the old text in their inbox.

**Every action names an admin and a reason.** `AdminAction` rows are what
answers "who cancelled this event and why" six weeks later, and a console that
can act without leaving a trace is worse than one that cannot act at all.
"""
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import ADMIN_ROLES, admin_role_required
from .models import AdminAction

# ---------------------------------------------------------------------------
# envelope and guards
# ---------------------------------------------------------------------------


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


# `@admin_role_required` is how the whole console is gated: it resolves the
# bearer token, checks the session went through the second factor, checks the
# sub-role, and hands the view `request.admin_user`. Reused rather than written
# again here, because a permission check that exists in three places is a
# permission check that is only right in two.
#
# Reading is open to every admin role. Acting on an event or a ticket is not:
# those are the two that take money and seats away from people, so they are
# limited to the roles that answer for it.
READ_ROLES = ADMIN_ROLES
ACT_ROLES = ('super_admin', 'support_admin')


def _event(ref):
    """An event by slug or id. Slugs everywhere, ids for anything older."""
    from vent_event.models import Event

    event = Event.objects.filter(slug=str(ref)).first()
    if event is None and str(ref).isdigit():
        event = Event.objects.filter(event_id=int(ref)).first()
    return event


def _person(user):
    if user is None:
        return None
    return {'user_id': user.user_id, 'username': user.username,
            'full_name': user.full_name or user.username,
            'email': user.email}


def _record(admin, action, target_id, reason='', **metadata):
    AdminAction.objects.create(
        admin=admin, action_type=action, target_model='Event',
        target_id=str(target_id), reason=reason or '', metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# X1: one event, with its real numbers
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_event_detail(request, event_ref):
    """Everything the console needs about one event.

    The numbers are counted from ticket rows, never from a stored counter. A
    counter drifts the first time a refund, a double-issue or a failed payment
    happens, and then nobody can tell which of the two numbers is the true one.
    """
    admin = request.admin_user

    from vent_event.models import Event, EventManager, Ticket, TicketTier

    event = _event(event_ref)
    if event is None:
        return _err('No event with that address.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    tickets = Ticket.objects.filter(event=event)
    counts = tickets.aggregate(
        total=Count('id'),
        valid=Count('id', filter=Q(status='valid')),
        checked_in=Count('id', filter=Q(status='checked_in')),
        refunded=Count('id', filter=Q(status='refunded')),
        cancelled=Count('id', filter=Q(status='cancelled')),
        # Revenue counts only what is still a ticket. Money taken on a ticket
        # that was refunded is not revenue, and showing it as such is the number
        # an organiser would quote back at us.
        revenue_vc=Sum('price_vc', filter=Q(status__in=['valid', 'checked_in'])),
        revenue_ngn=Sum('price_ngn', filter=Q(status__in=['valid', 'checked_in'])),
    )

    # A comped ticket is one an organiser handed out: price zero, and the
    # `comped_by` the comp endpoint writes into `answers`. Counted separately
    # because "sold 200" and "sold 140 and gave away 60" are different rooms.
    comped = tickets.filter(price_vc=0, answers__has_key='comped_by').count()

    tiers = [
        {
            'id': t.id, 'name': t.name, 'price_ngn': str(t.price),
            # `quantity` is the tier's own allowance, and null means it is only
            # bounded by the room.
            'quantity': t.quantity, 'sold': t.sold,
            'remaining': None if t.quantity is None else max(0, t.quantity - t.sold),
        }
        for t in TicketTier.objects.filter(event=event).order_by('id')
    ]

    managers = [
        {'role': m.role, 'added_at': m.created_at,
         'user': _person(m.user), 'added_by': _person(m.added_by)}
        for m in EventManager.objects.select_related('user', 'added_by').filter(event=event)
    ]

    return _ok({
        'event': {
            'id': event.event_id,
            'slug': event.slug,
            'name': event.name,
            'is_active': event.is_active,
            'organizer': _person(event.creator),
            'location': event.location,
            'capacity': event.capacity,
            'start_date': getattr(event, 'start_date', None),
            'end_date': getattr(event, 'end_date', None),
            'created_at': event.created_at,
        },
        'numbers': {
            'tickets': counts['total'] or 0,
            'valid': counts['valid'] or 0,
            'checked_in': counts['checked_in'] or 0,
            'refunded': counts['refunded'] or 0,
            'cancelled': counts['cancelled'] or 0,
            'comped': comped,
            'capacity': event.capacity,
            'revenue_vc': counts['revenue_vc'] or 0,
            'revenue_ngn': str(counts['revenue_ngn'] or 0),
        },
        'tiers': tiers,
        'managers': managers,
    }, 'Event detail.')


# ---------------------------------------------------------------------------
# X2: acting on the event itself
# ---------------------------------------------------------------------------

STATE_ACTIONS = {
    # action -> (is_active after, what it is called in the audit log)
    'cancel': (False, 'cancel_event'),
    'restore': (True, 'restore_event'),
}


@api_view(['POST'])
@admin_role_required(ACT_ROLES)
def admin_event_state(request, event_ref):
    """Cancel an event, or put it back.

    A cancelled event stops selling and stops being listed. It is not deleted:
    people hold tickets to it, and the page has to keep answering so they find
    out what happened rather than a 404.

    A reason is required on a cancel. "Why is this event cancelled" is the first
    question support gets, and the answer has to be in the row rather than in
    somebody's memory of a Slack thread.
    """
    admin = request.admin_user

    event = _event(event_ref)
    if event is None:
        return _err('No event with that address.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    action = (request.data.get('action') or '').strip()
    if action not in STATE_ACTIONS:
        return _err('That is not something you can do to an event.',
                    'BAD_ACTION', status.HTTP_400_BAD_REQUEST)

    reason = (request.data.get('reason') or '').strip()
    if action == 'cancel' and not reason:
        return _err('Say why it is being cancelled.', 'REASON_REQUIRED',
                    status.HTTP_400_BAD_REQUEST)

    active, log_name = STATE_ACTIONS[action]
    if event.is_active == active:
        return _err('It is already like that.', 'NO_CHANGE', status.HTTP_409_CONFLICT)

    event.is_active = active
    event.save(update_fields=['is_active'])
    _record(admin, log_name, event.event_id, reason,
            event_name=event.name, slug=event.slug)

    return _ok({'event': {'id': event.event_id, 'slug': event.slug,
                          'is_active': event.is_active}},
               'Event updated.')


# ---------------------------------------------------------------------------
# X3: the tickets on an event
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_event_tickets(request, event_ref):
    """Every ticket on an event, searchable, with who holds it.

    Searchable by code, name and email together, because whoever is looking has
    exactly one of those three and does not know which field it lives in.
    """
    admin = request.admin_user

    from vent_event.models import Ticket

    event = _event(event_ref)
    if event is None:
        return _err('No event with that address.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    qs = (Ticket.objects
          .filter(event=event)
          .select_related('tier', 'user', 'checked_in_by'))

    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(code__icontains=search)
            | Q(attendee_name__icontains=search)
            | Q(attendee_email__icontains=search)
            | Q(user__username__icontains=search)
        )

    wanted = (request.GET.get('status') or '').strip()
    if wanted == 'comped':
        qs = qs.filter(price_vc=0, answers__has_key='comped_by')
    elif wanted:
        qs = qs.filter(status=wanted)

    try:
        page = max(1, int(request.GET.get('page', 1)))
        page_size = min(100, max(1, int(request.GET.get('page_size', 25))))
    except (TypeError, ValueError):
        page, page_size = 1, 25
    offset = (page - 1) * page_size

    total = qs.count()
    rows = qs.order_by('-purchased_at')[offset:offset + page_size]

    return _ok({
        'results': [_ticket_row(t) for t in rows],
        'count': total, 'page': page, 'page_size': page_size,
    }, 'Tickets.')


def _ticket_row(ticket):
    comped_by = (ticket.answers or {}).get('comped_by') if isinstance(ticket.answers, dict) else None
    return {
        'id': ticket.id,
        'code': ticket.code,
        'status': ticket.status,
        'tier': ticket.tier.name if ticket.tier else None,
        'price_vc': ticket.price_vc,
        'price_ngn': str(ticket.price_ngn),
        # A guest ticket has no account behind it, so the attendee columns are
        # the only name there is. Falling back to the account's name when there
        # is one keeps the column filled for both kinds.
        'attendee_name': ticket.attendee_name or (ticket.user.full_name if ticket.user else ''),
        'attendee_email': ticket.attendee_email or (ticket.user.email if ticket.user else ''),
        'holder': _person(ticket.user),
        'is_guest': ticket.user_id is None,
        'comped_by': comped_by,
        'purchased_at': ticket.purchased_at,
        'checked_in_at': ticket.checked_in_at,
        'checked_in_gate': ticket.checked_in_gate,
        'checked_in_by': _person(ticket.checked_in_by),
    }


@api_view(['POST'])
@admin_role_required(ACT_ROLES)
def admin_ticket_action(request, code):
    """Void a ticket, or put it back.

    Voiding returns the seat to its tier. A tier's `sold` is what the next buyer
    is checked against, so a void that left it alone would shrink the room by
    one every time somebody was refused entry.

    A voided ticket keeps its row and its code. Somebody turned away at the door
    holding it needs the scanner to say why, and a deleted row says nothing.
    """
    admin = request.admin_user

    from vent_event.models import Ticket, TicketTier

    action = (request.data.get('action') or '').strip()
    if action not in ('void', 'reinstate'):
        return _err('That is not something you can do to a ticket.',
                    'BAD_ACTION', status.HTTP_400_BAD_REQUEST)

    reason = (request.data.get('reason') or '').strip()
    if action == 'void' and not reason:
        return _err('Say why it is being voided.', 'REASON_REQUIRED',
                    status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        ticket = (Ticket.objects
                  .select_for_update()
                  .select_related('event', 'tier')
                  .filter(code=str(code)).first())
        if ticket is None:
            return _err('No ticket with that code.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

        if action == 'void':
            if ticket.status == 'cancelled':
                return _err('That ticket is already void.', 'NO_CHANGE',
                            status.HTTP_409_CONFLICT)
            was = ticket.status
            ticket.status = 'cancelled'
            ticket.save(update_fields=['status'])
            if ticket.tier_id:
                TicketTier.objects.filter(pk=ticket.tier_id, sold__gt=0).update(sold=F('sold') - 1)
        else:
            if ticket.status != 'cancelled':
                return _err('That ticket is not void.', 'NO_CHANGE',
                            status.HTTP_409_CONFLICT)
            # Reinstating has to check the room again. An event that sold out
            # while the ticket was void has no seat to give back, and issuing
            # one anyway is how a venue ends up over capacity.
            tier = ticket.tier
            if tier and tier.quantity is not None and tier.sold >= tier.quantity:
                return _err('That tier is full, so this cannot be reinstated.',
                            'TIER_FULL', status.HTTP_409_CONFLICT)
            was = ticket.status
            ticket.status = 'checked_in' if ticket.checked_in_at else 'valid'
            ticket.save(update_fields=['status'])
            if tier:
                TicketTier.objects.filter(pk=tier.pk).update(sold=F('sold') + 1)

    AdminAction.objects.create(
        admin=admin, action_type='void_ticket' if action == 'void' else 'reinstate_ticket',
        target_model='Ticket', target_id=ticket.code, reason=reason,
        metadata={'event': ticket.event.name, 'event_slug': ticket.event.slug,
                  'was': was, 'now': ticket.status},
    )

    return _ok({'ticket': _ticket_row(ticket)}, 'Ticket updated.')


# ---------------------------------------------------------------------------
# X4: what the organiser actually sent
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_event_sent(request, event_ref):
    """Everything an organiser sent out about this event.

    Announcements with their full text, free tickets with who gave them to whom,
    and vendor invitations. All of it read-only: this is a record of what
    happened, and a console that could edit it would be editing the answer to
    the question it exists to answer.
    """
    admin = request.admin_user

    from vent_event.models import EventAnnouncement, Ticket, VendorInvite

    event = _event(event_ref)
    if event is None:
        return _err('No event with that address.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    announcements = [
        {
            'id': a.id,
            'subject': a.subject,
            # The whole body. Somebody asking "what did they send my customer"
            # needs the message, not a preview of it.
            'body': a.body,
            'audience': a.audience,
            'recipients': a.recipients,
            'notified_in_app': a.notified_in_app,
            'sent_at': a.sent_at,
            'sent_by': _person(a.sent_by),
            # A send that half worked says so. This is the field that explains a
            # complaint about a message somebody never received.
            'email_error': a.email_error,
        }
        for a in EventAnnouncement.objects.select_related('sent_by').filter(event=event)
    ]

    comps = [
        {
            'code': t.code,
            'status': t.status,
            'tier': t.tier.name if t.tier else None,
            'to_name': t.attendee_name,
            'to_email': t.attendee_email,
            'given_by': (t.answers or {}).get('comped_by'),
            'note': (t.answers or {}).get('note', ''),
            'issued_at': t.purchased_at,
        }
        for t in (Ticket.objects
                  .select_related('tier')
                  .filter(event=event, price_vc=0, answers__has_key='comped_by')
                  .order_by('-purchased_at'))
    ]

    invites = [
        {'id': v.id, 'name': v.name, 'email': v.email, 'booth': v.booth}
        for v in VendorInvite.objects.filter(event=event)
    ]

    return _ok({
        'announcements': announcements,
        'comped_tickets': comps,
        'vendor_invites': invites,
        'totals': {
            'announcements': len(announcements),
            'announced_to': sum(a['recipients'] for a in announcements),
            'comped_tickets': len(comps),
            'vendor_invites': len(invites),
        },
    }, 'What was sent.')


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_tournament_sent(request, tournament_ref):
    """The same question, for a tournament.

    CEO: "what was sent out by tournament organizers AND event managers". A
    tournament organiser sends different things - scheduled reminders, addressed
    invitations, and codes handed out - so this reads those rather than
    pretending they are announcements.
    """
    admin = request.admin_user

    from vent_tournament.models import (ScheduledReminder, Tournament,
                                        TournamentInvitation, TournamentInvite)

    tournament = Tournament.objects.filter(slug=str(tournament_ref)).first()
    if tournament is None and str(tournament_ref).isdigit():
        tournament = Tournament.objects.filter(tournament_id=int(tournament_ref)).first()
    if tournament is None:
        return _err('No tournament with that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    reminders = [
        {
            'id': r.id,
            'kind': r.kind,
            'subject': r.subject,
            'body': r.body,
            # The time is stored as an anchor plus an offset, because "an hour
            # before check-in" is what an organiser means and it survives them
            # moving the tournament. Both halves are shown rather than a
            # computed timestamp, so the console says the same thing the
            # organiser set.
            'anchor': r.anchor,
            'offset_minutes': r.offset_minutes,
            'fixed_at': r.fixed_at,
            'sent_at': r.sent_at,
            'people_reached': r.people_reached,
            'skipped_reason': r.skipped_reason,
            'created_by': _person(r.created_by),
            # Four different things, and a complaint about each is a different
            # complaint: not sent yet, sent, called off, or skipped because the
            # send decided there was nobody to send to.
            'state': ('cancelled' if r.cancelled_at else
                      'skipped' if r.skipped_reason else
                      'sent' if r.sent_at else 'scheduled'),
        }
        for r in ScheduledReminder.objects.select_related('created_by')
                                          .filter(tournament=tournament)
    ]

    invitations = [
        {
            'id': i.id,
            'status': i.status,
            'to_user': _person(i.user),
            'to_team': i.team.team_name if i.team_id else None,
            'message': i.message,
            'invited_by': _person(i.invited_by),
            'created_at': i.created_at,
            'answered_at': i.answered_at,
        }
        for i in (TournamentInvitation.objects
                  .select_related('user', 'team', 'invited_by')
                  .filter(tournament=tournament))
    ]

    codes = [
        {'id': c.id, 'code': c.code, 'label': c.label,
         'used_count': c.used_count, 'max_uses': c.max_uses,
         'created_by': _person(c.created_by), 'created_at': c.created_at}
        for c in TournamentInvite.objects.select_related('created_by')
                                         .filter(tournament=tournament)
    ]

    return _ok({
        'reminders': reminders,
        'invitations': invitations,
        'codes': codes,
        'totals': {
            'reminders': len(reminders),
            'reminders_sent': sum(1 for r in reminders if r['state'] == 'sent'),
            'invitations': len(invitations),
            'codes': len(codes),
        },
    }, 'What was sent.')

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
import logging

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import ROLE_PERMISSIONS, admin_role_required
from .models import AdminAction

logger = logging.getLogger(__name__)

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
READ_ROLES = ROLE_PERMISSIONS['manage_events']
CANCEL_ROLES = ROLE_PERMISSIONS['cancel_event']
VOID_ROLES = ROLE_PERMISSIONS['void_ticket']


def _event(ref):
    """An event by slug or id. Slugs everywhere, ids for anything older."""
    from vent_event.refs import event_by_ref

    # The event app's own resolver, which reads the slug history: the
    # console opened from a link in last month's report still lands.
    return event_by_ref(ref)


def _person(user):
    if user is None:
        return None
    # Through the one person builder, so an admin screen shows the same face
    # and the same founder mark as every other screen. The email is added on
    # top because the console legitimately shows it and no other screen does.
    from .views_community import _person

    row = _person(None, user)
    row['full_name'] = user.full_name or user.username
    row['email'] = user.email
    return row


def _refunds_owed(event):
    from vent_event import refunds
    return refunds.still_owed(event)


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
         'user': _person(m.user), 'added_by': _person(m.added_by), 'through': None}
        for m in EventManager.objects.select_related('user', 'added_by').filter(event=event)
    ]
    # The organisation's own people run it too (permissions.py, 4 September
    # 2026): its owner, its admins and its events managers reach every door
    # with no per-event row. The console said "the organiser runs this one
    # alone" on an event with three of them (18 September).
    if event.organization_id:
        from .models import OrgMember
        org = event.organization
        seen = {m['user']['user_id'] for m in managers if m.get('user')}
        if org.org_owner_id and org.org_owner_id != event.creator_id and org.org_owner_id not in seen:
            managers.append({'role': 'org_owner', 'added_at': None,
                             'user': _person(org.org_owner), 'added_by': None,
                             'through': org.org_name})
            seen.add(org.org_owner_id)
        rows = (OrgMember.objects.select_related('user').filter(org=org)
                .filter(Q(role=OrgMember.ROLE_ADMIN) | Q(role=OrgMember.ROLE_MANAGER)))
        for row in rows:
            if row.user_id in seen or row.user_id == event.creator_id:
                continue
            if row.role == OrgMember.ROLE_MANAGER and OrgMember.SCOPE_EVENTS not in (row.scopes or []):
                continue
            managers.append({'role': 'org_admin' if row.role == OrgMember.ROLE_ADMIN else 'org_events',
                             'added_at': row.joined_at, 'user': _person(row.user),
                             'added_by': None, 'through': org.org_name})
            seen.add(row.user_id)

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
            # Live paid tickets on a cancelled event: what a card network
            # refused the first time, for the retry door. 0 on a live event.
            'refunds_owed': 0 if event.is_active else _refunds_owed(event),
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
@admin_role_required(CANCEL_ROLES)
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

    # "If an event is cancelled then refunds must happen" (CEO, 18 September
    # 2026). Every live ticket: coins back to the wallet that paid, a
    # Paystack refund for a guest's card, a free one cancelled. A gateway
    # refusal leaves that ticket live and named in `failed`; the retry door
    # below asks again. The refunds run AFTER the state is saved, so a
    # cancelled event never sells while they run.
    refunds_summary = None
    if action == 'cancel':
        from vent_event import refunds
        refunds_summary = refunds.refund_event(
            event, 'Event cancelled: %s' % reason, by=admin)
        _record(admin, 'refund_event', event.event_id, reason,
                event_name=event.name, slug=event.slug,
                refunded=refunds_summary['refunded'],
                coins=refunds_summary['coins'], ngn=refunds_summary['ngn'],
                failed=refunds_summary['failed'])

    # The organiser and everybody holding a ticket are told. Until
    # 18 September only the audit log was, and a ticket holder found out
    # from a page that answered 404. Fire-and-forget: a mail server being
    # down must not undo the cancellation.
    _tell_everybody_about_the_state(event, action, reason, admin, refunds_summary)

    data = {'event': {'id': event.event_id, 'slug': event.slug,
                      'is_active': event.is_active}}
    if refunds_summary is not None:
        data['refunds'] = {k: v for k, v in refunds_summary.items() if k != 'outcomes'}
    return _ok(data, 'Event updated.')


@api_view(['POST'])
@admin_role_required(CANCEL_ROLES)
def admin_event_refunds(request, event_ref):
    """POST /auth/admin/events/<ref>/refunds/ - refund whatever is still live
    on a cancelled event.

    The cancel refunds everybody it can; a card network that refused or
    did not answer leaves that ticket live and named. This asks again, and
    only about what is still live, so it is safe to press until the list
    is empty. Refused on an event that is not cancelled: a refund on a
    live event is a different decision with a different door.
    """
    admin = request.admin_user
    event = _event(event_ref)
    if event is None:
        return _err('No event with that address.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if event.is_active:
        return _err('This event is not cancelled. Cancel it, and the refunds follow.',
                    'NOT_CANCELLED', status.HTTP_409_CONFLICT)
    reason = (request.data.get('reason') or '').strip() or 'Event cancelled'
    from vent_event import refunds
    summary = refunds.refund_event(event, reason, by=admin)
    _record(admin, 'refund_event', event.event_id, reason,
            event_name=event.name, slug=event.slug,
            refunded=summary['refunded'], coins=summary['coins'],
            ngn=summary['ngn'], failed=summary['failed'])
    return _ok({'refunds': {k: v for k, v in summary.items() if k != 'outcomes'},
                'still_owed': refunds.still_owed(event)},
               'Refunds run.')


def _tell_everybody_about_the_state(event, action, reason, admin, refunds_summary=None):
    from vent_event.models import Ticket
    from .views_notifications import create_notification
    from . import emails

    link = '/events/%s' % event.slug
    if action == 'cancel':
        money = ''
        if refunds_summary:
            money = (' %s paid tickets were refunded (%s VC to wallets, card payments '
                     'through Paystack).' % (refunds_summary['refunded'], refunds_summary['coins']))
            if refunds_summary['failed']:
                money += (' %d card refunds were refused by the gateway and are being '
                          'retried.' % len(refunds_summary['failed']))
        # The organiser cancelling their own event is told what came back;
        # an admin cancelling it is named.
        if admin is not None and admin.user_id == event.creator_id:
            organiser_title = 'You cancelled %s' % event.name
            organiser_body = ('It stops selling and leaves the listing; its page '
                              'keeps answering with the notice.%s' % money)
        else:
            organiser_title = '%s was cancelled by V-ENT' % event.name
            organiser_body = ('%s cancelled it. Reason: %s. It stops selling and '
                              'leaves the listing; its page keeps answering.%s'
                              % (admin.username, reason, money))
        holder_title = '%s was cancelled' % event.name
        holder_body = ('The event you hold a ticket for was cancelled. '
                       'Reason: %s. Your ticket no longer admits anybody. What you '
                       'paid is on its way back: to your wallet if you paid from it, '
                       'or to your card through Paystack within a few days.'
                       % reason)
    else:
        organiser_title = '%s is back on' % event.name
        organiser_body = '%s restored it. It sells and is listed again.' % admin.username
        holder_title = '%s is back on' % event.name
        holder_body = 'The event was restored. Your ticket admits you again.'

    try:
        create_notification(event.creator_id, 'event', organiser_title,
                            organiser_body, link=link,
                            metadata={'event_id': event.event_id,
                                      'action': action, 'by': admin.username})
    except Exception:                                   # noqa: BLE001
        logger.exception('could not tell the organiser of %s', event.slug)

    # On a cancel the refunds have already moved every live ticket to
    # refunded or cancelled, so the people to tell are the ones whose
    # ticket moved today; on a restore, the ones whose ticket is live again.
    if action == 'cancel':
        holders = Ticket.objects.filter(event=event).filter(
            Q(status__in=('valid', 'checked_in')) | Q(refunded_at__isnull=False))
    else:
        holders = Ticket.objects.filter(event=event, status__in=('valid', 'checked_in'))
    live = holders.select_related('user')
    told = set()
    for ticket in live:
        try:
            if ticket.user_id:
                if ticket.user_id in told:
                    continue
                told.add(ticket.user_id)
                create_notification(ticket.user_id, 'event', holder_title,
                                    holder_body, link='/events/my-tickets',
                                    metadata={'event_id': event.event_id,
                                              'action': action})
            elif ticket.attendee_email:
                key = ticket.attendee_email.lower()
                if key in told:
                    continue
                told.add(key)
                emails.send_event_announcement(
                    ticket.attendee_email, event=event,
                    subject=holder_title, body=holder_body)
        except Exception:                               # noqa: BLE001
            logger.exception('could not tell the holder of %s', ticket.code)


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
@admin_role_required(VOID_ROLES)
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
            from vent_event.transfers import transferred_away
            moved = transferred_away(code)
            if moved is not None:
                return _err('That code was transferred and no longer works.',
                            'TICKET_TRANSFERRED', status.HTTP_409_CONFLICT)
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
            # And what it was worth to everybody. A voided ticket that stays on
            # the ledger is an organiser paid for a seat nobody sat in, and an
            # affiliate paid commission on a sale that was undone.
            from vent_event import ledger as _ledger
            _ledger.reverse_sale(ticket, reason=reason or 'Voided')
            # The seat is back on sale: the queue is offered it.
            from vent_event.views_waitlist import capacity_changed
            capacity_changed(ticket.event, how_many=1)
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

    # The person holding the code is told. Until 18 September they found
    # out at the door: the seat was back on sale and the money reversed,
    # and nothing had said so. An account gets its inbox; a guest address
    # gets the event's own mail. Neither may undo the action if it fails.
    _tell_the_holder(ticket, action, reason)

    return _ok({'ticket': _ticket_row(ticket)}, 'Ticket updated.')


def _tell_the_holder(ticket, action, reason):
    event = ticket.event
    if action == 'void':
        title = 'Your ticket for %s was voided' % event.name
        body = ('Ticket %s no longer admits anybody. Reason: %s.'
                % (ticket.code, reason or 'not given'))
    else:
        title = 'Your ticket for %s is valid again' % event.name
        body = 'Ticket %s admits you again. Show it at the door.' % ticket.code
    try:
        if ticket.user_id:
            from .views_notifications import create_notification
            create_notification(
                ticket.user_id, 'event', title, body,
                link='/events/my-tickets',
                metadata={'event_id': event.event_id, 'code': ticket.code,
                          'action': action})
        elif ticket.attendee_email:
            from . import emails
            emails.send_event_announcement(
                ticket.attendee_email, event=event, subject=title, body=body)
    except Exception:                                   # noqa: BLE001
        logger.exception('could not tell the holder of %s', ticket.code)


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

    from vent_tournament.lookup import find as find_tournament

    tournament = find_tournament(tournament_ref)
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

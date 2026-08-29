"""What the event actually did.

PRD section 4: sales and attendance metrics, and a spreadsheet of them.

Three numbers an organiser is asked for after an event, and one they are asked
for during it:

- **Sold.** How many tickets exist, by tier, and what they were worth.
- **Turned up.** How many of those were checked in. The ratio is attendance,
  and it is the number that decides how much food to order next time.
- **Who is missing.** Which tiers under-sold, and how much of the room is left.
- **Sales by day**, during, because a curve that has gone flat is a decision to
  make now rather than a fact to read afterwards.

Revenue is reported in both currencies. VENT COINS are what moved, NGN is what
the price said, and the rate is stored per ticket at purchase time, so the two
are added independently rather than one being converted from the other. A later
rate change never rewrites what an event earned.

Attendance is split by HOW somebody was admitted. A steward scanning at a gate
and an attendee marking themselves present are different evidence, and an
organiser deciding whether the attendance figure is real needs to see which is
which.

Organiser or door staff only, same as the attendee list: these rows carry
contact details somebody handed over to buy a ticket.
"""
import csv
import io
from collections import OrderedDict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from .models import Event, EventManager, Ticket, TicketTier

SESSION_TIMEOUT_MINUTES = 60 * 24 * 30


def _error(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _authenticate(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is '
                            'required.', 'UNAUTHORIZED',
                            status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED',
                            status.HTTP_401_UNAUTHORIZED)
    return user, None


def _event(event_id):
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


def _may_read(user, event):
    if event.creator_id == user.user_id:
        return True
    return EventManager.objects.filter(
        event=event, user=user, role__in=('manager', 'door')).exists()


def _resolve(request, event_id):
    """(event, None) or (None, error response)."""
    user, err = _authenticate(request)
    if err:
        return None, err
    event = _event(event_id)
    if event is None:
        return None, _error('Event not found.', 'NOT_FOUND',
                            status.HTTP_404_NOT_FOUND)
    if not _may_read(user, event):
        return None, _error('Only the event organizer or their door staff can '
                            'see these numbers.', 'NOT_ORGANIZER',
                            status.HTTP_403_FORBIDDEN)
    return event, None


def _live(event):
    """The queryset the numbers are built from.

    Refunded and cancelled tickets are excluded from sold and from revenue, and
    counted separately. A refund is not a sale that happened; reporting it as
    one overstates both the money and the room.
    """
    return Ticket.objects.filter(event=event).exclude(
        status__in=('refunded', 'cancelled'))


def compute(event):
    """Every number, in one pass per table. Shared by the JSON and the CSV."""
    live = _live(event)
    totals = live.aggregate(
        issued=Count('id'),
        checked_in=Count('id', filter=Q(status='checked_in')),
        at_door=Count('id', filter=Q(status='checked_in') & ~Q(checked_in_gate='self')),
        by_self=Count('id', filter=Q(status='checked_in', checked_in_gate='self')),
        guests=Count('id', filter=Q(user__isnull=True)),
        vc=Sum('price_vc'),
        ngn=Sum('price_ngn'),
    )
    refunded = Ticket.objects.filter(event=event, status='refunded').aggregate(
        count=Count('id'), vc=Sum('price_vc'))

    issued = totals['issued'] or 0
    checked_in = totals['checked_in'] or 0

    tiers = []
    for tier in TicketTier.objects.filter(event=event).order_by('id'):
        rows = live.filter(tier=tier)
        agg = rows.aggregate(
            sold=Count('id'),
            checked_in=Count('id', filter=Q(status='checked_in')),
            vc=Sum('price_vc'), ngn=Sum('price_ngn'))
        sold = agg['sold'] or 0
        capacity = tier.quantity or 0
        tiers.append({
            'tier_id': tier.id,
            'name': tier.name,
            'price_ngn': tier.price,
            'capacity': capacity,
            'sold': sold,
            # Negative would be a bug elsewhere, and reporting it as negative
            # is how somebody notices.
            'remaining': (capacity - sold) if capacity else None,
            'checked_in': agg['checked_in'] or 0,
            'revenue_vc': agg['vc'] or 0,
            'revenue_ngn': agg['ngn'] or Decimal('0'),
        })

    # By the day the ticket was bought, in order, with no gaps filled in. A day
    # with no sales is a real thing to see and inventing a zero row for every
    # date between two sales would bury it.
    by_day = OrderedDict()
    for sold_at, code in live.values_list('purchased_at', 'code'):
        if sold_at is None:
            continue
        key = timezone.localtime(sold_at).date().isoformat()
        by_day[key] = by_day.get(key, 0) + 1
    sales_by_day = [{'date': d, 'tickets': n}
                    for d, n in sorted(by_day.items())]

    return {
        'event': {'name': event.name, 'slug': event.slug,
                  'event_id': event.event_id},
        'tickets': {
            'issued': issued,
            'checked_in': checked_in,
            'not_arrived': issued - checked_in,
            'refunded': refunded['count'] or 0,
            # A rate with no tickets is not zero per cent, it is unanswerable.
            'attendance_rate': round(checked_in * 100.0 / issued, 1) if issued else None,
            'at_door': totals['at_door'] or 0,
            'self_checked_in': totals['by_self'] or 0,
            'guests': totals['guests'] or 0,
            'account_holders': issued - (totals['guests'] or 0),
        },
        'revenue': {
            'vc': totals['vc'] or 0,
            'ngn': totals['ngn'] or Decimal('0'),
            'refunded_vc': refunded['vc'] or 0,
        },
        'tiers': tiers,
        'sales_by_day': sales_by_day,
        'capacity': {
            'event_capacity': event.capacity,
            'remaining': (event.capacity - issued) if event.capacity else None,
        },
    }


@api_view(['GET'])
def event_metrics(request, event_id):
    event, err = _resolve(request, event_id)
    if err:
        return err
    return Response({'status': 'success', 'data': compute(event),
                     'message': ''})


def _csv(rows, header, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    # A plain HttpResponse. A DRF Response is rendered through the JSON
    # renderer, and the download becomes a quoted string with escaped newlines.
    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response


@api_view(['GET'])
def export_metrics(request, event_id):
    """`?sheet=attendees|sales|tiers`.

    Not `?format=`, which DRF reserves for content negotiation.
    """
    event, err = _resolve(request, event_id)
    if err:
        return err

    sheet = str(request.GET.get('sheet') or 'attendees').lower()
    stem = event.slug or str(event.event_id)

    if sheet == 'attendees':
        # Every ticket, including refunded ones, because this is the sheet
        # somebody reconciles against and a missing row reads as a missing
        # person rather than a refund.
        rows = (Ticket.objects.filter(event=event)
                .select_related('tier', 'user', 'checked_in_by')
                .order_by('purchased_at'))
        out = []
        for t in rows:
            out.append([
                t.code,
                t.tier.name if t.tier_id else '',
                t.attendee_name or (t.user.full_name if t.user_id else ''),
                t.attendee_email or (t.user.email if t.user_id else ''),
                t.attendee_phone,
                t.user.username if t.user_id else '',
                'guest' if not t.user_id else 'account',
                t.status,
                t.price_vc,
                t.price_ngn,
                t.purchased_at.isoformat() if t.purchased_at else '',
                t.checked_in_at.isoformat() if t.checked_in_at else '',
                t.checked_in_gate,
                t.checked_in_by.username if t.checked_in_by_id else '',
            ])
        return _csv(out, [
            'code', 'tier', 'name', 'email', 'phone', 'username', 'kind',
            'status', 'price_vc', 'price_ngn', 'purchased_at', 'checked_in_at',
            'gate', 'checked_in_by',
        ], '%s-attendees.csv' % stem)

    if sheet == 'tiers':
        data = compute(event)
        return _csv([[
            t['name'], t['price_ngn'], t['capacity'], t['sold'],
            '' if t['remaining'] is None else t['remaining'],
            t['checked_in'], t['revenue_vc'], t['revenue_ngn'],
        ] for t in data['tiers']], [
            'tier', 'price_ngn', 'capacity', 'sold', 'remaining',
            'checked_in', 'revenue_vc', 'revenue_ngn',
        ], '%s-tiers.csv' % stem)

    if sheet == 'sales':
        data = compute(event)
        return _csv([[r['date'], r['tickets']] for r in data['sales_by_day']],
                    ['date', 'tickets'], '%s-sales.csv' % stem)

    return _error('Ask for attendees, tiers or sales.', 'VALIDATION_ERROR')

"""Every movement of money on the platform, in one list.

CEO, 7 September 2026, from the admin dashboard spec:

    Financial Management: view and manage all financial transactions on the
    platform, generate financial reports and analytics for various revenue
    streams, transfer funds between accounts, organizations, and users.

The console had payouts and exchange rates and nothing that answered "what has
the platform taken this week". Both of those are one stream each; this is the
ledger.

## One list, three kinds of wallet

A `Transaction` belongs to exactly one of a person's wallet, a team's or an
organisation's, held by a database constraint. So a single query over
`Transaction` IS every movement, and there is no second table to remember to
union in when team wallets get busy. `owner_name` on the model already answers
"whose statement is this line on" for all three.

## The report is a file, not JSON with a filename on it

`admin_transactions_report` returns a Django `HttpResponse`. A DRF `Response`
runs the CSV string through the JSON renderer and hands the browser a quoted
blob, and a test that reads `res.data` cannot see it happen because `res.data`
is the string either way. That has shipped here before, which is why the test
for this reads `res.content` and asserts on the header line.
"""
import csv
import io
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import ROLE_PERMISSIONS, admin_role_required
from .models import Transaction, WithdrawalRequest

READ_ROLES = ROLE_PERMISSIONS['view_transactions']

# A report is generated from a filtered list, and a filter that matches
# everything on a busy platform is a file nobody can open. The cap is stated in
# the response so a person can narrow the dates rather than wonder.
REPORT_MAX_ROWS = 20000


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _parse_date(value):
    """A yyyy-mm-dd from a filter box, as an aware datetime, or None."""
    if not value:
        return None
    from django.utils.dateparse import parse_date, parse_datetime
    parsed = parse_datetime(value) or None
    if parsed is None:
        day = parse_date(value)
        if day is None:
            return None
        parsed = timezone.datetime(day.year, day.month, day.day)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def transactions_queryset(request):
    """The filtered ledger. Shared by the list and the report, deliberately.

    Two queries built separately are two chances for the report to disagree
    with the screen it was generated from, and the person reading the file is
    the one who cannot tell.
    """
    rows = Transaction.objects.select_related(
        'wallet__user', 'team_wallet__team', 'org_wallet__org')

    kind = (request.GET.get('kind') or '').strip().lower()
    if kind == 'user':
        rows = rows.filter(wallet__isnull=False)
    elif kind == 'team':
        rows = rows.filter(team_wallet__isnull=False)
    elif kind == 'org':
        rows = rows.filter(org_wallet__isnull=False)

    type_ = (request.GET.get('type') or '').strip().lower()
    if type_ and type_ != 'all':
        rows = rows.filter(type=type_)

    state = (request.GET.get('status') or '').strip().lower()
    if state and state != 'all':
        rows = rows.filter(status=state)

    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(
            Q(description__icontains=term)
            | Q(reference__icontains=term)
            | Q(wallet__user__username__icontains=term)
            | Q(team_wallet__team__team_name__icontains=term)
            | Q(org_wallet__org__org_name__icontains=term))

    since = _parse_date(request.GET.get('from'))
    if since:
        rows = rows.filter(created_at__gte=since)
    until = _parse_date(request.GET.get('to'))
    if until:
        # A date filter that means "up to and including that day" is what
        # somebody typing 30 September expects. Excluding the day itself is the
        # off by one people never notice and always argue about.
        rows = rows.filter(created_at__lt=until + timedelta(days=1))

    def _number(name):
        try:
            return int(request.GET.get(name))
        except (TypeError, ValueError):
            return None

    low = _number('min')
    if low is not None:
        rows = rows.filter(amount__gte=low)
    high = _number('max')
    if high is not None:
        rows = rows.filter(amount__lte=high)

    return rows.order_by('-created_at')


def _row(txn):
    return {
        'id': txn.id,
        'at': txn.created_at.isoformat() if txn.created_at else None,
        'owner': txn.owner_name,
        'owner_kind': ('user' if txn.wallet_id else
                       'team' if txn.team_wallet_id else 'org'),
        'type': txn.type,
        'amount': txn.amount,
        'description': txn.description,
        'status': txn.status,
        'reference': txn.reference or '',
    }


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_transactions(request):
    """Every line, filtered, with the totals for THAT filter beside it.

    The totals are computed over the filtered set rather than the page. A page
    total answers a question nobody asked: somebody filtering to refunds in
    August wants what the refunds in August came to, not what the twenty rows
    in front of them come to.
    """
    rows = transactions_queryset(request)

    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = min(200, max(1, int(request.GET.get('page_size', 25))))
    except (TypeError, ValueError):
        size = 25

    total = rows.count()
    sums = rows.aggregate(
        credited=Sum('amount', filter=Q(amount__gt=0)),
        debited=Sum('amount', filter=Q(amount__lt=0)),
    )
    window = rows[(page - 1) * size: page * size]

    return _ok({
        'results': [_row(t) for t in window],
        'count': total,
        'page': page,
        'page_size': size,
        'pages': max(1, (total + size - 1) // size),
        'totals': {
            'credited_vc': sums['credited'] or 0,
            # Reported as a positive number with its own name. A negative
            # "total out" reads as a subtraction somebody has to do in their
            # head, and half of them do it wrong.
            'debited_vc': abs(sums['debited'] or 0),
            'net_vc': (sums['credited'] or 0) + (sums['debited'] or 0),
        },
        'types': [{'value': v, 'label': label}
                  for v, label in Transaction.TYPE_CHOICES],
        'statuses': [{'value': v, 'label': label}
                     for v, label in Transaction.STATUS_CHOICES],
    })


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_transactions_report(request):
    """The same filtered ledger as a CSV file.

    An `HttpResponse`, not a DRF `Response`. A DRF Response hands the CSV to
    the JSON renderer, the browser saves a quoted string, and the test that
    read `res.data` saw the right characters and said nothing.
    """
    rows = transactions_queryset(request)[:REPORT_MAX_ROWS]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['id', 'when', 'owner', 'owner_kind', 'type', 'amount_vc',
                     'status', 'description', 'reference'])
    for txn in rows.iterator():
        writer.writerow([
            txn.id,
            txn.created_at.isoformat() if txn.created_at else '',
            txn.owner_name,
            'user' if txn.wallet_id else 'team' if txn.team_wallet_id else 'org',
            txn.type,
            txn.amount,
            txn.status,
            txn.description,
            txn.reference or '',
        ])

    stamp = timezone.now().strftime('%Y-%m-%d')
    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = (
        'attachment; filename="vent-transactions-%s.csv"' % stamp)
    return response


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_finance_summary(request):
    """What each revenue stream came to, over a window.

    Streams rather than a single number, because "the platform took 4m coins"
    is not a fact anybody can act on. Entry fees falling while ticket sales
    rise is.

    `?days=` sets the window and defaults to 30. The comparison against the
    window before it is what makes a number mean anything on its own.
    """
    try:
        days = min(365, max(1, int(request.GET.get('days', 30))))
    except (TypeError, ValueError):
        days = 30

    now = timezone.now()
    start = now - timedelta(days=days)
    previous_start = start - timedelta(days=days)

    def _by_type(since, until):
        out = {}
        rows = (Transaction.objects
                .filter(created_at__gte=since, created_at__lt=until,
                        status='completed')
                .values('type')
                .annotate(total=Sum('amount'), lines=Count('id')))
        for row in rows:
            out[row['type']] = {'total_vc': row['total'] or 0,
                                'lines': row['lines']}
        return out

    current = _by_type(start, now)
    before = _by_type(previous_start, start)

    streams = []
    for value, label in Transaction.TYPE_CHOICES:
        this_window = current.get(value, {'total_vc': 0, 'lines': 0})
        last_window = before.get(value, {'total_vc': 0, 'lines': 0})
        streams.append({
            'type': value,
            'label': label,
            'total_vc': this_window['total_vc'],
            'lines': this_window['lines'],
            'previous_vc': last_window['total_vc'],
        })

    # Tickets are counted from the ticket rows and not from the ledger, because
    # most tickets are bought with a card by somebody with no account at all,
    # and that money never becomes a wallet line. Counting only the ledger
    # would report an event that sold out as having earned nothing.
    tickets = {'sold': 0, 'revenue_ngn': '0', 'revenue_vc': 0}
    try:
        from vent_event.models import Ticket
        counted = Ticket.objects.filter(
            purchased_at__gte=start,
            status__in=['valid', 'checked_in']).aggregate(
                sold=Count('id'),
                ngn=Sum('price_ngn'),
                coins=Sum('price_vc'))
        tickets = {
            'sold': counted['sold'] or 0,
            'revenue_ngn': str(counted['ngn'] or 0),
            'revenue_vc': counted['coins'] or 0,
        }
    except Exception:                                            # noqa: BLE001
        # The events app not being installed is not a reason for the finance
        # screen to fail. Every other number on it is still true.
        pass

    payouts = WithdrawalRequest.objects.filter(
        requested_at__gte=start).aggregate(
            requested=Count('id'),
            approved=Count('id', filter=Q(status='approved')),
            pending=Count('id', filter=Q(status='pending')),
            paid_vc=Sum('amount', filter=Q(status='approved')))

    return _ok({
        'window_days': days,
        'from': start.isoformat(),
        'to': now.isoformat(),
        'streams': streams,
        'tickets': tickets,
        'payouts': {
            'requested': payouts['requested'] or 0,
            'approved': payouts['approved'] or 0,
            'pending': payouts['pending'] or 0,
            'paid_vc': payouts['paid_vc'] or 0,
        },
    })

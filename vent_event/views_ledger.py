"""What an event earned, who is owed it, and paying them.

CEO, 7 September 2026, from the ticketing research: who bears the platform fee,
affiliates that actually get paid, and a settlement run rather than a
one-at-a-time payout queue.

Three endpoints, and the third is the one worth reading twice:

    GET  /event/<ref>/earnings/     what the event took and who is owed what
    POST /event/<ref>/fee-bearer/   the organiser choosing who pays the fee
    POST /event/<ref>/settle/       one pass that pays everybody, once

`settle` is idempotent by construction, not by a guard bolted on top. Each line
it pays is stamped with the run that paid it, inside the same transaction that
moves the coins, so pressing the button twice pays nothing the second time.
That is what separates a RUN from a queue: a queue worked through twice pays
twice, and the person it pays twice never tells you.

Who may do what:

- **Earnings** is the organiser and anybody who may manage the event. It is
  money, so it is never public.
- **Settle** is the organiser too. The platform holds the coins either way -
  they never left it - so this is not a bank transfer, it is a balance moving
  between two accounts on the platform. Cashing out to naira is the wallet's
  own withdrawal path, which already has KYC on it.
"""
from decimal import Decimal

from django.db.models import Sum
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Users

from . import ledger
from .models import Event, EventLedgerEntry, EventReferral


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


def _event(event_id):
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


def _resolve(request, event_id):
    """(event, viewer, None) or (None, None, error). Organiser-only."""
    viewer = _viewer(request)
    if viewer is None:
        return None, None, _error('Sign in to see this.', 'UNAUTHORIZED',
                                  status.HTTP_401_UNAUTHORIZED)
    event = _event(event_id)
    if event is None:
        return None, None, _error('Event not found.', 'NOT_FOUND',
                                  status.HTTP_404_NOT_FOUND)
    # `may_run_event`, not the wider door role: somebody put on the gate for
    # the day admits people and has no business reading the takings.
    from .permissions import may_run_event
    if not may_run_event(viewer, event):
        return None, None, _error('Only the organiser can see what this event '
                                  'earned.', 'NOT_ORGANIZER',
                                  status.HTTP_403_FORBIDDEN)
    return event, viewer, None


@api_view(['GET'])
def earnings(request, event_id):
    event, _who, err = _resolve(request, event_id)
    if err:
        return err

    figures = ledger.balances(event)

    # Named, so the organiser reads "Ada" rather than a row id. An affiliate
    # link deleted since keeps its number, which is the honest answer.
    by_id = {r.id: r for r in EventReferral.objects.filter(
        id__in=[a['referral_id'] for a in figures['affiliates']])}
    affiliates = []
    for row in figures['affiliates']:
        link = by_id.get(row['referral_id'])
        affiliates.append({
            'referral_id': row['referral_id'],
            'name': link.name if link else '',
            'code': link.code if link else '',
            'commission_pct': link.commission_pct if link else 0,
            'owed_vc': row['owed_vc'],
            'owed_ngn': float(row['owed_ngn']),
            'has_payee': bool(link and link.payee_id),
        })

    runs = [{
        'id': run.id,
        'amount_vc': run.amount_vc,
        'lines_paid': run.lines_paid,
        'at': run.created_at.isoformat(),
        'note': run.note,
    } for run in event.settlements.all()[:20]]

    # An affiliate line with nobody attached is money accruing to a link whose
    # owner has not claimed an account. It is paid the day they do, and saying
    # so is better than an organiser wondering why a settlement paid less than
    # the balance said.
    unclaimed = EventLedgerEntry.objects.filter(
        event=event, kind=EventLedgerEntry.KIND_AFFILIATE,
        user__isnull=True, settled_at__isnull=True
    ).aggregate(n=Sum('amount_ngn'))['n'] or 0
    pct, flat = ledger.platform_fee()

    return _ok({
        'fee_bearer': event.fee_bearer,
        'fee_pct': pct,
        'fee_flat_ngn': float(flat),
        # Naira is the number; the coins beside each are its whole-coin
        # floor, which is what a settlement can actually pay into a wallet.
        'organiser_owed_vc': figures['organiser_owed_vc'],
        'organiser_owed_ngn': float(figures['organiser_owed_ngn']),
        'organiser_paid_vc': figures['organiser_paid_vc'],
        'organiser_paid_ngn': float(figures['organiser_paid_ngn']),
        'affiliates_owed_vc': figures['affiliates_owed_vc'],
        'affiliates_owed_ngn': float(figures['affiliates_owed_ngn']),
        'affiliates_paid_vc': figures['affiliates_paid_vc'],
        'affiliates_paid_ngn': float(figures['affiliates_paid_ngn']),
        'platform_fee_vc': figures['platform_fee_vc'],
        'platform_fee_ngn': float(figures['platform_fee_ngn']),
        'affiliates': affiliates,
        'settlements': runs,
        'unclaimed_vc': int(Decimal(str(unclaimed)) // ledger.ngn_per_coin()),
        'unclaimed_ngn': float(unclaimed),
    })


@api_view(['POST'])
def set_fee_bearer(request, event_id):
    """`{fee_bearer: 'organiser'|'buyer'}`.

    Changing this affects tickets sold from now on and nothing already sold:
    the rate and the bearer are stamped on each ledger line at the sale, so
    last week's takings are not rewritten by this week's decision.
    """
    event, _who, err = _resolve(request, event_id)
    if err:
        return err

    choice = str(request.data.get('fee_bearer') or '').strip()
    if choice not in (Event.FEE_ORGANISER, Event.FEE_BUYER):
        return _error('Say whether the organiser or the buyer pays the fee.',
                      'VALIDATION_ERROR')
    event.fee_bearer = choice
    event.save(update_fields=['fee_bearer'])
    return _ok({'fee_bearer': event.fee_bearer,
                'fee_pct': ledger.platform_rate()})


@api_view(['POST'])
def settle(request, event_id):
    """Pay everybody owed anything on this event, once.

    Answering 200 with `lines_paid: 0` when there is nothing to do is
    deliberate. Pressing Settle on an event that is already settled should say
    so plainly, not fail: an error there reads as something being broken and
    invites somebody to press it again.
    """
    event, viewer, err = _resolve(request, event_id)
    if err:
        return err

    run = ledger.settle(event, run_by=viewer,
                        note=str(request.data.get('note') or ''))
    return _ok({
        'settlement_id': run.id,
        'amount_vc': run.amount_vc,
        'lines_paid': run.lines_paid,
        'at': run.created_at.isoformat(),
        **ledger.balances(event),
    })

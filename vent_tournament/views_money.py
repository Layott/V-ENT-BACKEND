"""What a tournament earns, and paying it out.

CEO, 13 September 2026, asked whether tournaments should pay organisers a
share of entry fees: "i want it". Until then an entry fee left the player's
wallet and reached nobody. Now each paid entry writes the same ledger lines a
ticket does (`vent_event.ledger`), prizes come out of that pool, and the
organiser is paid what is left by the same settlement run an event uses.

Three doors, mirroring `vent_event.views_ledger`:

    GET  /tournament/<ref>/entry-quote/   what an entry costs, public
    GET  /tournament/<ref>/earnings/      the organiser's numbers
    POST /tournament/<ref>/settle/        pay the organiser, once
"""
from decimal import Decimal

from django.db.models import Sum
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_event import ledger
from vent_event.models import EventLedgerEntry

from . import lookup


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'data': data or {}, 'message': message,
                     'code': code}, status=http_status)


def _organiser(request, tournament):
    user, err = actor_from_request(request)
    if err:
        return None, err
    if tournament.tournament_creator_id == user.user_id:
        return user, None
    if may_override(user, 'cancel_tournament'):
        return user, None
    return None, _err('Only the organiser can see the money on this tournament.',
                      'NOT_YOURS', status.HTTP_403_FORBIDDEN)


@api_view(['GET'])
def entry_quote(request, tournament_id):
    """What entering costs, before the PIN. Public: the register modal reads
    it signed in or not, and a stranger deciding whether to join is exactly
    who needs the number."""
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    priced = ledger.quote_entry(tournament)
    return _ok({
        'entry_vc': priced['unit_vc'],
        'entry_ngn': float(priced['unit_ngn']),
        'fee_pct': float(priced['fee_pct']),
        'fee_flat_ngn': float(priced['fee_flat_ngn']),
        'fee_ngn': float(priced['fee_ngn']),
        'fee_bearer': priced['fee_bearer'],
        'buyer_pays_fee': priced['buyer_pays_fee'],
        'buyer_fee_vc': priced['buyer_fee_vc'],
        'buyer_fee_ngn': float(priced['buyer_fee_ngn']),
        'total_vc': priced['total_vc'],
        'total_ngn': float(priced['total_ngn']),
        'organiser_ngn': float(priced['organiser_ngn']),
    })


def _sum(qs):
    return Decimal(str(qs.aggregate(n=Sum('amount_ngn'))['n'] or 0))


def figures_for(tournament):
    """The organiser's numbers, summed from the lines. Nothing stored."""
    lines = EventLedgerEntry.objects.filter(tournament=tournament)
    organiser = lines.filter(kind=EventLedgerEntry.KIND_ORGANISER)
    reversals = lines.filter(kind=EventLedgerEntry.KIND_REVERSAL)

    entries_live = organiser.filter(registration__isnull=False, reversed_by__isnull=True)
    entries_ngn = Decimal(str(entries_live.aggregate(n=Sum('gross_ngn'))['n'] or 0))
    fee_kept_ngn = Decimal(str(entries_live.aggregate(
        n=Sum('fee_ngn'))['n'] or 0)) - Decimal(str(entries_live.aggregate(
            n=Sum('buyer_fee_ngn'))['n'] or 0))
    prizes_ngn = -_sum(organiser.filter(prize__isnull=False))
    topup_ngn = _sum(organiser.filter(note=ledger.PRIZE_TOPUP_NOTE))
    refunded_ngn = -Decimal(str(reversals.filter(
        reverses__kind=EventLedgerEntry.KIND_ORGANISER).aggregate(n=Sum('gross_ngn'))['n'] or 0))

    balances = ledger.balances(tournament)
    unit = ledger.ngn_per_coin()
    return {
        'entries_paid': entries_live.count(),
        'entries_ngn': float(entries_ngn),
        'entries_vc': int(entries_ngn // unit),
        'refunded_entries': reversals.filter(
            reverses__kind=EventLedgerEntry.KIND_ORGANISER, registration__isnull=False).count(),
        'refunded_ngn': float(refunded_ngn),
        # What the platform took off the organiser's share (a fee the player
        # bore on top is not the organiser's cost).
        'fee_ngn': float(fee_kept_ngn),
        'platform_fee_ngn': float(balances['platform_fee_ngn']),
        'prizes_ngn': float(prizes_ngn),
        'prizes_vc': int(prizes_ngn // unit),
        'topup_ngn': float(topup_ngn),
        'topup_vc': int(topup_ngn // unit),
        'organiser_owed_ngn': float(balances['organiser_owed_ngn']),
        'organiser_owed_vc': balances['organiser_owed_vc'],
        'organiser_paid_ngn': float(balances['organiser_paid_ngn']),
        'organiser_paid_vc': balances['organiser_paid_vc'],
    }


@api_view(['GET'])
def earnings(request, tournament_id):
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    _user, err = _organiser(request, tournament)
    if err:
        return err

    pct, flat = ledger.tournament_fee()
    runs = [{
        'id': run.id,
        'amount_vc': run.amount_vc,
        'lines_paid': run.lines_paid,
        'at': run.created_at.isoformat(),
        'note': run.note,
    } for run in tournament.settlements.all()[:20]]
    return _ok({
        'fee_bearer': tournament.fee_bearer,
        'fee_pct': float(pct),
        'fee_flat_ngn': float(flat),
        'entry_vc': int(tournament.entry_fee_price or 0) if tournament.entry_fee == 'Paid' else 0,
        **figures_for(tournament),
        'settlements': runs,
    })


@api_view(['POST'])
def settle(request, tournament_id):
    """Pay the organiser what the entries have earned, once.

    Answering 200 with `lines_paid: 0` when there is nothing to do is
    deliberate: pressing it twice pays nothing the second time and says so.
    """
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    user, err = _organiser(request, tournament)
    if err:
        return err

    run = ledger.settle(tournament, run_by=user,
                        note=str(request.data.get('note') or ''))
    return _ok({
        'settlement_id': run.id,
        'amount_vc': run.amount_vc,
        'lines_paid': run.lines_paid,
        'at': run.created_at.isoformat(),
        **figures_for(tournament),
    })

# -*- coding: utf-8 -*-
"""Refunds when an event is cancelled.

CEO, 18 September 2026: "if an event is cancelled then refunds must happen."
Before this, an admin cancel stopped the tickets admitting anybody and
nobody got anything back; the organiser's delete refused a sold event with
"Cancel the event first, which refunds the holders", and no cancel refunded.

One function per ticket, one per event, and every cancel path calls the
second: the admin's cancel, the organiser's cancel, and the retry door for
whatever a card network refused the first time.

What a refund is, per ticket:

- what the buyer PAID is the ticket's own price, plus (once per purchase,
  on the ticket that carries the purchase's ledger line) whatever of the
  service fee they bore;
- the sale's ledger lines are reversed (the organiser's take, the fee, an
  affiliate's share), so a settled organiser carries the debt on their next
  payout and an unsettled one is simply owed less;
- the buyer with a wallet is credited the coins (the person who paid: the
  original buyer of a ticket that was given away, not the friend holding
  it); a guest who paid by card is refunded by Paystack against the
  payment's reference, in naira; a free ticket is cancelled;
- the tier's sold count comes down, the ticket says where the money went.

A Paystack refusal does NOT undo the cancellation: the ticket stays live
with the outcome named, the admin console lists it, and the retry door asks
again. The network call happens outside any database transaction.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from vent_auth import paystack
from vent_auth.models import Transaction, UserWallet

from . import ledger
from .models import EventLedgerEntry, Ticket, TicketTier, TicketTransfer

logger = logging.getLogger(__name__)

LIVE = ('valid', 'checked_in')


def paid_for(ticket):
    """(coins, naira) the buyer actually paid for this ticket.

    The ticket's own price is what it cost (`price_vc` is the whole coins
    the wallet gave for it, `price_ngn` the naira). The service fee a buyer
    bore was paid once per PURCHASE, and the ledger attaches a purchase's
    lines to its first ticket, so that ticket carries the fee back too:
    over the whole purchase the buyer gets exactly what they paid.
    """
    coins = int(ticket.price_vc or 0)
    naira = Decimal(ticket.price_ngn or 0)
    line = (EventLedgerEntry.objects
            .filter(ticket=ticket, kind=EventLedgerEntry.KIND_ORGANISER,
                    reversed_by__isnull=True)
            .order_by('id').first())
    if line is not None and line.buyer_fee_ngn:
        coins += ledger._floor_vc(line.buyer_fee_ngn)
        naira += Decimal(line.buyer_fee_ngn)
    return coins, naira


def payer_of(ticket):
    """The account that paid: the original buyer, whoever holds it now."""
    first = (TicketTransfer.objects.filter(ticket=ticket)
             .order_by('at', 'id').first())
    if first is not None and first.from_user_id:
        return first.from_user_id
    return ticket.user_id


def refund_ticket(ticket, reason, *, by=None):
    """Refund one live ticket. Returns an outcome dict:

        {'code': ticket.code, 'outcome': 'wallet' | 'card' | 'free' | 'card_failed'
                                          | 'skipped' | 'no_way_back',
         'coins': int, 'ngn': str, 'reference': str, 'error': str}

    `no_way_back` is a paid ticket with neither a wallet to credit nor a card
    payment to reverse (a comp at a price, or a sale from before references
    were kept); it is cancelled and named so a person can settle it.
    """
    out = {'code': ticket.code, 'outcome': 'skipped', 'coins': 0, 'ngn': '0',
           'reference': '', 'error': ''}
    if ticket.status not in LIVE:
        return out

    coins, naira = paid_for(ticket)
    out['coins'] = coins
    out['ngn'] = str(naira)
    now = timezone.now()

    # Nothing was paid: cancel it, nothing to send back.
    if coins <= 0 and naira <= 0:
        with transaction.atomic():
            row = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if row.status not in LIVE:
                return out
            ledger.reverse_sale(row, reason)
            row.status = 'cancelled'
            row.refunded_at = now
            row.save(update_fields=['status', 'refunded_at'])
            TicketTier.objects.filter(pk=row.tier_id, sold__gt=0).update(sold=F('sold') - 1)
        out['outcome'] = 'free'
        return out

    payer_id = payer_of(ticket)
    wallet = UserWallet.objects.filter(user_id=payer_id).first() if payer_id else None

    if wallet is not None:
        with transaction.atomic():
            row = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if row.status not in LIVE:
                return out
            locked = UserWallet.objects.select_for_update().get(pk=wallet.pk)
            ledger.reverse_sale(row, reason)
            if coins > 0:
                locked.wallet_balance += coins
                locked.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=locked, type='refund', amount=coins, status='completed',
                    description=('Refund - %s cancelled: %s'
                                 % (row.event.name, row.code))[:255])
            row.status = 'refunded'
            row.refunded_at = now
            row.refund_reference = 'wallet'
            row.save(update_fields=['status', 'refunded_at', 'refund_reference'])
            TicketTier.objects.filter(pk=row.tier_id, sold__gt=0).update(sold=F('sold') - 1)
        out['outcome'] = 'wallet'
        return out

    if ticket.payment_reference and naira > 0:
        # The network call first and outside any transaction: a slow gateway
        # must not hold a lock, and a refusal must leave the ticket live and
        # named rather than half-refunded.
        try:
            data = paystack.refund(ticket.payment_reference, naira)
        except (paystack.Unreachable, paystack.Refused) as exc:
            out['outcome'] = 'card_failed'
            out['error'] = str(exc)[:200]
            logger.warning('refund of %s refused by the gateway: %s', ticket.code, exc)
            return out
        reference = str(data.get('id') or data.get('reference') or ticket.payment_reference)[:64]
        with transaction.atomic():
            row = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if row.status not in LIVE:
                return out
            ledger.reverse_sale(row, reason)
            row.status = 'refunded'
            row.refunded_at = now
            row.refund_reference = reference
            row.save(update_fields=['status', 'refunded_at', 'refund_reference'])
            TicketTier.objects.filter(pk=row.tier_id, sold__gt=0).update(sold=F('sold') - 1)
        out['outcome'] = 'card'
        out['reference'] = reference
        return out

    # Paid, and no way to send it back by itself. Cancelled and named.
    with transaction.atomic():
        row = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if row.status not in LIVE:
            return out
        ledger.reverse_sale(row, reason)
        row.status = 'cancelled'
        row.refunded_at = now
        row.refund_reference = 'no way back'
        row.save(update_fields=['status', 'refunded_at', 'refund_reference'])
        TicketTier.objects.filter(pk=row.tier_id, sold__gt=0).update(sold=F('sold') - 1)
    out['outcome'] = 'no_way_back'
    return out


def refund_event(event, reason, *, by=None):
    """Refund every live ticket on an event. Returns a summary:

        {'refunded': n, 'wallet': n, 'card': n, 'free': n, 'card_failed': n,
         'no_way_back': n, 'coins': total, 'ngn': str(total),
         'failed': [{'code', 'error'}, ...], 'outcomes': [...]}

    Safe to run again: a ticket already refunded is skipped, so the retry
    door after a gateway refusal asks only about what is still live.
    """
    summary = {'refunded': 0, 'wallet': 0, 'card': 0, 'free': 0, 'card_failed': 0,
               'no_way_back': 0, 'skipped': 0, 'coins': 0, 'ngn': Decimal(0),
               'failed': [], 'outcomes': []}
    live = (Ticket.objects.filter(event=event, status__in=LIVE)
            .select_related('tier', 'user', 'event').order_by('id'))
    for ticket in live:
        out = refund_ticket(ticket, reason, by=by)
        summary['outcomes'].append(out)
        summary[out['outcome']] = summary.get(out['outcome'], 0) + 1
        if out['outcome'] in ('wallet', 'card'):
            summary['refunded'] += 1
            summary['coins'] += out['coins']
            summary['ngn'] += Decimal(out['ngn'])
        elif out['outcome'] == 'card_failed':
            summary['failed'].append({'code': out['code'], 'error': out['error']})
    summary['ngn'] = str(summary['ngn'])
    return summary


def still_owed(event):
    """Live paid tickets on a cancelled event: what the retry door has left."""
    return (Ticket.objects.filter(event=event, status__in=LIVE)
            .filter(price_vc__gt=0) | Ticket.objects.filter(event=event, status__in=LIVE, price_ngn__gt=0)).distinct().count()

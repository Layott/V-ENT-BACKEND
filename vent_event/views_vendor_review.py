"""The organiser's side of the stalls: who is trading, and the yes or no.

"I want to approve each stall before it opens" has been a checkbox on the
pitch form since 7 September 2026, and buying an approved-only pitch answered
"The organiser will approve it shortly." There was no way for the organiser to
do that: nothing listed the stalls at the event, and nothing could move one
out of `pending`. A buyer paid, their stall sat unopenable, and the organiser
had the money (walk, 18 September 2026).

    GET  /event/<event>/stalls/manage/            every stall, every status
    POST /event/<event>/stall/<stall>/decide/     approve, reject, close, reopen

A rejection gives the buyer their coins back. The pitch was paid straight into
the organiser's wallet, so the refund comes straight out of it, and it is
refused rather than half done when the organiser has already spent it: the
stall stays pending and the message says how much is short. Closing a stall
that has been trading refunds nothing, because it has had what it paid for.
"""
from django.db import transaction
from django.db.models import F
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Transaction, UserWallet
from vent_auth.views_notifications import create_notification

from .models import VendorSlot, VendorSlotPurchase
from .views_promos import _actor_for_event, event_by_ref
from .views_tickets import _error, _ok
from .views_vendors import _vendor_by_ref, serialize_vendor

DECISIONS = {
    # decision: (statuses it may be taken from, status it lands on)
    'approve': (('pending',), 'approved'),
    'reject': (('pending',), 'closed'),
    'close': (('approved', 'live'), 'closed'),
    'reopen': (('closed',), 'approved'),
}


def _stall_row(request, stall):
    row = serialize_vendor(request, stall)
    purchase = VendorSlotPurchase.objects.filter(vendor=stall).select_related('slot').first()
    row['owner_email'] = stall.owner.email if stall.owner else None
    row['created_at'] = stall.created_at.isoformat()
    row['orders'] = stall.orders.count()
    row['purchase'] = None if purchase is None else {
        'slot': purchase.slot.name,
        'price_vc': purchase.price_vc,
        'accepted_at': purchase.accepted_at.isoformat() if purchase.accepted_at else None,
        'rules_version': purchase.rules_version_accepted,
        'refunded_at': purchase.refunded_at.isoformat() if purchase.refunded_at else None,
    }
    return row


@api_view(['GET'])
def event_stalls_manage(request, event_id):
    """Every stall at the event, pending and closed included."""
    event = event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    _, err = _actor_for_event(request, event)
    if err is not None:
        return err
    stalls = (event.vendors.select_related('owner')
              .order_by('status', 'name'))
    rows = [_stall_row(request, s) for s in stalls]
    return _ok({
        'stalls': rows,
        'pending': sum(1 for r in rows if r['status'] == 'pending'),
    }, 'OK')


@api_view(['POST'])
def decide_stall(request, event_id, vendor_id):
    """Approve, reject, close or reopen one stall, and set its booth."""
    event = event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    actor, err = _actor_for_event(request, event)
    if err is not None:
        return err
    stall = _vendor_by_ref(vendor_id, event=event)
    if stall is None:
        return _error('Vendor not found for this event.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    decision = (request.data.get('decision') or '').strip().lower()
    booth = request.data.get('booth')
    if not decision and booth is None:
        return _error('Say what to do with the stall.', 'VALIDATION_ERROR',
                      status.HTTP_400_BAD_REQUEST)
    if decision and decision not in DECISIONS:
        return _error('That is not a decision this stall can take.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        stall = type(stall).objects.select_for_update().get(pk=stall.pk)
        fields = []
        if booth is not None:
            stall.booth = (str(booth) or '').strip()[:40]
            fields.append('booth')

        refunded_vc = 0
        if decision:
            allowed_from, lands_on = DECISIONS[decision]
            if stall.status not in allowed_from:
                return _error(
                    'A %s stall cannot be %sed.' % (stall.status, decision.rstrip('e')),
                    'WRONG_STATUS', status.HTTP_409_CONFLICT)
            if decision == 'reopen' and VendorSlotPurchase.objects.filter(
                    vendor=stall, refunded_at__isnull=False).exists():
                # The coins went back. Reopening would give the pitch away.
                return _error('This stall was turned down and refunded. They can '
                              'buy a pitch again if they want one.',
                              'STALL_REFUNDED', status.HTTP_409_CONFLICT)

            if decision == 'reject':
                purchase = (VendorSlotPurchase.objects.select_for_update()
                            .filter(vendor=stall).select_related('slot').first())
                if purchase is not None and purchase.price_vc > 0:
                    refund = purchase.price_vc
                    payer = None
                    if event.creator_id and event.creator_id != purchase.buyer_id:
                        payer = UserWallet.objects.select_for_update().filter(
                            user_id=event.creator_id).first()
                        if payer is None or payer.wallet_balance < refund:
                            held = payer.wallet_balance if payer else 0
                            return _error(
                                'Rejecting refunds %s VC to %s from your wallet, and it '
                                'holds %s VC.' % (refund, stall.owner.username if stall.owner else 'the buyer', held),
                                'CANNOT_REFUND_STALL', status.HTTP_409_CONFLICT,
                                extra={'needed_vc': refund, 'balance_vc': held})
                    buyer = UserWallet.objects.select_for_update().filter(
                        user_id=purchase.buyer_id).first()
                    if buyer is None:
                        return _error('The buyer has no wallet to refund into.',
                                      'NO_WALLET', status.HTTP_409_CONFLICT)
                    if payer is not None:
                        payer.wallet_balance -= refund
                        payer.save(update_fields=['wallet_balance'])
                        Transaction.objects.create(
                            wallet=payer, type='refund', amount=-refund,
                            description='%s at %s refunded' % (purchase.slot.name, event.name),
                            status='completed')
                    buyer.wallet_balance += refund
                    buyer.save(update_fields=['wallet_balance'])
                    Transaction.objects.create(
                        wallet=buyer, type='refund', amount=refund,
                        description='%s at %s refunded' % (purchase.slot.name, event.name),
                        status='completed')
                    refunded_vc = refund
                if purchase is not None:
                    # The pitch goes back on the shelf: it was never used.
                    VendorSlot.objects.filter(id=purchase.slot_id, sold__gt=0).update(
                        sold=F('sold') - 1)
                    from django.utils import timezone as _tz
                    purchase.refunded_at = _tz.now()
                    purchase.save(update_fields=['refunded_at'])

            stall.status = lands_on
            fields.append('status')

        if fields:
            stall.save(update_fields=fields)

    if decision and stall.owner_id:
        link = '/my-stalls/%s' % stall.slug
        if decision == 'approve':
            create_notification(
                stall.owner_id, 'event', '%s is approved' % stall.name,
                body='Your stall at %s is open. Add what you sell.' % event.name,
                link=link)
        elif decision == 'reject':
            create_notification(
                stall.owner_id, 'event', '%s was not approved' % stall.name,
                body=('The organiser of %s did not approve your stall.%s'
                      % (event.name, ' %s VC is back in your wallet.' % refunded_vc if refunded_vc else '')),
                link='/wallets' if refunded_vc else link)
        elif decision == 'close':
            create_notification(
                stall.owner_id, 'event', '%s is closed' % stall.name,
                body='The organiser of %s closed your stall.' % event.name, link=link)
        elif decision == 'reopen':
            create_notification(
                stall.owner_id, 'event', '%s is open again' % stall.name,
                body='The organiser of %s reopened your stall.' % event.name, link=link)

    messages = {
        'approve': '%s is approved and can trade.' % stall.name,
        'reject': ('%s was turned down. %s VC went back to %s.'
                   % (stall.name, refunded_vc, stall.owner.username if stall.owner else 'the buyer')
                   if refunded_vc else '%s was turned down.' % stall.name),
        'close': '%s is closed.' % stall.name,
        'reopen': '%s is open again.' % stall.name,
        '': 'Saved.',
    }
    return _ok({'stall': _stall_row(request, stall), 'refunded_vc': refunded_vc},
               messages[decision])

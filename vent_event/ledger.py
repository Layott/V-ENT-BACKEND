"""Who is owed what, out of every ticket sold.

CEO, 7 September 2026, on the ticketing research: who bears the platform fee,
affiliates that actually get PAID, and a settlement run rather than a
one-at-a-time payout queue.

## What was there before

A ticket sale debited the buyer's wallet and credited nobody. The money left
one account and arrived nowhere: no organiser balance, no platform fee, no
affiliate commission, and nothing to reconcile against afterwards. Every
question in the research reduces to the same missing thing, which is a record
of who is owed what.

## The three rules this is built to

1. **A ledger, not a balance.** Every sale writes the lines it created, and a
   balance is the SUM of unsettled lines. A running total incremented at the
   till drifts the first time a refund lands, an issue runs twice, or a
   settlement half-completes, and once it has drifted there is no way to find
   out by how much. This is the same rule the referral counts are built to, and
   it is the one that matters most where money is concerned.

2. **A line is paid once.** A settlement stamps every line it paid with the run
   that paid it, inside the same transaction that moves the coins. Running a
   settlement twice pays nothing the second time, which is what makes a RUN
   safe where a queue of individual payouts is not: a queue that is retried
   pays twice.

3. **The fee is decided at the sale, not at the settlement.** The platform rate
   can change; what a ticket sold under cannot. The rate and the bearer are
   stamped on the lines when the ticket is bought, so a rate change next month
   never rewrites what an event earned last month.

## Who bears the fee

`Event.fee_bearer` is 'organiser' by default, which is what every event on the
platform has been doing implicitly. Set to 'buyer' it is added on top at
checkout and the buyer is told the number before they pay - never after, and
never as a surprise line on a receipt.

A free ticket carries no fee either way. A 0 VC ticket that quietly costs 1 VC
is the trap this would otherwise walk into, and it would land on exactly the
events least able to absorb it.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import EventLedgerEntry, EventSettlement


def platform_rate():
    """The ticketing fee, as a percentage. 0 unless an admin has set one.

    Read from the platform settings rather than a constant, so it is one
    number an admin can see and change rather than a deploy.
    """
    try:
        from vent_auth.models import AdminSetting
        fees = AdminSetting.load().merged().get('platform_fees') or {}
        return max(0.0, float(fees.get('ticket_fee_pct') or 0))
    except Exception:
        # A settings row that does not exist yet, or a value somebody typed
        # wrongly. Charging nothing is the safe direction to fail in: the
        # alternative is charging an amount nobody chose.
        return 0.0


def fee_on(amount_vc, rate=None):
    """The platform's cut of one amount, in whole VENT COINS.

    Rounded DOWN, deliberately. VENT COINS are whole numbers, and rounding a
    fee up means the platform takes a coin it did not earn on every small
    ticket. Losing a fraction of a coin per sale is the right side to lose on.
    """
    rate = platform_rate() if rate is None else rate
    if not amount_vc or rate <= 0:
        return 0
    return int(Decimal(str(amount_vc)) * Decimal(str(rate)) / Decimal('100'))


def quote(tier, quantity, event=None, buyer=None):
    """What the buyer is asked for and what each party is owed, before any sale.

    The screen showing a price and the code charging one read this same
    function, so the two cannot drift. That is not a hypothetical: the listing
    and the checkout answered different questions about availability once, and
    it read as "sold out" with 4814 tickets left.

    `buyer` is who is being quoted, and it is optional because a guest checkout
    genuinely has nobody. When there IS somebody, a membership of this event's
    organiser can carry a ticket discount, and it is applied HERE rather than
    at the checkout for exactly the reason above: a discount shown by the panel
    and not taken by the charge is the same fault wearing a nicer face.
    """
    event = event or tier.event
    list_ngn = tier.price_for(quantity)

    # A membership discount from whoever runs this event. Read on this request,
    # so a lapsed member is quoted the full price now rather than at the next
    # deploy - which is the whole of gate C1.
    from vent_billing import entitlements as _ent
    member_pct = _ent.ticket_discount_pct(buyer, event) if buyer is not None else 0
    unit_ngn = _ent.discounted(list_ngn, member_pct) if member_pct else list_ngn

    from .views_tickets import _ngn_to_coins
    unit_vc = _ngn_to_coins(unit_ngn)
    list_unit_vc = _ngn_to_coins(list_ngn)
    tickets_vc = unit_vc * quantity

    rate = platform_rate()
    bearer = getattr(event, 'fee_bearer', 'organiser') or 'organiser'
    fee_vc = fee_on(tickets_vc, rate)

    if bearer == 'buyer':
        # Added on top. The organiser keeps the whole ticket price, and the
        # buyer is told the number before they pay.
        return {
            'unit_vc': unit_vc,
            'unit_ngn': unit_ngn,
            'quantity': quantity,
            'tickets_vc': tickets_vc,
            'fee_vc': fee_vc,
            'fee_pct': rate,
            'fee_bearer': 'buyer',
            'total_vc': tickets_vc + fee_vc,
            'organiser_vc': tickets_vc,
            'member_discount_pct': member_pct,
            'member_saving_vc': (list_unit_vc - unit_vc) * quantity,
        }

    # Taken out of what the organiser receives. The buyer pays the ticket
    # price and nothing else, which is what the price on the page said.
    return {
        'unit_vc': unit_vc,
        'unit_ngn': unit_ngn,
        'quantity': quantity,
        'tickets_vc': tickets_vc,
        'fee_vc': fee_vc,
        'fee_pct': rate,
        'fee_bearer': 'organiser',
        'total_vc': tickets_vc,
        'organiser_vc': tickets_vc - fee_vc,
        'member_discount_pct': member_pct,
        'member_saving_vc': (list_unit_vc - unit_vc) * quantity,
    }


def record_sale(event, tickets, priced, referral=None):
    """Write the lines one purchase created. Returns them.

    Called inside the purchase transaction, so a sale and its ledger either
    both exist or neither does. A ticket with no ledger line is money that
    arrived and is owed to nobody, which is the failure that is invisible until
    somebody asks where their takings are.
    """
    if not tickets:
        return []

    gross = priced['tickets_vc']
    fee = priced['fee_vc']
    rate = priced['fee_pct']
    bearer = priced['fee_bearer']
    first = tickets[0]

    lines = []

    commission = 0
    if referral is not None and getattr(referral, 'commission_pct', 0):
        # Of the ticket price, never of the buyer's total: an affiliate did not
        # earn a share of the platform's fee, and paying them one would mean an
        # event with the fee passed to the buyer quietly pays its affiliates
        # more than the same event without.
        commission = int(Decimal(str(gross))
                         * Decimal(str(referral.commission_pct))
                         / Decimal('100'))

    # The organiser's line is what is left after both. Written as one number
    # rather than a gross line and two negatives, because the question an
    # organiser asks is "what am I owed", and an answer they have to add up
    # is an answer they will get wrong.
    payable = gross - (fee if bearer == 'organiser' else 0) - commission

    lines.append(EventLedgerEntry.objects.create(
        event=event, kind=EventLedgerEntry.KIND_ORGANISER,
        user=event.creator, amount_vc=payable,
        gross_vc=gross, fee_vc=fee, fee_pct=rate, fee_bearer=bearer,
        ticket=first, quantity=len(tickets)))

    if fee:
        lines.append(EventLedgerEntry.objects.create(
            event=event, kind=EventLedgerEntry.KIND_PLATFORM,
            user=None, amount_vc=fee,
            gross_vc=gross, fee_vc=fee, fee_pct=rate, fee_bearer=bearer,
            ticket=first, quantity=len(tickets),
            # The platform is not paid through anybody's wallet, so its lines
            # are settled the moment they are written. Leaving them open would
            # make every settlement report a debt to ourselves.
            settled_at=None))

    if commission:
        lines.append(EventLedgerEntry.objects.create(
            event=event, kind=EventLedgerEntry.KIND_AFFILIATE,
            # None when the link is addressed to somebody who has not signed
            # up yet. The line still exists and still counts - see
            # `claim_pending`, which attaches both the link and its open lines
            # the moment that person arrives.
            user=getattr(referral, 'payee', None), amount_vc=commission,
            gross_vc=gross, fee_vc=fee, fee_pct=rate, fee_bearer=bearer,
            ticket=first, quantity=len(tickets), referral=referral))

    return lines


def reverse_sale(ticket, reason=''):
    """Undo the lines one ticket created, when it is refunded or cancelled.

    A reversal is a NEW line with the opposite sign, never an edit to the
    original. Editing a settled line would rewrite a payment that has already
    been made, and editing an unsettled one would erase the fact that a sale
    happened at all.
    """
    originals = EventLedgerEntry.objects.filter(ticket=ticket).exclude(
        kind=EventLedgerEntry.KIND_REVERSAL)
    out = []
    for line in originals:
        if line.reversed_by_id:
            continue
        reversal = EventLedgerEntry.objects.create(
            event=line.event, kind=EventLedgerEntry.KIND_REVERSAL,
            user=line.user, amount_vc=-line.amount_vc,
            gross_vc=-line.gross_vc, fee_vc=-line.fee_vc,
            fee_pct=line.fee_pct, fee_bearer=line.fee_bearer,
            ticket=ticket, quantity=line.quantity,
            referral=line.referral, note=reason[:200],
            reverses=line)
        EventLedgerEntry.objects.filter(pk=line.pk).update(reversed_by=reversal)
        out.append(reversal)
    return out


def balances(event):
    """What each party is owed on this event, and what has already been paid.

    Summed from the lines. Nothing here reads a stored total, because a stored
    total is the thing that drifts.
    """
    rows = (EventLedgerEntry.objects.filter(event=event)
            .values('kind', 'user_id', 'referral_id')
            .annotate(owed=Sum('amount_vc')))

    def total(kind, settled):
        q = EventLedgerEntry.objects.filter(event=event)
        # A reversal carries its own kind, so it has to be matched against the
        # kind it reverses rather than counted on its own.
        q = q.filter(kind=kind) | q.filter(kind=EventLedgerEntry.KIND_REVERSAL,
                                           reverses__kind=kind)
        q = q.filter(settled_at__isnull=not settled)
        return q.aggregate(n=Sum('amount_vc'))['n'] or 0

    affiliates = []
    for row in rows:
        if row['kind'] != EventLedgerEntry.KIND_AFFILIATE or not row['referral_id']:
            continue
        affiliates.append({'referral_id': row['referral_id'],
                           'owed_vc': row['owed'] or 0})

    return {
        'organiser_owed_vc': total(EventLedgerEntry.KIND_ORGANISER, False),
        'organiser_paid_vc': total(EventLedgerEntry.KIND_ORGANISER, True),
        'affiliates_owed_vc': total(EventLedgerEntry.KIND_AFFILIATE, False),
        'affiliates_paid_vc': total(EventLedgerEntry.KIND_AFFILIATE, True),
        'platform_fee_vc': total(EventLedgerEntry.KIND_PLATFORM, False)
                           + total(EventLedgerEntry.KIND_PLATFORM, True),
        'affiliates': affiliates,
    }


def settle(event, run_by=None, note=''):
    """Pay everybody who is owed anything on this event, once, in one pass.

    This is the whole difference between a run and a queue. A queue is a list of
    payments somebody works through, and the second time somebody works through
    it they pay twice. A run stamps each line with the run that paid it, in the
    same transaction that moves the coins, so running it again pays nothing.

    Returns the settlement, whose `lines_paid` is 0 when there was nothing to
    do. That is a real answer and not an error: pressing Settle on an event
    that is already settled should say so, not fail.
    """
    from vent_auth.models import Transaction, UserWallet

    with transaction.atomic():
        run = EventSettlement.objects.create(event=event, run_by=run_by,
                                             note=note[:200])

        open_lines = list(EventLedgerEntry.objects
                          .select_for_update()
                          .filter(event=event, settled_at__isnull=True)
                          .exclude(kind=EventLedgerEntry.KIND_PLATFORM))

        # Grouped by person, so somebody owed on four sales gets one payment
        # and one line on their statement rather than four.
        by_user = {}
        for line in open_lines:
            if line.user_id is None:
                # An affiliate with nobody to pay: a tracking link whose owner
                # has not claimed an account. The line stays open rather than
                # being written off, so it is paid the day they claim it.
                continue
            by_user.setdefault(line.user_id, []).append(line)

        paid_total = 0
        paid_lines = 0
        for user_id, lines in by_user.items():
            amount = sum(line.amount_vc for line in lines)
            ids = [line.pk for line in lines]
            if amount <= 0:
                # Refunds outweighed sales for this person on this event. There
                # is nothing to pay, and clawing coins back out of somebody's
                # wallet is not something a settlement run may do on its own.
                # The lines are left open so the balance stays visible.
                continue

            wallet = UserWallet.objects.select_for_update().filter(
                user_id=user_id).first()
            if wallet is None:
                continue
            wallet.wallet_balance += amount
            wallet.save(update_fields=['wallet_balance'])
            Transaction.objects.create(
                wallet=wallet, type='prize', amount=amount,
                description='Settlement - %s' % event.name,
                status='completed')

            # Stamped inside the same transaction as the coins moving. This is
            # what makes a second run pay nothing.
            EventLedgerEntry.objects.filter(pk__in=ids).update(
                settled_at=run.created_at, settlement=run)
            paid_total += amount
            paid_lines += len(ids)

        run.amount_vc = paid_total
        run.lines_paid = paid_lines
        run.save(update_fields=['amount_vc', 'lines_paid'])
        return run

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

## The fee, and the unit it is kept in

CEO, 12 September 2026: "V-ent takes 5% + N100 of all tickets sold." Five per
cent of the ticket price plus a flat 100 naira, per paid ticket, both admin
settings with those defaults and both stamped on the line at the sale.

One coin is 1,000 naira, so that fee on a 2,000 naira ticket is 200 naira and
not a coin. The ledger held whole coins until this day, and the fee on every
ticket under 20,000 naira came to 0: the platform took nothing on 60 coins of
sales while the Money tab said it took 10 per cent. So the line holds NAIRA
(`amount_ngn`, exact) and coins are what a wallet receives: a settlement pays
each person the whole coins their naira has reached and carries the rest as
an open line for the next run. Nothing under a coin is lost; it waits.

A wallet buyer cannot carry the fee either, for the same reason: 2,200 naira
is not a whole number of coins. With the fee on the buyer, a guest paying
naira at Paystack pays price plus fee exactly; a wallet buyer pays the price
in coins and the fee comes out of the organiser's share for that sale. The
quote says which (`buyer_pays_fee`), so the panel and the console can too.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q, Sum

from .models import EventLedgerEntry, EventSettlement

CENT = Decimal('0.01')


def _ngn(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def ngn_per_coin():
    from vent_auth.views_wallet import NGN_PER_COIN
    return Decimal(str(NGN_PER_COIN))


def _floor_vc(amount_ngn):
    """The whole coins an amount of naira has reached. Down, never up: a coin
    the platform or a person did not earn is not theirs. Negative amounts
    (reversals) floor towards zero so a refund never over-claws."""
    amount = Decimal(str(amount_ngn or 0))
    unit = ngn_per_coin()
    whole = int(abs(amount) // unit)
    return -whole if amount < 0 else whole


def platform_fee():
    """The ticketing fee as `(pct, flat_ngn)`: a percentage of the ticket price
    plus a flat amount per ticket. 5 and 100 unless an admin has changed them.

    Read from the platform settings rather than a constant, so it is one
    number an admin can see and change rather than a deploy.
    """
    try:
        from vent_auth.models import AdminSetting, DEFAULT_ADMIN_SETTINGS
        defaults = DEFAULT_ADMIN_SETTINGS['platform_fees']
        fees = AdminSetting.load().merged().get('platform_fees') or {}
        pct = fees.get('ticket_fee_pct')
        flat = fees.get('ticket_fee_flat_ngn')
        pct = defaults['ticket_fee_pct'] if pct is None else pct
        flat = defaults['ticket_fee_flat_ngn'] if flat is None else flat
        return max(0.0, float(pct)), max(Decimal('0'), _ngn(flat))
    except Exception:
        # A settings row that does not exist yet, or a value somebody typed
        # wrongly. Charging nothing is the safe direction to fail in: the
        # alternative is charging an amount nobody chose.
        return 0.0, Decimal('0')


def platform_rate():
    """The percentage half of the fee, for callers that only state the rate."""
    return platform_fee()[0]


def fee_for(unit_ngn, quantity, pct=None, flat=None):
    """The platform's fee on `quantity` tickets at `unit_ngn` each, in naira,
    exact: pct of the price plus the flat amount, per PAID ticket. A free
    ticket carries none, whatever the flat amount says."""
    if pct is None or flat is None:
        p, f = platform_fee()
        pct = p if pct is None else pct
        flat = f if flat is None else flat
    unit = _ngn(unit_ngn)
    if unit <= 0 or quantity <= 0:
        return Decimal('0')
    per_ticket = unit * Decimal(str(pct)) / Decimal('100') + _ngn(flat)
    return _ngn(per_ticket * quantity)


def fee_on(amount_vc, rate=None):
    """The percentage part of the fee on an amount of whole coins, in whole
    coins, rounded down. Kept for the callers that reason in coins (the
    memberships module states its rate this way); ticket sales use `fee_for`
    and keep naira.
    """
    rate = platform_rate() if rate is None else rate
    if not amount_vc or rate <= 0:
        return 0
    return int(Decimal(str(amount_vc)) * Decimal(str(rate)) / Decimal('100'))


def quote(tier, quantity, event=None, buyer=None, channel='wallet'):
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

    `channel` is how the money arrives: 'wallet' (whole coins) or 'naira'
    (a card at Paystack). It decides whether a fee the event puts on the buyer
    can actually be charged to them.
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
    tickets_ngn = _ngn(unit_ngn) * quantity

    pct, flat = platform_fee()
    fee_ngn = fee_for(unit_ngn, quantity, pct, flat)
    bearer = getattr(event, 'fee_bearer', 'organiser') or 'organiser'
    # A coin cannot carry 200 naira. With the fee on the buyer, only a naira
    # checkout can add it to what they pay; a wallet buyer pays the price and
    # the fee comes out of the organiser's share for that sale.
    buyer_pays_fee = bearer == 'buyer' and channel == 'naira' and fee_ngn > 0

    out = {
        'unit_vc': unit_vc,
        'unit_ngn': unit_ngn,
        'quantity': quantity,
        'tickets_vc': tickets_vc,
        'tickets_ngn': tickets_ngn,
        'fee_ngn': fee_ngn,
        # The fee in whole coins, for a screen that renders coins. It is 0 on
        # every ticket under twenty coins; the naira above is the real number.
        'fee_vc': _floor_vc(fee_ngn),
        'fee_pct': pct,
        'fee_flat_ngn': flat,
        'fee_bearer': bearer,
        'buyer_pays_fee': buyer_pays_fee,
        'channel': channel,
        'member_discount_pct': member_pct,
        'member_saving_vc': (list_unit_vc - unit_vc) * quantity,
    }
    if buyer_pays_fee:
        # Added on top, in naira, at the card. The organiser keeps the whole
        # ticket price, and the buyer is told the number before they pay.
        out.update({
            'total_ngn': tickets_ngn + fee_ngn,
            'total_vc': tickets_vc,
            'organiser_ngn': tickets_ngn,
            'organiser_vc': tickets_vc,
        })
    else:
        # Taken out of what the organiser receives. The buyer pays the ticket
        # price and nothing else, which is what the price on the page said.
        out.update({
            'total_ngn': tickets_ngn,
            'total_vc': tickets_vc,
            'organiser_ngn': tickets_ngn - fee_ngn,
            'organiser_vc': _floor_vc(tickets_ngn - fee_ngn),
        })
    return out


def _line(event, kind, amount_ngn, priced, first, count, user=None, referral=None):
    return EventLedgerEntry.objects.create(
        event=event, kind=kind, user=user, referral=referral,
        amount_ngn=_ngn(amount_ngn), amount_vc=_floor_vc(amount_ngn),
        gross_ngn=_ngn(priced['tickets_ngn']), gross_vc=priced['tickets_vc'],
        fee_ngn=_ngn(priced['fee_ngn']), fee_vc=priced['fee_vc'],
        fee_pct=priced['fee_pct'], fee_flat_ngn=_ngn(priced.get('fee_flat_ngn', 0)),
        fee_bearer='buyer' if priced.get('buyer_pays_fee') else 'organiser',
        ticket=first, quantity=count)


def record_sale(event, tickets, priced, referral=None):
    """Write the lines one purchase created. Returns them.

    Called inside the purchase transaction, so a sale and its ledger either
    both exist or neither does. A ticket with no ledger line is money that
    arrived and is owed to nobody, which is the failure that is invisible until
    somebody asks where their takings are.
    """
    if not tickets:
        return []

    gross = _ngn(priced['tickets_ngn'])
    fee = _ngn(priced['fee_ngn'])
    first = tickets[0]
    count = len(tickets)
    lines = []

    commission = Decimal('0')
    if referral is not None and getattr(referral, 'commission_pct', 0):
        # Of the ticket price, never of the buyer's total: an affiliate did not
        # earn a share of the platform's fee, and paying them one would mean an
        # event with the fee passed to the buyer quietly pays its affiliates
        # more than the same event without. In naira, exact: 10 per cent of a
        # 3,000 naira ticket is 300 naira, which used to floor to 0 coins.
        commission = _ngn(gross * Decimal(str(referral.commission_pct)) / Decimal('100'))

    # The organiser's line is what is left after both. Written as one number
    # rather than a gross line and two negatives, because the question an
    # organiser asks is "what am I owed", and an answer they have to add up
    # is an answer they will get wrong.
    payable = gross - (Decimal('0') if priced.get('buyer_pays_fee') else fee) - commission
    lines.append(_line(event, EventLedgerEntry.KIND_ORGANISER, payable, priced,
                       first, count, user=event.creator))

    if fee:
        # The platform is not paid through anybody's wallet, so its lines are
        # never settled; they are the record of what was taken.
        lines.append(_line(event, EventLedgerEntry.KIND_PLATFORM, fee, priced,
                           first, count, user=None))

    if commission:
        # None when the link is addressed to somebody who has not signed up
        # yet. The line still exists and still counts - see `claim_pending`,
        # which attaches both the link and its open lines the moment that
        # person arrives.
        lines.append(_line(event, EventLedgerEntry.KIND_AFFILIATE, commission, priced,
                           first, count, user=getattr(referral, 'payee', None),
                           referral=referral))

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
            user=line.user,
            amount_ngn=-line.amount_ngn, amount_vc=-line.amount_vc,
            gross_ngn=-line.gross_ngn, gross_vc=-line.gross_vc,
            fee_ngn=-line.fee_ngn, fee_vc=-line.fee_vc,
            fee_pct=line.fee_pct, fee_flat_ngn=line.fee_flat_ngn,
            fee_bearer=line.fee_bearer,
            ticket=ticket, quantity=line.quantity,
            referral=line.referral, note=reason[:200],
            reverses=line)
        EventLedgerEntry.objects.filter(pk=line.pk).update(reversed_by=reversal)
        out.append(reversal)
    return out


def balances(event):
    """What each party is owed on this event, and what has already been paid.

    Summed from the lines, in naira. Nothing here reads a stored total,
    because a stored total is the thing that drifts. Each figure comes with
    its whole-coin floor beside it, for the screens that render coins.
    """
    def total(kind, settled=None):
        q = EventLedgerEntry.objects.filter(event=event)
        # A reversal carries its own kind, so it has to be matched against the
        # kind it reverses rather than counted on its own.
        q = q.filter(kind=kind) | q.filter(kind=EventLedgerEntry.KIND_REVERSAL,
                                           reverses__kind=kind)
        if settled is not None:
            q = q.filter(settled_at__isnull=not settled)
        return _ngn(q.aggregate(n=Sum('amount_ngn'))['n'] or 0)

    affiliates = []
    rows = (EventLedgerEntry.objects.filter(event=event, referral__isnull=False)
            .filter(Q(kind=EventLedgerEntry.KIND_AFFILIATE)
                    | Q(kind=EventLedgerEntry.KIND_REVERSAL,
                        reverses__kind=EventLedgerEntry.KIND_AFFILIATE))
            .filter(settled_at__isnull=True)
            .values('referral_id').annotate(owed=Sum('amount_ngn')))
    for row in rows:
        owed = _ngn(row['owed'] or 0)
        affiliates.append({'referral_id': row['referral_id'],
                           'owed_ngn': owed, 'owed_vc': _floor_vc(owed)})

    figures = {
        'organiser_owed_ngn': total(EventLedgerEntry.KIND_ORGANISER, False),
        'organiser_paid_ngn': total(EventLedgerEntry.KIND_ORGANISER, True),
        'affiliates_owed_ngn': total(EventLedgerEntry.KIND_AFFILIATE, False),
        'affiliates_paid_ngn': total(EventLedgerEntry.KIND_AFFILIATE, True),
        'platform_fee_ngn': total(EventLedgerEntry.KIND_PLATFORM),
        'affiliates': affiliates,
    }
    for key in list(figures):
        if key.endswith('_ngn'):
            figures[key[:-4] + '_vc'] = _floor_vc(figures[key])
    return figures


def settle(event, run_by=None, note=''):
    """Pay everybody who is owed anything on this event, once, in one pass.

    This is the whole difference between a run and a queue. A queue is a list of
    payments somebody works through, and the second time somebody works through
    it they pay twice. A run stamps each line with the run that paid it, in the
    same transaction that moves the coins, so running it again pays nothing.

    A wallet holds whole coins and a line holds naira, so each person is paid
    the whole coins their naira has reached and the remainder is written back
    as one open line for the next run. 1,800 naira owed pays 1 coin and
    carries 800; the next 1,800 pays 2 coins and carries 600. Nothing under a
    coin is written off, and nothing is paid before it has been earned.

    Returns the settlement, whose `lines_paid` is 0 when there was nothing to
    do. That is a real answer and not an error: pressing Settle on an event
    that is already settled should say so, not fail.
    """
    from vent_auth.models import Transaction, UserWallet

    unit = ngn_per_coin()
    with transaction.atomic():
        run = EventSettlement.objects.create(event=event, run_by=run_by,
                                             note=note[:200])

        open_lines = list(EventLedgerEntry.objects
                          .select_for_update()
                          .filter(event=event, settled_at__isnull=True)
                          .exclude(kind=EventLedgerEntry.KIND_PLATFORM))

        # Grouped by person AND by what the money is (their own takings, or
        # a link's commission), so somebody owed on four sales gets one
        # payment and one line on their statement rather than four, and the
        # carry written back keeps the kind it came from.
        groups = {}
        for line in open_lines:
            if line.user_id is None:
                # An affiliate with nobody to pay: a tracking link whose owner
                # has not claimed an account. The line stays open rather than
                # being written off, so it is paid the day they claim it.
                continue
            kind = line.kind
            if kind == EventLedgerEntry.KIND_REVERSAL and line.reverses_id:
                kind = line.reverses.kind
            groups.setdefault((line.user_id, kind, line.referral_id), []).append(line)

        paid_total = 0
        paid_lines = 0
        for (user_id, kind, referral_id), lines in groups.items():
            owed = _ngn(sum((line.amount_ngn for line in lines), Decimal('0')))
            coins = _floor_vc(owed)
            if coins <= 0:
                # Under a coin, or refunds outweighed sales for this person on
                # this event. Nothing to pay yet, and clawing coins back out
                # of somebody's wallet is not something a settlement run may
                # do on its own. The lines are left open so the balance stays
                # visible and is paid the day it reaches a coin.
                continue

            wallet = UserWallet.objects.select_for_update().filter(
                user_id=user_id).first()
            if wallet is None:
                continue
            wallet.wallet_balance += coins
            wallet.save(update_fields=['wallet_balance'])
            Transaction.objects.create(
                wallet=wallet, type='prize', amount=coins,
                description='Settlement - %s' % event.name,
                status='completed')

            # Stamped inside the same transaction as the coins moving. This is
            # what makes a second run pay nothing.
            ids = [line.pk for line in lines]
            EventLedgerEntry.objects.filter(pk__in=ids).update(
                settled_at=run.created_at, settlement=run)
            paid_total += coins
            paid_lines += len(ids)

            remainder = owed - Decimal(coins) * unit
            if remainder > 0:
                # The part of the naira that is not yet a coin. Two lines, so
                # the books balance either way you read them: a settled line
                # taking the remainder OUT of this run (what was paid now sums
                # to exactly the coins that moved) and an open line carrying
                # it to the next run, which pays it once it is a coin.
                EventLedgerEntry.objects.create(
                    event=event, kind=kind, user_id=user_id,
                    referral_id=referral_id,
                    amount_ngn=-remainder, amount_vc=0, quantity=0,
                    note='Carried to the next payout',
                    settled_at=run.created_at, settlement=run)
                EventLedgerEntry.objects.create(
                    event=event, kind=kind, user_id=user_id,
                    referral_id=referral_id,
                    amount_ngn=remainder, amount_vc=0,
                    quantity=0, note='Carried from settlement %d' % run.id)

        run.amount_vc = paid_total
        run.lines_paid = paid_lines
        run.save(update_fields=['amount_vc', 'lines_paid'])
        return run

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

A wallet cannot carry all of the fee either, for the same reason: 2,200 naira
is not a whole number of coins. So, with the fee on the buyer (CEO, 13
September: "the organizer decides if they want to handle the cost or they want
people buying the tickets to, same for vendors"): a card payment adds the
whole fee; a wallet payment adds the WHOLE COINS of it, told to the buyer
before they pay, and the part under a coin comes off the seller. `split_fee`
is that rule, and tickets and stalls both go through it.

## Tournaments are the same ledger

CEO, 13 September 2026, asked whether tournaments should pay organisers a
share of entry fees: "i want it". Until then an entry fee left the player's
wallet and reached nobody, and prizes were minted to winners out of nothing at
distribution. Both move together, because handing entries to organisers while
the platform kept minting prizes would pay every prize twice:

- a paid entry writes an organiser line and a platform line, exactly as a
  ticket does, with `tournament_fee_pct` + `tournament_fee_flat_ngn` stamped;
- a prize is a negative organiser line, so it comes out of what the entries
  brought in; when the pool cannot cover the prizes, the organiser's own
  wallet is debited the shortfall in whole coins first (a positive line);
- the organiser is paid what is left by the same `settle()` run as an event.

A line names its event or its tournament; every function below takes either.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q, Sum

from .models import EventLedgerEntry, EventSettlement

CENT = Decimal('0.01')


def _is_tournament(record):
    return record.__class__.__name__ == 'Tournament'


def _scope(record):
    """The filter/create kwargs naming whose ledger this is."""
    return {'tournament': record} if _is_tournament(record) else {'event': record}


def owner_of(record):
    """Whose takings these are: the event's creator or the tournament's."""
    return record.tournament_creator if _is_tournament(record) else record.creator


def name_of(record):
    return record.tournament_title if _is_tournament(record) else record.name


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


def tournament_fee():
    """The fee on a tournament entry as `(pct, flat_ngn)`, from the dashboard.

    Its own two keys, beside the ticket ones: a ticket and an entry are not
    the same sale and an admin may want them priced apart. Same shape, same
    stamping, same settlement.
    """
    try:
        from vent_auth.models import AdminSetting, DEFAULT_ADMIN_SETTINGS
        defaults = DEFAULT_ADMIN_SETTINGS['platform_fees']
        fees = AdminSetting.load().merged().get('platform_fees') or {}
        pct = fees.get('tournament_fee_pct')
        flat = fees.get('tournament_fee_flat_ngn')
        pct = defaults['tournament_fee_pct'] if pct is None else pct
        flat = defaults['tournament_fee_flat_ngn'] if flat is None else flat
        return max(Decimal('0'), _ngn(pct)), max(Decimal('0'), _ngn(flat))
    except Exception:
        return Decimal('0'), Decimal('0')


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


def split_fee(fee_ngn, bearer_pays, channel):
    """Who pays how much of a fee, as `(buyer_ngn, seller_ngn, buyer_vc)`.

    `bearer_pays` is whether the seller put the fee on the buyer. At a card
    (`channel='naira'`) the buyer then pays all of it. From a wallet the buyer
    pays the whole coins of it, on top of the price, and the seller absorbs
    what is left under a coin: the buyer is told a whole number before they
    pay and the platform still takes its exact fee.
    """
    fee = _ngn(fee_ngn)
    if not bearer_pays or fee <= 0:
        return Decimal('0.00'), fee, 0
    if channel == 'naira':
        return fee, Decimal('0.00'), 0
    coins = _floor_vc(fee)
    buyer = _ngn(Decimal(coins) * ngn_per_coin())
    return buyer, _ngn(fee - buyer), coins


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
    buyer_fee_ngn, seller_fee_ngn, buyer_fee_vc = split_fee(fee_ngn, bearer == 'buyer', channel)
    organiser_ngn = tickets_ngn - seller_fee_ngn

    return {
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
        'channel': channel,
        # What the buyer pays of the fee, on top of the price, and what the
        # organiser absorbs. At a card the buyer pays all of it; from a wallet
        # the whole coins of it; none when the organiser absorbs it.
        'buyer_fee_ngn': buyer_fee_ngn,
        'buyer_fee_vc': buyer_fee_vc,
        'seller_fee_ngn': seller_fee_ngn,
        'buyer_pays_fee': buyer_fee_ngn > 0,
        'total_ngn': tickets_ngn + buyer_fee_ngn,
        'total_vc': tickets_vc + buyer_fee_vc,
        'organiser_ngn': organiser_ngn,
        'organiser_vc': _floor_vc(organiser_ngn),
        'member_discount_pct': member_pct,
        'member_saving_vc': (list_unit_vc - unit_vc) * quantity,
    }


def quote_entry(tournament, channel='wallet'):
    """What a player is asked for to enter, and what each party is owed.

    The same shape `quote()` gives a ticket, so the screens and the ledger
    lines read one dictionary. Quantity is always 1: one registration, one
    entry. A free tournament quotes zeros and carries no fee.
    """
    unit = ngn_per_coin()
    entry_vc = int(tournament.entry_fee_price or 0) if tournament.entry_fee == 'Paid' else 0
    entry_ngn = _ngn(Decimal(entry_vc) * unit)
    pct, flat = tournament_fee()
    fee_ngn = fee_for(entry_ngn, 1, pct, flat)
    bearer = getattr(tournament, 'fee_bearer', 'organiser') or 'organiser'
    buyer_fee_ngn, seller_fee_ngn, buyer_fee_vc = split_fee(fee_ngn, bearer == 'player', channel)
    organiser_ngn = entry_ngn - seller_fee_ngn
    return {
        'unit_vc': entry_vc,
        'unit_ngn': entry_ngn,
        'quantity': 1,
        'tickets_vc': entry_vc,
        'tickets_ngn': entry_ngn,
        'fee_ngn': fee_ngn,
        'fee_vc': _floor_vc(fee_ngn),
        'fee_pct': pct,
        'fee_flat_ngn': flat,
        'fee_bearer': bearer,
        'channel': channel,
        'buyer_fee_ngn': buyer_fee_ngn,
        'buyer_fee_vc': buyer_fee_vc,
        'seller_fee_ngn': seller_fee_ngn,
        'buyer_pays_fee': buyer_fee_ngn > 0,
        'total_ngn': entry_ngn + buyer_fee_ngn,
        'total_vc': entry_vc + buyer_fee_vc,
        'organiser_ngn': organiser_ngn,
        'organiser_vc': _floor_vc(organiser_ngn),
    }


def _line(record, kind, amount_ngn, priced, first, count, user=None, referral=None,
          registration=None):
    return EventLedgerEntry.objects.create(
        kind=kind, user=user, referral=referral,
        amount_ngn=_ngn(amount_ngn), amount_vc=_floor_vc(amount_ngn),
        gross_ngn=_ngn(priced['tickets_ngn']), gross_vc=priced['tickets_vc'],
        fee_ngn=_ngn(priced['fee_ngn']), fee_vc=priced['fee_vc'],
        fee_pct=priced['fee_pct'], fee_flat_ngn=_ngn(priced.get('fee_flat_ngn', 0)),
        fee_bearer=priced.get('fee_bearer', 'organiser'),
        buyer_fee_ngn=_ngn(priced.get('buyer_fee_ngn', 0)),
        ticket=first, registration=registration, quantity=count, **_scope(record))


def record_entry(registration, priced):
    """Write the lines one paid entry created: the organiser's take and the
    platform's fee. Called inside the registration transaction. A free entry
    writes nothing, because nothing is owed."""
    tournament = registration.tournament
    gross = _ngn(priced['tickets_ngn'])
    if gross <= 0:
        return []
    fee = _ngn(priced['fee_ngn'])
    lines = [_line(tournament, EventLedgerEntry.KIND_ORGANISER,
                   gross - _ngn(priced.get('seller_fee_ngn', fee)), priced,
                   None, 1, user=tournament.tournament_creator,
                   registration=registration)]
    if fee:
        lines.append(_line(tournament, EventLedgerEntry.KIND_PLATFORM, fee, priced,
                           None, 1, user=None, registration=registration))
    return lines


def _reverse_lines(originals, reason):
    out = []
    for line in originals:
        if line.reversed_by_id:
            continue
        reversal = EventLedgerEntry.objects.create(
            event=line.event, tournament=line.tournament,
            kind=EventLedgerEntry.KIND_REVERSAL, user=line.user,
            amount_ngn=-line.amount_ngn, amount_vc=-line.amount_vc,
            gross_ngn=-line.gross_ngn, gross_vc=-line.gross_vc,
            fee_ngn=-line.fee_ngn, fee_vc=-line.fee_vc,
            fee_pct=line.fee_pct, fee_flat_ngn=line.fee_flat_ngn,
            fee_bearer=line.fee_bearer, buyer_fee_ngn=-line.buyer_fee_ngn,
            ticket=line.ticket, registration=line.registration,
            quantity=line.quantity,
            referral=line.referral, note=reason[:200],
            reverses=line)
        EventLedgerEntry.objects.filter(pk=line.pk).update(reversed_by=reversal)
        out.append(reversal)
    return out


def reverse_entry(registration, reason=''):
    """Undo the lines one entry created. Returns `(reversals, refund_vc)`:
    what the player actually paid for it, the entry plus whatever of the fee
    they bore, in whole coins. None when this entry wrote no line (an entry
    from before the ledger, or a free one)."""
    originals = list(EventLedgerEntry.objects.filter(registration=registration)
                     .exclude(kind=EventLedgerEntry.KIND_REVERSAL))
    paid = None
    for line in originals:
        if line.kind == EventLedgerEntry.KIND_ORGANISER and not line.reversed_by_id:
            paid = line.gross_vc + _floor_vc(line.buyer_fee_ngn)
    return _reverse_lines(originals, reason), paid


def record_prize(tournament, payout, amount_vc):
    """A prize paid out, as a negative organiser line: it comes out of the
    pool the entries built. Stamped with the payout row it paid."""
    ngn = _ngn(Decimal(int(amount_vc)) * ngn_per_coin())
    return EventLedgerEntry.objects.create(
        tournament=tournament, kind=EventLedgerEntry.KIND_ORGANISER,
        user=tournament.tournament_creator, prize=payout,
        amount_ngn=-ngn, amount_vc=-int(amount_vc), quantity=0,
        note='Prize - position %d' % payout.position)


PRIZE_TOPUP_NOTE = 'Prize top-up from your wallet'


def record_prize_topup(tournament, coins):
    """Coins the organiser put in from their own wallet to cover prizes the
    entries did not, as a positive organiser line."""
    ngn = _ngn(Decimal(int(coins)) * ngn_per_coin())
    return EventLedgerEntry.objects.create(
        tournament=tournament, kind=EventLedgerEntry.KIND_ORGANISER,
        user=tournament.tournament_creator,
        amount_ngn=ngn, amount_vc=int(coins), quantity=0, note=PRIZE_TOPUP_NOTE)


def pool_ngn(tournament):
    """What the organiser is owed on this tournament right now, in naira:
    entries less the fee, less prizes already paid, plus any top-up."""
    return balances(tournament)['organiser_owed_ngn']


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
    payable = gross - _ngn(priced.get('seller_fee_ngn', fee)) - commission
    lines.append(_line(event, EventLedgerEntry.KIND_ORGANISER, payable, priced,
                       first, count, user=owner_of(event)))

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
    return _reverse_lines(originals, reason)


def balances(record):
    """What each party is owed on this event or tournament, and what has
    already been paid.

    Summed from the lines, in naira. Nothing here reads a stored total,
    because a stored total is the thing that drifts. Each figure comes with
    its whole-coin floor beside it, for the screens that render coins.
    """
    scope = _scope(record)

    def total(kind, settled=None):
        q = EventLedgerEntry.objects.filter(**scope)
        # A reversal carries its own kind, so it has to be matched against the
        # kind it reverses rather than counted on its own.
        q = q.filter(kind=kind) | q.filter(kind=EventLedgerEntry.KIND_REVERSAL,
                                           reverses__kind=kind)
        if settled is not None:
            q = q.filter(settled_at__isnull=not settled)
        return _ngn(q.aggregate(n=Sum('amount_ngn'))['n'] or 0)

    affiliates = []
    rows = (EventLedgerEntry.objects.filter(referral__isnull=False, **scope)
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


def settle(record, run_by=None, note=''):
    """Pay everybody who is owed anything on this event or tournament, once,
    in one pass.

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
    scope = _scope(record)
    with transaction.atomic():
        run = EventSettlement.objects.create(run_by=run_by, note=note[:200], **scope)

        open_lines = list(EventLedgerEntry.objects
                          .select_for_update()
                          .filter(settled_at__isnull=True, **scope)
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
                description='Settlement - %s' % name_of(record),
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
                    kind=kind, user_id=user_id,
                    referral_id=referral_id,
                    amount_ngn=-remainder, amount_vc=0, quantity=0,
                    note='Carried to the next payout',
                    settled_at=run.created_at, settlement=run, **scope)
                EventLedgerEntry.objects.create(
                    kind=kind, user_id=user_id,
                    referral_id=referral_id,
                    amount_ngn=remainder, amount_vc=0,
                    quantity=0, note='Carried from settlement %d' % run.id, **scope)

        run.amount_vc = paid_total
        run.lines_paid = paid_lines
        run.save(update_fields=['amount_vc', 'lines_paid'])
        return run

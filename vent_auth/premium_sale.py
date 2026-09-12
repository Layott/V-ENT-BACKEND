"""Buying premium, without asking anybody.

CEO, 10 September 2026, reading the refusal on the entry requirement picker:

    "They shouldnt be requesting a vent admin to turn on anything."

The sentence they were reading was "Ask a V-ENT admin to turn premium on for
this account". Every premium refusal on the platform said some version of it,
because on 9 September the only writer of `is_premium` was an admin control.
That is a dead end with instructions attached: it tells somebody the feature
exists, that they cannot have it, and that their next move is to find a member
of staff.

This module is the other path. `premium_admin.apply_premium` still exists and is
still how V-ENT GIVES premium away (a partner, a season, a favour). This is how
somebody takes it for themselves.

## What it charges, and who decides

The price is a platform setting, `premium.price_vc_monthly` and
`premium.price_vc_yearly`, beside the fees an admin already edits. Not a
constant in this file: a price in code is a deploy every time somebody changes
their mind, and this one has not been decided yet.

**Zero means not on sale.** It is the default, and it is the honest state of a
platform whose pricing is still open. It does NOT mean free, and it does not
mean the button disappears: with no price the offer page still has a control and
that control records that somebody wanted it (`PremiumInterest`). Nobody is ever
sent to find a person.

## Where the coins come from

The buyer's wallet, through `vent_tournament.services.wallet.debit`, the same
function the marketplace and tournament entry use. There is no second ledger and
no second idea of what a coin is. An organisation has no wallet, so buying
premium FOR an organisation charges the person doing it, and the row records
both.

## Time is added, never overwritten

Buying while premium is already running extends it from the end of what is
already paid for, not from today. Somebody who pays twice in a month has bought
two months. Buying while premium was GRANTED (no end date) is refused rather
than silently converting an open-ended grant into a dated one, because taking
somebody's money for something they already have is the worst outcome available
here.
"""
from datetime import timedelta

from django.db import transaction as db_transaction
from django.utils import timezone

from vent_tournament.services import wallet as wallet_service

from .models import (AdminSetting, Organization, PremiumInterest,  # noqa: F401
                     PremiumPurchase, UserWallet, Users)

#: A month, for billing. 30 days rather than a calendar month, because "the
#: 31st of next month" does not exist six times a year and the alternative is a
#: rule about what happens then that nobody reads.
DAYS_PER_MONTH = 30


class PremiumSaleError(Exception):
    """Carries a code, because the screen showing it may be in French."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def offer():
    """What premium costs today, and whether it is on sale at all.

    One reader, so the page, the purchase and the tests cannot disagree about
    the number.
    """
    try:
        blob = AdminSetting.load().merged().get('premium') or {}
    except Exception:                                       # noqa: BLE001
        blob = {}

    monthly = max(0, int(blob.get('price_vc_monthly') or 0))
    yearly = max(0, int(blob.get('price_vc_yearly') or 0))
    return {
        'price_vc_monthly': monthly,
        'price_vc_yearly': yearly,
        # On sale when there is a monthly price. A yearly price with no monthly
        # one would be a plan somebody half-configured, and offering it would be
        # offering something nobody chose.
        'on_sale': monthly > 0,
    }


def price_for(months):
    """What `months` costs, at today's prices.

    Twelve months uses the yearly price when one is set, because a year that
    costs twelve times the month is not a year, it is arithmetic.
    """
    months = max(1, int(months or 1))
    prices = offer()
    if not prices['on_sale']:
        raise PremiumSaleError('PREMIUM_NOT_ON_SALE',
                               'Premium is not on sale yet.')
    if months == 12 and prices['price_vc_yearly'] > 0:
        return prices['price_vc_yearly']
    return prices['price_vc_monthly'] * months


def _holder(user, org_slug=None):
    """Who the premium is for: the buyer, or an organisation they may buy for.

    Buying for an organisation is deliberately narrow. Anybody who can spend an
    organisation's money can already do damage; buying it a subscription with
    their OWN coins is a gift, and the only question is whether they are close
    enough to it for the gift to make sense.
    """
    if not org_slug:
        return user, 'User'

    from .org_link import resolve
    # The same resolver every wizard uses, so "which organisation, and may
    # they" is answered once on the platform rather than once per feature.
    org, err = resolve(org_slug, user)
    # The error is read BEFORE the None, because `resolve` returns None for
    # both "no such organisation" and "not yours" and the first version of this
    # answered 404 to somebody who was merely not allowed. Two different
    # refusals reading as one is how somebody spends an afternoon looking for a
    # slug that was right all along.
    if err == 'ORG_NOT_YOURS':
        raise PremiumSaleError('ORG_NOT_YOURS',
                               'You cannot buy premium for that organisation.')
    if err or org is None:
        raise PremiumSaleError('ORG_NOT_FOUND', 'That organisation was not found.')
    return org, 'Organization'


def buy(user, *, months=1, org_slug=None):
    """Charge the buyer and put premium on the holder. Returns the purchase.

    Everything inside one transaction: a debit that lands while the flag does
    not is somebody paying for nothing, and it is the only failure here worth
    designing against.
    """
    months = max(1, min(int(months or 1), 12))
    holder, kind = _holder(user, org_slug)
    cost = price_for(months)

    if holder.is_premium and holder.premium_until is None:
        # Open ended, so there is nothing to extend and nothing to sell.
        raise PremiumSaleError(
            'ALREADY_PREMIUM',
            'That account already has premium, with no end date.')

    now = timezone.now()
    with db_transaction.atomic():
        wallet = UserWallet.objects.select_for_update().filter(user=user).first()
        if wallet is None:
            raise PremiumSaleError('NO_WALLET', 'That account has no wallet yet.')
        if wallet.wallet_balance < cost:
            raise PremiumSaleError(
                'INSUFFICIENT_FUNDS',
                'You need %s VENT COINS and have %s.'
                % (cost, wallet.wallet_balance))

        # Extend from whichever is later: what is already paid for, or now. A
        # lapsed subscription starts again today rather than backdating itself
        # into a period nobody used.
        start = holder.premium_until if (
            holder.premium_until and holder.premium_until > now) else now
        end = start + timedelta(days=DAYS_PER_MONTH * months)

        tx = wallet_service.debit(
            wallet, cost,
            tx_type='deduction',
            description='V-ENT premium, %s month%s' % (
                months, '' if months == 1 else 's'))

        holder.is_premium = True
        holder.premium_until = end
        # Says what was paid and nothing about WHEN. The date belongs in
        # `premium_until`, which every screen renders in the reader's own zone
        # through `src/lib/datetime.js`. A date formatted here would be the
        # server's, and the server is on UTC: the first version of this line
        # wrote "09 September" onto a purchase somebody made on the 10th.
        holder.premium_note = 'Paid %s VC for %s month%s' % (
            cost, months, '' if months == 1 else 's')
        holder.save(update_fields=['is_premium', 'premium_until', 'premium_note'])

        purchase = PremiumPurchase.objects.create(
            buyer=user,
            user=holder if kind == 'User' else None,
            org=holder if kind == 'Organization' else None,
            coins=cost, months=months,
            period_start=start, period_end=end,
            transaction=tx)

    # Whoever wanted it has it now.
    PremiumInterest.objects.filter(user=user).delete()
    return purchase


def register_interest(user, surface=''):
    """Record that somebody wants premium while it is not on sale.

    The point of this row is that the button is never inert and never sends
    anybody to find a member of staff. It also answers a question the console
    could not previously ask: WHICH refusal are people actually hitting.
    """
    row, created = PremiumInterest.objects.get_or_create(
        user=user, defaults={'surface': str(surface or '')[:60]})
    if not created:
        row.times += 1
        if surface:
            row.surface = str(surface)[:60]
        row.save(update_fields=['times', 'surface', 'last_at'])
    return row


def expire_due(now=None):
    """Turn premium off where the paid period has ended. Returns counts.

    Run from `manage.py expire_premium`. Nothing calls it on a read: a nightly
    sweep being a few hours late is generous to somebody who paid, and a check
    on every request would make a paid account's features flicker off mid
    session the moment the clock ticked past.
    """
    now = now or timezone.now()
    counts = {}
    for label, model in (('users', Users), ('orgs', Organization)):
        rows = list(model.objects.filter(is_premium=True,
                                         premium_until__lte=now))
        for row in rows:
            row.is_premium = False
            row.premium_until = None
            row.premium_note = ''
            row.save(update_fields=['is_premium', 'premium_until',
                                    'premium_note'])
        counts[label] = len(rows)
    return counts

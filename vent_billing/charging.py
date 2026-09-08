"""Taking the money, and failing to take it.

Written after cancellation and dunning, deliberately. The charging half is the
half that is easy to build and dangerous to ship first: a subscription that can
take money and cannot stop is not a product, it is a complaint.

## Where the money comes from

Two sources, and the limit is a decision rather than an omission:

- **The VENT COINS wallet.** The server debits it. No browser is involved at any
  point, so there is nothing for a browser to lie about.
- **A saved card.** Paystack charges a stored authorization server side and we
  read the answer off Paystack's response, never off a callback the browser
  made. A card is saved by having been used once for a top-up, which is the
  only way this platform stores one - see `vent_auth/views_cards.py`.

There is deliberately NO new redirect checkout here. A first charge that needs a
browser round trip cannot be repeated by a renewal command a month later, so
building one would mean the first charge and every charge after it work
differently, which is exactly how a renewal path ends up untested. Somebody with
no wallet balance and no saved card is told to top up, with a link, before they
press anything.

## Verified server side, always

`activate` is never called by a view that read `?status=success` off a redirect.
The wallet path debits inside a transaction and knows it worked; the card path
believes Paystack's own `data.status == 'success'` from a response to a request
this server made. Gate B1 is about that sentence and nothing else.

## Proration

There is none, and the screen says so before anybody presses. A plan change
takes effect at the start of the next period, and the date is shown.

This is the product decision, not an omission. VENT COINS are whole numbers
worth 1,000 NGN each, so half a month of a 5 VC plan is 2.5 VC and there is no
way to express it: rounding the credit down takes money from the subscriber on
every single change, and rounding it up gives money away on every single change.
An exact answer that arrives a few weeks later beats an inexact one that arrives
now, and the screen naming the date is what makes it honest rather than
surprising.
"""
import logging
import uuid
from decimal import Decimal

import requests as http_requests
from django.db import transaction
from django.utils import timezone

from vent_auth.models import SavedCard, Transaction, UserWallet

from . import states
from .models import BillingLedgerEntry, Invoice, Subscription

logger = logging.getLogger(__name__)

PAYSTACK_BASE = 'https://api.paystack.co'

#: Failure codes. Codes, never sentences: the subscriber may be reading French,
#: and a gateway's own English words are for whoever is investigating.
INSUFFICIENT_FUNDS = 'INSUFFICIENT_FUNDS'
NO_PAYMENT_METHOD = 'NO_PAYMENT_METHOD'
CARD_DECLINED = 'CARD_DECLINED'
GATEWAY_ERROR = 'GATEWAY_ERROR'
PAYMENTS_UNAVAILABLE = 'PAYMENTS_UNAVAILABLE'


def platform_rate():
    """The platform's cut of a subscription, as a percentage.

    Read from the admin settings, exactly as the ticketing fee is, so it is one
    number somebody can see and change rather than a deploy. Its own key: a
    membership and a ticket are not the same trade and should not be forced to
    charge the same rate.
    """
    try:
        from vent_auth.models import AdminSetting
        fees = AdminSetting.load().merged().get('platform_fees') or {}
        value = fees.get('subscription_fee_pct')
        if value is None:
            value = fees.get('ticket_fee_pct')
        return max(0.0, float(value or 0))
    except Exception:
        # A settings row that does not exist yet, or a value somebody typed
        # wrongly. Charging nothing is the safe direction to fail in: the
        # alternative is charging an amount nobody chose.
        return 0.0


def fee_on(amount_vc, rate=None):
    """The platform's cut of one amount, in whole VENT COINS, rounded DOWN.

    Down rather than up for the same reason as the ticket fee: a fee rounded up
    takes a coin the platform did not earn, on every small membership, every
    month, for as long as somebody stays subscribed.
    """
    rate = platform_rate() if rate is None else rate
    if not amount_vc or rate <= 0:
        return 0
    return int(Decimal(str(amount_vc)) * Decimal(str(rate)) / Decimal('100'))


def payment_source(user):
    """What this person can actually be charged with, right now.

    Returns (source, card). The wallet is preferred when it can cover the
    amount, because it costs nobody a gateway fee and cannot be declined.
    """
    card = SavedCard.objects.filter(
        user=user, removed_at__isnull=True,
    ).exclude(authorization_code='').order_by('-is_default', '-created_at').first()
    wallet = UserWallet.objects.filter(user=user).first()
    return wallet, card


def can_pay(user, amount_vc):
    """Whether a charge of this size would succeed, without attempting it.

    Used by the subscribe screen so somebody is told they need to top up
    BEFORE they press, rather than being refused after. It is a courtesy and
    not a permission: `collect` re-asks everything.
    """
    if amount_vc <= 0:
        return True, Subscription.SOURCE_NONE, None
    wallet, card = payment_source(user)
    if wallet is not None and wallet.wallet_balance >= amount_vc:
        return True, Subscription.SOURCE_WALLET, None
    if card is not None:
        return True, Subscription.SOURCE_CARD, card
    if wallet is not None:
        return False, Subscription.SOURCE_WALLET, None
    return False, Subscription.SOURCE_NONE, None


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------

def _collect_from_wallet(user, amount_vc, description):
    """Debit VENT COINS. Returns (ok, reference, code, detail)."""
    with transaction.atomic():
        wallet = UserWallet.objects.select_for_update().filter(user=user).first()
        if wallet is None:
            return False, '', NO_PAYMENT_METHOD, 'no wallet row'
        if wallet.wallet_balance < amount_vc:
            return False, '', INSUFFICIENT_FUNDS, 'balance %s' % wallet.wallet_balance
        reference = 'VENTSUB-%s' % uuid.uuid4().hex[:16].upper()
        wallet.wallet_balance -= amount_vc
        wallet.save(update_fields=['wallet_balance'])
        Transaction.objects.create(
            wallet=wallet, type='deduction', amount=-amount_vc,
            description=description[:255], status='completed', reference=reference,
        )
        return True, reference, '', ''


def _collect_from_card(user, card, amount_ngn, amount_vc, description):
    """Charge a saved Paystack authorization. Returns (ok, reference, code, detail).

    A successful card charge is written as a top-up followed by a deduction, so
    the wallet statement still adds up. One line saying "deduction" against a
    balance that did not move would be a statement nobody can reconcile, and a
    statement nobody can reconcile is how a real overcharge stays hidden.
    """
    from vent_auth import paystack

    if not paystack.configured():
        return False, '', PAYMENTS_UNAVAILABLE, 'no paystack key'

    reference = 'VENTSUB-%s' % uuid.uuid4().hex[:16].upper()
    try:
        res = http_requests.post(
            '%s/transaction/charge_authorization' % PAYSTACK_BASE,
            json={
                'authorization_code': card.authorization_code,
                'email': user.email,
                'amount': amount_ngn * 100,   # kobo
                'reference': reference,
                'metadata': {'user_id': user.user_id, 'purpose': 'subscription'},
            },
            headers=paystack.headers(),
            timeout=20,
        )
        body = res.json()
    except Exception:
        logger.exception('subscription card charge failed')
        return False, reference, GATEWAY_ERROR, 'gateway did not answer'

    data = body.get('data') or {}
    # Paystack's own answer to a request THIS SERVER made. Never a browser's.
    if not body.get('status') or data.get('status') != 'success':
        detail = data.get('gateway_response') or body.get('message') or ''
        return False, reference, CARD_DECLINED, str(detail)[:300]

    with transaction.atomic():
        wallet = UserWallet.objects.select_for_update().filter(user=user).first()
        if wallet is None:
            # The money is at Paystack and there is nowhere to put it. Logged
            # loudly rather than swallowed: this is a refund somebody has to
            # make by hand, and it must not look like a decline.
            logger.error('subscription card charge %s succeeded with no wallet '
                         'for user %s', reference, user.user_id)
            return False, reference, GATEWAY_ERROR, 'charged with no wallet'
        wallet.wallet_balance += amount_vc
        wallet.save(update_fields=['wallet_balance'])
        Transaction.objects.create(
            wallet=wallet, type='top_up', amount=amount_vc,
            description='%s ending %s' % (card.brand or 'Card', card.last4),
            status='completed', reference=reference,
        )
        wallet.wallet_balance -= amount_vc
        wallet.save(update_fields=['wallet_balance'])
        Transaction.objects.create(
            wallet=wallet, type='deduction', amount=-amount_vc,
            description=description[:255], status='completed',
            reference='%s-SUB' % reference,
        )
        card.last_used_at = timezone.now()
        card.save(update_fields=['last_used_at'])

    return True, reference, '', ''


def record_earnings(invoice):
    """Write the ledger lines one paid invoice created.

    A ledger, not a balance, for the reason written on `BillingLedgerEntry`.
    The rate and the amounts are stamped here, at the charge, so a rate change
    next month never rewrites what a plan earned last month.
    """
    plan = invoice.plan
    gross = invoice.collected_vc
    if gross <= 0:
        return []

    rate = platform_rate()
    fee = fee_on(gross, rate)
    payable = gross - fee

    lines = [BillingLedgerEntry.objects.create(
        plan=plan, invoice=invoice, kind=BillingLedgerEntry.KIND_ORGANISER,
        user=plan.owner, org=plan.org, amount_vc=payable,
        gross_vc=gross, fee_vc=fee, fee_pct=rate)]

    if fee:
        lines.append(BillingLedgerEntry.objects.create(
            plan=plan, invoice=invoice, kind=BillingLedgerEntry.KIND_PLATFORM,
            user=None, org=None, amount_vc=fee,
            gross_vc=gross, fee_vc=fee, fee_pct=rate))
    return lines


def attempt_charge(subscription, *, period_start, period_end, attempt=1, at=None):
    """One charge attempt against one period. Always writes an Invoice.

    Returns the Invoice, whatever happened. A charge that is not on an invoice
    did not happen, and a FAILURE that is not on an invoice is a failure nobody
    can count - which is the half that makes dunning auditable rather than a
    counter somebody has to trust.
    """
    at = at or timezone.now()
    plan = subscription.plan
    amount_vc = plan.price_vc
    amount_ngn = plan.price_ngn

    invoice, created = Invoice.objects.get_or_create(
        subscription=subscription, period_start=period_start, attempt=attempt,
        defaults={
            'plan': plan,
            'plan_name': plan.name,
            'period_end': period_end,
            'amount_vc': amount_vc,
            'amount_ngn': amount_ngn,
            'source': subscription.source,
        },
    )
    if not created and invoice.state == Invoice.STATE_PAID:
        # The backstop under the renewal command's idempotency. Reaching here
        # means two runs raced; the second one must not charge again.
        return invoice

    # A free plan is a real plan - a free membership tier is a thing organisers
    # sell - and it takes the same path so there is one path.
    if amount_vc <= 0:
        invoice.state = Invoice.STATE_PAID
        invoice.collected_vc = 0
        invoice.paid_at = at
        invoice.source = Subscription.SOURCE_NONE
        invoice.save(update_fields=['state', 'collected_vc', 'paid_at', 'source'])
        return invoice

    user = subscription.subscriber
    description = 'Membership - %s' % plan.name

    ok, reference, code, detail = (False, '', NO_PAYMENT_METHOD, '')
    wallet, card = payment_source(user)

    if wallet is not None and wallet.wallet_balance >= amount_vc:
        invoice.source = Subscription.SOURCE_WALLET
        ok, reference, code, detail = _collect_from_wallet(user, amount_vc, description)
    elif card is not None:
        invoice.source = Subscription.SOURCE_CARD
        ok, reference, code, detail = _collect_from_card(
            user, card, amount_ngn, amount_vc, description)
    elif wallet is not None:
        code, detail = INSUFFICIENT_FUNDS, 'balance %s' % wallet.wallet_balance

    if ok:
        invoice.state = Invoice.STATE_PAID
        invoice.collected_vc = amount_vc
        invoice.provider_reference = reference
        invoice.paid_at = at
        invoice.failure_code = ''
        invoice.failure_detail = ''
        invoice.save()
        record_earnings(invoice)
        # The source that actually worked, remembered, so the screen can say
        # what the next charge will take from rather than guessing.
        if subscription.source != invoice.source or subscription.card_id != (
                card.pk if invoice.source == Subscription.SOURCE_CARD and card else None):
            subscription.source = invoice.source
            subscription.card = card if invoice.source == Subscription.SOURCE_CARD else None
            subscription.save(update_fields=['source', 'card'])
    else:
        invoice.state = Invoice.STATE_FAILED
        invoice.provider_reference = reference
        invoice.failure_code = code
        invoice.failure_detail = (detail or '')[:300]
        invoice.save()

    return invoice


# ---------------------------------------------------------------------------
# Giving it back
# ---------------------------------------------------------------------------

def refund(invoice, *, reason='', actor=None, end_access=True, at=None):
    """Give back a charge taken in error, writing lines rather than adjusting.

    Three things happen, in one transaction:

    1. The subscriber's wallet is credited, as a `refund` transaction, so it
       appears on their statement as what it is.
    2. Every ledger line the charge wrote gets a REVERSAL line with the
       opposite sign. Never an edit: editing a settled line rewrites a payment
       already made, and editing an unsettled one erases the fact that the
       charge happened at all. If the organiser has already been settled, the
       negative line stays open and nets against their next settlement, which
       is the entire reason this is a ledger.
    3. The period ends, unless the caller says otherwise. A charge taken in
       error did not buy anything, so leaving the access in place would be
       giving away what was just refunded. `end_access=False` exists for a
       goodwill refund, where the opposite is true.
    """
    at = at or timezone.now()
    if invoice.state == Invoice.STATE_REFUNDED:
        return invoice
    if invoice.state != Invoice.STATE_PAID:
        raise ValueError('only a paid invoice can be refunded')

    subscription = invoice.subscription

    with transaction.atomic():
        amount = invoice.collected_vc
        if amount > 0:
            wallet = UserWallet.objects.select_for_update().filter(
                user=subscription.subscriber).first()
            if wallet is not None:
                wallet.wallet_balance += amount
                wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=wallet, type='refund', amount=amount,
                    description='Refund - %s' % invoice.plan_name,
                    status='completed',
                    reference='%s-REF' % (invoice.provider_reference or invoice.token),
                )

        for line in invoice.ledger_entries.exclude(
                kind=BillingLedgerEntry.KIND_REVERSAL):
            if line.reversed_by_id:
                continue
            reversal = BillingLedgerEntry.objects.create(
                plan=line.plan, invoice=invoice,
                kind=BillingLedgerEntry.KIND_REVERSAL,
                user=line.user, org=line.org,
                amount_vc=-line.amount_vc, gross_vc=-line.gross_vc,
                fee_vc=-line.fee_vc, fee_pct=line.fee_pct,
                note=reason[:200], reverses=line)
            BillingLedgerEntry.objects.filter(pk=line.pk).update(reversed_by=reversal)

        invoice.state = Invoice.STATE_REFUNDED
        invoice.refunded_at = at
        invoice.save(update_fields=['state', 'refunded_at'])

        if end_access and subscription.state in (
                states.TRIALING, states.ACTIVE, states.PAST_DUE, states.CANCELLED):
            subscription.cancel_at_period_end = True
            subscription.period_end = min(subscription.period_end, at)
            subscription.save(update_fields=['cancel_at_period_end', 'period_end'])
            if subscription.state != states.CANCELLED:
                states.move(subscription, states.REFUNDED, actor=actor,
                            note=reason, at=at)

    return invoice

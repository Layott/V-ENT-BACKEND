"""Paying for something with a card, without going and buying coins first.

CEO, 13 September 2026: "Also i hope people can still bu stuff directly on the
platform without having to buy V-ENT coins, that option must always be
vaailable."

Before this, one door in the platform took a card: a guest buying a ticket.
Everybody else met `INSUFFICIENT_BALANCE` and was sent to the wallet to buy
coins as a separate errand, then back to find what they had been doing. The
tournament register had built its own way through, alone.

## What this module is

The one answer to "cover what this purchase is short, with a card, now":

    options(user)                  what this person can actually pay with
    cover(user, coins, ...)        charge a SAVED card and credit the coins
    start(user, coins, callback)   a Paystack page, for somebody with no card

Coins stay the unit the platform settles in: sellers are paid in them, the
ledger keeps naira behind them, and every purchase view already debits them.
What changes is that the coins can arrive in the same breath as the purchase,
at the price the door quoted, without anybody choosing an amount.

## Two rules worth keeping

**The statement has to add up.** A card charge is written as a top-up, and the
purchase writes its own deduction a moment later. One line reading "deduction"
against a balance that never moved is a statement nobody can reconcile, and a
statement nobody can reconcile is how a real overcharge stays hidden. This is
the same rule `vent_billing.charging` was already written to.

**A charge that succeeds with nowhere to put the coins is loud.** It is money
at Paystack and a refund somebody has to make by hand; it must never look like
a decline, because a decline is the one thing nobody investigates.
"""
import logging
import math
import uuid

import requests as http_requests
from django.db import transaction
from django.utils import timezone

from . import paystack
from .models import SavedCard, Transaction, UserWallet

logger = logging.getLogger(__name__)

PAYSTACK_BASE = 'https://api.paystack.co'

#: Failure codes. Codes, never sentences: the buyer may be reading French.
NO_CARD = 'NO_CARD'
NO_WALLET = 'NO_WALLET'
CARDS_UNAVAILABLE = 'CARDS_UNAVAILABLE'
CARD_DECLINED = 'CARD_DECLINED'
GATEWAY_ERROR = 'GATEWAY_ERROR'
NOTHING_TO_PAY = 'NOTHING_TO_PAY'


class PayError(Exception):
    def __init__(self, code, message, **params):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params


def ngn_per_coin():
    from .views_wallet import NGN_PER_COIN
    return int(NGN_PER_COIN)


def coins_to_ngn(coins):
    return int(coins) * ngn_per_coin()


def shortfall_vc(wallet, coins):
    """How many coins short this wallet is for a purchase of `coins`."""
    have = wallet.wallet_balance if wallet else 0
    return max(0, int(coins) - int(have))


def default_card(user):
    return SavedCard.objects.filter(
        user=user, removed_at__isnull=True,
    ).exclude(authorization_code='').order_by('-is_default', '-created_at').first()


def options(user):
    """What this person can pay with, for a screen deciding what to offer.

    A control that is rendered live and refused on press is the fault this
    exists to avoid: a screen offering "pay with card" on a platform with no
    Paystack key wastes somebody's time and tells them nothing.
    """
    wallet = UserWallet.objects.filter(user=user).first()
    card = default_card(user) if paystack.configured() else None
    return {
        'cards_enabled': paystack.configured(),
        'test_mode': paystack.is_test(),
        'balance_vc': wallet.wallet_balance if wallet else 0,
        'ngn_per_coin': ngn_per_coin(),
        'saved_card': {
            'id': card.id,
            'brand': card.brand,
            'last4': card.last4,
        } if card else None,
    }


def _credit(user, coins, reference, description):
    """Put the coins in, once, and say so on the statement.

    Idempotent on the reference: a verify that arrives twice (the browser
    came back AND the webhook landed) must not credit twice. That is the same
    backstop `topup_verify` relies on.
    """
    with transaction.atomic():
        if Transaction.objects.filter(reference=reference, type='top_up',
                                      status='completed').exists():
            wallet = UserWallet.objects.filter(user=user).first()
            return wallet.wallet_balance if wallet else 0, False
        wallet = UserWallet.objects.select_for_update().filter(user=user).first()
        if wallet is None:
            logger.error('card charge %s succeeded with no wallet for user %s',
                         reference, user.user_id)
            raise PayError(NO_WALLET, 'That account has no wallet to pay from.')
        wallet.wallet_balance += int(coins)
        wallet.save(update_fields=['wallet_balance'])
        Transaction.objects.create(
            wallet=wallet, type='top_up', amount=int(coins),
            description=description[:255], status='completed', reference=reference)
        return wallet.wallet_balance, True


def cover(user, coins, *, purpose='purchase', card_id=None):
    """Charge a saved card for exactly `coins` and credit them. One request.

    This is what makes "pay by card" feel like paying for the thing rather
    than a detour: no redirect, no amount to choose, and the purchase the
    caller was making runs immediately afterwards on the same request.
    """
    coins = int(coins)
    if coins <= 0:
        raise PayError(NOTHING_TO_PAY, 'There is nothing to pay for.')
    if not paystack.configured():
        raise PayError(CARDS_UNAVAILABLE,
                       'Card payment is not set up on this platform yet.')

    card = (SavedCard.objects.filter(user=user, pk=card_id,
                                     removed_at__isnull=True).first()
            if card_id else default_card(user))
    if card is None or not card.authorization_code:
        raise PayError(NO_CARD, 'There is no saved card on this account.')

    amount_ngn = coins_to_ngn(coins)
    reference = 'VENT-%s' % uuid.uuid4().hex[:16].upper()
    try:
        res = http_requests.post(
            '%s/transaction/charge_authorization' % PAYSTACK_BASE,
            json={
                'authorization_code': card.authorization_code,
                'email': user.email,
                'amount': amount_ngn * 100,   # Paystack works in kobo
                'reference': reference,
                'metadata': {'user_id': user.user_id, 'purpose': purpose},
            },
            headers=paystack.headers(), timeout=20)
        body = res.json()
    except Exception:                                       # noqa: BLE001
        logger.exception('saved-card charge failed')
        raise PayError(GATEWAY_ERROR,
                       'The payment gateway did not answer. Nothing was charged.')

    data = body.get('data') or {}
    # Paystack's own answer to a request THIS server made, never a browser's.
    if not body.get('status') or data.get('status') != 'success':
        raise PayError(CARD_DECLINED,
                       str(data.get('gateway_response')
                           or body.get('message') or 'That card was declined.')[:300])

    balance, _new = _credit(
        user, coins, reference,
        'Top-up with %s ending %s' % (card.brand or 'card', card.last4))
    SavedCard.objects.filter(pk=card.pk).update(last_used_at=timezone.now())
    return {
        'paid': True,
        'coins_added': coins,
        'amount_ngn': amount_ngn,
        'balance_vc': balance,
        'reference': reference,
        'card': {'brand': card.brand, 'last4': card.last4},
        'test_mode': paystack.is_test(),
    }


def start(user, coins, callback_url='', *, purpose='purchase'):
    """A Paystack page for somebody with no saved card.

    The coins are credited by `topup_verify` when they come back, which is the
    same path the wallet's own top-up uses. Nothing is written here: a payment
    nobody completes should leave nothing behind to clean up.
    """
    coins = int(coins)
    if coins <= 0:
        raise PayError(NOTHING_TO_PAY, 'There is nothing to pay for.')
    if not paystack.configured():
        raise PayError(CARDS_UNAVAILABLE,
                       'Card payment is not set up on this platform yet.')

    wallet = UserWallet.objects.filter(user=user).first()
    if wallet is None:
        raise PayError(NO_WALLET, 'That account has no wallet to pay from.')

    amount_ngn = coins_to_ngn(coins)
    reference = 'VENT-%s' % uuid.uuid4().hex[:16].upper()
    payload = {
        'email': user.email,
        'amount': amount_ngn * 100,
        'reference': reference,
        'metadata': {'user_id': user.user_id, 'wallet_id': wallet.user_wallet_id,
                     'vent_coins': coins, 'purpose': purpose},
    }
    if callback_url:
        payload['callback_url'] = callback_url

    try:
        res = http_requests.post(
            '%s/transaction/initialize' % PAYSTACK_BASE,
            json=payload, headers=paystack.headers(), timeout=10)
        body = res.json()
    except Exception:                                       # noqa: BLE001
        logger.exception('paystack initialize failed')
        raise PayError(GATEWAY_ERROR,
                       'The payment gateway could not be reached. Nothing was charged.')
    # Paystack says WHY in the body, and a 400 carries the useful half: an
    # address it will not accept, an amount under its floor. `raise_for_status`
    # threw that away and left "could not be reached", which sent somebody
    # looking at the network for a problem with their email address.
    if not body.get('status'):
        logger.warning('paystack refused an initialize: %s', body.get('message'))
        raise PayError(GATEWAY_ERROR,
                       body.get('message') or 'The payment could not be started.')

    # The pending row the wallet's own top-up writes, so the reference is
    # known to `topup_verify` when they come back and the statement shows the
    # attempt rather than appearing from nowhere.
    Transaction.objects.create(
        wallet=wallet, type='top_up', amount=coins,
        description='Top up via Paystack - %s NGN' % amount_ngn,
        status='pending', reference=reference)

    return {
        'paid': False,
        'authorization_url': body['data']['authorization_url'],
        'reference': reference,
        'coins': coins,
        'amount_ngn': amount_ngn,
        'test_mode': paystack.is_test(),
    }


def cover_or_start(user, coins, callback_url='', *, purpose='purchase', card_id=None):
    """Cover it with a saved card when there is one, else hand back a page.

    One call for a screen that does not want to branch: `paid` says which
    happened, and the screen either carries straight on or sends them out.
    """
    coins = int(math.ceil(float(coins)))
    try:
        return cover(user, coins, purpose=purpose, card_id=card_id)
    except PayError as exc:
        if exc.code != NO_CARD:
            raise
    return start(user, coins, callback_url, purpose=purpose)

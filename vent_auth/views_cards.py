"""Saved cards, the only way a card can safely be saved.

The settings page asked for a card number, an expiry and a CVV, in our own form,
and then asked the person to tell us which brand it was. Two things wrong with
that, and the second follows from the first:

1. A card number and CVV must never reach our page or our servers. Handling them
   puts the whole platform inside PCI DSS scope, and it is exactly the data that
   makes a breach a catastrophe rather than an incident. Paystack exists so that
   those digits go to Paystack.
2. Because we were collecting the number ourselves and could not do anything
   with it, the brand had to be a dropdown. Paystack tells us the brand, the last
   four digits, the expiry and the issuing bank - accurately, from the card that
   was actually charged.

So a card is saved by being used once: a top-up with "save this card" ticked
returns an authorization we can charge again, and that is what a saved card is.
Nobody loses money to a verification charge, and we never hold a PAN.
"""
import logging
import uuid

import requests as http_requests
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import SavedCard, Transaction, UserWallet
from .views_profile import _user_from_bearer
from .views_wallet import PAYSTACK_BASE, _ngn_to_coins, _paystack_headers

logger = logging.getLogger(__name__)


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(message, code='ERROR', http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': None},
                    status=http_status)


def _card_row(card):
    return {
        'id': card.id,
        'brand': card.brand,
        'last4': card.last4,
        'expires': f'{card.exp_month:0>2}/{card.exp_year[-2:]}' if card.exp_month else '',
        'bank': card.bank,
        'is_default': card.is_default,
        'added': card.created_at,
    }


@api_view(['GET'])
def list_cards(request):
    """GET /auth/wallet/cards/ - what can be charged again, and nothing more."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    cards = SavedCard.objects.filter(user=user, removed_at__isnull=True)
    return _ok({'cards': [_card_row(c) for c in cards]}, 'Saved cards')


@api_view(['POST'])
def remove_card(request, card_id):
    """Forget a card. The authorization is cleared, not just hidden."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    card = SavedCard.objects.filter(user=user, pk=card_id, removed_at__isnull=True).first()
    if card is None:
        return _err('No such card.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    card.authorization_code = ''
    card.removed_at = timezone.now()
    card.is_default = False
    card.save(update_fields=['authorization_code', 'removed_at', 'is_default'])
    return _ok({'removed': card_id}, 'Card removed.')


@api_view(['POST'])
def set_default_card(request, card_id):
    user, err = _user_from_bearer(request)
    if err:
        return err

    card = SavedCard.objects.filter(user=user, pk=card_id, removed_at__isnull=True).first()
    if card is None:
        return _err('No such card.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    with transaction.atomic():
        SavedCard.objects.filter(user=user).update(is_default=False)
        card.is_default = True
        card.save(update_fields=['is_default'])
    return _ok(_card_row(card), 'Default card updated.')


def store_authorization(user, payload):
    """Keep a reusable card from a verified Paystack transaction.

    Only what a person needs to recognise the card, plus the token that charges
    it again. No PAN, ever - Paystack does not hand one over and we would not
    want it if they did.
    """
    auth = (payload or {}).get('authorization') or {}
    if not auth.get('reusable') or not auth.get('authorization_code'):
        return None

    signature = auth.get('signature') or auth['authorization_code']
    existing = SavedCard.objects.filter(user=user, signature=signature).first()
    if existing is not None:
        existing.authorization_code = auth['authorization_code']
        existing.removed_at = None
        existing.save(update_fields=['authorization_code', 'removed_at'])
        return existing

    is_first = not SavedCard.objects.filter(user=user, removed_at__isnull=True).exists()
    return SavedCard.objects.create(
        user=user,
        authorization_code=auth['authorization_code'],
        signature=signature,
        brand=(auth.get('brand') or '').title(),
        last4=auth.get('last4') or '',
        exp_month=auth.get('exp_month') or '',
        exp_year=auth.get('exp_year') or '',
        bank=auth.get('bank') or '',
        channel=auth.get('channel') or 'card',
        country=auth.get('country_code') or '',
        is_default=is_first,
    )


@api_view(['POST'])
def charge_saved_card(request):
    """Top up with a card already saved, without leaving the app.

    This is the whole point of saving one: Paystack charges the stored
    authorization server-side, so there is no redirect and no second entry of
    anything.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    card = SavedCard.objects.filter(
        user=user, pk=request.data.get('card_id'), removed_at__isnull=True,
    ).first() if request.data.get('card_id') else SavedCard.objects.filter(
        user=user, is_default=True, removed_at__isnull=True,
    ).first()

    if card is None:
        return _err('No saved card to charge.', 'NO_CARD', status.HTTP_404_NOT_FOUND)

    try:
        amount_ngn = int(request.data.get('amount_ngn') or 0)
    except (TypeError, ValueError):
        amount_ngn = 0
    if amount_ngn < 1000:
        return _err('The smallest top-up is 1,000 NGN, which is 1 VENT COIN.', 'AMOUNT_TOO_SMALL')

    wallet = UserWallet.objects.filter(user=user).first()
    if wallet is None:
        return _err('No wallet for this account.', 'NO_WALLET', status.HTTP_404_NOT_FOUND)

    reference = f'VENT-{uuid.uuid4().hex[:16].upper()}'
    try:
        res = http_requests.post(
            f'{PAYSTACK_BASE}/transaction/charge_authorization',
            json={
                'authorization_code': card.authorization_code,
                'email': user.email,
                'amount': amount_ngn * 100,
                'reference': reference,
                'metadata': {'user_id': user.user_id, 'purpose': 'wallet_topup'},
            },
            headers=_paystack_headers(),
            timeout=20,
        )
        body = res.json()
    except Exception:
        logger.exception('saved-card charge failed')
        return _err('The payment gateway did not answer. Nothing was charged.',
                    'GATEWAY_ERROR', status.HTTP_502_BAD_GATEWAY)

    data = body.get('data') or {}
    if not body.get('status') or data.get('status') != 'success':
        return _err(
            data.get('gateway_response') or body.get('message') or 'That card was declined.',
            'CHARGE_FAILED',
        )

    coins = _ngn_to_coins(amount_ngn)
    with transaction.atomic():
        wallet = UserWallet.objects.select_for_update().get(pk=wallet.pk)
        wallet.wallet_balance += coins
        wallet.save(update_fields=['wallet_balance'])
        Transaction.objects.create(
            wallet=wallet, type='top_up', amount=coins,
            description=f'Top-up with {card.brand} ending {card.last4}',
            status='completed', reference=reference,
        )
        card.last_used_at = timezone.now()
        card.save(update_fields=['last_used_at'])

    return _ok(
        {'coins_added': coins, 'balance': wallet.wallet_balance, 'reference': reference},
        f'{coins:,} VENT COINS added.',
    )

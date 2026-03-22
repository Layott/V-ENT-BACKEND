import os
import uuid
from datetime import timedelta

import requests as http_requests
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import UserWallet, TeamWallet, OrgWallet, Transaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PAYSTACK_BASE = 'https://api.paystack.co'
# Exchange rate: how many VENT COINS per 100 NGN
# Default: 50 coins per 100 NGN  (i.e. 1 NGN = 0.5 VENT COINS)
COINS_PER_100_NGN = int(os.environ.get('VENT_COINS_PER_100_NGN', 50))


def _paystack_headers():
    return {
        'Authorization': f"Bearer {os.environ.get('PAYSTACK_SECRET_KEY', '')}",
        'Content-Type': 'application/json',
    }


def _ngn_to_coins(amount_ngn: int) -> int:
    """Convert NGN amount to VENT COINS (integer)."""
    return (amount_ngn * COINS_PER_100_NGN) // 100


def _get_user_from_token(request):
    """Return (user, error_response) from Authorization header."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return None, Response(
            {'status': 'error', 'message': 'Authorization header is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    token = session_token.split(' ', 1)[1]
    user = UserWallet.objects.filter(user__login_session_token=token).select_related('user').first()
    if user is None:
        return None, Response(
            {'status': 'error', 'message': 'Invalid or expired session token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if (
        user.user.login_session_created_at is None
        or timezone.now() - user.user.login_session_created_at > timedelta(minutes=120)
    ):
        return None, Response(
            {'status': 'error', 'message': 'Session token has expired'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return user, None  # returns wallet object + user via wallet.user


# ---------------------------------------------------------------------------
# W1 — GET /auth/wallet/balance/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def get_wallet_balance(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    pending_withdrawal = wallet.withdrawals.filter(
        status__in=['pending', 'approved', 'processing']
    ).values_list('amount', flat=True)
    pending_total = sum(pending_withdrawal)

    return Response({
        'status': 'success',
        'data': {
            'balance': wallet.wallet_balance,
            'currency': 'VENT COINS',
            'kyc_verified': wallet.kyc_verified,
            'pending_withdrawal': pending_total,
            'exchange_rate': f'{COINS_PER_100_NGN} VENT COINS per 100 NGN',
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /auth/wallet/transactions/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def get_wallet_transactions(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    per_page = 20
    offset = (page - 1) * per_page

    qs = wallet.transactions.order_by('-created_at')
    total = qs.count()
    transactions = qs[offset: offset + per_page]

    txn_list = [
        {
            'id': t.id,
            'type': t.type,
            'amount': t.amount,
            'description': t.description,
            'status': t.status,
            'reference': t.reference,
            'tournament_id': t.tournament_id,
            'created_at': t.created_at,
        }
        for t in transactions
    ]

    return Response({
        'status': 'success',
        'data': {
            'transactions': txn_list,
            'total': total,
            'page': page,
            'per_page': per_page,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# W3 — POST /auth/wallet/topup/initiate/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def topup_initiate(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    amount_ngn = request.data.get('amount_ngn')
    if not amount_ngn:
        return Response(
            {'status': 'error', 'message': 'amount_ngn is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount_ngn = int(amount_ngn)
    except (ValueError, TypeError):
        return Response(
            {'status': 'error', 'message': 'amount_ngn must be an integer'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount_ngn < 100:
        return Response(
            {'status': 'error', 'message': 'Minimum top-up is 100 NGN'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    vent_coins = _ngn_to_coins(amount_ngn)
    reference = f"VENT-{uuid.uuid4().hex[:16].upper()}"

    # Initialize Paystack transaction (amount in kobo = NGN * 100)
    payload = {
        'email': wallet.user.email,
        'amount': amount_ngn * 100,  # kobo
        'reference': reference,
        'metadata': {
            'user_id': wallet.user.user_id,
            'wallet_id': wallet.user_wallet_id,
            'vent_coins': vent_coins,
        },
    }

    try:
        resp = http_requests.post(
            f'{PAYSTACK_BASE}/transaction/initialize',
            json=payload,
            headers=_paystack_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except http_requests.RequestException as e:
        return Response(
            {'status': 'error', 'message': f'Payment gateway error: {str(e)}'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if not data.get('status'):
        return Response(
            {'status': 'error', 'message': data.get('message', 'Paystack error')},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # Create a pending transaction record
    Transaction.objects.create(
        wallet=wallet,
        type='top_up',
        amount=vent_coins,
        description=f'Top up via Paystack — {amount_ngn} NGN',
        status='pending',
        reference=reference,
    )

    return Response({
        'status': 'success',
        'data': {
            'authorization_url': data['data']['authorization_url'],
            'reference': reference,
            'vent_coins': vent_coins,
            'amount_ngn': amount_ngn,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# W4 — POST /auth/wallet/topup/verify/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def topup_verify(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    reference = request.data.get('reference')
    if not reference:
        return Response(
            {'status': 'error', 'message': 'reference is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Fetch the pending transaction
    try:
        txn = Transaction.objects.get(
            wallet=wallet,
            reference=reference,
            type='top_up',
        )
    except Transaction.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Transaction not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if txn.status == 'completed':
        return Response({
            'status': 'success',
            'data': {'message': 'Already verified', 'balance': wallet.wallet_balance}
        }, status=status.HTTP_200_OK)

    if txn.status in ('failed', 'cancelled'):
        return Response(
            {'status': 'error', 'message': f'Transaction is {txn.status}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Verify with Paystack
    try:
        resp = http_requests.get(
            f'{PAYSTACK_BASE}/transaction/verify/{reference}',
            headers=_paystack_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except http_requests.RequestException as e:
        return Response(
            {'status': 'error', 'message': f'Payment gateway error: {str(e)}'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if not data.get('status') or data['data']['status'] != 'success':
        txn.status = 'failed'
        txn.save(update_fields=['status'])
        return Response(
            {'status': 'error', 'message': 'Payment not successful'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Credit the wallet
    wallet.wallet_balance += txn.amount
    wallet.save(update_fields=['wallet_balance'])

    txn.status = 'completed'
    txn.save(update_fields=['status'])

    return Response({
        'status': 'success',
        'data': {
            'message': 'Top-up successful',
            'coins_added': txn.amount,
            'new_balance': wallet.wallet_balance,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/send/  (updated from send_funds)
# ---------------------------------------------------------------------------

@api_view(['POST'])
def send_funds(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    recipient_username = request.data.get('recipient_username')
    amount = request.data.get('amount')
    pin = request.data.get('pin')
    note = request.data.get('note', '')

    if not all([recipient_username, amount, pin]):
        return Response(
            {'status': 'error', 'message': 'recipient_username, amount, and pin are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return Response(
            {'status': 'error', 'message': 'amount must be an integer'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount <= 0:
        return Response(
            {'status': 'error', 'message': 'amount must be positive'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not wallet.pin_hash or not check_password(str(pin), wallet.pin_hash):
        return Response(
            {'status': 'error', 'message': 'Invalid PIN'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if wallet.wallet_balance < amount:
        return Response(
            {'status': 'error', 'message': 'Insufficient balance'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        recipient_wallet = UserWallet.objects.select_related('user').get(
            user__username=recipient_username
        )
    except UserWallet.DoesNotExist:
        return Response(
            {'status': 'error', 'message': 'Recipient not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if recipient_wallet.user_wallet_id == wallet.user_wallet_id:
        return Response(
            {'status': 'error', 'message': 'Cannot send to yourself'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Debit sender
    wallet.wallet_balance -= amount
    wallet.save(update_fields=['wallet_balance'])
    Transaction.objects.create(
        wallet=wallet,
        type='send',
        amount=-amount,
        description=f'Sent to @{recipient_username}{": " + note if note else ""}',
        status='completed',
    )

    # Credit recipient
    recipient_wallet.wallet_balance += amount
    recipient_wallet.save(update_fields=['wallet_balance'])
    Transaction.objects.create(
        wallet=recipient_wallet,
        type='receive',
        amount=amount,
        description=f'Received from @{wallet.user.username}{": " + note if note else ""}',
        status='completed',
    )

    return Response({
        'status': 'success',
        'data': {
            'new_balance': wallet.wallet_balance,
        }
    }, status=status.HTTP_200_OK)

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

from django.contrib.auth.hashers import make_password
from .models import UserWallet, TeamWallet, OrgWallet, Transaction, WithdrawalRequest, KYCDocument


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


# ---------------------------------------------------------------------------
# POST /auth/wallet/pin/verify/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def verify_wallet_pin(request):
    """Verify wallet PIN — used by frontend before showing sensitive actions."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    pin = request.data.get('pin')
    if not pin:
        return Response({'status': 'error', 'message': 'pin is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not wallet.pin_hash:
        return Response({'status': 'error', 'message': 'No PIN set on this wallet'}, status=status.HTTP_400_BAD_REQUEST)

    if not check_password(str(pin), wallet.pin_hash):
        return Response({'status': 'error', 'message': 'Invalid PIN'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'status': 'success', 'message': 'PIN verified'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/pin/set/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def set_wallet_pin(request):
    """Set or update the wallet PIN. Requires current PIN if one already exists."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    new_pin = request.data.get('new_pin')
    current_pin = request.data.get('current_pin')

    if not new_pin:
        return Response(
            {'status': 'error', 'message': 'new_pin is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(str(new_pin)) != 4 or not str(new_pin).isdigit():
        return Response(
            {'status': 'error', 'message': 'PIN must be exactly 4 digits'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # If PIN already set, verify current PIN
    if wallet.pin_hash:
        if not current_pin:
            return Response(
                {'status': 'error', 'message': 'current_pin is required to change an existing PIN'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not check_password(str(current_pin), wallet.pin_hash):
            return Response(
                {'status': 'error', 'message': 'Current PIN is incorrect'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    wallet.pin_hash = make_password(str(new_pin))
    wallet.save(update_fields=['pin_hash'])

    return Response({'status': 'success', 'message': 'PIN set successfully'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/deduct/  (internal — called by tournament registration)
# ---------------------------------------------------------------------------

@api_view(['POST'])
def wallet_deduct(request):
    """Deduct VENT COINS from user wallet for tournament registration fee."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    amount = request.data.get('amount')
    tournament_id = request.data.get('tournament_id')
    description = request.data.get('description', 'Tournament registration fee')
    pin = request.data.get('pin')

    if not amount or not tournament_id:
        return Response(
            {'status': 'error', 'message': 'amount and tournament_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return Response({'status': 'error', 'message': 'amount must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

    if amount <= 0:
        return Response({'status': 'error', 'message': 'amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)

    if not wallet.pin_hash or not check_password(str(pin), wallet.pin_hash):
        return Response({'status': 'error', 'message': 'Invalid PIN'}, status=status.HTTP_400_BAD_REQUEST)

    if wallet.wallet_balance < amount:
        return Response({'status': 'error', 'message': 'Insufficient balance'}, status=status.HTTP_400_BAD_REQUEST)

    from vent_tournament.models import Tournament
    try:
        tournament = Tournament.objects.get(tournament_id=tournament_id)
    except Tournament.DoesNotExist:
        return Response({'status': 'error', 'message': 'Tournament not found'}, status=status.HTTP_404_NOT_FOUND)

    wallet.wallet_balance -= amount
    wallet.save(update_fields=['wallet_balance'])

    Transaction.objects.create(
        wallet=wallet,
        type='deduction',
        amount=-amount,
        description=description,
        status='completed',
        tournament=tournament,
    )

    return Response({
        'status': 'success',
        'data': {'new_balance': wallet.wallet_balance}
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/withdraw/initiate/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def withdraw_initiate(request):
    """Request a fiat withdrawal. Requires KYC + PIN."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    amount = request.data.get('amount')
    bank_name = request.data.get('bank_name')
    account_number = request.data.get('account_number')
    account_name = request.data.get('account_name')
    pin = request.data.get('pin')

    if not all([amount, bank_name, account_number, account_name, pin]):
        return Response(
            {'status': 'error', 'message': 'amount, bank_name, account_number, account_name, and pin are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return Response({'status': 'error', 'message': 'amount must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

    if amount <= 0:
        return Response({'status': 'error', 'message': 'amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)

    if not wallet.kyc_verified:
        return Response(
            {'status': 'error', 'message': 'KYC verification required before withdrawing'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not wallet.pin_hash or not check_password(str(pin), wallet.pin_hash):
        return Response({'status': 'error', 'message': 'Invalid PIN'}, status=status.HTTP_400_BAD_REQUEST)

    if wallet.wallet_balance < amount:
        return Response({'status': 'error', 'message': 'Insufficient balance'}, status=status.HTTP_400_BAD_REQUEST)

    wr = WithdrawalRequest.objects.create(
        wallet=wallet,
        amount=amount,
        bank_name=bank_name,
        account_number=account_number,
        account_name=account_name,
    )

    Transaction.objects.create(
        wallet=wallet,
        type='withdrawal',
        amount=-amount,
        description=f'Withdrawal to {bank_name} {account_number[-4:]}',
        status='pending',
    )

    return Response({
        'status': 'success',
        'data': {
            'withdrawal_id': wr.id,
            'amount': wr.amount,
            'status': wr.status,
            'message': 'Withdrawal request submitted. Pending admin approval.',
        }
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /auth/wallet/withdraw/status/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def withdraw_status(request):
    """Check withdrawal request history and status."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    withdrawals = wallet.withdrawals.order_by('-requested_at')

    data = [
        {
            'id': w.id,
            'amount': w.amount,
            'bank_name': w.bank_name,
            'account_number': w.account_number[-4:].rjust(len(w.account_number), '*'),
            'account_name': w.account_name,
            'status': w.status,
            'admin_note': w.admin_note,
            'requested_at': w.requested_at,
            'processed_at': w.processed_at,
        }
        for w in withdrawals
    ]

    return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/kyc/submit/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def kyc_submit(request):
    """Submit a KYC document for review."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    user = wallet.user
    document_type = request.data.get('document_type')
    document_image = request.FILES.get('document_image')

    valid_types = ['national_id', 'passport', 'drivers_license']
    if document_type not in valid_types:
        return Response(
            {'status': 'error', 'message': f'document_type must be one of: {", ".join(valid_types)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not document_image:
        return Response(
            {'status': 'error', 'message': 'document_image is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Replace any existing pending document of same type
    KYCDocument.objects.filter(user=user, document_type=document_type, status='pending').delete()

    doc = KYCDocument.objects.create(
        user=user,
        document_type=document_type,
        document_image=document_image,
    )

    return Response({
        'status': 'success',
        'data': {
            'kyc_id': doc.id,
            'document_type': doc.document_type,
            'status': doc.status,
            'submitted_at': doc.submitted_at,
        }
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /auth/wallet/kyc/status/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def kyc_status(request):
    """Check user's KYC verification status."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    latest_doc = wallet.user.kyc_documents.order_by('-submitted_at').first()

    return Response({
        'status': 'success',
        'data': {
            'kyc_verified': wallet.kyc_verified,
            'latest_submission': {
                'id': latest_doc.id,
                'document_type': latest_doc.document_type,
                'status': latest_doc.status,
                'rejection_reason': latest_doc.rejection_reason if latest_doc.status == 'rejected' else None,
                'submitted_at': latest_doc.submitted_at,
            } if latest_doc else None,
        }
    }, status=status.HTTP_200_OK)

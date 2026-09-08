import logging
import os
from .views_helpers import session_timeout_minutes, get_or_create_user_wallet
import uuid
from datetime import timedelta

import requests as http_requests
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from django.contrib.auth.hashers import make_password
from . import kyc as kyc_service
from . import payouts
from . import wallets
from .models import (Users, UserWallet, TeamWallet, OrgWallet, Transaction,
                     WithdrawalRequest, KYCDocument, PayoutAddress)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

PAYSTACK_BASE = 'https://api.paystack.co'

# One VENT COIN costs 1,000 NGN. That is the rate the product states everywhere
# a user can read it: the top-up screen, the onboarding page, the home wallet
# card, and the platform docs.
#
# The default used to be `VENT_COINS_PER_100_NGN = 50`, which prices a coin at
# 2 NGN - five hundred times cheaper than the screen the user is looking at
# while they pay. Nobody had noticed because production has never taken a real
# payment. Set NGN_PER_COIN in the environment to change the rate; the legacy
# variable is still honoured so an existing deployment is not silently repriced.
_legacy_per_100 = os.environ.get('VENT_COINS_PER_100_NGN')
if _legacy_per_100 and int(_legacy_per_100) > 0:
    NGN_PER_COIN = 100 // int(_legacy_per_100) or 1
else:
    NGN_PER_COIN = int(os.environ.get('NGN_PER_COIN', 1000))


def _paystack_headers():
    # Same one helper as the guest checkout. Two copies of this function
    # reading the environment directly is why a test key sat unused in .env.
    from . import paystack
    return paystack.headers()


def _ngn_to_coins(amount_ngn: int) -> int:
    """Convert an NGN amount to whole VENT COINS, rounding down."""
    return int(amount_ngn) // NGN_PER_COIN


def coins_to_ngn(coins: int) -> int:
    """What a coin balance is worth in NGN. The inverse of _ngn_to_coins."""
    return int(coins) * NGN_PER_COIN


def _get_user_from_token(request):
    """Return (wallet, error_response) for the caller's Bearer token.

    Authentication and wallet lookup are deliberately separate steps. This used
    to do both at once:

        UserWallet.objects.filter(user__login_session_token=token).first()

    so a user who simply had no wallet row got 401 "Invalid or expired session
    token" - which is false, and worse, the frontend's session guard treats any
    401 on an authenticated request as a dead session and signs the user out of
    the entire app. Wallets were only created at email verification, so every
    account that had not been through that path was logged straight back out.

    Now: a bad or expired token is a 401, and a missing wallet is simply created.
    """
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, Response(
            { 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, Response(
            { 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if (
        user.login_session_created_at is None
        or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes())
    ):
        return None, Response(
            { 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    return get_or_create_user_wallet(user), None


# ---------------------------------------------------------------------------
# W1 - GET /auth/wallet/balance/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def get_wallet_balance(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    # Money on its way out. Since 8 September it has ALREADY left `balance`:
    # a payout is held when it is asked for, not when an admin gets to it. So
    # this is shown beside the balance rather than subtracted from it, and a
    # screen must never take it off the balance a second time.
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
            'has_pin': bool(wallet.pin_hash),
            # Whether a spend from this wallet has to carry an authenticator
            # code. The screen asks for one only when the answer is yes, so
            # nobody is shown a field they have nothing to type into.
            'requires_2fa': wallets.second_factor_required(wallet.user),
            'pending_withdrawal': pending_total,
            'pending_withdrawal_already_deducted': True,
            'balance_ngn': coins_to_ngn(wallet.wallet_balance),
            'ngn_per_coin': NGN_PER_COIN,
            'exchange_rate': f'{NGN_PER_COIN:,} NGN per VENT COIN',
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
# W3 - POST /auth/wallet/topup/initiate/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def topup_initiate(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    amount_ngn = request.data.get('amount_ngn')
    if not amount_ngn:
        return Response(
            { 'code': 'AMOUNT_NGN_REQUIRED','status': 'error', 'message': 'amount_ngn is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount_ngn = int(amount_ngn)
    except (ValueError, TypeError):
        return Response(
            { 'code': 'AMOUNT_NGN_MUST_INTEGER','status': 'error', 'message': 'amount_ngn must be an integer'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount_ngn < NGN_PER_COIN:
        return Response(
            {'status': 'error',
             'message': f'Minimum top-up is {NGN_PER_COIN:,} NGN (1 VENT COIN)'},
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
        description=f'Top up via Paystack - {amount_ngn} NGN',
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
# W4 - POST /auth/wallet/topup/verify/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def topup_verify(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    reference = request.data.get('reference')
    if not reference:
        return Response(
            { 'code': 'REFERENCE_REQUIRED','status': 'error', 'message': 'reference is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Idempotency + concurrency (F5 / F12): lock the transaction row for this
    # reference before doing anything. A concurrent verify (or the Paystack
    # webhook) for the same reference blocks here, then reads 'completed' and
    # returns without crediting a second time. The DB-level unique constraint on
    # Transaction.reference is the backstop against two rows for one payment.
    with transaction.atomic():
        try:
            txn = Transaction.objects.select_for_update().get(
                wallet=wallet,
                reference=reference,
                type='top_up',
            )
        except Transaction.DoesNotExist:
            return Response(
                { 'code': 'TRANSACTION_NOT_FOUND','status': 'error', 'message': 'Transaction not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if txn.status == 'completed':
            locked_wallet = UserWallet.objects.select_for_update().get(pk=wallet.pk)
            return Response({
                'status': 'success',
                'data': {
                    'message': 'Already verified',
                    'credited': False,
                    'idempotent': True,
                    'balance': locked_wallet.wallet_balance,
                }
            }, status=status.HTTP_200_OK)

        if txn.status in ('failed', 'cancelled'):
            return Response(
                {'status': 'error', 'message': f'Transaction is {txn.status}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify with Paystack (inside the lock so duplicate calls serialize)
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
                { 'code': 'PAYMENT_NOT_SUCCESSFUL','status': 'error', 'message': 'Payment not successful'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Credit the wallet under a row lock
        locked_wallet = UserWallet.objects.select_for_update().get(pk=wallet.pk)
        locked_wallet.wallet_balance += txn.amount
        locked_wallet.save(update_fields=['wallet_balance'])

        txn.status = 'completed'
        txn.save(update_fields=['status'])
        new_balance = locked_wallet.wallet_balance

        # A card is saved by being used. Paystack returns a reusable
        # authorization plus the brand, last four and expiry, which is
        # everything needed to charge it again and to show it - and none of it
        # is a card number.
        saved_card = None
        if request.data.get('save_card'):
            from .views_cards import store_authorization
            try:
                saved_card = store_authorization(wallet.user, data.get('data') or {})
            except Exception:
                logger.warning('could not store the card authorization', exc_info=True)

    return Response({
        'status': 'success',
        'data': {
            'message': 'Top-up successful',
            'credited': True,
            'coins_added': txn.amount,
            'new_balance': new_balance,
            'card_saved': bool(saved_card),
            'card': {
                'brand': saved_card.brand, 'last4': saved_card.last4,
            } if saved_card else None,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/send/  (updated from send_funds)
# ---------------------------------------------------------------------------

@api_view(['POST'])
def send_funds(request):
    """Send coins from this person's wallet to a person, a team or an organisation.

    The spec's first user-wallet line is "Send VENT COINS to other users,
    organizations, teams, tournament organizers", and until 8 September this
    endpoint could only do the first of those: it took a `recipient_username`
    and looked it up in `UserWallet`. A team wallet that only another team
    could pay is not a wallet anybody can use.

    `recipient_username` still works, because `/wallets/send` sends it and a
    payload nothing sends is a contract nobody is keeping. It now means
    `to_kind='user'`, which is what it always meant.

    The money itself is `wallets.transfer`, the same function the team and
    organisation wallets use. What was here before was a second copy of it -
    its own locking, its own balance check, its own two row writes - and two
    copies of "take from one, give to the other" is two chances for one of them
    to forget a line.
    """
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    amount = request.data.get('amount')
    pin = request.data.get('pin')
    note = str(request.data.get('note', '') or '')[:200]

    recipient_username = request.data.get('recipient_username')
    to_kind = request.data.get('to_kind') or ('user' if recipient_username else '')
    to_ref = request.data.get('to') or recipient_username

    if not all([to_ref, amount, pin]):
        return Response(
            { 'code': 'RECIPIENT_USERNAME_AMOUNT_PIN','status': 'error', 'message': 'recipient_username, amount, and pin are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return Response(
            { 'code': 'AMOUNT_MUST_INTEGER','status': 'error', 'message': 'amount must be an integer'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount <= 0:
        return Response(
            { 'code': 'AMOUNT_MUST_POSITIVE','status': 'error', 'message': 'amount must be positive'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not wallet.pin_hash or not check_password(str(pin), wallet.pin_hash):
        return Response(
            { 'code': 'INVALID_PIN','status': 'error', 'message': 'Invalid PIN'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    sender_username = wallet.user.username

    try:
        wallets.check_second_factor(wallet.user, request.data.get('code'))
        target = wallets.resolve_target(to_kind, to_ref)
    except wallets.WalletError as exc:
        http = (status.HTTP_404_NOT_FOUND if exc.code == 'NOT_FOUND'
                else status.HTTP_400_BAD_REQUEST)
        return Response({'code': exc.code, 'status': 'error',
                         'message': str(exc)}, status=http)

    if isinstance(target, UserWallet) and target.pk == wallet.pk:
        return Response(
            { 'code': 'CANNOT_SEND_YOURSELF','status': 'error', 'message': 'Cannot send to yourself'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    to_user = target.user if isinstance(target, UserWallet) else None
    named = wallets.describe(target)

    # Person to person keeps the two words it has always had on the two
    # statements. Money to a team or an organisation is a transfer on both
    # sides, because neither `send` nor `receive` describes it.
    if to_user is not None:
        debit_kind, credit_kind = 'send', 'receive'
    else:
        debit_kind = credit_kind = 'transfer'

    try:
        debit, _credit = wallets.transfer(
            wallet, target, amount,
            debit_kind=debit_kind, credit_kind=credit_kind,
            debit_note='Sent to %s%s' % (named, (': ' + note) if note else ''),
            credit_note='Received from @%s%s' % (
                sender_username, (': ' + note) if note else ''),
        )
    except wallets.WalletError as exc:
        return Response({'code': exc.code, 'status': 'error',
                         'message': str(exc)},
                        status=status.HTTP_400_BAD_REQUEST)

    wallet.refresh_from_db()
    new_balance = wallet.wallet_balance

    # Notify the recipient they received VC (fire-and-forget - never break the
    # transfer if the notification insert fails). Only a person has somewhere
    # for a notification to arrive; a team or an organisation is told by its
    # wallet screen, which is the statement this just wrote.
    if to_user is not None:
        try:
            from vent_auth.views_notifications import create_notification
            create_notification(
                to_user, 'wallet',
                f'You received {amount} VC from @{sender_username}',
                link='/wallets',
                metadata={'amount': amount, 'from': sender_username},
            )
        except Exception:
            pass

    return Response({
        'status': 'success',
        'data': {
            'new_balance': new_balance,
            'sent_to': named,
            'transaction_id': debit.id,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/pin/verify/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def verify_wallet_pin(request):
    """Verify wallet PIN - used by frontend before showing sensitive actions."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    pin = request.data.get('pin')
    if not pin:
        return Response({ 'code': 'PIN_REQUIRED','status': 'error', 'message': 'pin is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not wallet.pin_hash:
        return Response({ 'code': 'NO_PIN_SET_WALLET','status': 'error', 'message': 'No PIN set on this wallet'}, status=status.HTTP_400_BAD_REQUEST)

    if not check_password(str(pin), wallet.pin_hash):
        return Response({ 'code': 'INVALID_PIN','status': 'error', 'message': 'Invalid PIN'}, status=status.HTTP_400_BAD_REQUEST)

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
            { 'code': 'NEW_PIN_REQUIRED','status': 'error', 'message': 'new_pin is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(str(new_pin)) != 4 or not str(new_pin).isdigit():
        return Response(
            { 'code': 'PIN_MUST_EXACTLY_DIGITS','status': 'error', 'message': 'PIN must be exactly 4 digits'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # If PIN already set, verify current PIN
    if wallet.pin_hash:
        if not current_pin:
            return Response(
                { 'code': 'CURRENT_PIN_REQUIRED_CHANGE','status': 'error', 'message': 'current_pin is required to change an existing PIN'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not check_password(str(current_pin), wallet.pin_hash):
            return Response(
                { 'code': 'CURRENT_PIN_INCORRECT','status': 'error', 'message': 'Current PIN is incorrect'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    wallet.pin_hash = make_password(str(new_pin))
    wallet.save(update_fields=['pin_hash'])

    return Response({'status': 'success', 'message': 'PIN set successfully'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/deduct/  (internal - called by tournament registration)
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
            { 'code': 'AMOUNT_TOURNAMENT_ID_REQUIRED','status': 'error', 'message': 'amount and tournament_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return Response({ 'code': 'AMOUNT_MUST_INTEGER','status': 'error', 'message': 'amount must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

    if amount <= 0:
        return Response({ 'code': 'AMOUNT_MUST_POSITIVE','status': 'error', 'message': 'amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)

    if not wallet.pin_hash or not check_password(str(pin), wallet.pin_hash):
        return Response({ 'code': 'INVALID_PIN','status': 'error', 'message': 'Invalid PIN'}, status=status.HTTP_400_BAD_REQUEST)

    from vent_tournament.models import Tournament
    try:
        tournament = Tournament.objects.get(tournament_id=tournament_id)
    except Tournament.DoesNotExist:
        return Response({ 'code': 'TOURNAMENT_NOT_FOUND','status': 'error', 'message': 'Tournament not found'}, status=status.HTTP_404_NOT_FOUND)

    # Lock the wallet so a concurrent debit (e.g. two tabs registering) can't
    # overdraw the balance (F12). Balance is re-checked under the lock.
    with transaction.atomic():
        locked_wallet = UserWallet.objects.select_for_update().get(pk=wallet.pk)

        if locked_wallet.wallet_balance < amount:
            return Response({ 'code': 'INSUFFICIENT_BALANCE','status': 'error', 'message': 'Insufficient balance'}, status=status.HTTP_400_BAD_REQUEST)

        locked_wallet.wallet_balance -= amount
        locked_wallet.save(update_fields=['wallet_balance'])

        Transaction.objects.create(
            wallet=locked_wallet,
            type='deduction',
            amount=-amount,
            description=description,
            status='completed',
            tournament=tournament,
        )
        new_balance = locked_wallet.wallet_balance

    return Response({
        'status': 'success',
        'data': {'new_balance': new_balance}
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/withdraw/initiate/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def withdraw_initiate(request):
    """Request a payout, to a bank in naira or to a proved USDT address.

    ONE queue for both. The hold on the balance, the admin approval, the
    return on a denial, the notification and the audit line are identical
    whichever rail the money leaves by, and a second payout table would be a
    second place a balance is debited from. What differs is four lines: where
    it is going.

    The USDT half stops at the send itself, which is the one part that cannot
    be written until the custody question in `tasks/specs/crypto-and-custody.md`
    is answered. Everything up to it is here.
    """
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    amount = request.data.get('amount')
    pin = request.data.get('pin')
    method = str(request.data.get('method')
                 or WithdrawalRequest.METHOD_BANK).strip().lower()
    if method not in (WithdrawalRequest.METHOD_BANK,
                      WithdrawalRequest.METHOD_USDT):
        return Response(
            {'code': 'VALIDATION_ERROR', 'status': 'error',
             'message': 'Choose a bank transfer or a USDT payout.'},
            status=status.HTTP_400_BAD_REQUEST)

    address = None
    bank_name = account_number = account_name = ''

    if method == WithdrawalRequest.METHOD_USDT:
        if not payouts.usdt_enabled():
            # Named rather than silently absent. The pipeline is built; what
            # it waits on is the custody decision and a funded float, and
            # holding somebody's balance for a payout nobody can send is
            # worse than saying so.
            return Response(
                {'code': 'USDT_NOT_OPEN', 'status': 'error',
                 'message': 'USDT payouts are not open yet.'},
                status=status.HTTP_400_BAD_REQUEST)
        try:
            # A proved address, chosen from the list, never one typed into
            # this request. See `payouts.confirmed_address`.
            address = payouts.confirmed_address(
                wallet.user, request.data.get('address_ref'))
        except payouts.PayoutError as exc:
            http = (status.HTTP_404_NOT_FOUND if exc.code == 'NOT_FOUND'
                    else status.HTTP_400_BAD_REQUEST)
            return Response({'code': exc.code, 'status': 'error',
                             'message': str(exc)}, status=http)
        if not all([amount, pin]):
            return Response(
                {'code': 'AMOUNT_AND_PIN', 'status': 'error',
                 'message': 'amount and pin are required'},
                status=status.HTTP_400_BAD_REQUEST)
    else:
        bank_name = request.data.get('bank_name')
        account_number = request.data.get('account_number')
        account_name = request.data.get('account_name')
        if not all([amount, bank_name, account_number, account_name, pin]):
            return Response(
                { 'code': 'AMOUNT_BANK_NAME_ACCOUNT','status': 'error', 'message': 'amount, bank_name, account_number, account_name, and pin are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return Response({ 'code': 'AMOUNT_MUST_INTEGER','status': 'error', 'message': 'amount must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

    if amount <= 0:
        return Response({ 'code': 'AMOUNT_MUST_POSITIVE','status': 'error', 'message': 'amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)

    if not wallet.kyc_verified:
        return Response(
            { 'code': 'KYC_VERIFICATION_REQUIRED_BEFORE','status': 'error', 'message': 'KYC verification required before withdrawing'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not wallet.pin_hash or not check_password(str(pin), wallet.pin_hash):
        return Response({ 'code': 'INVALID_PIN','status': 'error', 'message': 'Invalid PIN'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        wallets.check_second_factor(wallet.user, request.data.get('code'))
    except wallets.WalletError as exc:
        return Response({'code': exc.code, 'status': 'error',
                         'message': str(exc)},
                        status=status.HTTP_400_BAD_REQUEST)

    # The floor and the daily ceiling, checked BEFORE anything is held. They
    # are the same numbers whichever rail the money leaves by, which is why
    # they are built now rather than waiting on the custody answer: a ceiling
    # is what limits how much a stolen account can take before anybody looks.
    try:
        payouts.check_limits(wallet, amount)
    except payouts.PayoutError as exc:
        # The numbers travel beside the code, so the translation can name them.
        # A sentence built in Python cannot be translated; a code with no
        # numbers cannot say which limit was hit.
        return Response(dict({'code': exc.code, 'status': 'error',
                              'message': str(exc)}, **exc.params),
                        status=status.HTTP_400_BAD_REQUEST)

    # The money is HELD here, not at approval.
    #
    # Before this, a request wrote a pending row and touched no balance, so
    # somebody could ask for their whole balance, spend it, and have the
    # approval fail on them days later. Worse, approval then wrote a SECOND
    # withdrawal line, so a single payout appeared twice on the statement and a
    # rejection left the first one pending for ever.
    #
    # One line per payout, and it is the held one. See `wallets.hold_for_payout`.
    try:
        with transaction.atomic():
            wr = WithdrawalRequest(
                wallet_id=wallet.pk,
                amount=amount,
                method=method,
                bank_name=bank_name or '',
                account_number=account_number or '',
                account_name=account_name or '',
                payout_address=address,
            )
            # The statement line says where it went, in the same words the
            # console and the email use. One function builds that sentence, so
            # a payout cannot be described three ways by three screens.
            wr.hold = wallets.hold_for_payout(
                wallet, amount, payouts.describe_destination(wr))
            wr.save()
    except wallets.WalletError as exc:
        return Response({'code': exc.code, 'status': 'error',
                         'message': str(exc)},
                        status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'status': 'success',
        'data': {
            'withdrawal_id': wr.id,
            'amount': wr.amount,
            'method': wr.method,
            'destination': payouts.describe_destination(wr),
            'status': wr.status,
            'message': 'Withdrawal request submitted. Pending admin approval.',
        }
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET/POST /auth/wallet/payout-addresses/
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def payout_addresses(request):
    """The crypto addresses this account may be paid to.

    One endpoint with an `action`, the same shape the team and organisation
    wallets use, rather than four routes carrying a row id. A payout address
    is somebody's money leaving, and a sequential id in a path lets anybody
    count how many exist and guess at somebody else's.

    Actions: `add` files one and emails the code, `confirm` proves it,
    `remove` takes it off.
    """
    wallet, err = _get_user_from_token(request)
    if err:
        return err
    user = wallet.user

    def listing(message=''):
        rows = [payouts.address_payload(r)
                for r in user.payout_addresses.all()]
        return Response({
            'status': 'success',
            'data': {
                'addresses': rows,
                'networks': [{'value': v, 'label': label}
                             for v, label in PayoutAddress.NETWORK_CHOICES],
                'limits': payouts.limits(),
                # Whether a payout to one of these may be asked for yet. The
                # screen reads this rather than assuming, so the day it is
                # switched on nothing else has to change.
                'usdt_enabled': payouts.usdt_enabled(),
            },
            'message': message,
        }, status=status.HTTP_200_OK)

    if request.method == 'GET':
        return listing()

    action = str(request.data.get('action') or 'add').strip().lower()
    try:
        if action == 'add':
            payouts.add_address(user, request.data.get('network'),
                                request.data.get('address'),
                                request.data.get('label'))
            return listing('Check your email for the code that confirms it.')
        if action == 'confirm':
            payouts.confirm_address(user, request.data.get('ref'),
                                    request.data.get('code'))
            return listing('That address is confirmed.')
        if action == 'remove':
            payouts.remove_address(user, request.data.get('ref'))
            return listing('That address has been removed.')
    except payouts.PayoutError as exc:
        http = (status.HTTP_404_NOT_FOUND if exc.code == 'NOT_FOUND'
                else status.HTTP_400_BAD_REQUEST)
        return Response({'code': exc.code, 'status': 'error',
                         'message': str(exc)}, status=http)

    return Response({'code': 'VALIDATION_ERROR', 'status': 'error',
                     'message': 'Say what to do: add, confirm or remove.'},
                    status=status.HTTP_400_BAD_REQUEST)


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
            'method': w.method,
            # One sentence naming where it went, built by the same function
            # the statement line and the console read.
            'destination': payouts.describe_destination(w),
            'bank_name': w.bank_name,
            'account_number': w.account_number[-4:].rjust(len(w.account_number), '*'),
            'account_name': w.account_name,
            # What the rail called it once it left: a bank reference or a
            # chain transaction hash. Empty until the money actually moves.
            'reference': w.payout_reference,
            'status': w.status,
            'admin_note': w.admin_note,
            'requested_at': w.requested_at,
            'processed_at': w.processed_at,
        }
        for w in withdrawals.select_related('payout_address')
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
            { 'code': 'DOCUMENT_IMAGE_REQUIRED','status': 'error', 'message': 'document_image is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Replace any existing pending document of same type
    KYCDocument.objects.filter(user=user, document_type=document_type, status='pending').delete()

    doc = KYCDocument.objects.create(
        user=user,
        document_type=document_type,
        document_image=document_image,
    )
    # Wherever identity checking happens, it happens through here. Today that
    # is a V-ENT reviewer; the day a provider is contracted, this same call
    # sends it and stores the reference it comes back with, and no screen
    # changes. See vent_auth/kyc.py for the providers under consideration and
    # what has to be decided before one is wired.
    kyc_service.submit(doc)

    return Response({
        'status': 'success',
        'data': {
            'kyc_id': doc.id,
            'document_type': doc.document_type,
            'status': doc.status,
            'submitted_at': doc.submitted_at,
            'checked_by': kyc_service.describe(doc),
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
                # Who verified it, when, and against what. A verified wallet
                # that cannot answer that is a compliance record with nothing
                # behind it.
                'checked_by': kyc_service.describe(latest_doc),
            } if latest_doc else None,
        }
    }, status=status.HTTP_200_OK)

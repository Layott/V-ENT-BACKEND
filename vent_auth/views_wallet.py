import logging
import os
from .views_helpers import session_timeout_minutes, get_or_create_user_wallet
import uuid
from datetime import timedelta

import requests as http_requests
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from django.contrib.auth.hashers import make_password
from . import kyc as kyc_service
from . import payouts
from . import coins, wallets
from .models import (Users, UserWallet, TeamWallet, OrgWallet, Transaction,
                     WithdrawalRequest, KYCDocument, PayoutAddress)
from vent_auth.errors import gateway_down, gateway_refused
from . import inputs
from .flutterwave_currency import choice as _fx_choice
# Also under a second name for the one view whose own variable is called
# `coins` (pay_shortfall), so the module stays reachable inside it.
from vent_auth import coins as vent_coins


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


def coins_to_ngn(amount) -> int:
    """What a coin balance is worth in NGN. The inverse of _ngn_to_coins.

    Balances hold hundredths of a coin since 30 September 2026, so 0.8 VC is
    800 naira; `int(amount)` first would have made it 0.
    """
    return int(coins.exact(amount) * NGN_PER_COIN)


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
            status=status.HTTP_401_UNAUTHORIZED,
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

def payment_method(reference):
    """How a row was paid, read off its reference, for the statement."""
    ref = str(reference or '')
    if ref.startswith('FLW-'):
        return 'flutterwave'
    if ref.startswith('VENT-'):
        return 'paystack'
    return 'wallet'


def wallet_summary(wallet, zone_name=''):
    """Money in and out over the WHOLE history, completed rows only.

    The wallet page used to add these up from the latest 20 rows it had been
    sent, pending ones included, so a top-up somebody started and never paid
    showed as money earned (CEO, 29 September 2026). A row counts once it has
    happened. The month is the reader's month, in their own zone.
    """
    from zoneinfo import ZoneInfo
    from django.db.models import Sum
    try:
        zone = ZoneInfo(zone_name) if zone_name else timezone.get_current_timezone()
    except Exception:                                          # noqa: BLE001
        zone = timezone.get_current_timezone()
    now = timezone.now().astimezone(zone)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    done = wallet.transactions.filter(status='completed')

    def total(qs):
        return coins.as_json(qs.aggregate(n=Sum('amount'))['n'] or 0)
    return {
        'month_in': total(done.filter(amount__gt=0, created_at__gte=month_start)),
        'month_out': -total(done.filter(amount__lt=0, created_at__gte=month_start)),
        'lifetime_in': total(done.filter(amount__gt=0)),
        'pending_count': wallet.transactions.filter(status='pending').count(),
    }


@api_view(['GET'])
def get_wallet_transactions(request):
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    try:
        page = max(1, inputs.read_int(request.GET, 'page', default=1))
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
            'method': payment_method(t.reference),
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
            'summary': wallet_summary(wallet, str(request.GET.get('tz') or '')[:64]),
        }
    }, status=status.HTTP_200_OK)


def topup_ceiling_ngn():
    """The most one account may top up in a day, in naira. 0 means none."""
    from .models import AdminSetting
    fees = AdminSetting.load().merged().get('platform_fees') or {}
    try:
        return max(0, int(fees.get('topup_max_ngn_per_day') or 0))
    except (TypeError, ValueError):
        return 0


def _over_topup_ceiling(wallet, amount_ngn):
    """None when this top-up fits under the day's ceiling, else the numbers."""
    cap = topup_ceiling_ngn()
    if cap <= 0:
        return None
    since = timezone.now() - timedelta(days=1)
    coins = sum(Transaction.objects.filter(
        wallet=wallet, type='top_up', created_at__gte=since,
        status__in=('pending', 'completed'),
    ).values_list('amount', flat=True))
    already = coins_to_ngn(coins)
    if already + int(amount_ngn) > cap:
        return {'daily_max_ngn': cap, 'already_ngn': already}
    return None


# ---------------------------------------------------------------------------
# GET /auth/wallet/pay/methods/  and  POST /auth/wallet/pay/
#
# CEO, 13 September 2026: "i hope people can still bu stuff directly on the
# platform without having to buy V-ENT coins, that option must always be
# vaailable." These two are how any purchase screen offers a card at the price
# it just quoted, without sending anybody to the wallet first.
# ---------------------------------------------------------------------------

@api_view(['GET'])
def pay_methods(request):
    """What this person can pay with, before a screen offers anything."""
    wallet, err = _get_user_from_token(request)
    if err:
        return err
    from . import pay
    return Response({'status': 'success', 'data': pay.options(wallet.user)},
                    status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def pay_providers(request):
    """GET /auth/wallet/pay/providers/ - which gateways can take a payment now.

    Public, because a guest buying a ticket has no account and still chooses
    between Paystack and Flutterwave. Names and a test flag only; never a key.
    """
    from . import pay
    return Response({'status': 'success', 'code': 'OK',
                     'data': {'providers': pay.providers()}, 'message': 'Providers'},
                    status=status.HTTP_200_OK)


@api_view(['POST'])
def pay_shortfall(request):
    """Cover what a purchase is short, with a card. `{coins}` or `{coins_ngn}`.

    Answers `paid: true` when a saved card covered it, and the caller carries
    straight on with the purchase they were making; otherwise it answers an
    `authorization_url` to send them to, and the coins arrive through the same
    `topup/verify` the wallet's own top-up uses.
    """
    wallet, err = _get_user_from_token(request)
    if err:
        return err
    from . import pay

    try:
        coins = inputs.read_int(request.data, 'coins', default=0)
    except (TypeError, ValueError):
        coins = 0
    if coins <= 0:
        return Response({'code': 'NOTHING_TO_PAY', 'status': 'error',
                         'message': 'Say how many VENT COINS to cover.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Only ever the shortfall: charging for coins somebody already has is
    # taking money for nothing, and a screen that computed the difference
    # itself would get it wrong the moment two tabs are open.
    short = pay.shortfall_vc(wallet, coins)
    if short <= 0:
        return Response({'status': 'success',
                         'data': {'paid': True, 'coins_added': 0,
                                  'balance_vc': vent_coins.as_json(wallet.wallet_balance),
                                  'already_covered': True},
                         'message': 'There are enough VENT COINS already.'},
                        status=status.HTTP_200_OK)

    try:
        data = pay.cover_or_start(
            wallet.user, short,
            str(request.data.get('callback_url') or ''),
            purpose=str(request.data.get('purpose') or 'purchase')[:40],
            card_id=request.data.get('card_id'),
            provider='flutterwave' if pay.choose_provider(request.data.get('provider')) == 'flutterwave'
            else 'paystack',
            **_fx_choice(request))
    except pay.PayError as exc:
        http = (status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.code == pay.CARDS_UNAVAILABLE else
                status.HTTP_502_BAD_GATEWAY if exc.code == pay.GATEWAY_ERROR else
                status.HTTP_400_BAD_REQUEST)
        return Response(dict({'code': exc.code, 'status': 'error',
                              'message': exc.message}, **exc.params), status=http)

    return Response({'status': 'success', 'data': dict(data, needed_vc=short)},
                    status=status.HTTP_200_OK)


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
            {'code': 'BELOW_MINIMUM_TOPUP', 'status': 'error',
             'minimum_ngn': NGN_PER_COIN,
             'message': f'Minimum top-up is {NGN_PER_COIN:,} NGN (1 VENT COIN)'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # The daily ceiling, from the dashboard (topup_max_ngn_per_day; 0 means
    # none). Counted from what this wallet asked for in the last day, pending
    # rows included: twenty top-ups started and not yet paid are exactly the
    # case a ceiling exists for. Checked BEFORE Paystack is asked for a link,
    # because a refusal after somebody has paid is a refund, not a refusal.
    over = _over_topup_ceiling(wallet, amount_ngn)
    if over is not None:
        return Response(dict({'code': 'OVER_TOPUP_LIMIT', 'status': 'error',
                              'message': 'That is over the daily top-up limit of '
                                         '%s NGN. You have topped up %s NGN in the '
                                         'last day.' % (over['daily_max_ngn'], over['already_ngn'])},
                             **over),
                        status=status.HTTP_400_BAD_REQUEST)

    vent_coins = _ngn_to_coins(amount_ngn)

    # Flutterwave, when the person chose it (CEO, 29 September 2026). Same
    # pending row, same verify door; the reference's FLW- prefix is what tells
    # the verify step where to ask.
    from . import pay
    if pay.choose_provider(request.data.get('provider')) == 'flutterwave':
        from vent_auth import flutterwave as _flw
        if not _flw.configured():
            return Response({'status': 'error', 'code': 'PROVIDER_UNAVAILABLE', 'data': {},
                             'message': 'Flutterwave is not set up on this platform yet.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        from vent_auth import flutterwave_currency as _fx
        reference = _flw.new_reference('TOP')
        try:
            started = _flw.start(
                reference=reference, amount_ngn=amount_ngn, email=wallet.user.email,
                name=wallet.user.full_name or wallet.user.username,
                callback_url=str(request.data.get('callback_url') or '') or _topup_return_url(),
                title='V-ENT', description='%s VENT COINS' % vent_coins,
                meta={'user_id': wallet.user.user_id, 'vent_coins': vent_coins},
                **_fx.choice(request))
        except _fx.CurrencyError as exc:
            return Response({'status': 'error', 'code': exc.code, 'data': {},
                             'message': exc.message}, status=status.HTTP_400_BAD_REQUEST)
        except _flw.Unreachable:
            return Response({'status': 'error', 'code': 'GATEWAY_ERROR', 'data': {},
                             'message': 'The payment gateway did not answer. Nothing was charged.'},
                            status=status.HTTP_502_BAD_GATEWAY)
        except _flw.Refused as exc:
            return gateway_refused(exc)
        Transaction.objects.create(
            wallet=wallet, type='top_up', amount=vent_coins,
            description=f'Top up via Flutterwave - {amount_ngn} NGN',
            status='pending', reference=reference)
        return Response({'status': 'success', 'data': {
            'authorization_url': started['authorization_url'], 'reference': reference,
            'vent_coins': vent_coins, 'amount_ngn': amount_ngn,
            'currency': started.get('currency', 'NGN'), 'amount': started.get('amount'),
            'provider': 'flutterwave', 'test_mode': _flw.is_test(),
        }}, status=status.HTTP_200_OK)

    # No key on this box: say so plainly, before anybody is sent anywhere.
    # Production had none on 29 September 2026 and people read Paystack's
    # own "Format is Authorization Bearer [secret key]" instead.
    from vent_auth import paystack as _ps
    if not _ps.configured():
        return Response({'status': 'error', 'code': 'PROVIDER_UNAVAILABLE', 'data': {},
                         'message': 'Card payments are not available right now. Choose another way to pay.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)

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
    # Back to the page that verifies it. Without this Paystack used the
    # dashboard's default, and the top-up page never sent anybody to pay at
    # all (29 September 2026, inbox 352).
    if request.data.get('callback_url'):
        payload['callback_url'] = str(request.data.get('callback_url'))

    # One initialize for every door (vent_auth.paystack.initialize). This
    # copy threw Paystack's reason away with raise_for_status.
    from vent_auth import paystack as _paystack
    try:
        data = {'status': True, 'data': _paystack.initialize(payload)}
    except _paystack.Unreachable:
        return Response(
            {'status': 'error', 'code': 'GATEWAY_ERROR', 'data': {},
             'message': 'The payment gateway did not answer. Nothing was charged.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except _paystack.Refused as exc:
        return gateway_refused(exc)

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

def _topup_return_url():
    site = os.environ.get('FRONTEND_PUBLIC_URL', 'https://v-ent.co').rstrip('/')
    return '%s/wallet-topup-callback' % site


def settle_flutterwave_topup(reference):
    """Confirm a Flutterwave top-up and credit it, once.

    The one path for both arrivals: the payer's browser coming back and the
    webhook. Locked on the pending row, so the second arrival reads
    `completed` and credits nothing. Returns (code, txn, balance): code is
    'credited', 'already', 'not_paid', 'not_found' or 'unreachable'.
    """
    from vent_auth import flutterwave as _flw
    with transaction.atomic():
        txn = (Transaction.objects.select_for_update()
               .filter(reference=reference, type='top_up').select_related('wallet').first())
        if txn is None:
            return 'not_found', None, None
        if txn.status == 'completed':
            return 'already', txn, txn.wallet.wallet_balance
        if txn.status in ('failed', 'cancelled'):
            return 'not_paid', txn, None
        try:
            found = _flw.verify(reference, expected_ngn=coins_to_ngn(txn.amount))
        except _flw.Unreachable:
            return 'unreachable', txn, None
        if not found['ok']:
            if found['status'] in ('failed', 'cancelled'):
                txn.status = 'failed'
                txn.save(update_fields=['status'])
            return 'not_paid', txn, None
        locked = UserWallet.objects.select_for_update().get(pk=txn.wallet_id)
        locked.wallet_balance += txn.amount
        locked.save(update_fields=['wallet_balance'])
        txn.status = 'completed'
        txn.save(update_fields=['status'])
        return 'credited', txn, locked.wallet_balance


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

    from vent_auth import flutterwave as _flw
    if _flw.owns(reference):
        if not Transaction.objects.filter(wallet=wallet, reference=reference, type='top_up').exists():
            return Response({'code': 'TRANSACTION_NOT_FOUND', 'status': 'error',
                             'message': 'Transaction not found'}, status=status.HTTP_404_NOT_FOUND)
        code, txn, balance = settle_flutterwave_topup(reference)
        if code == 'unreachable':
            return Response({'code': 'PAYMENT_GATEWAY', 'status': 'error',
                             'message': 'The payment gateway could not be reached.'},
                            status=status.HTTP_502_BAD_GATEWAY)
        if code == 'not_paid':
            return Response({'code': 'PAYMENT_NOT_SUCCESSFUL', 'status': 'error',
                             'message': 'Payment not successful'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'status': 'success', 'data': {
            'message': 'Top-up successful' if code == 'credited' else 'Already verified',
            'credited': code == 'credited', 'idempotent': code == 'already',
            'coins_added': txn.amount if code == 'credited' else 0,
            'new_balance': balance, 'balance': balance,
            'card_saved': False, 'card': None,
        }}, status=status.HTTP_200_OK)

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
            return gateway_down(e)

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

    # Hundredths of a coin (coins.py): 0.2 VC is 200 naira, and the CEO asked
    # for amounts under 1000 naira to move (30 September 2026).
    amount, refused = _coins_or_refusal(amount)
    if refused:
        return refused

    try:
        wallets.check_pin(wallet, pin)
    except wallets.WalletError as exc:
        return Response(exc.body(), status=status.HTTP_400_BAD_REQUEST)

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
                f'You received {coins.label(amount)} VC from @{sender_username}',
                link='/wallets',
                metadata={'amount': coins.as_json(amount), 'from': sender_username},
            )
        except Exception:
            pass

    return Response({
        'status': 'success',
        'data': {
            'new_balance': coins.as_json(new_balance),
            'sent_to': named,
            'transaction_id': debit.id,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/wallet/send-many/
# ---------------------------------------------------------------------------

#: The most recipients one send may carry. Enough for a squad or a prize
#: table; past it, a list is a payout and belongs on the payout screen.
SEND_MANY_LIMIT = 20


def _refuse(code, message, http=status.HTTP_400_BAD_REQUEST, **extra):
    body = {'code': code, 'status': 'error', 'message': message}
    body.update(extra)
    return Response(body, status=http)


#: What each coins.parse refusal says. The screen translates by the code.
_AMOUNT_SAYS = {
    'AMOUNT_REQUIRED': 'Say how much to send.',
    'AMOUNT_MUST_NUMBER': 'The amount has to be a number, like 5 or 0.25.',
    'AMOUNT_TOO_PRECISE': 'Coins go to two decimal places at most, like 0.25.',
    'AMOUNT_MUST_POSITIVE': 'amount must be positive',
}


def _coins_or_refusal(value, **extra):
    """(amount, None) for a sendable amount, or (None, the refusal)."""
    try:
        return coins.parse(value), None
    except coins.AmountError as exc:
        return None, _refuse(exc.code, _AMOUNT_SAYS[exc.code], **extra)


@api_view(['POST'])
def send_many(request):
    """Send coins to several people, teams or organisations at once (inbox 386).

    CEO, 30 September 2026: "What of if I want to send to multiple people at
    the same time?"

    One PIN and one second factor for the whole send. Every recipient is
    checked (exists, is not the sender, appears once, a positive amount in hundredths)
    before anything moves, the total is checked against the balance, and the
    transfers run in one database transaction: all of them land or none do,
    so nobody is left paid while a friend in the same send is not.

    Body: {recipients: [{to_kind, to, amount}], pin, code?, note?}
    """
    wallet, err = _get_user_from_token(request)
    if err:
        return err

    rows = request.data.get('recipients')
    pin = request.data.get('pin')
    note = str(request.data.get('note', '') or '')[:200]
    if not isinstance(rows, list) or not rows:
        return _refuse('NO_RECIPIENTS', 'Add at least one person to send to.')
    if len(rows) > SEND_MANY_LIMIT:
        return _refuse('TOO_MANY_RECIPIENTS', 'One send can go to at most %d.' % SEND_MANY_LIMIT,
                       limit=SEND_MANY_LIMIT)
    if not pin:
        return _refuse('PIN_REQUIRED', 'pin is required')

    plan = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return _refuse('INVALID_INPUT', 'Some of the details could not be read.', field='recipients')
        to_kind = row.get('to_kind') or 'user'
        to_ref = row.get('to')
        amount, refused = _coins_or_refusal(row.get('amount'), index=index)
        if refused:
            return refused
        try:
            target = wallets.resolve_target(to_kind, to_ref)
        except wallets.WalletError as exc:
            http = status.HTTP_404_NOT_FOUND if exc.code == 'NOT_FOUND' else status.HTTP_400_BAD_REQUEST
            return _refuse(exc.code, str(exc), http, index=index)
        if isinstance(target, UserWallet) and target.pk == wallet.pk:
            return _refuse('CANNOT_SEND_YOURSELF', 'Cannot send to yourself', index=index)
        key = (type(target).__name__, target.pk)
        if key in seen:
            return _refuse('DUPLICATE_RECIPIENT', 'Somebody is on the list twice.', index=index)
        seen.add(key)
        plan.append((target, amount))

    total = sum(amount for _target, amount in plan)
    try:
        wallets.check_pin(wallet, pin)
    except wallets.WalletError as exc:
        return Response(exc.body(), status=status.HTTP_400_BAD_REQUEST)
    try:
        wallets.check_second_factor(wallet.user, request.data.get('code'))
    except wallets.WalletError as exc:
        return _refuse(exc.code, str(exc))
    wallet.refresh_from_db()
    if wallet.wallet_balance < total:
        return _refuse('INSUFFICIENT_BALANCE',
                       'There is not enough in that wallet: %s VC available.' % coins.label(wallet.wallet_balance),
                       available=coins.as_json(wallet.wallet_balance), total=coins.as_json(total))

    sender_username = wallet.user.username
    done = []
    try:
        with transaction.atomic():
            for target, amount in plan:
                to_user = target.user if isinstance(target, UserWallet) else None
                named = wallets.describe(target)
                kinds = ('send', 'receive') if to_user is not None else ('transfer', 'transfer')
                debit, _credit = wallets.transfer(
                    wallet, target, amount,
                    debit_kind=kinds[0], credit_kind=kinds[1],
                    debit_note='Sent to %s%s' % (named, (': ' + note) if note else ''),
                    credit_note='Received from @%s%s' % (sender_username, (': ' + note) if note else ''),
                )
                done.append((to_user, named, amount, debit.id))
    except wallets.WalletError as exc:
        return _refuse(exc.code, str(exc))

    # After the money, never inside it: a notification that fails must not
    # undo a send that succeeded.
    for to_user, _named, amount, _txn in done:
        if to_user is None:
            continue
        try:
            from vent_auth.views_notifications import create_notification
            create_notification(to_user, 'wallet', f'You received {coins.label(amount)} VC from @{sender_username}',
                                link='/wallets', metadata={'amount': coins.as_json(amount), 'from': sender_username})
        except Exception:
            pass

    wallet.refresh_from_db()
    return Response({
        'status': 'success',
        'data': {
            'new_balance': coins.as_json(wallet.wallet_balance),
            'total': coins.as_json(total),
            'sent': [{'to': named, 'amount': coins.as_json(amount), 'transaction_id': txn}
                     for _user, named, amount, txn in done],
            'transaction_id': done[0][3] if done else None,
        },
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

    try:
        wallets.check_pin(wallet, pin)
    except wallets.WalletError as exc:
        return Response(exc.body(), status=status.HTTP_400_BAD_REQUEST)

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
        try:
            wallets.check_pin(wallet, current_pin)
        except wallets.WalletError as exc:
            # The same count as every other door: guessing the current PIN
            # on the change screen is guessing the PIN.
            if exc.code == 'PIN_LOCKED':
                return Response(exc.body(), status=status.HTTP_400_BAD_REQUEST)
            return Response(
                dict({ 'code': 'CURRENT_PIN_INCORRECT','status': 'error', 'message': 'Current PIN is incorrect'}, **exc.params),
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

    try:
        wallets.check_pin(wallet, pin)
    except wallets.WalletError as exc:
        return Response(exc.body(), status=status.HTTP_400_BAD_REQUEST)

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

    try:
        wallets.check_pin(wallet, pin)
    except wallets.WalletError as exc:
        return Response(exc.body(), status=status.HTTP_400_BAD_REQUEST)

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
    # What comes off it, at today's rate, copied onto the row so the queue,
    # the email and the statement all say the same number for ever.
    priced = payouts.fee_on(amount)

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
                fee_pct=priced['pct'],
                fee_flat_ngn=priced['flat_ngn'],
                fee_ngn=priced['fee_ngn'],
                payout_ngn=priced['payout_ngn'],
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
            'fee_ngn': float(wr.fee_ngn),
            'payout_ngn': float(wr.payout_ngn),
            'message': 'Withdrawal request submitted. Pending admin approval.',
        }
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /auth/wallet/withdraw/quote/?amount=<vc>
# ---------------------------------------------------------------------------

@api_view(['GET'])
def withdraw_quote(request):
    """What a payout of this many coins would land as, before the PIN.

    The screen used to compute "2% + 50 naira" itself while the server took
    nothing, so the number a person was shown was not the number they got.
    One function prices a payout (`payouts.fee_on`) and this is the only way
    a screen learns the answer. The limits ride along so the same call can
    say whether the amount is allowed at all.
    """
    wallet, err = _get_user_from_token(request)
    if err:
        return err
    try:
        amount = max(0, inputs.read_int(request.query_params, 'amount', default=0))
    except (TypeError, ValueError):
        amount = 0
    priced = payouts.fee_on(amount)
    return Response({
        'status': 'success',
        'data': {
            'amount_vc': amount,
            'gross_ngn': float(priced['gross_ngn']),
            'fee_pct': float(priced['pct']),
            'fee_flat_ngn': float(priced['flat_ngn']),
            'fee_ngn': float(priced['fee_ngn']),
            'payout_ngn': float(priced['payout_ngn']),
            'limits': payouts.limits(),
        },
    }, status=status.HTTP_200_OK)


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
            'fee_ngn': float(w.fee_ngn or 0),
            'payout_ngn': float(w.payout_ngn or 0),
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

    # What the file really is, from its bytes, and how big (owner rule R70).
    # An identity document went to storage unchecked until 29 September 2026.
    from .uploads import image_refusal
    refused = image_refusal(document_image, 8 * 1024 * 1024)
    if refused:
        return Response(
            {'code': refused, 'status': 'error', 'data': {'limit_mb': 8},
             'message': ('The photo must be 8 MB or smaller.' if refused == 'IMAGE_TOO_LARGE'
                         else 'The document must be a photo: PNG, JPG or WebP.')},
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

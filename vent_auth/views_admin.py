import os
from datetime import timedelta

from django.contrib.auth import authenticate
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    Users, Waitlist, UserWallet, UserProfile,
    Transaction, WithdrawalRequest, KYCDocument, AdminAction,
)
from .views_helpers import send_email, generate_session_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_admin_user(request):
    """Return (user, error_response). User must be is_staff and session active."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return None, Response(
            {'status': 'error', 'message': 'Authorization header is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    token = session_token.split(' ', 1)[1]
    try:
        user = Users.objects.get(login_session_token=token)
    except Users.DoesNotExist:
        return None, Response(
            {'status': 'error', 'message': 'Invalid or expired session token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if (
        user.login_session_created_at is None
        or timezone.now() - user.login_session_created_at > timedelta(minutes=120)
    ):
        return None, Response(
            {'status': 'error', 'message': 'Session token has expired'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.is_staff:
        return None, Response(
            {'status': 'error', 'message': 'Admin access required'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return user, None


def _log_action(admin, action_type, target_model, target_id, reason='', metadata=None):
    AdminAction.objects.create(
        admin=admin,
        action_type=action_type,
        target_model=target_model,
        target_id=str(target_id),
        reason=reason,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Admin Login
# ---------------------------------------------------------------------------

@api_view(['POST'])
def admin_login(request):
    """Authenticate an admin user. Returns login_session_token on success."""
    username = request.data.get('username')
    password = request.data.get('password')

    if not username or not password:
        return Response(
            {'status': 'error', 'message': 'Username and password are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(username=username, password=password)
    if user is None:
        # Try email
        try:
            u = Users.objects.get(email=username)
            user = authenticate(username=u.username, password=password)
        except Users.DoesNotExist:
            pass

    if user is None or not user.is_staff:
        return Response(
            {'status': 'error', 'message': 'Invalid credentials or not an admin'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    token = generate_session_token()
    user.login_session_token = token
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_token', 'login_session_created_at'])

    return Response({
        'status': 'success',
        'message': 'Admin login successful',
        'data': {
            'session_token': token,
            'username': user.username,
            'email': user.email,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Platform Metrics — GET /auth/admin/metrics/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def admin_metrics(request):
    admin, err = _get_admin_user(request)
    if err:
        return err

    from vent_tournament.models import Tournament
    from django.db.models import Sum

    today = timezone.now().date()

    total_users = Users.objects.count()
    new_users_today = Users.objects.filter(date_joined__date=today).count()
    active_tournaments = Tournament.objects.filter(
        is_draft=False,
        start_date_and_time__lte=timezone.now(),
        end_date_and_time__gte=timezone.now(),
    ).count()
    total_tournaments = Tournament.objects.filter(is_draft=False).count()
    coins_in_circulation = UserWallet.objects.aggregate(
        total=Sum('wallet_balance')
    )['total'] or 0
    pending_withdrawals = WithdrawalRequest.objects.filter(status='pending').count()
    pending_kyc = KYCDocument.objects.filter(status='pending').count()

    from vent_tournament.models import TournamentDispute
    open_disputes = TournamentDispute.objects.filter(status='open').count()

    return Response({
        'status': 'success',
        'data': {
            'total_users': total_users,
            'new_users_today': new_users_today,
            'active_tournaments': active_tournaments,
            'total_tournaments_all_time': total_tournaments,
            'vent_coins_in_circulation': coins_in_circulation,
            'pending_withdrawals': pending_withdrawals,
            'pending_kyc': pending_kyc,
            'open_disputes': open_disputes,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@api_view(['GET'])
def admin_list_users(request):
    """GET /auth/admin/users/ — paginated, searchable, filterable."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    qs = Users.objects.select_related('wallet').order_by('-date_joined')

    q = request.GET.get('q')
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))

    role_filter = request.GET.get('role')
    if role_filter:
        qs = qs.filter(role=role_filter)

    is_active_filter = request.GET.get('is_active')
    if is_active_filter is not None:
        qs = qs.filter(is_active=(is_active_filter.lower() == 'true'))

    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    per_page = 25
    offset = (page - 1) * per_page
    total = qs.count()
    users = qs[offset: offset + per_page]

    def kyc_status(user):
        doc = user.kyc_documents.order_by('-submitted_at').first()
        return doc.status if doc else 'not_submitted'

    data = [
        {
            'user_id': u.user_id,
            'username': u.username,
            'email': u.email,
            'full_name': u.full_name,
            'role': u.role,
            'is_active': u.is_active,
            'is_staff': u.is_staff,
            'kyc_status': kyc_status(u),
            'wallet_balance': u.wallet.wallet_balance if hasattr(u, 'wallet') else 0,
            'joined_at': u.date_joined,
        }
        for u in users
    ]

    return Response({
        'status': 'success',
        'data': {'users': data, 'total': total, 'page': page, 'per_page': per_page}
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
def admin_get_user(request, user_id):
    """GET /auth/admin/users/{id}/ — full user detail."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    user = get_object_or_404(Users, user_id=user_id)

    try:
        wallet = user.wallet
        wallet_data = {
            'balance': wallet.wallet_balance,
            'kyc_verified': wallet.kyc_verified,
            'pin_set': bool(wallet.pin_hash),
        }
    except UserWallet.DoesNotExist:
        wallet_data = None

    from vent_tournament.models import TournamentRegistration
    tournaments_played = TournamentRegistration.objects.filter(
        user=user, status='confirmed'
    ).count()

    kyc_docs = [
        {
            'id': d.id,
            'document_type': d.document_type,
            'status': d.status,
            'submitted_at': d.submitted_at,
            'rejection_reason': d.rejection_reason,
        }
        for d in user.kyc_documents.order_by('-submitted_at')
    ]

    return Response({
        'status': 'success',
        'data': {
            'user_id': user.user_id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'role': user.role,
            'is_active': user.is_active,
            'is_staff': user.is_staff,
            'signup_type': user.signup_type,
            'date_joined': user.date_joined,
            'last_login': user.last_login,
            'wallet': wallet_data,
            'tournaments_played': tournaments_played,
            'kyc_documents': kyc_docs,
        }
    }, status=status.HTTP_200_OK)


@api_view(['PATCH'])
def admin_ban_user(request, user_id):
    """PATCH /auth/admin/users/{id}/ban/ — ban or unban a user."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    user = get_object_or_404(Users, user_id=user_id)
    ban = request.data.get('ban')  # True to ban, False to unban
    reason = request.data.get('reason', '')

    if ban is None:
        return Response(
            {'status': 'error', 'message': '"ban" (true/false) is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user.user_id == admin.user_id:
        return Response(
            {'status': 'error', 'message': 'Cannot ban yourself'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.is_active = not bool(ban)
    user.save(update_fields=['is_active'])

    action = 'ban_user' if ban else 'unban_user'
    _log_action(admin, action, 'User', user_id, reason=reason)

    return Response({
        'status': 'success',
        'message': f'User {"banned" if ban else "unbanned"} successfully',
    }, status=status.HTTP_200_OK)


@api_view(['PATCH'])
def admin_set_user_role(request, user_id):
    """PATCH /auth/admin/users/{id}/role/ — assign role."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    user = get_object_or_404(Users, user_id=user_id)
    role = request.data.get('role')
    valid_roles = ['user', 'organizer', 'admin']

    if role not in valid_roles:
        return Response(
            {'status': 'error', 'message': f'role must be one of: {", ".join(valid_roles)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_role = user.role
    user.role = role
    if role == 'admin':
        user.is_staff = True
    elif old_role == 'admin':
        user.is_staff = False
    user.save(update_fields=['role', 'is_staff'])

    _log_action(admin, 'set_role', 'User', user_id, metadata={'old_role': old_role, 'new_role': role})

    return Response({
        'status': 'success',
        'message': f'Role updated to {role}',
    }, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def admin_delete_user(request, user_id):
    """DELETE /auth/admin/users/{id}/ — permanently delete account."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    user = get_object_or_404(Users, user_id=user_id)
    reason = request.data.get('reason', '')
    confirm = request.data.get('confirm')

    if not confirm:
        return Response(
            {'status': 'error', 'message': 'confirm=true is required to delete an account'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user.user_id == admin.user_id:
        return Response(
            {'status': 'error', 'message': 'Cannot delete your own account'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    _log_action(admin, 'delete_user', 'User', user_id, reason=reason,
                metadata={'username': user.username, 'email': user.email})
    user.delete()

    return Response({'status': 'success', 'message': 'Account deleted'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Tournament Oversight
# ---------------------------------------------------------------------------

@api_view(['GET'])
def admin_list_tournaments(request):
    """GET /auth/admin/tournaments/ — all tournaments with dispute flags."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    from vent_tournament.models import Tournament, TournamentDispute

    qs = Tournament.objects.select_related('tournament_creator', 'tournament_game').order_by('-start_date_and_time')

    data = [
        {
            'tournament_id': t.tournament_id,
            'tournament_title': t.tournament_title,
            'game': t.tournament_game.game_title,
            'organizer': t.tournament_creator.username,
            'start_date_and_time': t.start_date_and_time,
            'end_date_and_time': t.end_date_and_time,
            'is_draft': t.is_draft,
            'entry_fee': t.entry_fee,
            'open_disputes': t.disputes.filter(status='open').count(),
            'registrations': t.registrations.count(),
        }
        for t in qs
    ]

    return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
def admin_get_tournament(request, tournament_id):
    """GET /auth/admin/tournaments/{id}/ — tournament detail with disputes."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    from vent_tournament.models import Tournament

    t = get_object_or_404(Tournament, tournament_id=tournament_id)

    disputes = [
        {
            'dispute_id': d.id,
            'raised_by': d.raised_by.username,
            'description': d.description,
            'status': d.status,
            'created_at': d.created_at,
            'match_id': d.match_id,
        }
        for d in t.disputes.select_related('raised_by').order_by('-created_at')
    ]

    registrations = [
        {
            'registration_id': r.id,
            'participant': r.team.team_name if r.team else r.user.username if r.user else '—',
            'type': 'team' if r.team else 'individual',
            'status': r.status,
            'entry_fee_paid': r.entry_fee_paid,
            'registered_at': r.registered_at,
        }
        for r in t.registrations.select_related('team', 'user').order_by('registered_at')
    ]

    return Response({
        'status': 'success',
        'data': {
            'tournament_id': t.tournament_id,
            'tournament_title': t.tournament_title,
            'organizer': t.tournament_creator.username,
            'is_draft': t.is_draft,
            'disputes': disputes,
            'registrations': registrations,
        }
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def admin_resolve_dispute(request, tournament_id):
    """POST /auth/admin/tournaments/{id}/dispute/resolve/ — resolve a dispute."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    from vent_tournament.models import TournamentDispute

    dispute_id = request.data.get('dispute_id')
    resolution = request.data.get('resolution')  # 'resolved' or 'dismissed'
    note = request.data.get('note', '')

    if not dispute_id or resolution not in ('resolved', 'dismissed'):
        return Response(
            {'status': 'error', 'message': 'dispute_id and resolution (resolved/dismissed) are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    dispute = get_object_or_404(TournamentDispute, id=dispute_id, tournament_id=tournament_id)
    dispute.status = resolution
    dispute.resolution_note = note
    dispute.resolved_at = timezone.now()
    dispute.save(update_fields=['status', 'resolution_note', 'resolved_at'])

    _log_action(admin, 'resolve_dispute', 'TournamentDispute', dispute_id,
                reason=note, metadata={'resolution': resolution})

    return Response({'status': 'success', 'message': f'Dispute {resolution}'}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
def admin_override_match_score(request, match_id):
    """PATCH /auth/admin/matches/{id}/score/ — override bracket match score."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    from vent_tournament.models import BracketMatch, TournamentRegistration

    match = get_object_or_404(BracketMatch, id=match_id)
    score_p1 = request.data.get('score_p1')
    score_p2 = request.data.get('score_p2')
    winner_registration_id = request.data.get('winner_registration_id')
    reason = request.data.get('reason', '')

    if score_p1 is None or score_p2 is None or not winner_registration_id:
        return Response(
            {'status': 'error', 'message': 'score_p1, score_p2, and winner_registration_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    winner = get_object_or_404(TournamentRegistration, id=winner_registration_id)

    match.score_p1 = int(score_p1)
    match.score_p2 = int(score_p2)
    match.winner = winner
    match.status = 'completed'
    match.completed_at = timezone.now()
    match.save(update_fields=['score_p1', 'score_p2', 'winner', 'status', 'completed_at'])

    _log_action(admin, 'override_score', 'BracketMatch', match_id, reason=reason,
                metadata={'score_p1': score_p1, 'score_p2': score_p2, 'winner_id': winner_registration_id})

    return Response({'status': 'success', 'message': 'Score overridden'}, status=status.HTTP_200_OK)


@api_view(['POST'])
def admin_cancel_tournament(request, tournament_id):
    """POST /auth/admin/tournaments/{id}/cancel/ — cancel tournament + refund fees."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    from vent_tournament.models import Tournament

    tournament = get_object_or_404(Tournament, tournament_id=tournament_id)
    reason = request.data.get('reason', '')

    if tournament.is_draft:
        return Response(
            {'status': 'error', 'message': 'Cannot cancel a draft tournament'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Refund entry fees for all confirmed paid registrations
    refunded = 0
    entry_fee_coins = int(float(tournament.entry_fee_price))
    if entry_fee_coins > 0:
        for reg in tournament.registrations.filter(status='confirmed', entry_fee_paid=True):
            wallet = None
            if reg.user:
                try:
                    wallet = reg.user.wallet
                except UserWallet.DoesNotExist:
                    pass
            if wallet:
                wallet.wallet_balance += entry_fee_coins
                wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=wallet,
                    type='refund',
                    amount=entry_fee_coins,
                    description=f'Refund — cancelled tournament: {tournament.tournament_title}',
                    status='completed',
                    tournament=tournament,
                )
                refunded += 1

        tournament.registrations.update(status='withdrawn')

    _log_action(admin, 'cancel_tournament', 'Tournament', tournament_id,
                reason=reason, metadata={'refunded_count': refunded})

    return Response({
        'status': 'success',
        'message': f'Tournament cancelled. {refunded} registrations refunded.',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Payout Approval
# ---------------------------------------------------------------------------

@api_view(['GET'])
def admin_pending_payouts(request):
    """GET /auth/admin/payouts/pending/ — pending withdrawal requests."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    # Coins → NGN: reverse of COINS_PER_100_NGN ratio
    from vent_auth.views_wallet import COINS_PER_100_NGN

    withdrawals = (
        WithdrawalRequest.objects
        .filter(status='pending')
        .select_related('wallet__user')
        .order_by('requested_at')
    )

    data = [
        {
            'id': w.id,
            'user': {
                'user_id': w.wallet.user.user_id,
                'username': w.wallet.user.username,
                'kyc_verified': w.wallet.kyc_verified,
            },
            'amount_vent_coins': w.amount,
            'amount_ngn': (w.amount * 100) // COINS_PER_100_NGN if COINS_PER_100_NGN else 0,
            'bank_name': w.bank_name,
            'account_number': w.account_number[-4:].rjust(len(w.account_number), '*'),
            'account_name': w.account_name,
            'requested_at': w.requested_at,
        }
        for w in withdrawals
    ]

    return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
def admin_approve_payout(request, withdrawal_id):
    """POST /auth/admin/payouts/{id}/approve/ — approve withdrawal."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    w = get_object_or_404(WithdrawalRequest, id=withdrawal_id)
    note = request.data.get('note', '')

    if w.status != 'pending':
        return Response(
            {'status': 'error', 'message': f'Withdrawal is already {w.status}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not w.wallet.kyc_verified:
        return Response(
            {'status': 'error', 'message': 'Cannot approve — user is not KYC verified'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Deduct from wallet
    if w.wallet.wallet_balance < w.amount:
        return Response(
            {'status': 'error', 'message': 'Insufficient wallet balance'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    w.wallet.wallet_balance -= w.amount
    w.wallet.save(update_fields=['wallet_balance'])

    Transaction.objects.create(
        wallet=w.wallet,
        type='withdrawal',
        amount=-w.amount,
        description=f'Withdrawal to {w.bank_name} {w.account_number[-4:]}',
        status='completed',
    )

    w.status = 'approved'
    w.admin_note = note
    w.processed_at = timezone.now()
    w.save(update_fields=['status', 'admin_note', 'processed_at'])

    _log_action(admin, 'approve_payout', 'WithdrawalRequest', withdrawal_id,
                note, metadata={'amount': w.amount})

    return Response({'status': 'success', 'message': 'Payout approved'}, status=status.HTTP_200_OK)


@api_view(['POST'])
def admin_reject_payout(request, withdrawal_id):
    """POST /auth/admin/payouts/{id}/reject/ — reject withdrawal."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    w = get_object_or_404(WithdrawalRequest, id=withdrawal_id)
    reason = request.data.get('reason', '')

    if w.status != 'pending':
        return Response(
            {'status': 'error', 'message': f'Withdrawal is already {w.status}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    w.status = 'rejected'
    w.admin_note = reason
    w.processed_at = timezone.now()
    w.save(update_fields=['status', 'admin_note', 'processed_at'])

    _log_action(admin, 'reject_payout', 'WithdrawalRequest', withdrawal_id, reason)

    return Response({'status': 'success', 'message': 'Payout rejected'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# KYC Review
# ---------------------------------------------------------------------------

@api_view(['GET'])
def admin_pending_kyc(request):
    """GET /auth/admin/kyc/pending/ — pending KYC submissions."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    docs = (
        KYCDocument.objects
        .filter(status='pending')
        .select_related('user')
        .order_by('submitted_at')
    )

    data = [
        {
            'id': d.id,
            'user_id': d.user.user_id,
            'username': d.user.username,
            'document_type': d.document_type,
            'document_image': d.document_image.url if d.document_image else None,
            'submitted_at': d.submitted_at,
        }
        for d in docs
    ]

    return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
def admin_approve_kyc(request, kyc_id):
    """POST /auth/admin/kyc/{id}/approve/ — mark user KYC verified."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    doc = get_object_or_404(KYCDocument, id=kyc_id)

    if doc.status != 'pending':
        return Response(
            {'status': 'error', 'message': f'KYC document is already {doc.status}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    doc.status = 'approved'
    doc.reviewed_at = timezone.now()
    doc.save(update_fields=['status', 'reviewed_at'])

    # Mark wallet as KYC verified
    try:
        doc.user.wallet.kyc_verified = True
        doc.user.wallet.save(update_fields=['kyc_verified'])
    except UserWallet.DoesNotExist:
        pass

    _log_action(admin, 'approve_kyc', 'KYCDocument', kyc_id,
                metadata={'user_id': doc.user.user_id})

    return Response({'status': 'success', 'message': 'KYC approved'}, status=status.HTTP_200_OK)


@api_view(['POST'])
def admin_reject_kyc(request, kyc_id):
    """POST /auth/admin/kyc/{id}/reject/ — reject KYC with reason."""
    admin, err = _get_admin_user(request)
    if err:
        return err

    doc = get_object_or_404(KYCDocument, id=kyc_id)
    reason = request.data.get('reason', '')

    if doc.status != 'pending':
        return Response(
            {'status': 'error', 'message': f'KYC document is already {doc.status}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    doc.status = 'rejected'
    doc.rejection_reason = reason
    doc.reviewed_at = timezone.now()
    doc.save(update_fields=['status', 'rejection_reason', 'reviewed_at'])

    _log_action(admin, 'reject_kyc', 'KYCDocument', kyc_id, reason,
                metadata={'user_id': doc.user.user_id})

    return Response({'status': 'success', 'message': 'KYC rejected'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Existing endpoints (kept, no auth guard needed for public ones)
# ---------------------------------------------------------------------------

@api_view(["GET"])
def get_all_username_and_email(request):
    users = Users.objects.all().values("username", "email")
    return Response({"status": "success", "data": list(users)}, status=status.HTTP_200_OK)


@api_view(["GET"])
def get_number_of_all_users(request):
    user_count = Users.objects.count()
    return Response({"status": "success", "total_users": user_count}, status=status.HTTP_200_OK)


@api_view(["POST"])
def check_username_availability(request):
    username = request.data.get("username")
    if not username:
        return Response({"status": "error", "message": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)
    exists = Users.objects.filter(username=username).exists()
    if exists:
        return Response({"status": "success", "message": "Username exists"}, status=status.HTTP_200_OK)
    return Response({"status": "error", "message": "Username does not exist"}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def add_email_to_waitlist(request):
    email = request.data.get("email")

    if not email:
        return Response({"status": "error", "message": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

    if Waitlist.objects.filter(email=email).exists():
        return Response({"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=email).exists():
        return Response({"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)

    Waitlist.objects.create(email=email)

    subject = "Welcome to Vermillion City 🎉"
    email_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to Vermillion Enterprise!</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      line-height: 1.6;
      color: #333;
      background-color: #f9f9f9;
      margin: 0;
      padding: 0;
    }
    .container {
      width: 90%;
      max-width: 600px;
      margin: 20px auto;
      background: #fff;
      border-radius: 10px;
      box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
      overflow: hidden;
    }
    .content {
      padding: 20px;
    }
    h2 {
      color: #ff4747;
    }
    ul {
      padding-left: 20px;
    }
    a {
      text-decoration: none;
    }
    .footer {
      margin-top: 20px;
      font-size: 14px;
      color: #555;
    }
    .banner img {
      width: 100%;
      display: block;
    }
    .social-buttons {
      margin: 15px 0;
      text-align: center;
    }
    .social-buttons a {
      display: inline-block;
      margin: 5px;
      padding: 10px 18px;
      border-radius: 30px;
      font-size: 14px;
      font-weight: bold;
      color: #fff;
    }
    .instagram { background-color: #E1306C; }
    .tiktok { background-color: #010101; }
    .x { background-color: #1DA1F2; }
    .discord { background-color: #5865F2; }
    .whatsapp { background-color: #25D366; }
  </style>
</head>
<body>
  <div class="banner">
    <img src="https://vermillionent.pythonanywhere.com/media/images/top.jpg" alt="Top Banner">
  </div>

  <div class="container">
    <div class="content">
      <h2>Welcome to Vermillion City 🎉</h2>
      <p>Hello there!!,</p>
      <p>Welcome to the Vermillion Enterprise community! 🎉 We're thrilled to have you on board.</p>
      <p>
        We are building a platform for people in the anime and gaming industry.
        We share the same passions as you — anime, games, graphic design,
        game development, video editing, esports, and so much more.
      </p>

      <h2>What to do:</h2>
      <ul>
        <li><strong>Explore:</strong> Check out our planned features (it's on the landing page if you haven't seen it yet).</li>
        <li><strong>Earn:</strong> Our referral program is starting soon! If you're up for earning some rewards or small change, keep an eye out for our emails 🤝</li>
      </ul>

      <h2>Stay Connected:</h2>
      <p>Follow us and join the conversation:</p>
      <div class="social-buttons">
        <a href="https://www.instagram.com/myventhq" target="_blank" class="instagram">Instagram</a>
        <a href="https://www.tiktok.com/@myventhq" target="_blank" class="tiktok">TikTok</a>
        <a href="https://www.x.com/myventhq" target="_blank" class="x">X</a>
        <a href="https://discord.gg/hNxg2qVq5Y" target="_blank" class="discord">Discord</a>
        <a href="https://whatsapp.com/channel/0029VaFQkAR0lwgpEABI4y1O" target="_blank" class="whatsapp">WhatsApp</a>
      </div>
      <ul>
        <li>We'll release updates regularly and run exciting programs, so prepare for the big launch 😉</li>
        <li>Keep an eye on your inbox for exclusive opportunities.</li>
      </ul>

      <h2>Shop:</h2>
      <p>
        We'll soon have some merchandise and gaming products for you in <strong>Vermillion City (our shop)</strong>.<br>
        We'll announce once it's live — you'll be able to browse, request custom items, and grab your favorites.
      </p>

      <p><em>Fun fact:</em> <strong>"Vermillion City"</strong> was inspired by the anime <em>Pokémon</em> — a place where you can find whatever it is you want.</p>

      <p>Thank you for joining us on this exciting journey.<br>
      If you have any questions, feel free to reach out at <a href="mailto:info@v-ent.co">info@v-ent.co</a>.</p>

      <div class="footer">
        <p>Thank you,<br>The V-ENT Team</p>
      </div>
    </div>
  </div>

  <div class="banner">
    <img src="https://vermillionent.pythonanywhere.com/media/images/bottom.jpg" alt="Bottom Banner">
  </div>
</body>
</html>
"""

    send_email(email, subject, email_content)
    return Response({"status": "success", "message": "Email added to waitlist successfully."}, status=status.HTTP_201_CREATED)

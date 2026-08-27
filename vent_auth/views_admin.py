import os
from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from django.core import signing

from . import emails
from .views_wallet import coins_to_ngn
from .models import (
    Users, Waitlist, UserWallet, UserProfile,
    Transaction, WithdrawalRequest, KYCDocument, AdminAction, AdminTOTP,
)
from . import totp as totp_lib
from .views_helpers import send_email, generate_session_token
from .views_kyc_files import kyc_document_url
from .decorators import (
    ADMIN_SESSION_MINUTES,
    ADMIN_ROLES, ROLE_PERMISSIONS, admin_role_required, resolve_admin, admin_identity,
)


# Pending-2FA tokens are signed, not stored: they carry only the user id and
# expire on their own.
# Whether a payout needs a KYC-verified wallet. Off since 2026-08-27: the review
# queue is not in use, so nobody can become verified, and leaving this on made
# every payout permanently unapprovable. One name, so turning KYC back on is a
# single edit here rather than a hunt through the withdrawal logic.
REQUIRE_KYC_FOR_PAYOUT = False

PENDING_2FA_SALT = 'vent.admin.2fa'
PENDING_2FA_MAX_AGE = 300  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_admin_user(request):
    """Authenticate an admin (Bearer + is_staff + live session). No role check.

    Thin wrapper over decorators.resolve_admin - kept for endpoints that need
    auth without a specific role gate (e.g. /admin/me/)."""
    return resolve_admin(request)


def _log_action(admin, action_type, target_model, target_id, reason='', metadata=None):
    AdminAction.objects.create(
        admin=admin,
        action_type=action_type,
        target_model=target_model,
        target_id=str(target_id),
        reason=reason,
        metadata=metadata or {},
    )


def _user_kyc_status(user):
    """Latest KYC doc status ('approved'|'pending'|'rejected') or 'none'."""
    doc = user.kyc_documents.order_by('-submitted_at').first()
    return doc.status if doc else 'none'


def _user_status(user):
    """USERROW status per contract §6/§7: banned if not is_active; else
    kyc_pending if a pending KYC exists; else active. ('suspended' not tracked)."""
    if not user.is_active:
        return 'banned'
    if user.kyc_documents.filter(status='pending').exists():
        return 'kyc_pending'
    return 'active'


def _wallet_vc(user):
    w = getattr(user, 'wallet', None)
    return w.wallet_balance if w else 0


def _paginate(request, default_size=20, max_size=200):
    """Return (page, page_size, offset) from query params."""
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = int(request.GET.get('page_size', default_size))
    except (ValueError, TypeError):
        page_size = default_size
    page_size = max(1, min(page_size, max_size))
    return page, page_size, (page - 1) * page_size


# ---------------------------------------------------------------------------
# Admin Login
# ---------------------------------------------------------------------------

@api_view(['POST'])
def admin_login(request):
    """Authenticate an admin user. Returns login_session_token on success.

    Accepts `email` OR `username` in the body (the FE sends `email`). The
    EmailOrUsernameModelBackend resolves either against the same `username`
    kwarg, so we just pass whichever identifier was supplied."""
    identifier = request.data.get('email') or request.data.get('username')
    password = request.data.get('password')

    if not identifier or not password:
        return Response(
            { 'code': 'EMAIL_USERNAME_PASSWORD_REQUIRED','status': 'error', 'message': 'Email/username and password are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(username=identifier, password=password)
    if user is None:
        # Fallback: resolve by email then authenticate by username (belt-and-braces
        # in case a non-email-aware backend is first in the chain).
        try:
            u = Users.objects.get(email=identifier)
            user = authenticate(username=u.username, password=password)
        except Users.DoesNotExist:
            pass

    if user is None or not user.is_staff:
        return Response(
            { 'code': 'INVALID_CREDENTIALS_NOT_ADMIN','status': 'error', 'message': 'Invalid credentials or not an admin'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # is_staff opens the door; admin_role decides what is behind it. They are
    # separate fields and can disagree: an account with is_staff and no role
    # signed in fine and then got 403 from every endpoint, so the dashboard
    # loaded as a grid of dashes under "Failed to load dashboard data." Refuse
    # the sign-in instead, and say why. Shared with the step-up door so the two
    # cannot answer this differently.
    refusal = _admin_refusal(user)
    if refusal is not None:
        return refusal

    return Response({
        'status': 'success',
        'message': 'Credentials accepted - two-factor code required',
        'data': _pending_2fa_payload(user),
    }, status=status.HTTP_200_OK)


def _admin_refusal(user):
    """Why this account may not open the console, or None if it may.

    Both doors ask the same two questions in the same order, so an account that
    is refused at one is refused at the other for the same stated reason.
    """
    if not user.is_staff:
        return Response(
            {'code': 'NOT_AN_ADMIN', 'status': 'error',
             'message': 'This account is not an administrator.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    # is_staff opens the door; admin_role decides what is behind it.
    if not user.admin_role:
        return Response(
            {'code': 'ACCOUNT_NO_ADMIN_ROLE', 'status': 'error',
             'message': 'This account has no admin role assigned. Ask a super admin to grant one.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _pending_2fa_payload(user, keep_session=False):
    """The short-lived token a TOTP code exchanges for a session token.

    Never a session token itself. Both the password path and the step-up path
    end here, so they cannot drift apart - the first version of this was copied
    into the second door and the copy forgot the provisioning URI, which is the
    only time the enrolling admin is ever shown their secret.
    """
    enrolment, _ = AdminTOTP.objects.get_or_create(
        user=user, defaults={'secret': totp_lib.generate_secret()}
    )

    # `keep` rides along so the verify step knows this came from a session that
    # is already signed in, and must not rotate the token out from under it.
    subject = '%s:keep' % user.user_id if keep_session else str(user.user_id)
    pending = signing.TimestampSigner(salt=PENDING_2FA_SALT).sign(subject)
    data = {
        'requires_2fa': True,
        'pending_token': pending,
        'expires_in': PENDING_2FA_MAX_AGE,
        'username': user.username,
        'email': user.email,
    }

    if not enrolment.confirmed:
        # First sign-in (or a reset): hand over the secret once so the admin can
        # add it to their authenticator, then confirm with a live code.
        data['enrollment_required'] = True
        data['secret'] = enrolment.secret
        data['provisioning_uri'] = totp_lib.provisioning_uri(
            enrolment.secret, user.email or user.username)

    return data


# ---------------------------------------------------------------------------
# POST /auth/admin/step-up/ - the second factor alone, for a live session
# ---------------------------------------------------------------------------

@api_view(['POST'])
def admin_step_up(request):
    """Trade a live site session for a pending-2FA token.

    An admin who has just signed in on the site was sent to /admin, bounced to
    /admin/login, and asked for the username and password they had typed a
    moment earlier. The session already carries that proof, so the only thing
    the second prompt added was friction.

    This does not weaken the door. The password authenticated the session; this
    endpoint refuses anyone whose session is not a staff account with a role,
    and still issues nothing but a pending token - the session token for the
    console comes from /auth/admin/2fa/verify/ after a real TOTP code, exactly
    as it does on the password path.
    """
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return Response(
            {'code': 'AUTHORIZATION_HEADER_REQUIRED', 'status': 'error',
             'message': 'Authorization header is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return Response(
            {'code': 'INVALID_EXPIRED_SESSION_TOKEN', 'status': 'error',
             'message': 'Invalid or expired session token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    refusal = _admin_refusal(user)
    if refusal is not None:
        return refusal

    return Response({
        'status': 'success',
        'message': 'Signed in already - two-factor code required',
        'data': _pending_2fa_payload(user, keep_session=True),
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /auth/admin/2fa/verify/ - step two of admin login
# ---------------------------------------------------------------------------

@api_view(['POST'])
def admin_2fa_verify(request):
    """Exchange a pending-2FA token plus a valid TOTP code for a session token."""
    pending = request.data.get('pending_token')
    code = request.data.get('code')

    if not pending or not code:
        return Response(
            { 'code': 'PENDING_TOKEN_CODE_REQUIRED','status': 'error', 'message': 'pending_token and code are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        subject = signing.TimestampSigner(salt=PENDING_2FA_SALT).unsign(
            pending, max_age=PENDING_2FA_MAX_AGE
        )
        # `<user_id>` from the password door, `<user_id>:keep` from the step-up
        # door, where the caller already holds a session that must survive.
        user_id, _, marker = subject.partition(':')
        keep_session = marker == 'keep'
    except signing.SignatureExpired:
        return Response(
            { 'code': 'SIGN_ATTEMPT_EXPIRED_START','status': 'error', 'message': 'This sign-in attempt expired. Start again.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    except signing.BadSignature:
        return Response(
            { 'code': 'INVALID_SIGN_ATTEMPT','status': 'error', 'message': 'Invalid sign-in attempt'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    user = Users.objects.filter(user_id=user_id).first()
    if user is None or not user.is_staff:
        return Response(
            { 'code': 'INVALID_CREDENTIALS_NOT_ADMIN','status': 'error', 'message': 'Invalid credentials or not an admin'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    enrolment = AdminTOTP.objects.filter(user=user).first()
    if enrolment is None:
        return Response(
            { 'code': 'TWO_FACTOR_NOT_SET','status': 'error', 'message': 'Two-factor is not set up for this account'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    matched_step = totp_lib.verify(enrolment.secret, code, enrolment.last_used_step)
    if matched_step is None:
        return Response(
            { 'code': 'CODE_NOT_VALID_CHECK','status': 'error', 'message': 'That code is not valid. Check your authenticator and try again.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    enrolment.last_used_step = matched_step
    if not enrolment.confirmed:
        enrolment.confirmed = True
        enrolment.confirmed_at = timezone.now()
    enrolment.save(update_fields=['last_used_step', 'confirmed', 'confirmed_at'])

    # The console gets its own token, always. It used to be minted into
    # `login_session_token`, which the website also reads, so the two grants
    # invalidated each other in both directions. `keep_session` was a patch over
    # one direction of that and is no longer needed: the website session is
    # never touched here, whichever door was used.
    token = generate_session_token()
    user.admin_session_token = token
    user.admin_session_created_at = timezone.now()
    user.save(update_fields=['admin_session_token', 'admin_session_created_at'])

    return Response({
        'status': 'success',
        'message': 'Admin login successful',
        'data': {
            'session_token': token,
            # The console stores this token in a cookie, and the cookie's
            # lifetime has to be the grant's lifetime. Publishing it means the
            # two cannot drift: a 7 day cookie over a 120 minute grant is what
            # produced "Failed to load dashboard data" after a long lunch.
            'expires_in': ADMIN_SESSION_MINUTES * 60,
            'username': user.username,
            'email': user.email,
            'admin': admin_identity(user),
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /auth/admin/me/ - current admin identity + permission map
# ---------------------------------------------------------------------------

@api_view(['GET'])
def admin_me(request):
    """Return the authenticated admin's identity + permission map. Available to
    any is_staff admin (all 4 roles) so the FE can render the correct nav."""
    admin, err = resolve_admin(request)
    if err:
        return err

    return Response({
        'status': 'success',
        'message': 'Admin identity',
        'data': admin_identity(admin),
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Platform Metrics - GET /auth/admin/metrics/
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_metrics(request):
    admin = request.admin_user

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
            # legacy keys (kept for back-compat)
            'total_users': total_users,
            'new_users_today': new_users_today,
            'active_tournaments': active_tournaments,
            'total_tournaments_all_time': total_tournaments,
            'vent_coins_in_circulation': coins_in_circulation,
            'pending_withdrawals': pending_withdrawals,
            'pending_kyc': pending_kyc,
            'open_disputes': open_disputes,
            # FE-named aliases (contract §3 - exactly the 7 keys the FE reads)
            'active_users_today': new_users_today,
            'pending_payouts': pending_withdrawals,
            'total_vc_circulation': coins_in_circulation,
        }
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_list_users(request):
    """GET /auth/admin/users/ - paginated/searchable/filterable (contract §6).

    Params: page, page_size (20), ordering, search, status, country,
    date_from, date_to. Response data = {results:[USERROW], count, page, page_size}.
    """
    from django.db.models import Q

    qs = Users.objects.select_related('wallet')

    search = request.GET.get('search')
    if search:
        qs = qs.filter(Q(username__icontains=search) | Q(email__icontains=search))

    country = request.GET.get('country')
    if country:
        qs = qs.filter(country__icontains=country)

    date_from = request.GET.get('date_from')
    if date_from:
        qs = qs.filter(date_joined__date__gte=date_from)
    date_to = request.GET.get('date_to')
    if date_to:
        qs = qs.filter(date_joined__date__lte=date_to)

    status_filter = request.GET.get('status')
    if status_filter == 'banned':
        qs = qs.filter(is_active=False)
    elif status_filter == 'kyc_pending':
        qs = qs.filter(is_active=True, kyc_documents__status='pending').distinct()
    elif status_filter == 'active':
        # active = is_active True AND no pending KYC
        qs = qs.filter(is_active=True).exclude(kyc_documents__status='pending').distinct()
    # 'suspended' not tracked - ignore.

    ordering_map = {
        '-date_joined': '-date_joined',
        'date_joined': 'date_joined',
        'username': 'username',
        '-wallet_vc': '-wallet__wallet_balance',
        'wallet_vc': 'wallet__wallet_balance',
    }
    ordering = ordering_map.get(request.GET.get('ordering'), '-date_joined')
    qs = qs.order_by(ordering)

    page, page_size, offset = _paginate(request, default_size=20)
    total = qs.count()
    users = qs[offset: offset + page_size]

    results = [
        {
            'id': u.user_id,
            'username': u.username,
            'email': u.email,
            'full_name': u.full_name,
            'country': u.country,
            'status': _user_status(u),
            'wallet_vc': _wallet_vc(u),
            'date_joined': u.date_joined,
        }
        for u in users
    ]

    return Response({
        'status': 'success',
        'data': {'results': results, 'count': total, 'page': page, 'page_size': page_size},
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_get_user(request, user_id):
    """GET /auth/admin/users/{id}/ - full user detail (contract §7)."""
    from vent_tournament.models import TournamentRegistration

    user = get_object_or_404(Users, user_id=user_id)

    tournaments_count = TournamentRegistration.objects.filter(user=user).count()

    user_block = {
        'username': user.username,
        'full_name': user.full_name,
        'email': user.email,
        'status': _user_status(user),
        'country': user.country,
        'wallet_vc': _wallet_vc(user),
        'tournaments_count': tournaments_count,
        'date_joined': user.date_joined,
        'last_login': user.last_login,
        'role': user.role,
        'kyc_status': _user_kyc_status(user),
    }

    # logins - no login-history model yet; synthesize a single stub row from
    # the current session timestamp (ip/device/location unavailable → "-"/null).
    logins = []
    if user.login_session_created_at:
        logins.append({
            'id': 1,
            'created_at': user.login_session_created_at,
            'ip': '-',
            'device': None,
            'location': None,
        })

    tournaments = [
        {
            'id': r.id,
            'name': r.tournament.tournament_title if r.tournament else None,
            'status': r.status,
            'placement': None,
            'prize_vc': None,
            'joined_at': r.registered_at,
        }
        for r in TournamentRegistration.objects
        .filter(user=user).select_related('tournament').order_by('-registered_at')
    ]

    wallet_txns = []
    wallet = getattr(user, 'wallet', None)
    if wallet is not None:
        wallet_txns = [
            {
                'id': t.id,
                'created_at': t.created_at,
                'type': t.type,
                'amount': t.amount,
                'description': t.description,
            }
            for t in wallet.transactions.order_by('-created_at')[:25]
        ]

    ban_history = [
        {
            'id': a.id,
            'reason': a.reason,
            'banned_by': a.admin.username if a.admin else None,
            'created_at': a.performed_at,
            'lifted_at': None,
        }
        for a in AdminAction.objects
        .filter(action_type__in=['ban_user', 'unban_user'], target_model='User',
                target_id=str(user_id))
        .select_related('admin').order_by('-performed_at')
    ]

    return Response({
        'status': 'success',
        'data': {
            'user': user_block,
            'logins': logins,
            'tournaments': tournaments,
            'wallet': wallet_txns,
            'reports': [],
            'ban_history': ban_history,
        }
    }, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@admin_role_required(['super_admin', 'mod_admin'])
def admin_ban_user(request, user_id):
    """PATCH /auth/admin/users/{id}/ban/ - ban or unban a user."""
    admin = request.admin_user

    user = get_object_or_404(Users, user_id=user_id)
    ban = request.data.get('ban')  # True to ban, False to unban
    reason = request.data.get('reason', '')

    if ban is None:
        return Response(
            { 'code': 'BAN_TRUE_FALSE_REQUIRED','status': 'error', 'message': '"ban" (true/false) is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user.user_id == admin.user_id:
        return Response(
            { 'code': 'CANNOT_BAN_YOURSELF','status': 'error', 'message': 'Cannot ban yourself'},
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
@admin_role_required(['super_admin'])
def admin_set_user_role(request, user_id):
    """PATCH /auth/admin/users/{id}/role/ - assign role + admin sub-role."""
    admin = request.admin_user

    user = get_object_or_404(Users, user_id=user_id)
    role = request.data.get('role')
    # shared-spec uses `admin_subrole`; accept `admin_role` as an alias too.
    admin_subrole = request.data.get('admin_subrole') or request.data.get('admin_role')
    valid_roles = ['user', 'organizer', 'admin']
    valid_admin_roles = dict(Users.ADMIN_ROLE_CHOICES)

    if role not in valid_roles:
        return Response(
            {'status': 'error', 'message': f'role must be one of: {", ".join(valid_roles)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if role == 'admin' and admin_subrole not in valid_admin_roles:
        return Response(
            {'status': 'error',
             'message': 'admin_subrole is required when role is admin and must be one of: '
                        + ', '.join(valid_admin_roles)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_role = user.role
    old_admin_role = user.admin_role
    user.role = role
    if role == 'admin':
        user.is_staff = True
        user.admin_role = admin_subrole
    else:
        user.admin_role = None
        if old_role == 'admin':
            user.is_staff = False
    user.save(update_fields=['role', 'is_staff', 'admin_role'])

    _log_action(admin, 'set_role', 'User', user_id, metadata={
        'old_role': old_role, 'new_role': role,
        'old_admin_role': old_admin_role, 'new_admin_role': user.admin_role,
    })

    return Response({
        'status': 'success',
        'message': f'Role updated to {role}' + (f' ({user.admin_role})' if user.admin_role else ''),
        'data': {'user_id': user.user_id, 'role': user.role, 'admin_role': user.admin_role},
    }, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@admin_role_required(['super_admin'])
def admin_delete_user(request, user_id):
    """DELETE /auth/admin/users/{id}/ - permanently delete account."""
    admin = request.admin_user

    user = get_object_or_404(Users, user_id=user_id)
    reason = request.data.get('reason', '')
    confirm = request.data.get('confirm')

    if not confirm:
        return Response(
            { 'code': 'CONFIRM_TRUE_REQUIRED_DELETE','status': 'error', 'message': 'confirm=true is required to delete an account'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user.user_id == admin.user_id:
        return Response(
            { 'code': 'CANNOT_DELETE_OWN_ACCOUNT','status': 'error', 'message': 'Cannot delete your own account'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    _log_action(admin, 'delete_user', 'User', user_id, reason=reason,
                metadata={'username': user.username, 'email': user.email})
    user.delete()

    return Response({'status': 'success', 'message': 'Account deleted'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Tournament Oversight
# ---------------------------------------------------------------------------

def _tournament_status(t, now):
    """Derive TROW status. 'cancelled' is not tracked in the schema, so it is
    never emitted (contract §11)."""
    if t.is_draft:
        return 'draft'
    if t.start_date_and_time and now < t.start_date_and_time:
        return 'active'
    if t.end_date_and_time and now > t.end_date_and_time:
        return 'completed'
    return 'ongoing'


@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_list_tournaments(request):
    """GET /auth/admin/tournaments/ - paginated (contract §11).

    Params: page, page_size (20), ordering, search, status.
    Response data = {results:[TROW], count, page, page_size}.
    """
    from vent_tournament.models import Tournament
    from django.db.models import Count, Sum

    now = timezone.now()

    qs = (
        Tournament.objects
        .select_related('tournament_creator', 'tournament_game')
        .annotate(_participants=Count('registrations', distinct=True),
                  _prize=Sum('prize_distributions__prize'))
    )

    search = request.GET.get('search')
    if search:
        qs = qs.filter(tournament_title__icontains=search)

    status_filter = request.GET.get('status')
    if status_filter == 'draft':
        qs = qs.filter(is_draft=True)
    elif status_filter == 'active':
        qs = qs.filter(is_draft=False, start_date_and_time__gt=now)
    elif status_filter == 'ongoing':
        qs = qs.filter(is_draft=False, start_date_and_time__lte=now, end_date_and_time__gte=now)
    elif status_filter == 'completed':
        qs = qs.filter(is_draft=False, end_date_and_time__lt=now)
    elif status_filter == 'cancelled':
        qs = qs.none()  # cancelled is not tracked → no rows

    ordering_map = {
        '-created_at': '-start_date_and_time',
        'created_at': 'start_date_and_time',
        'name': 'tournament_title',
        '-prize_pool': '-_prize',
        '-participants_count': '-_participants',
    }
    ordering = ordering_map.get(request.GET.get('ordering'), '-start_date_and_time')
    qs = qs.order_by(ordering)

    page, page_size, offset = _paginate(request, default_size=20)
    total = qs.count()
    rows = qs[offset: offset + page_size]

    results = [
        {
            'id': t.tournament_id,
            'name': t.tournament_title,
            'game': t.tournament_game.game_title if t.tournament_game else None,
            'organizer_username': t.tournament_creator.username if t.tournament_creator else None,
            'status': _tournament_status(t, now),
            'participants_count': t._participants or 0,
            'prize_pool': float(t._prize) if t._prize is not None else 0,
            'created_at': t.start_date_and_time,
        }
        for t in rows
    ]

    return Response({
        'status': 'success',
        'data': {'results': results, 'count': total, 'page': page, 'page_size': page_size},
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_get_tournament(request, tournament_id):
    """GET /auth/admin/tournaments/{id}/ - tournament detail with disputes."""
    admin = request.admin_user

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
            'participant': r.team.team_name if r.team else r.user.username if r.user else '-',
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
@admin_role_required(['super_admin', 'mod_admin'])
def admin_resolve_dispute(request, tournament_id):
    """POST /auth/admin/tournaments/{id}/dispute/resolve/ - resolve a dispute."""
    admin = request.admin_user

    from vent_tournament.models import TournamentDispute

    dispute_id = request.data.get('dispute_id')
    resolution = request.data.get('resolution')  # 'resolved' or 'dismissed'
    note = request.data.get('note', '')

    if not dispute_id or resolution not in ('resolved', 'dismissed'):
        return Response(
            { 'code': 'DISPUTE_ID_RESOLUTION_RESOLVED','status': 'error', 'message': 'dispute_id and resolution (resolved/dismissed) are required'},
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
@admin_role_required(['super_admin', 'mod_admin'])
def admin_override_match_score(request, match_id):
    """PATCH /auth/admin/matches/{id}/score/ - override bracket match score."""
    admin = request.admin_user

    from vent_tournament.models import BracketMatch, TournamentRegistration

    match = get_object_or_404(BracketMatch, id=match_id)
    score_p1 = request.data.get('score_p1')
    score_p2 = request.data.get('score_p2')
    winner_registration_id = request.data.get('winner_registration_id')
    reason = request.data.get('reason', '')

    if score_p1 is None or score_p2 is None or not winner_registration_id:
        return Response(
            { 'code': 'SCORE_P_SCORE_P','status': 'error', 'message': 'score_p1, score_p2, and winner_registration_id are required'},
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
@admin_role_required(['super_admin', 'mod_admin'])
def admin_cancel_tournament(request, tournament_id):
    """POST /auth/admin/tournaments/{id}/cancel/ - cancel tournament + refund fees."""
    admin = request.admin_user

    from vent_tournament.models import Tournament

    tournament = get_object_or_404(Tournament, tournament_id=tournament_id)
    reason = request.data.get('reason', '')

    if tournament.is_draft:
        return Response(
            { 'code': 'CANNOT_CANCEL_DRAFT_TOURNAMENT','status': 'error', 'message': 'Cannot cancel a draft tournament'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Refund entry fees for all confirmed paid registrations.
    # Whole cancel + refund must be atomic (F2): either every refund lands or
    # none do. Each wallet row is locked before crediting (F12).
    refunded = 0
    entry_fee_coins = int(float(tournament.entry_fee_price))
    with transaction.atomic():
        if entry_fee_coins > 0:
            paid_regs = (
                tournament.registrations
                .select_for_update()
                .filter(status='confirmed', entry_fee_paid=True)
            )
            for reg in paid_regs:
                if not reg.user_id:
                    continue
                try:
                    wallet = UserWallet.objects.select_for_update().get(user_id=reg.user_id)
                except UserWallet.DoesNotExist:
                    continue
                wallet.wallet_balance += entry_fee_coins
                wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=wallet,
                    type='refund',
                    amount=entry_fee_coins,
                    description=f'Refund - cancelled tournament: {tournament.tournament_title}',
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
@admin_role_required(['super_admin', 'finance_admin'])
def admin_pending_payouts(request):
    """GET /auth/admin/payouts/pending/ - pending withdrawal requests."""
    admin = request.admin_user

    from vent_auth.views_wallet import coins_to_ngn

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
            'amount_ngn': coins_to_ngn(w.amount),
            'bank_name': w.bank_name,
            'account_number': w.account_number[-4:].rjust(len(w.account_number), '*'),
            'account_name': w.account_name,
            'requested_at': w.requested_at,
        }
        for w in withdrawals
    ]

    return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)


def _mask_account(number):
    number = number or ''
    return number[-4:].rjust(len(number), '*')


@api_view(['GET'])
@admin_role_required(['super_admin', 'finance_admin'])
def admin_payouts_list(request):
    """GET /auth/admin/payouts/ - status-filterable payout list (contract §15).

    Params: page, page_size (20), ordering, search, status (default pending).
    Response data = {results:[PROW], count, page, page_size}.
    """
    from vent_auth.views_wallet import coins_to_ngn

    qs = WithdrawalRequest.objects.select_related('wallet__user')

    status_filter = request.GET.get('status', 'pending')
    if status_filter:
        qs = qs.filter(status=status_filter)

    search = request.GET.get('search')
    if search:
        qs = qs.filter(wallet__user__username__icontains=search)

    ordering_map = {
        '-submitted_at': '-requested_at',
        'submitted_at': 'requested_at',
        '-amount_vc': '-amount',
        'amount_vc': 'amount',
    }
    ordering = ordering_map.get(request.GET.get('ordering'), '-requested_at')
    qs = qs.order_by(ordering)

    page, page_size, offset = _paginate(request, default_size=20)
    total = qs.count()
    rows = qs[offset: offset + page_size]

    results = [
        {
            'id': w.id,
            'username': w.wallet.user.username if w.wallet and w.wallet.user else None,
            'amount_vc': w.amount,
            'amount_ngn': coins_to_ngn(w.amount),
            'bank_name': w.bank_name,
            'account_number': _mask_account(w.account_number),
            'submitted_at': w.requested_at,
            'status': w.status,
        }
        for w in rows
    ]

    return Response({
        'status': 'success',
        'data': {'results': results, 'count': total, 'page': page, 'page_size': page_size},
    }, status=status.HTTP_200_OK)


def _approve_payout_core(admin, withdrawal_id, note=''):
    """Approve a single pending payout atomically (KYC + balance gated).

    Returns (True, None) on success or (False, reason) when it can't be
    approved. Shared by the single-approve view and the bulk-approve endpoint
    so the KYC gate + wallet locking live in one place (F6 / F12)."""
    with transaction.atomic():
        try:
            w = WithdrawalRequest.objects.select_for_update().get(id=withdrawal_id)
        except WithdrawalRequest.DoesNotExist:
            return False, 'Withdrawal not found'

        if w.status != 'pending':
            return False, f'Withdrawal is already {w.status}'

        try:
            wallet = UserWallet.objects.select_for_update().get(pk=w.wallet_id)
        except UserWallet.DoesNotExist:
            return False, 'Wallet not found'

        # KYC is switched off for now (CEO, 2026-08-27), and this gate made every
        # payout unapprovable: nobody can become verified while the review queue
        # is not in use, so every Approve answered 400 and the whole payouts flow
        # was dead. The flag is still on the wallet and still reported to the
        # console, so turning KYC back on is restoring this check, not rebuilding
        # anything.
        if REQUIRE_KYC_FOR_PAYOUT and not wallet.kyc_verified:
            return False, 'Cannot approve - user is not KYC verified'

        if wallet.wallet_balance < w.amount:
            return False, 'Insufficient wallet balance'

        wallet.wallet_balance -= w.amount
        wallet.save(update_fields=['wallet_balance'])

        Transaction.objects.create(
            wallet=wallet,
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
    return True, None


@api_view(['POST'])
@admin_role_required(['super_admin', 'finance_admin'])
def admin_approve_payout(request, withdrawal_id):
    """POST /auth/admin/payouts/{id}/approve/ - approve withdrawal."""
    admin = request.admin_user
    note = request.data.get('note', '')

    ok, reason = _approve_payout_core(admin, withdrawal_id, note)
    if not ok:
        code = (status.HTTP_404_NOT_FOUND if reason == 'Withdrawal not found'
                else status.HTTP_400_BAD_REQUEST)
        return Response({'status': 'error', 'message': reason}, status=code)

    try:
        from vent_auth.views_notifications import create_notification
        w = WithdrawalRequest.objects.select_related('wallet__user').get(id=withdrawal_id)
        create_notification(
            w.wallet.user, 'payout', f'Your payout of {w.amount} VC was approved',
            link='/wallets', metadata={'withdrawal_id': w.id, 'amount': w.amount},
        )
        emails.send_payout_approved(w, amount_ngn=coins_to_ngn(w.amount))
    except Exception:
        pass

    return Response({'status': 'success', 'message': 'Payout approved'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@admin_role_required(['super_admin', 'finance_admin'])
def admin_reject_payout(request, withdrawal_id):
    """POST /auth/admin/payouts/{id}/reject/ - reject withdrawal."""
    admin = request.admin_user

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

    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            w.wallet.user, 'payout',
            f'Your payout of {w.amount} VC was rejected: {reason}' if reason
            else f'Your payout of {w.amount} VC was rejected',
            link='/wallets', metadata={'withdrawal_id': w.id, 'amount': w.amount},
        )
        emails.send_payout_rejected(w, reason=reason)
    except Exception:
        pass

    return Response({'status': 'success', 'message': 'Payout rejected'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# KYC Review
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_pending_kyc(request):
    """GET /auth/admin/kyc/pending/ - pending KYC submissions (all admin roles can view)."""
    admin = request.admin_user

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
            # Identity documents are not public files - this is the authenticated
            # read endpoint, not a /media/ URL (see vent_auth/views_kyc_files.py).
            'document_image': kyc_document_url(request, d),
            'submitted_at': d.submitted_at,
        }
        for d in docs
    ]

    return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_kyc_list(request):
    """GET /auth/admin/kyc/ - status-filterable KYC list (contract §18).

    Params: page_size (50), status (omitted = all; else pending|approved|rejected).
    Response data = {results:[KROW], count}.
    """
    qs = KYCDocument.objects.select_related('user').order_by('-submitted_at')

    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    page, page_size, offset = _paginate(request, default_size=50)
    total = qs.count()
    docs = qs[offset: offset + page_size]

    def _abs(image):
        if not image:
            return None
        return request.build_absolute_uri(image.url)

    def _kyc_url(doc):
        return kyc_document_url(request, doc)

    results = [
        {
            'id': d.id,
            'username': d.user.username if d.user else None,
            'email': d.user.email if d.user else None,
            'submitted_at': d.submitted_at,
            'doc_type': d.document_type,
            'status': d.status,
            'doc_url': _kyc_url(d),
            'doc_back_url': None,
            'selfie_url': None,
            'rejection_reason': d.rejection_reason,
        }
        for d in docs
    ]

    return Response({
        'status': 'success',
        'data': {'results': results, 'count': total},
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@admin_role_required(['super_admin', 'finance_admin', 'mod_admin'])
def admin_approve_kyc(request, kyc_id):
    """POST /auth/admin/kyc/{id}/approve/ - mark user KYC verified."""
    admin = request.admin_user

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

    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            doc.user, 'kyc', 'Your KYC was approved',
            link='/wallets/verify', metadata={'kyc_id': doc.id},
        )
        emails.send_kyc_approved(doc.user)
    except Exception:
        pass

    return Response({'status': 'success', 'message': 'KYC approved'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@admin_role_required(['super_admin', 'finance_admin', 'mod_admin'])
def admin_reject_kyc(request, kyc_id):
    """POST /auth/admin/kyc/{id}/reject/ - reject KYC with reason."""
    admin = request.admin_user

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

    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            doc.user, 'kyc', f'Your KYC was rejected: {reason}' if reason else 'Your KYC was rejected',
            link='/wallets/verify', metadata={'kyc_id': doc.id},
        )
        emails.send_kyc_rejected(doc.user, reason=reason)
    except Exception:
        pass

    return Response({'status': 'success', 'message': 'KYC rejected'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Audit log  (N38 / N39)
# ---------------------------------------------------------------------------

def _audit_multi(request, keys):
    """Collect filter values across alias keys, supporting comma-separated and
    repeated params (multiselect)."""
    values = []
    for key in keys:
        for raw in request.GET.getlist(key):
            values.extend(v.strip() for v in raw.split(',') if v.strip())
    return values


def _audit_parse_date(value):
    from django.utils.dateparse import parse_date, parse_datetime
    d = parse_date(value)
    if d:
        return d
    dt = parse_datetime(value)
    return dt.date() if dt else None


def _audit_log_queryset(request):
    """Filtered + role-scoped AdminAction queryset. super_admin sees all;
    every other role sees only their own actions (m1-spec §9)."""
    from django.db.models import Q

    qs = AdminAction.objects.select_related('admin').order_by('-performed_at')

    # Role scoping - request.admin_role is set by the decorator.
    if getattr(request, 'admin_role', None) != 'super_admin':
        qs = qs.filter(admin=request.admin_user)

    # Free-text search (accept `q` or FE's `search`)
    q = request.GET.get('q') or request.GET.get('search')
    if q:
        qs = qs.filter(
            Q(action_type__icontains=q) | Q(target_model__icontains=q)
            | Q(target_id__icontains=q) | Q(reason__icontains=q)
            | Q(admin__username__icontains=q)
        )

    # Action type (accept `action_type` or FE's `action`; multiselect)
    actions = _audit_multi(request, ['action_type', 'action'])
    if actions:
        qs = qs.filter(action_type__in=actions)

    # Actor (accept `actor` / FE's `admin_username` / `admin`; ids or usernames)
    actors = _audit_multi(request, ['actor', 'admin_username', 'admin'])
    if actors:
        ids = [a for a in actors if a.isdigit()]
        names = [a for a in actors if not a.isdigit()]
        cond = Q()
        if ids:
            cond |= Q(admin__user_id__in=ids)
        if names:
            cond |= Q(admin__username__in=names)
        qs = qs.filter(cond)

    # Date range on performed_at (accept `from`/`to` or FE's `date_from`/`date_to`)
    d_from = request.GET.get('from') or request.GET.get('date_from')
    d_to = request.GET.get('to') or request.GET.get('date_to')
    if d_from:
        parsed = _audit_parse_date(d_from)
        if parsed:
            qs = qs.filter(performed_at__date__gte=parsed)
    if d_to:
        parsed = _audit_parse_date(d_to)
        if parsed:
            qs = qs.filter(performed_at__date__lte=parsed)

    return qs


def _serialize_action(a):
    """Emit both spec field names and the FE aliases the audit-log page reads."""
    meta = a.metadata or {}
    return {
        'id': a.id,
        'admin_username': a.admin.username if a.admin else None,
        'admin_user_id': a.admin.user_id if a.admin else None,
        'action': a.action_type,        # FE alias
        'action_type': a.action_type,   # spec
        'target_type': a.target_model,  # FE alias
        'target_model': a.target_model,  # spec
        'target_id': a.target_id,
        'description': a.reason,         # FE alias
        'reason': a.reason,              # spec
        'metadata': meta,
        'ip': meta.get('ip'),            # not tracked yet (M2) → null
        'result': meta.get('result', 'success'),
        'created_at': a.performed_at,    # FE alias
        'performed_at': a.performed_at,  # spec
    }


@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['view_audit_log'])
def admin_audit_log(request):
    """GET /auth/admin/audit-log/ - paginated (25/page) AdminAction feed.

    Filters: q/search, action_type/action, actor/admin_username, from/to
    (date_from/date_to). Role-scoped: super_admin sees all; others see own."""
    qs = _audit_log_queryset(request)
    total = qs.count()

    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = int(request.GET.get('page_size', 25))
    except (ValueError, TypeError):
        page_size = 25
    page_size = max(1, min(page_size, 100))

    offset = (page - 1) * page_size
    rows = [_serialize_action(a) for a in qs[offset: offset + page_size]]
    total_pages = (total + page_size - 1) // page_size if total else 1

    return Response({
        'status': 'success',
        'data': {
            'results': rows,        # FE alias
            'actions': rows,        # spec
            'count': total,         # FE alias
            'total_count': total,   # spec
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages,
        },
        'message': 'Audit log',
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['export_audit_log'])
def admin_audit_log_export(request):
    """GET /auth/admin/audit-log/export.csv - streamed CSV of the (filtered)
    audit log. super_admin only. Same filters as the list endpoint."""
    import csv
    import json
    from django.http import StreamingHttpResponse

    qs = _audit_log_queryset(request)

    class _Echo:
        def write(self, value):
            return value

    writer = csv.writer(_Echo())
    header = [
        'id', 'performed_at', 'admin_username', 'admin_user_id', 'action_type',
        'target_model', 'target_id', 'reason', 'metadata',
    ]

    def _rows():
        yield writer.writerow(header)
        for a in qs.iterator():
            yield writer.writerow([
                a.id,
                a.performed_at.isoformat() if a.performed_at else '',
                a.admin.username if a.admin else '',
                a.admin.user_id if a.admin else '',
                a.action_type,
                a.target_model,
                a.target_id,
                a.reason,
                json.dumps(a.metadata or {}),
            ])

    response = StreamingHttpResponse(_rows(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit-log.csv"'
    return response


# ---------------------------------------------------------------------------
# Existing endpoints (kept, no auth guard needed for public ones)
# ---------------------------------------------------------------------------

@api_view(["GET"])
@admin_role_required(['super_admin', 'support_admin'])
def get_all_username_and_email(request):
    # SECURITY (F1): tightened from fully-public → admin RBAC
    # (super_admin / support_admin per m1-spec §9).
    admin = request.admin_user
    users = Users.objects.all().values("username", "email")
    return Response({"status": "success", "data": list(users)}, status=status.HTTP_200_OK)


@api_view(["GET"])
@admin_role_required(ADMIN_ROLES)
def get_number_of_all_users(request):
    # Gated to any admin role (no unauthenticated FE caller - grep confirmed).
    user_count = Users.objects.count()
    return Response({"status": "success", "total_users": user_count}, status=status.HTTP_200_OK)


@api_view(["POST"])
def check_username_availability(request):
    username = request.data.get("username")
    if not username:
        return Response({ 'code': 'USERNAME_REQUIRED',"status": "error", "message": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)
    exists = Users.objects.filter(username=username).exists()
    if exists:
        return Response({"status": "success", "message": "Username exists"}, status=status.HTTP_200_OK)
    return Response({ 'code': 'USERNAME_DOES_NOT_EXIST',"status": "error", "message": "Username does not exist"}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def add_email_to_waitlist(request):
    email = request.data.get("email")

    if not email:
        return Response({ 'code': 'EMAIL_REQUIRED',"status": "error", "message": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

    if Waitlist.objects.filter(email=email).exists():
        return Response({ 'code': 'EMAIL_ALREADY_WAITLIST',"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=email).exists():
        return Response({ 'code': 'EMAIL_ALREADY_WAITLIST',"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)

    Waitlist.objects.create(email=email)

    # The old waitlist mail embedded a banner from
    # vermillionent.pythonanywhere.com, which stopped resolving when the
    # platform moved, so the very first thing a signup saw was a broken image.
    emails.send_waitlist_welcome(email)
    return Response({"status": "success", "message": "Email added to waitlist successfully."}, status=status.HTTP_201_CREATED)

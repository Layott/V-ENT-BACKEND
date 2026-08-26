"""RBAC for the admin surface (m1-spec §9).

Single source of truth for admin roles, the action->role permission matrix, the
`admin_role_required` decorator, and the shape used by `/auth/admin/me/` and the
admin login response.

DB stores the canonical sub-role on `Users.admin_role`
(super_admin / finance_admin / mod_admin / support_admin). API responses also
expose a short `role` (super / finance / moderator / support) + `role_label`
because the frontend AdminNav keys on the short form.
"""
from datetime import timedelta
from functools import wraps

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import Users

SESSION_TIMEOUT_MINUTES = 120

ADMIN_ROLES = ('super_admin', 'finance_admin', 'mod_admin', 'support_admin')

# canonical -> short alias consumed by the FE AdminNav / adminUser
ROLE_SHORT = {
    'super_admin': 'super',
    'finance_admin': 'finance',
    'mod_admin': 'moderator',
    'support_admin': 'support',
}

ROLE_LABEL = {
    'super_admin': 'Super Admin',
    'finance_admin': 'Finance',
    'mod_admin': 'Moderator',
    'support_admin': 'Support',
}

# action -> set of admin_roles allowed. Derived from m1-spec §9 + shared-spec §6.
# NOTE: KYC action is the union of both specs (super/finance/mod) - flagged for
# the lead to narrow if desired. Payouts = super/finance per the lead's ruling.
ROLE_PERMISSIONS = {
    'view_dashboard':        set(ADMIN_ROLES),
    'view_users':            set(ADMIN_ROLES),
    'ban_users':             {'super_admin', 'mod_admin'},
    'set_user_roles':        {'super_admin'},
    'delete_users':          {'super_admin'},
    'view_transactions':     {'super_admin', 'finance_admin'},
    'list_payouts':          {'super_admin', 'finance_admin'},
    'approve_payouts':       {'super_admin', 'finance_admin'},
    'reject_payouts':        {'super_admin', 'finance_admin'},
    'list_kyc':              {'super_admin', 'finance_admin', 'mod_admin', 'support_admin'},
    'approve_kyc':           {'super_admin', 'finance_admin', 'mod_admin'},
    'reject_kyc':            {'super_admin', 'finance_admin', 'mod_admin'},
    'cancel_tournament':     {'super_admin', 'mod_admin'},
    'resolve_dispute':       {'super_admin', 'mod_admin'},
    'override_match_score':  {'super_admin', 'mod_admin'},
    'distribute_prizes':     {'super_admin', 'finance_admin'},
    'view_audit_log':        set(ADMIN_ROLES),
    'export_audit_log':      {'super_admin'},
    'manage_admins':         {'super_admin'},
    'list_usernames_emails': {'super_admin', 'support_admin'},
}


def effective_admin_role(user):
    """Resolve the effective role. Django superusers act as super_admin so a
    freshly-created superuser can never be locked out of the admin surface."""
    if getattr(user, 'is_superuser', False):
        return 'super_admin'
    return user.admin_role


def permissions_for(admin_role):
    """action -> bool map for the given role (for /auth/admin/me/ + login)."""
    return {action: (admin_role in roles) for action, roles in ROLE_PERMISSIONS.items()}


def admin_identity(user):
    """The admin descriptor the FE stores as `adminUser` / reads from /me/."""
    role = effective_admin_role(user)
    return {
        'user_id': user.user_id,
        'username': user.username,
        'email': user.email,
        'full_name': user.full_name,
        'admin_role': role,                          # canonical (spec model value)
        'role': ROLE_SHORT.get(role),                # short alias for AdminNav
        'role_label': ROLE_LABEL.get(role, 'Admin'),
        'permissions': permissions_for(role),
    }


def resolve_admin(request):
    """(user, error_response). Bearer + is_staff + non-expired session. No role check."""
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, Response(
            { 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    token = header.split(' ', 1)[1]
    try:
        user = Users.objects.get(login_session_token=token)
    except Users.DoesNotExist:
        return None, Response(
            { 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if (
        user.login_session_created_at is None
        or timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    ):
        return None, Response(
            { 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.is_staff:
        return None, Response(
            { 'code': 'ADMIN_ACCESS_REQUIRED','status': 'error', 'message': 'Admin access required'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return user, None


def admin_role_required(allowed_roles):
    """Gate a view to the given admin sub-roles. On success, attaches
    `request.admin_user` + `request.admin_role` for the view to use."""
    allowed = set(allowed_roles)

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user, err = resolve_admin(request)
            if err:
                return err
            role = effective_admin_role(user)
            if role not in allowed:
                return Response(
                    { 'code': 'DO_NOT_PERMISSION_PERFORM',
                        'status': 'error',
                        'message': 'You do not have permission to perform this action',
                        'data': {'your_role': role, 'required': sorted(allowed)},
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            request.admin_user = user
            request.admin_role = role
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator

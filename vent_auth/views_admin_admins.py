"""Who the administrators are, and who made them one.

CEO, 7 September 2026, from the admin dashboard spec:

    Super Admin: full access to all features and settings, can create and
    manage other admin accounts and assign roles.

That sentence is the entire difference between Super Admin and Admin, and it
had no screen. `admin_set_user_role` could promote somebody, but only from
inside one person's detail page, and only if you already knew which account to
open. There was nowhere that answered "who can get into the console", which is
the question somebody asks when an admin leaves.

## The role ladder is not duplicated here

The seven roles the spec names already exist as `Users.ADMIN_ROLE_CHOICES` and
`decorators.ROLE_PERMISSIONS`, and `tests_admin_roles.py` fails if those two
disagree. This module reads both and invents neither, so the catalogue the
screen draws IS the table the API enforces. A second list of roles written for
the console would be the "one model per thing" fault at exactly the place it
does the most damage.

## Two roles that grant nothing, on purpose

`marketplace_admin` and `wager_admin` can be assigned today and open nothing
but the console door, because the marketplace is Phase 4 and the wager system
is Phase 6. The catalogue says so per role rather than leaving somebody to
discover it by granting one and watching a colleague find an empty console.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import (ADMIN_ROLES, ROLE_LABEL, ROLE_PERMISSIONS,
                         ROLE_SHORT, ROLES_AWAITING_THEIR_FEATURE,
                         admin_role_required, effective_admin_role)
from .models import AdminAction, Users

MANAGE_ROLES = ROLE_PERMISSIONS['manage_admins']

# What each permission is called on a screen. A permission key is a fine name
# for code and a poor one for the person deciding whether to hand it over.
PERMISSION_LABEL = {
    'view_dashboard': 'Open the console',
    'view_users': 'See member accounts',
    'ban_users': 'Ban and unban accounts',
    'reset_user_password': 'Send a password reset',
    'set_user_roles': 'Change what somebody is',
    'delete_users': 'Delete an account for good',
    'view_transactions': 'See every transaction',
    'list_payouts': 'See payout requests',
    'approve_payouts': 'Approve a payout',
    'reject_payouts': 'Reject a payout',
    'list_kyc': 'See identity checks',
    'approve_kyc': 'Approve an identity check',
    'reject_kyc': 'Reject an identity check',
    'cancel_tournament': 'Cancel a tournament',
    'manage_tournaments': 'Run and edit tournaments',
    'manage_events': 'Run and edit events',
    'resolve_dispute': 'Settle a dispute',
    'override_match_score': 'Correct a match score',
    'distribute_prizes': 'Pay out prizes',
    'view_audit_log': 'Read the audit log',
    'export_audit_log': 'Download the audit log',
    'manage_admins': 'Create and remove administrators',
    'list_usernames_emails': 'Export usernames and addresses',
    'manage_organizations': 'Create and edit organisations',
    'manage_communities': 'Moderate communities',
    'moderate_content': 'Act on reports and content',
    'transfer_funds': 'Move money between wallets',
    'send_notifications': 'Announce to participants',
    'manage_marketplace': 'Run the marketplace',
    'manage_wagers': 'Run the wager system',
    'manage_shop': 'Run the shop',
}


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _two_factor_ready(user):
    """Whether this account has an authenticator it can actually sign in with.

    An admin without one cannot open the console at all: `resolve_admin`
    refuses a session that never met the code. So an administrator listed here
    with `two_factor` false is somebody who has been granted a role and cannot
    use it yet, which is worth seeing on the row rather than hearing about.
    """
    from .login_2fa import factor_for

    factor = factor_for(user)
    return bool(factor is not None and factor.confirmed)


def _admin_row(user):
    role = effective_admin_role(user)
    return {
        'user_id': user.user_id,
        'username': user.username,
        'full_name': user.full_name or user.username,
        'email': user.email,
        'admin_role': role,
        'role': ROLE_SHORT.get(role),
        'role_label': ROLE_LABEL.get(role, 'Admin'),
        'is_active': user.is_active,
        'is_superuser': bool(user.is_superuser),
        'two_factor': _two_factor_ready(user),
        'last_login': (user.login_session_created_at.isoformat()
                       if user.login_session_created_at else None),
        'date_joined': user.date_joined.isoformat() if user.date_joined else None,
        'awaiting_feature': ROLES_AWAITING_THEIR_FEATURE.get(role, ''),
    }


@api_view(['GET'])
@admin_role_required(MANAGE_ROLES)
def admin_admins(request):
    """Every account that can open the console, and what each one may do."""
    rows = Users.objects.filter(is_staff=True).order_by('username')

    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(username__icontains=term)

    return _ok({
        'results': [_admin_row(u) for u in rows],
        'count': rows.count(),
        'roles': _catalogue(),
    })


def _catalogue():
    """The seven roles, their labels, and what each one may do.

    Built from ROLE_PERMISSIONS rather than written out, so a permission added
    to a role tomorrow appears on this screen the same day. A hand-written copy
    of this table is a table that tells somebody they granted less than they
    did.
    """
    out = []
    for role in ADMIN_ROLES:
        may = sorted(action for action, roles in ROLE_PERMISSIONS.items()
                     if role in roles)
        out.append({
            'value': role,
            'label': ROLE_LABEL.get(role, role),
            'short': ROLE_SHORT.get(role),
            'may': [{'key': action,
                     'label': PERMISSION_LABEL.get(action, action)}
                    for action in may],
            'awaiting_feature': ROLES_AWAITING_THEIR_FEATURE.get(role, ''),
        })
    return out


@api_view(['GET'])
@admin_role_required(MANAGE_ROLES)
def admin_roles_catalogue(request):
    """The role table on its own, for a screen that only needs the choices."""
    return _ok({'roles': _catalogue()})


@api_view(['POST'])
@admin_role_required(MANAGE_ROLES)
def admin_grant_role(request):
    """Make somebody an administrator, or stop them being one.

    An account, never a new login. There is no "create an admin account" that
    makes a person out of nothing: somebody signs up like everybody else and is
    then granted a role, which means their email is verified, their password is
    their own, and the person who granted it is on the record. Creating logins
    for other people from a console is how shared passwords start.
    """
    admin = request.admin_user
    username = str(request.data.get('username') or '').strip()
    wanted = str(request.data.get('admin_role') or '').strip()
    revoke = bool(request.data.get('revoke'))
    reason = str(request.data.get('reason') or '').strip()[:500]

    if not username:
        return _err('Say which account.', 'VALIDATION_ERROR')

    target = (Users.objects.filter(username__iexact=username).first()
              or Users.objects.filter(email__iexact=username).first())
    if target is None:
        return _err('No account called %s.' % username, 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if target.user_id == admin.user_id:
        # Removing your own last permission locks the console for everybody if
        # you are the only super admin, and it is never what somebody means to
        # press.
        return _err('You cannot change your own role here.',
                    'CANNOT_CHANGE_SELF')

    before = target.admin_role

    if revoke:
        if not target.is_staff and not target.admin_role:
            return _err('%s is not an administrator.' % target.username,
                        'VALIDATION_ERROR')
        target.admin_role = None
        target.is_staff = False
        if target.role == 'admin':
            target.role = 'user'
        target.save(update_fields=['admin_role', 'is_staff', 'role'])
        AdminAction.objects.create(
            admin=admin, action_type='revoke_admin_role', target_model='User',
            target_id=str(target.user_id), reason=reason,
            metadata={'username': target.username, 'was': before})
        return _ok({'admin': _admin_row(target)},
                   'They are no longer an administrator.')

    if wanted not in ADMIN_ROLES:
        return _err('That is not one of the roles.', 'VALIDATION_ERROR')

    target.admin_role = wanted
    target.is_staff = True
    target.role = 'admin'
    target.save(update_fields=['admin_role', 'is_staff', 'role'])

    AdminAction.objects.create(
        admin=admin, action_type='grant_admin_role', target_model='User',
        target_id=str(target.user_id), reason=reason,
        metadata={'username': target.username, 'was': before, 'now': wanted})

    return _ok({'admin': _admin_row(target),
                'two_factor': _two_factor_ready(target)},
               'Role granted.')

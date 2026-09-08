"""A team's wallet and an organisation's wallet, from the outside.

CEO, 7 September 2026: "Teams should have their own wallets and organizations
should also have their own wallets", and from the VENT WALLET spec:

    a team leader manages a team wallet, sends to team members or to the
    organisation wallet, and views the transaction history for it
    an organisation admin manages an organisation wallet, sends to team
    wallets or user wallets within the organisation, and views its history

## Who may spend

Not everybody in a team. A team wallet reachable by every member is a wallet
any member can empty, so spending is the owner, the captain or a manager; and
for an organisation it is the owner, an admin, or a manager carrying the teams
scope. Everybody who belongs can READ the statement, because a team whose
members cannot see where the money went is worse than no wallet.

That split - read is wide, spend is narrow - is the whole permission model
here, and it lives in one function per kind so a second endpoint cannot get a
different answer.
"""
from django.contrib.auth.hashers import make_password
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import wallets
from .models import OrgMember, Organization, TeamMembers, Teams, Users


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------

#: Team roles that may spend the team's money.
TEAM_SPENDERS = ('owner', 'captain', 'manager')


def team_roles(team, user):
    """(may_read, may_spend) for this person on this team."""
    if user is None:
        return False, False
    if team.team_owner_id == user.user_id:
        return True, True
    row = TeamMembers.objects.filter(team=team, user=user).first()
    if row is None:
        return False, False
    role = getattr(row, 'role', None) or (
        'captain' if getattr(row, 'is_captain', False) else 'member')
    return True, role in TEAM_SPENDERS


def org_roles(org, user):
    """(may_read, may_spend) for this person on this organisation."""
    if user is None:
        return False, False
    if org.org_owner_id == user.user_id:
        return True, True
    row = OrgMember.objects.filter(org=org, user=user).first()
    if row is None:
        return False, False
    if row.role in (OrgMember.ROLE_OWNER, OrgMember.ROLE_ADMIN):
        return True, True
    # A manager spends only if they were given the teams scope, which is the
    # scope that means "you run what this organisation fields".
    scopes = row.scopes or []
    return True, (row.role == OrgMember.ROLE_MANAGER
                  and OrgMember.SCOPE_TEAMS in scopes)


# ---------------------------------------------------------------------------
# Resolving the other end of a transfer
# ---------------------------------------------------------------------------

def _target_wallet(payload):
    """The wallet money is going to, from `{to_kind, to}`.

    Named explicitly rather than guessed from the string, because "vermillion"
    could be a username, a team or an organisation, and guessing wrong sends
    somebody's money to a stranger with the same name.
    """
    kind = str(payload.get('to_kind') or '').strip().lower()
    ref = str(payload.get('to') or '').strip()
    if not kind or not ref:
        return None, _err('Say who it is going to.', 'VALIDATION_ERROR')

    if kind == 'user':
        from .invites import invitee_for
        user, _email, problem = invitee_for(ref)
        if problem or user is None:
            return None, _err('No account called %s.' % ref, 'NOT_FOUND',
                              status.HTTP_404_NOT_FOUND)
        from .models import UserWallet
        wallet = UserWallet.objects.filter(user=user).first()
        if wallet is None:
            return None, _err('That account has no wallet yet.', 'NO_WALLET')
        return wallet, None

    if kind == 'team':
        team = Teams.objects.filter(slug=ref).first() or \
            Teams.objects.filter(team_name__iexact=ref).first()
        if team is None:
            return None, _err('No team called %s.' % ref, 'NOT_FOUND',
                              status.HTTP_404_NOT_FOUND)
        return wallets.wallet_for_team(team), None

    if kind == 'org':
        org = Organization.objects.filter(slug=ref).first() or \
            Organization.objects.filter(org_name__iexact=ref).first()
        if org is None:
            return None, _err('No organisation called %s.' % ref, 'NOT_FOUND',
                              status.HTTP_404_NOT_FOUND)
        return wallets.wallet_for_org(org), None

    return None, _err('Send to a user, a team or an organisation.',
                      'VALIDATION_ERROR')


# ---------------------------------------------------------------------------
# The endpoints. Four shapes, twice.
# ---------------------------------------------------------------------------

def _wallet_payload(wallet, may_spend):
    return {
        'balance': wallet.wallet_balance,
        'has_pin': bool(wallet.pin_hash),
        'can_spend': may_spend,
        'transactions': wallets.statement(wallet),
    }


def _handle(request, owner, wallet, may_read, may_spend, what):
    if not may_read:
        return _err('Only %s can see this wallet.' % what, 'NOT_ALLOWED',
                    status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        return _ok(_wallet_payload(wallet, may_spend))

    if not may_spend:
        return _err('You can see this wallet but not spend from it.',
                    'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)

    action = str(request.data.get('action') or 'send').lower()

    if action == 'set_pin':
        pin = str(request.data.get('pin') or '')
        if not pin.isdigit() or not 4 <= len(pin) <= 6:
            return _err('A PIN is 4 to 6 digits.', 'VALIDATION_ERROR')
        wallet.pin_hash = make_password(pin)
        wallet.save(update_fields=['pin_hash'])
        return _ok(_wallet_payload(wallet, may_spend), 'PIN saved.')

    if action == 'send':
        target, err = _target_wallet(request.data)
        if err:
            return err
        try:
            wallets.transfer(wallet, target, request.data.get('amount'),
                             note=str(request.data.get('note') or '')[:200],
                             pin=request.data.get('pin'))
        except wallets.WalletError as exc:
            return _err(str(exc), exc.code)
        wallet.refresh_from_db()
        return _ok(_wallet_payload(wallet, may_spend),
                   'Sent to %s.' % wallets.describe(target))

    return _err('Say what to do: send, or set_pin.', 'VALIDATION_ERROR')


@api_view(['GET', 'POST'])
def team_wallet(request, team_ref):
    """GET/POST /auth/team/<ref>/wallet/"""
    viewer = _viewer(request)
    if viewer is None:
        return _err('Sign in to see a team wallet.', 'UNAUTHORIZED',
                    status.HTTP_401_UNAUTHORIZED)
    team = Teams.objects.filter(slug=team_ref).first() or \
        Teams.objects.filter(team_name__iexact=team_ref).first()
    if team is None:
        return _err('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    may_read, may_spend = team_roles(team, viewer)
    return _handle(request, team, wallets.wallet_for_team(team),
                   may_read, may_spend, 'people in this team')


@api_view(['GET', 'POST'])
def org_wallet(request, org_ref):
    """GET/POST /auth/organization/<ref>/wallet/"""
    viewer = _viewer(request)
    if viewer is None:
        return _err('Sign in to see an organisation wallet.', 'UNAUTHORIZED',
                    status.HTTP_401_UNAUTHORIZED)
    org = Organization.objects.filter(slug=org_ref).first() or \
        Organization.objects.filter(org_name__iexact=org_ref).first()
    if org is None:
        return _err('Organisation not found.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    may_read, may_spend = org_roles(org, viewer)
    return _handle(request, org, wallets.wallet_for_org(org),
                   may_read, may_spend, 'people in this organisation')

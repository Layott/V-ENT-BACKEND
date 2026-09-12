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
from .models import (OrgMember, Organization, TeamMembers, Teams,
                     UserWallet, Users)


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
    # A manager spends only if they were given the FINANCE scope. It was the
    # teams scope until 8 September, which meant somebody handed the roster to
    # run could also empty the wallet, having been given no such thing. Money
    # is granted on purpose or not at all.
    scopes = row.scopes or []
    return True, (row.role == OrgMember.ROLE_MANAGER
                  and OrgMember.SCOPE_FINANCE in scopes)


# ---------------------------------------------------------------------------
# Resolving the other end of a transfer
# ---------------------------------------------------------------------------

def _target_wallet(payload):
    """The wallet money is going to, from `{to_kind, to}`.

    One resolver, in `wallets.resolve_target`, shared with the person's own
    wallet. It used to live here and `/auth/wallet/send/` had no equivalent at
    all, so a person could not pay a team. Two copies of "what does this name
    mean" would eventually give two answers, and the answer decides who gets
    the money.
    """
    try:
        return wallets.resolve_target(payload.get('to_kind'),
                                      payload.get('to')), None
    except wallets.WalletError as exc:
        http = (status.HTTP_404_NOT_FOUND if exc.code == 'NOT_FOUND'
                else status.HTTP_400_BAD_REQUEST)
        return None, _err(str(exc), exc.code, http)


# ---------------------------------------------------------------------------
# The endpoints. Four shapes, twice.
# ---------------------------------------------------------------------------

def _wallet_payload(wallet, may_spend, viewer=None):
    return {
        'balance': wallet.wallet_balance,
        'has_pin': bool(wallet.pin_hash),
        'can_spend': may_spend,
        # Whether THIS viewer has to produce an authenticator code to spend.
        # It is a property of the person, not of the wallet: one team member
        # may have a second factor and another may not, and each is held to
        # what they set up.
        'requires_2fa': bool(viewer is not None
                             and wallets.second_factor_required(viewer)),
        'transactions': wallets.statement(wallet),
    }


def _handle(request, owner, wallet, may_read, may_spend, what, viewer=None):
    if not may_read:
        return _err('Only %s can see this wallet.' % what, 'NOT_ALLOWED',
                    status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        return _ok(_wallet_payload(wallet, may_spend, viewer))

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
        return _ok(_wallet_payload(wallet, may_spend, viewer), 'PIN saved.')

    if action == 'send':
        target, err = _target_wallet(request.data)
        if err:
            return err

        # The PIN is REQUIRED here, and saying so is the whole point.
        #
        # `wallets.transfer` takes `pin=None` to mean "no PIN check", because
        # the platform itself moves money with no PIN: a prize payout, an event
        # settlement, a refund. That default is right for those callers and
        # exactly wrong for this one, and the two are told apart by nothing but
        # whether the caller remembered. This endpoint did not, so a send that
        # simply omitted the key moved the money: `request.data.get('pin')`
        # returned None, transfer skipped `check_pin`, and anybody who could
        # reach a team or organisation wallet could empty it without knowing
        # the PIN. Found on 8 September by posting the payload with the field
        # left out.
        #
        # The person's own wallet has always checked this (see `send_funds`,
        # "recipient_username, amount, and pin are required"). Shared wallets
        # are the second surface, built later, and the guard was not carried
        # across. Refusing an absent PIN before anything moves is what makes
        # the two behave alike.
        pin = request.data.get('pin')
        if pin is None or str(pin).strip() == '':
            return _err('Enter the wallet PIN to send anything.',
                        'PIN_REQUIRED')

        try:
            # The PIN is checked BEFORE the authenticator code, because
            # spending the code is the only step here that costs the person
            # something. `spend_code` burns it so it cannot be replayed, so
            # checking the second factor first meant a mistyped PIN consumed a
            # perfectly good code and the retry had to wait out the 30 second
            # window. Found while walking this on 8 September: the wrong PIN
            # came back as "that code has been used", which is both the wrong
            # reason and the wrong thing to have happened.
            #
            # `transfer` checks the PIN again on the way through. That is
            # deliberate: this call is about the ORDER of the refusals, and
            # removing the check inside transfer would put every other caller
            # at the mercy of remembering it, which is the fault directly
            # above this one.
            wallets.check_pin(wallet, pin)

            # The code belongs to the PERSON pressing send, not to the team.
            # A shared wallet has no device of its own, and the person who
            # moved the money is who anybody would want to ask about it.
            wallets.check_second_factor(viewer, request.data.get('code'))
            wallets.transfer(wallet, target, request.data.get('amount'),
                             note=str(request.data.get('note') or '')[:200],
                             pin=pin)
        except wallets.WalletError as exc:
            return _err(str(exc), exc.code)

        # The person on the other end is told, the same as they are when
        # another person pays them. The spec asks for "notifications for
        # completed transactions" and a team paying a member is exactly the
        # transaction somebody wants to hear about: nothing else on the site
        # would tell them, because a team wallet is not a screen they watch.
        # A team or an organisation has no inbox of its own; its statement is
        # the notice, and this just wrote to it.
        if isinstance(target, UserWallet):
            try:
                from .views_notifications import create_notification
                create_notification(
                    target.user, 'wallet',
                    'You received %d VC from %s' % (
                        int(request.data.get('amount') or 0),
                        wallets.describe(wallet)),
                    link='/wallets',
                    metadata={'from': wallets.describe(wallet)})
            except Exception:                                    # noqa: BLE001
                pass

        wallet.refresh_from_db()
        return _ok(_wallet_payload(wallet, may_spend, viewer),
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
                   may_read, may_spend, 'people in this team', viewer)


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
                   may_read, may_spend, 'people in this organisation', viewer)

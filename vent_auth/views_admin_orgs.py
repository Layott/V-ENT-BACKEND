"""Organisations and communities, from the console.

CEO, 7 September 2026, from the admin dashboard spec:

    Organization Management: create organizations, edit profiles, manage
    member lists, assign roles within, oversee organization funds, transfer
    funds, generate financial reports
    Community Management: create communities, moderate discussions, enforce
    guidelines, highlight community content, track engagement

Two of the ten sections in that spec. The other eight are either already built
- users, tournaments, events, financial, content moderation - or waiting on a
feature that does not exist: marketplace is Phase 4, wager is Phase 6, the shop
is Phase 3. A console section for a feature nobody has built is a screen of
controls that do nothing, and that is worse than the section being absent.

## Money here is the same money

`transfer_funds` moves coins between an organisation's wallet and anywhere
else, and it goes through `vent_auth.wallets.transfer` like every other
movement on the platform. An admin's transfer is not a different kind of
transfer: it writes the same two lines, is bound by the same "a balance is
never edited without a transaction beside it" rule, and appears on the
organisation's own statement where its owner can see it.

That last part matters. An admin moving an organisation's money invisibly is
how a platform loses an argument it cannot reconstruct.
"""
from django.db.models import Count, Q, Sum
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import wallets
from .decorators import admin_role_required
from .models import (AdminAction, OrgMember, Organization, OrgWallet,
                     TeamWallet, UserWallet, Users)

READ_ROLES = {'super_admin', 'admin', 'finance_admin', 'mod_admin',
              'tournament_admin', 'support_admin'}
MANAGE_ROLES = {'super_admin', 'admin'}
MONEY_ROLES = {'super_admin', 'finance_admin'}


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _org(ref):
    if str(ref).isdigit():
        return Organization.objects.filter(org_id=int(ref)).first()
    return Organization.objects.filter(slug=str(ref)).first()


def _row(org):
    wallet = OrgWallet.objects.filter(org=org).first()
    return {
        'org_id': org.org_id,
        'slug': org.slug,
        'name': org.org_name,
        'type': org.org_type,
        'owner': org.org_owner.username if org.org_owner_id else '',
        'verified': org.verified,
        'members': OrgMember.objects.filter(org=org).count(),
        'balance_vc': wallet.wallet_balance if wallet else 0,
        'capabilities': org.capabilities(),
    }


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_organizations(request):
    """Every organisation, with what it holds.

    `?q=` searches the name. `?verified=1` narrows to the verified ones, which
    is the question somebody actually asks when they open this.
    """
    rows = Organization.objects.select_related('org_owner').all()

    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(org_name__icontains=term)
    if request.GET.get('verified') in ('1', 'true'):
        rows = rows.filter(verified=True)

    rows = rows.order_by('org_name')[:200]
    return _ok({'results': [_row(org) for org in rows], 'count': len(rows)})


@api_view(['GET', 'POST'])
@admin_role_required(READ_ROLES)
def admin_organization_detail(request, org_ref):
    """One organisation, and the things an admin may change about it."""
    admin = request.admin_user
    org = _org(org_ref)
    if org is None:
        return _err('No organisation with that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        members = OrgMember.objects.filter(org=org).select_related('user')
        wallet = OrgWallet.objects.filter(org=org).first()
        return _ok({
            **_row(org),
            'members_list': [{
                'username': m.user.username,
                'role': m.role,
                'scopes': m.scopes or [],
            } for m in members],
            'transactions': wallets.statement(wallet) if wallet else [],
        })

    from .decorators import effective_admin_role
    role = effective_admin_role(admin)
    if role not in MANAGE_ROLES:
        return _err('Only an admin can change an organisation.', 'NOT_ALLOWED',
                    status.HTTP_403_FORBIDDEN)

    action = str(request.data.get('action') or '').lower()

    if action == 'verify':
        org.verified = bool(request.data.get('verified', True))
        org.save(update_fields=['verified'])
        AdminAction.objects.create(
            admin=admin, action_type='verify_organization',
            target_model='Organization', target_id=str(org.org_id),
            reason=str(request.data.get('reason') or '')[:500],
            metadata={'verified': org.verified, 'name': org.org_name})
        return _ok(_row(org), 'Saved.')

    if action == 'set_type':
        wanted = str(request.data.get('org_type') or '').strip()
        if wanted not in dict(Organization.TYPE_CHOICES):
            return _err('That is not an organisation type.', 'VALIDATION_ERROR')
        org.org_type = wanted
        org.save(update_fields=['org_type'])
        AdminAction.objects.create(
            admin=admin, action_type='set_organization_type',
            target_model='Organization', target_id=str(org.org_id),
            metadata={'org_type': wanted, 'name': org.org_name})
        return _ok(_row(org), 'Saved.')

    if action == 'set_member_role':
        if role not in MANAGE_ROLES:
            return _err('Not allowed.', 'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)
        username = str(request.data.get('username') or '').strip()
        wanted = str(request.data.get('role') or '').strip()
        member = OrgMember.objects.filter(
            org=org, user__username__iexact=username).first()
        if member is None:
            return _err('%s is not in this organisation.' % username,
                        'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if wanted not in dict(OrgMember.ROLE_CHOICES):
            return _err('That is not a role.', 'VALIDATION_ERROR')
        member.role = wanted
        member.save(update_fields=['role'])
        AdminAction.objects.create(
            admin=admin, action_type='set_org_member_role',
            target_model='OrgMember', target_id=str(member.pk),
            metadata={'org': org.org_name, 'username': username, 'role': wanted})
        return _ok(_row(org), 'Saved.')

    return _err('Say what to do.', 'VALIDATION_ERROR')


@api_view(['POST'])
@admin_role_required(MONEY_ROLES)
def admin_transfer_funds(request):
    """Move coins between any two wallets on the platform.

    The spec calls this "Fund Transfers: transfer funds between accounts,
    organizations, and users". It goes through the SAME `wallets.transfer` as
    everything else, so it writes the same two lines and lands on the
    recipient's own statement.

    An admin moving money invisibly is how a platform loses an argument it
    cannot reconstruct, so this also writes an AdminAction naming both ends.
    """
    admin = request.admin_user

    def resolve(kind, ref):
        kind = str(kind or '').lower()
        ref = str(ref or '').strip()
        if kind == 'user':
            user = Users.objects.filter(username__iexact=ref).first()
            return UserWallet.objects.filter(user=user).first() if user else None
        if kind == 'org':
            org = _org(ref)
            return wallets.wallet_for_org(org) if org else None
        if kind == 'team':
            from .models import Teams
            team = Teams.objects.filter(slug=ref).first() or \
                Teams.objects.filter(team_name__iexact=ref).first()
            return wallets.wallet_for_team(team) if team else None
        return None

    source = resolve(request.data.get('from_kind'), request.data.get('from'))
    target = resolve(request.data.get('to_kind'), request.data.get('to'))
    if source is None:
        return _err('No wallet to take it from.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if target is None:
        return _err('No wallet to send it to.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    reason = str(request.data.get('reason') or '').strip()
    if not reason:
        # An admin moving somebody else's money says why. Six weeks later this
        # is the only thing that answers "and why did that happen".
        return _err('Say why this is being moved.', 'REASON_REQUIRED')

    try:
        # No PIN: an admin transfer is authorised by the admin session and its
        # second factor, not by the wallet's own PIN, which belongs to whoever
        # owns the wallet and which an admin must never be asked for.
        wallets.transfer(source, target, request.data.get('amount'),
                         note=reason[:200], kind='transfer')
    except wallets.WalletError as exc:
        return _err(str(exc), exc.code)

    AdminAction.objects.create(
        admin=admin, action_type='transfer_funds',
        target_model='Wallet', target_id=str(target.pk), reason=reason,
        metadata={'from': wallets.describe(source),
                  'to': wallets.describe(target),
                  'amount': int(request.data.get('amount') or 0)})
    return _ok({'from': wallets.describe(source),
                'to': wallets.describe(target)}, 'Moved.')


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_communities(request):
    """Clubs, and how busy each one is.

    The spec's "Community Management". A club IS the community model on this
    platform - `vent_auth.Club` - so this reports on those rather than
    inventing a second thing called a community.
    """
    from .models import Club, ClubMember, ClubMessage, ClubTopic

    rows = Club.objects.all()
    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(name__icontains=term)

    out = []
    for club in rows.order_by('name')[:200]:
        out.append({
            'slug': club.slug,
            'name': club.name,
            'members': ClubMember.objects.filter(club=club).count(),
            # A message belongs to a TOPIC, and a topic to a club: a club is
            # not one undifferentiated wall. Counting through the topic is the
            # only way to ask "how busy is this club".
            'topics': ClubTopic.objects.filter(club=club).count(),
            'messages': ClubMessage.objects.filter(
                topic__club=club, deleted_at__isnull=True).count(),
            'created_at': club.created_at.isoformat()
            if getattr(club, 'created_at', None) else None,
        })
    return _ok({'results': out, 'count': len(out)})

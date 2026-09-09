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
from .decorators import ROLE_PERMISSIONS, admin_role_required
from .models import (AdminAction, OrgMember, Organization, OrgWallet,
                     Users)

READ_ROLES = ROLE_PERMISSIONS['view_organizations']
MANAGE_ROLES = ROLE_PERMISSIONS['manage_organizations']
MONEY_ROLES = ROLE_PERMISSIONS['transfer_funds']
# The organisation's statement is a financial report, so it answers to
# the same permission as every other financial report rather than to org
# management.
REPORT_ROLES = ROLE_PERMISSIONS['view_transactions']


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _org(ref):
    if str(ref).isdigit():
        return Organization.objects.filter(org_id=int(ref)).first()
    return Organization.objects.filter(slug=str(ref)).first()


def _member(user):
    """One description of a person, the same one every other screen draws."""
    from .views_community import _person

    row = _person(None, user)
    row['email'] = user.email
    return row


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
        # An organisation's premium carries everybody acting for it, which is
        # the whole reason `has_premium` prefers the org: somebody running a
        # tournament for an org that pays is not refused because their personal
        # account does not.
        'is_premium': org.is_premium,
        'premium_note': org.premium_note,
        'premium_until': org.premium_until,
    }


def _create_organization(request):
    """Make an organisation, owned by a real account.

    The spec asks for "Create Organizations: create new organizations,
    including name, description, and type". Type matters more than it looks:
    `capabilities()` reads it, and an organisation created as the wrong type
    has tabs its owner cannot explain.
    """
    from .decorators import effective_admin_role

    admin = request.admin_user
    if effective_admin_role(admin) not in MANAGE_ROLES:
        return _err('Only an admin can create an organisation.', 'NOT_ALLOWED',
                    status.HTTP_403_FORBIDDEN)

    name = str(request.data.get('name') or '').strip()
    org_type = str(request.data.get('org_type') or 'mixed').strip()
    bio = str(request.data.get('description') or '').strip()[:280]
    owner_ref = str(request.data.get('owner') or '').strip()

    if not name:
        return _err('Give it a name.', 'VALIDATION_ERROR')
    if org_type not in dict(Organization.TYPE_CHOICES):
        return _err('That is not an organisation type.', 'VALIDATION_ERROR')
    if Organization.objects.filter(org_name__iexact=name).exists():
        return _err('There is already an organisation with that name.',
                    'NAME_TAKEN', status.HTTP_409_CONFLICT)

    owner = (Users.objects.filter(username__iexact=owner_ref).first()
             or Users.objects.filter(email__iexact=owner_ref).first())
    if owner is None:
        return _err('No account called %s to own it.' % (owner_ref or '-'),
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    org = Organization.objects.create(
        org_name=name, org_type=org_type, bio=bio,
        org_creator=owner, org_owner=owner)
    # The owner is a member as well as the owner. Every membership question on
    # the platform reads OrgMember, and an owner missing from it is an owner
    # who does not appear in their own organisation.
    OrgMember.objects.get_or_create(
        org=org, user=owner,
        defaults={'role': OrgMember.ROLE_OWNER,
                  'scopes': list(OrgMember.ALL_SCOPES)})

    AdminAction.objects.create(
        admin=admin, action_type='create_organization',
        target_model='Organization', target_id=str(org.org_id),
        reason=str(request.data.get('reason') or '')[:500],
        metadata={'name': org.org_name, 'org_type': org.org_type,
                  'owner': owner.username})
    return _ok(_row(org), 'Created.')


@api_view(['GET', 'POST'])
@admin_role_required(READ_ROLES)
def admin_organizations(request):
    """Every organisation, with what it holds. POST creates one.

    `?q=` searches the name. `?verified=1` narrows to the verified ones, which
    is the question somebody actually asks when they open this.

    Creating one names an OWNER who is an existing account. An organisation
    with no owner is an organisation nobody can run, and the console would be
    the only way to touch it ever again.
    """
    if request.method == 'POST':
        return _create_organization(request)

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
            # Through the one person builder, so a member here carries the
            # same face and the same founder mark as every other screen. A
            # hand-built person dict is how an avatar goes missing on one
            # screen and nowhere else.
            'members_list': [{
                **_member(m.user),
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

    if action == 'set_premium':
        # A DIFFERENT permission from the rest of this view. Verifying an
        # organisation or renaming it is administration; giving it the paid
        # features for nothing is a commercial decision, and `manage_
        # organizations` includes people who should not be making it.
        from .premium_admin import apply_premium

        if role not in ROLE_PERMISSIONS['grant_premium']:
            return _err('Only a super admin or the financial manager can grant '
                        'premium.', 'NOT_ALLOWED', status.HTTP_403_FORBIDDEN)

        wanted = request.data.get('premium')
        if wanted is None:
            return _err('Say whether premium is on or off.', 'VALIDATION_ERROR')
        note = str(request.data.get('note') or '').strip()
        if wanted and not note:
            return _err('Say why this organisation is being given premium.',
                        'NOTE_REQUIRED')

        apply_premium(org, on=bool(wanted), note=note, admin=admin,
                      kind='Organization')
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

    if action == 'set_details':
        # Renaming an organisation moves its address with it, and every
        # address it has ever had keeps working: `save()` calls `sync_slug`
        # and the old one goes to SlugHistory. That is why this writes through
        # the model rather than updating the column.
        name = str(request.data.get('name') or '').strip()
        if name and name.lower() != org.org_name.lower():
            if Organization.objects.filter(
                    org_name__iexact=name).exclude(pk=org.pk).exists():
                return _err('There is already an organisation with that name.',
                            'NAME_TAKEN', status.HTTP_409_CONFLICT)
            org.org_name = name
        if 'description' in request.data:
            org.bio = str(request.data.get('description') or '')[:280]
        if 'location' in request.data:
            org.location = str(request.data.get('location') or '')[:120]
        org.save()
        AdminAction.objects.create(
            admin=admin, action_type='edit_organization',
            target_model='Organization', target_id=str(org.org_id),
            reason=str(request.data.get('reason') or '')[:500],
            metadata={'name': org.org_name})
        return _ok(_row(org), 'Saved.')

    if action == 'remove_member':
        username = str(request.data.get('username') or '').strip()
        member = OrgMember.objects.filter(
            org=org, user__username__iexact=username).first()
        if member is None:
            return _err('%s is not in this organisation.' % username,
                        'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if member.user_id == org.org_owner_id:
            # Removing the owner leaves an organisation nobody can run, and
            # the console is not where an ownership transfer belongs.
            return _err('The owner cannot be removed. Change the owner first.',
                        'CANNOT_REMOVE_OWNER')
        member.delete()
        AdminAction.objects.create(
            admin=admin, action_type='remove_org_member',
            target_model='Organization', target_id=str(org.org_id),
            reason=str(request.data.get('reason') or '')[:500],
            metadata={'org': org.org_name, 'username': username})
        return _ok(_row(org), 'Removed.')

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

    # `wallets.resolve_target` and nothing local. This view had its own copy of
    # the same nine lines, and the module it copied them from carries a comment
    # saying why there is only supposed to be one: "two copies would eventually
    # disagree about what 'vermillion' means, which is the same fault seen from
    # the inside". The copy here had already drifted: it refused an account
    # whose wallet row predates automatic creation, where the shared resolver
    # makes one.
    try:
        source = wallets.resolve_target(request.data.get('from_kind'),
                                        request.data.get('from'))
        target = wallets.resolve_target(request.data.get('to_kind'),
                                        request.data.get('to'))
    except wallets.WalletError as exc:
        return _err(str(exc), exc.code,
                    status.HTTP_404_NOT_FOUND if exc.code == 'NOT_FOUND'
                    else status.HTTP_400_BAD_REQUEST)

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
@admin_role_required(ROLE_PERMISSIONS['manage_communities'])
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


@api_view(['GET'])
@admin_role_required(REPORT_ROLES)
def admin_organization_report(request, org_ref):
    """An organisation's statement as a CSV file.

    The spec asks for "generate financial reports for the organization". An
    `HttpResponse`, not a DRF `Response`: a DRF Response hands the CSV to the
    JSON renderer and the browser saves a quoted string. The test reads
    `res.content` and asserts on the header line, because a test reading
    `res.data` sees the right characters either way and proves nothing.
    """
    import csv
    import io as _io

    from django.http import HttpResponse

    org = _org(org_ref)
    if org is None:
        return _err('No organisation with that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    wallet = OrgWallet.objects.filter(org=org).first()
    lines = wallets.statement(wallet, limit=5000) if wallet else []

    buffer = _io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['when', 'type', 'amount_vc', 'status', 'description'])
    for line in lines:
        writer.writerow([line['at'], line['type'], line['amount'],
                         line['status'], line['description']])

    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = (
        'attachment; filename="%s-statement.csv"' % (org.slug or org.org_id))
    return response

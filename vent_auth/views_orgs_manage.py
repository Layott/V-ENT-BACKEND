"""Running an organisation: its profile, who is in it, and what it holds.

CEO, 31 August 2026: "trying to invite members to my organization says coming
soon why? I should be able to invite people and give them different roles to
manage different things. An organization can have different teams, events,
tournaments, clubs."

Three things follow, and they are all in this file:

1. **The profile can be edited.** The create wizard said "you can update them
   anytime" and there was no endpoint that could. A logo is the one field
   nobody gets right first time.
2. **Invites, with the role decided by the person sending it.** An invite that
   arrives without a role is a request somebody then has to grade, which is the
   join-request flow the platform already had. The point of an invite is that
   accepting it is one press.
3. **Scopes.** A manager runs only the areas named on their membership, so
   "different roles to manage different things" is a stored decision rather
   than a rule somebody has to remember.

Permission lives in `OrgMember.outranks` and `OrgMember.may_run`, not here, so
the ladder is written once. Every endpoint re-asks: a hidden control is a
courtesy, and anybody can call an endpoint directly.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view

from .models import Club, OrgInvite, OrgMember, Organization, Users
from .views_notifications import create_notification
from .views_orgs import (
    _abs, _authenticate, _error, _ok, _optional_user, _org_by_ref, _person_row,
    _role_of, serialize_member, serialize_org,
)

# Fields on the profile a manager may not touch and an admin may.
TEXT_FIELDS = {
    'tag': 12, 'bio': 280, 'focus': 120, 'location': 120, 'region': 60,
    'contact_email': 254,
}


def _membership(org, user):
    """The caller's membership row, inventing one for an owner whose row is
    missing. Ownership is recorded on the organisation as well as in the member
    table, and an organisation created before the member table existed has the
    first and not the second."""
    if user is None:
        return None
    row = OrgMember.objects.filter(org=org, user=user).select_related('user').first()
    if row is None and org.org_owner_id == user.user_id:
        row = OrgMember(org=org, user=user, role=OrgMember.ROLE_OWNER)
    return row


def _require(request, org_ref, minimum=None, area=None):
    """(org, membership, error). `minimum` is a role, `area` a scope."""
    user, err = _authenticate(request)
    if err:
        return None, None, err
    org = _org_by_ref(org_ref)
    if org is None:
        return None, None, _error('Organization not found.', 'NOT_FOUND',
                                  status.HTTP_404_NOT_FOUND)
    me = _membership(org, user)
    if me is None:
        return org, None, _error('You are not in this organization.', 'FORBIDDEN',
                                 status.HTTP_403_FORBIDDEN)
    if minimum and me.rank < OrgMember.RANK[minimum]:
        return org, me, _error('Your role in this organization does not allow that.',
                               'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if area and not me.may_run(area):
        return org, me, _error('You do not manage that part of this organization.',
                               'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    return org, me, None


def capabilities(membership):
    """What the caller may do, as plain booleans, in the shape the screen reads."""
    if membership is None:
        return {
            'is_member': False, 'role': None, 'areas': [],
            'can_edit_profile': False, 'can_invite': False,
            'can_manage_roles': False, 'can_remove_members': False,
        }
    rank = membership.rank
    return {
        'is_member': True,
        'role': membership.role,
        'areas': membership.areas,
        'can_edit_profile': rank >= OrgMember.RANK[OrgMember.ROLE_ADMIN],
        'can_invite': rank >= OrgMember.RANK[OrgMember.ROLE_ADMIN],
        'can_manage_roles': rank >= OrgMember.RANK[OrgMember.ROLE_ADMIN],
        'can_remove_members': rank >= OrgMember.RANK[OrgMember.ROLE_ADMIN],
    }


def serialize_invite(request, inv):
    return {
        'token': inv.token,
        'status': inv.status,
        'role': inv.role,
        'scopes': inv.scopes or [],
        'message': inv.message,
        'created_at': inv.created_at,
        'responded_at': inv.responded_at,
        'user': _person_row(request, inv.user),
        'invited_by': _person_row(request, inv.invited_by) if inv.invited_by else None,
        'organization': {
            'id': inv.org.org_id,
            'slug': inv.org.slug,
            'name': inv.org.org_name,
            'logo': _abs(request, inv.org.logo),
        },
    }


def _clean_scopes(raw):
    if not isinstance(raw, (list, tuple)):
        return []
    return [s for s in OrgMember.ALL_SCOPES if s in set(raw)]


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------

@api_view(['POST'])
def org_update(request, org_id):
    """Edit the organisation, pictures included.

    Multipart when there is a file, JSON otherwise. The create wizard sent the
    logo as a `blob:` object URL inside a JSON body, so `request.FILES` was
    empty and the picture was dropped without a word - the same fault the
    tournament wizard had.
    """
    org, me, err = _require(request, org_id, minimum=OrgMember.ROLE_ADMIN)
    if err:
        return err

    changed = []
    name = (request.data.get('name') or request.data.get('org_name') or '').strip()
    if name and name != org.org_name:
        if Organization.objects.filter(org_name__iexact=name).exclude(pk=org.pk).exists():
            return _error('An organization with that name already exists.', 'DUPLICATE',
                          status.HTTP_409_CONFLICT)
        org.org_name = name[:148]
        # The slug follows the name, and `save()` adds it to update_fields.
        changed.append('org_name')

    for field, limit in TEXT_FIELDS.items():
        if field in request.data:
            setattr(org, field, (request.data.get(field) or '').strip()[:limit])
            changed.append(field)
    if 'mission' in request.data:
        org.mission = (request.data.get('mission') or '').strip()
        changed.append('mission')
    if 'social_links' in request.data:
        links = request.data.get('social_links')
        if isinstance(links, str):
            import json
            try:
                links = json.loads(links)
            except ValueError:
                links = None
        if links is not None:
            org.social_links = links
            changed.append('social_links')

    for field in ('logo', 'banner'):
        uploaded = request.FILES.get(field)
        if uploaded is not None:
            setattr(org, field, uploaded)
            changed.append(field)
        elif request.data.get('remove_%s' % field):
            setattr(org, field, None)
            changed.append(field)

    if changed:
        org.save(update_fields=list(dict.fromkeys(changed)))
    return _ok({'organization': serialize_org(request, org, me.user, detail=True),
                'me': capabilities(me)},
               'Organization updated.')


# ---------------------------------------------------------------------------
# Who is in it
# ---------------------------------------------------------------------------

@api_view(['GET'])
def org_capabilities(request, org_id):
    """What the caller may do here. Its own endpoint because every management
    screen needs it and the detail payload is read by signed-out visitors."""
    org = _org_by_ref(org_id)
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({'me': capabilities(_membership(org, _optional_user(request)))},
               'Capabilities retrieved.')


@api_view(['POST'])
def org_set_role(request, org_id):
    """Set somebody's role, and the areas a manager runs.

    Only the owner may appoint an admin: an admin who can make admins can hand
    the organisation away.
    """
    org, me, err = _require(request, org_id, minimum=OrgMember.ROLE_ADMIN)
    if err:
        return err

    role = (request.data.get('role') or '').strip().lower()
    if role not in dict(OrgMember.ROLE_CHOICES):
        return _error('That is not a role.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if role == OrgMember.ROLE_OWNER:
        return _error('Ownership is handed over, not assigned.', 'VALIDATION_ERROR',
                      status.HTTP_400_BAD_REQUEST)

    username = (request.data.get('username') or '').strip()
    target_user = (Users.objects.filter(username__iexact=username).first() if username
                   else Users.objects.filter(pk=request.data.get('user_id')).first())
    target = OrgMember.objects.filter(org=org, user=target_user).select_related('user').first() \
        if target_user else None
    if target is None:
        return _error('That person is not a member.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if target.pk == getattr(me, 'pk', None):
        return _error('You cannot change your own role.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if not me.outranks(target):
        return _error('You cannot change the role of somebody at your own level or above.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if role == OrgMember.ROLE_ADMIN and me.role != OrgMember.ROLE_OWNER:
        return _error('Only the owner can make somebody an admin.', 'FORBIDDEN',
                      status.HTTP_403_FORBIDDEN)

    target.role = role
    # Scopes only mean anything for a manager. Clearing them on any other role
    # stops a demoted manager keeping rights nobody can see on the screen.
    target.scopes = _clean_scopes(request.data.get('scopes')) \
        if role == OrgMember.ROLE_MANAGER else []
    target.save(update_fields=['role', 'scopes'])

    create_notification(
        target.user, 'system',
        'Your role at %s changed' % org.org_name,
        'You are now %s.' % role,
        link='/organizations/%s' % (org.slug or org.org_id),
        metadata={'org': org.slug, 'role': role},
    )
    return _ok({'member': serialize_member(request, target)},
               '@%s is now %s.' % (target.user.username, role))


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

@api_view(['POST'])
def org_invite(request, org_id):
    org, me, err = _require(request, org_id, minimum=OrgMember.ROLE_ADMIN)
    if err:
        return err

    username = (request.data.get('username') or '').strip().lstrip('@')
    if not username:
        return _error('Give the username of the person to invite.', 'VALIDATION_ERROR',
                      status.HTTP_400_BAD_REQUEST)
    target = Users.objects.filter(username__iexact=username).first()
    if target is None:
        return _error('No V-ENT account with that username.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    if OrgMember.objects.filter(org=org, user=target).exists():
        return _error('@%s is already in this organization.' % target.username,
                      'ALREADY_MEMBER', status.HTTP_400_BAD_REQUEST)

    role = (request.data.get('role') or OrgMember.ROLE_MEMBER).strip().lower()
    if role not in dict(OrgMember.ROLE_CHOICES) or role == OrgMember.ROLE_OWNER:
        return _error('That is not a role somebody can be invited as.', 'VALIDATION_ERROR',
                      status.HTTP_400_BAD_REQUEST)
    if role == OrgMember.ROLE_ADMIN and me.role != OrgMember.ROLE_OWNER:
        return _error('Only the owner can invite somebody as an admin.', 'FORBIDDEN',
                      status.HTTP_403_FORBIDDEN)

    scopes = _clean_scopes(request.data.get('scopes')) if role == OrgMember.ROLE_MANAGER else []

    existing = OrgInvite.objects.filter(
        org=org, user=target, status=OrgInvite.STATUS_PENDING).first()
    if existing:
        # Re-inviting somebody is how a role is corrected before they answer,
        # rather than a second invite they then have to choose between.
        existing.role, existing.scopes = role, scopes
        existing.message = (request.data.get('message') or '').strip()[:280]
        existing.invited_by = me.user
        existing.save(update_fields=['role', 'scopes', 'message', 'invited_by'])
        invite = existing
    else:
        invite = OrgInvite.objects.create(
            org=org, user=target, invited_by=me.user, role=role, scopes=scopes,
            message=(request.data.get('message') or '').strip()[:280],
        )

    create_notification(
        target, 'system',
        '%s invited you' % org.org_name,
        'You have been invited to join as %s.' % role,
        link='/organizations/invites',
        metadata={'org': org.slug, 'invite': invite.token, 'role': role},
    )
    return _ok({'invite': serialize_invite(request, invite)},
               'Invite sent to @%s.' % target.username)


@api_view(['GET'])
def org_invites(request, org_id):
    org, me, err = _require(request, org_id, minimum=OrgMember.ROLE_ADMIN)
    if err:
        return err
    rows = (OrgInvite.objects.filter(org=org)
            .select_related('user', 'invited_by', 'org')
            .order_by('-created_at')[:100])
    return _ok({'invites': [serialize_invite(request, i) for i in rows]},
               'Invites retrieved.')


@api_view(['POST'])
def org_cancel_invite(request, org_id, token):
    org, me, err = _require(request, org_id, minimum=OrgMember.ROLE_ADMIN)
    if err:
        return err
    invite = OrgInvite.objects.filter(org=org, token=token).first()
    if invite is None:
        return _error('Invite not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if invite.status != OrgInvite.STATUS_PENDING:
        return _error('That invite has already been answered.', 'ALREADY_ANSWERED',
                      status.HTTP_400_BAD_REQUEST)
    invite.status = OrgInvite.STATUS_CANCELLED
    invite.responded_at = timezone.now()
    invite.save(update_fields=['status', 'responded_at'])
    return _ok({'token': token}, 'Invite cancelled.')


@api_view(['GET'])
def my_org_invites(request):
    user, err = _authenticate(request)
    if err:
        return err
    rows = (OrgInvite.objects.filter(user=user, status=OrgInvite.STATUS_PENDING)
            .select_related('org', 'invited_by', 'user'))
    return _ok({'invites': [serialize_invite(request, i) for i in rows],
                'count': rows.count()}, 'Invites retrieved.')


@api_view(['POST'])
def respond_to_invite(request, token):
    """Accept or decline. The answer is on the invite so a second press is a
    no-op rather than a second membership."""
    user, err = _authenticate(request)
    if err:
        return err
    invite = (OrgInvite.objects.filter(token=token)
              .select_related('org', 'user', 'invited_by').first())
    if invite is None or invite.user_id != user.user_id:
        return _error('Invite not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if invite.status != OrgInvite.STATUS_PENDING:
        return _error('You have already answered this invite.', 'ALREADY_ANSWERED',
                      status.HTTP_400_BAD_REQUEST)

    accept = bool(request.data.get('accept'))
    invite.status = OrgInvite.STATUS_ACCEPTED if accept else OrgInvite.STATUS_DECLINED
    invite.responded_at = timezone.now()
    invite.save(update_fields=['status', 'responded_at'])

    if accept:
        OrgMember.objects.get_or_create(
            org=invite.org, user=user,
            defaults={'role': invite.role, 'scopes': invite.scopes or []},
        )
        if invite.invited_by:
            create_notification(
                invite.invited_by, 'system',
                '@%s joined %s' % (user.username, invite.org.org_name),
                'They accepted your invite as %s.' % invite.role,
                link='/organizations/%s' % (invite.org.slug or invite.org.org_id),
            )

    return _ok({'invite': serialize_invite(request, invite),
                'joined': accept},
               'You joined %s.' % invite.org.org_name if accept else 'Invite declined.')


# ---------------------------------------------------------------------------
# What the organisation holds
# ---------------------------------------------------------------------------

@api_view(['GET'])
def org_clubs(request, org_id):
    org = _org_by_ref(org_id)
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    from .views_community import serialize_club

    viewer = _optional_user(request)
    rows = [serialize_club(request, c, viewer)
            for c in org.clubs.select_related('game', 'owner')]
    return _ok({'clubs': rows, 'count': len(rows)}, 'Clubs retrieved.')


@api_view(['GET'])
def linkable_clubs(request):
    """Clubs the caller owns that belong to no organisation yet.

    The link endpoint needs the caller to own the club and to run the clubs
    area of the organisation. Without this list the screen has no honest way to
    offer the first half of that, and the control would be a text box somebody
    has to type a slug into.
    """
    user, err = _authenticate(request)
    if err:
        return err
    rows = [
        {
            'id': c.id,
            'slug': c.slug,
            'name': c.name,
            'game': c.game.game_title if c.game else None,
            'logo': _abs(request, c.logo),
            'member_count': c.members.count(),
        }
        for c in Club.objects.filter(owner=user, organization__isnull=True)
        .select_related('game')
    ]
    return _ok({'clubs': rows, 'count': len(rows)}, 'Linkable clubs retrieved.')


@api_view(['POST'])
def org_link_club(request, org_id):
    """Put a club under the organisation. Only somebody who runs the club may
    hand it over, and only somebody who runs that area of the organisation may
    take it."""
    org, me, err = _require(request, org_id, area=OrgMember.SCOPE_CLUBS)
    if err:
        return err

    ref = str(request.data.get('club') or request.data.get('slug') or '').strip()
    club = (Club.objects.filter(id=int(ref)).first() if ref.isdigit()
            else Club.objects.filter(slug=ref).first())
    if club is None:
        return _error('Club not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if club.owner_id != me.user_id:
        return _error('Only the club owner can hand it to an organization.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if club.organization_id and club.organization_id != org.org_id:
        return _error('That club already belongs to another organization.',
                      'ALREADY_LINKED', status.HTTP_400_BAD_REQUEST)

    club.organization = org
    club.save(update_fields=['organization'])
    return _ok({'club': {'id': club.id, 'slug': club.slug, 'name': club.name}},
               '%s now belongs to %s.' % (club.name, org.org_name))


@api_view(['POST'])
def org_unlink_club(request, org_id):
    org, me, err = _require(request, org_id, area=OrgMember.SCOPE_CLUBS)
    if err:
        return err

    ref = str(request.data.get('club') or request.data.get('slug') or '').strip()
    club = (Club.objects.filter(id=int(ref), organization=org).first() if ref.isdigit()
            else Club.objects.filter(slug=ref, organization=org).first())
    if club is None:
        return _error('That club is not under this organization.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    club.organization = None
    club.save(update_fields=['organization'])
    return _ok({'club': {'id': club.id, 'slug': club.slug, 'name': club.name}},
               '%s is no longer under %s.' % (club.name, org.org_name))

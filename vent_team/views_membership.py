"""Getting people into a team, and saying what they may do once they are in.

CEO, 29 August 2026: "There is no way for me to add players to my teams or
invite people, or get a link players can use to join directly. no where to also
manage the roles of players in the team and the access they have and what they
can control."

Three ways in, and they are genuinely different things:

- A player ASKS to join. That already existed (`request_join`), and somebody
  with authority accepts it.
- An owner INVITES a named player. New here. The player is told and answers;
  accepting puts them straight in, because the invitation was the decision.
- An owner posts a LINK. New here. Whoever follows it joins, which is the
  point and also the risk, so a link expires, can be capped and can be revoked.

And once somebody is in, their role decides what they may do. That matrix lives
in `vent_team/permissions.py` and is enforced here rather than described in a
comment somewhere.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import TeamInvite, TeamMembers, Teams, Users

from . import permissions as perms


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ok(data, message='OK', http=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _authenticate(request):
    """The session token, the way every other view in this project reads it."""
    from vent_auth.views_community import _authenticate as shared
    return shared(request)


def _team_by_ref(ref):
    """A team by its slug, or by its id for a link shared before the rename."""
    ref = str(ref).strip()
    if ref.isdigit():
        return Teams.objects.filter(team_id=int(ref)).first()
    return Teams.objects.filter(slug=ref).first()


def role_of(team, user):
    """What this person is in this team, or None if they are not in it."""
    if user is None:
        return None
    if team.team_owner_id == user.user_id:
        return 'owner'
    row = TeamMembers.objects.filter(team=team, user=user).first()
    return row.role if row else None


def _require(team, user, permission):
    """(role, None) when allowed, (None, response) when not.

    The message names the permission rather than saying "not allowed", because
    somebody who is a coach and expected to be a manager needs to know which of
    those two things is wrong.
    """
    role = role_of(team, user)
    if role is None:
        return None, _err('You are not in this team.', 'NOT_A_MEMBER',
                          status.HTTP_403_FORBIDDEN)
    if not perms.can(role, permission):
        label = role.replace('_', ' ')
        return None, _err(
            f'A {label} cannot do that. Ask an owner or a manager.',
            'ROLE_NOT_ALLOWED', status.HTTP_403_FORBIDDEN)
    return role, None


def _member_row(request, member):
    from vent_auth.views_community import _person
    return {
        'id': member.team_member_id,
        'role': member.role,
        'joined_at': member.join_date,
        'user': _person(request, member.user),
        'permissions': sorted(perms.permissions_for(member.role)),
    }


def _invite_row(request, invite):
    from vent_auth.views_community import _person
    from django.conf import settings

    base = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    if not base.startswith('https://v-ent.co'):
        base = 'https://v-ent.co'

    return {
        'id': invite.id,
        # The team, by name and by address. The invited player's screen shows
        # the invitation without the team page around it, so it has to say
        # which team this is.
        'team_id': invite.team_id,
        'team_name': invite.team.team_name,
        'team_slug': invite.team.slug,
        'kind': invite.kind,
        'role': invite.role,
        'status': invite.status,
        'message': invite.message,
        'invited_by': _person(request, invite.invited_by),
        'user': _person(request, invite.user) if invite.user_id else None,
        # The address the owner copies and posts. Built here rather than on the
        # page, for the same reason the influencer link is: the organiser sends
        # it to somebody else, and a link that is only right when the console
        # happens to be on the right host goes out wrong.
        'url': f'{base}/teams/join/{invite.token}' if invite.kind == 'link' else None,
        'token': invite.token or None,
        'expires_at': invite.expires_at,
        'max_uses': invite.max_uses,
        'uses': invite.uses,
        'spent': invite.is_spent,
        'created_at': invite.created_at,
    }


# ---------------------------------------------------------------------------
# what a role means
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def role_catalogue(request):
    """Every role and what it can do.

    Served rather than duplicated in the front end, so the picker that offers a
    role and the endpoint that enforces it cannot drift apart, and so the
    screen can explain each role instead of showing seven words with no
    consequences attached.
    """
    return _ok({'roles': perms.role_table(), 'permissions': list(perms.ALL)},
               'Roles retrieved.')


# ---------------------------------------------------------------------------
# the roster
# ---------------------------------------------------------------------------

@api_view(['GET'])
def team_roster(request, team_id):
    """Who is in the team, what each may do, and what the viewer may do."""
    user, err = _authenticate(request)
    if err:
        return err
    team = _team_by_ref(team_id)
    if team is None:
        return _err('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    members = (TeamMembers.objects.filter(team=team)
               .select_related('user').order_by('role', 'join_date'))
    my_role = role_of(team, user)
    return _ok({
        'members': [_member_row(request, m) for m in members],
        'count': members.count(),
        'my_role': my_role,
        # What the person looking at the page may do, so the page can draw the
        # controls they can actually use and no others.
        'my_permissions': sorted(perms.permissions_for(my_role)),
    }, 'Roster retrieved.')


@api_view(['POST'])
def set_member_role(request, team_id):
    """Change what somebody is in the team."""
    user, err = _authenticate(request)
    if err:
        return err
    team = _team_by_ref(team_id)
    if team is None:
        return _err('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    _, denied = _require(team, user, perms.SET_ROLE)
    if denied:
        return denied

    username = (request.data.get('username') or '').strip()
    new_role = (request.data.get('role') or '').strip().lower()
    if new_role not in perms.ROLE_PERMISSIONS or new_role == 'owner':
        # Owner is not a role you are given. It is transferred, deliberately,
        # through its own endpoint that says what is happening.
        return _err('That is not a role you can assign. To hand over the team, '
                    'transfer ownership.', 'BAD_ROLE')

    target = Users.objects.filter(username__iexact=username).first()
    if target is None:
        return _err(f'No player called "{username}".', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if team.team_owner_id == target.user_id:
        return _err('The owner keeps their role until the team is transferred.',
                    'CANNOT_DEMOTE_OWNER')

    row = TeamMembers.objects.filter(team=team, user=target).first()
    if row is None:
        return _err(f'{target.username} is not in this team.', 'NOT_A_MEMBER',
                    status.HTTP_404_NOT_FOUND)

    row.role = new_role
    row.is_captain = new_role in ('owner', 'captain')
    row.save(update_fields=['role', 'is_captain'])

    _notify(target, f'You are now {new_role.replace("_", " ")} of {team.team_name}',
            team)
    return _ok({'member': _member_row(request, row)}, 'Role updated.')


@api_view(['POST'])
def remove_member(request, team_id):
    """Take somebody out of the team."""
    user, err = _authenticate(request)
    if err:
        return err
    team = _team_by_ref(team_id)
    if team is None:
        return _err('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    username = (request.data.get('username') or '').strip()
    target = Users.objects.filter(username__iexact=username).first()
    if target is None:
        return _err(f'No player called "{username}".', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if team.team_owner_id == target.user_id:
        return _err('The owner cannot be removed. Transfer the team first.',
                    'CANNOT_REMOVE_OWNER')

    row = TeamMembers.objects.filter(team=team, user=target).first()
    if row is None:
        return _err(f'{target.username} is not in this team.', 'NOT_A_MEMBER',
                    status.HTTP_404_NOT_FOUND)

    # Removing a leader is a stronger power than removing a member, so that two
    # captains cannot remove each other and a manager cannot quietly unseat the
    # people above them.
    needed = perms.REMOVE_LEADER if row.role in ('captain', 'vice_captain') else perms.REMOVE_MEMBER
    _, denied = _require(team, user, needed)
    if denied:
        return denied

    with transaction.atomic():
        row.delete()
        team.number_of_members = TeamMembers.objects.filter(team=team).count()
        team.save(update_fields=['number_of_members'])

    return _ok({'removed': target.username}, f'{target.username} removed.')


# ---------------------------------------------------------------------------
# invitations
# ---------------------------------------------------------------------------

def _notify(user, title, team, link=None):
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            user=user, category='team', title=title, body='',
            link=link or f'/teams/{team.slug or team.team_id}',
            metadata={'team_id': team.team_id},
        )
    except Exception:
        # A notification that fails must never fail the thing it is about.
        pass


@api_view(['GET', 'POST'])
def team_invites(request, team_id):
    """List the invitations on a team, or make one."""
    user, err = _authenticate(request)
    if err:
        return err
    team = _team_by_ref(team_id)
    if team is None:
        return _err('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        _, denied = _require(team, user, perms.INVITE)
        if denied:
            return denied
        rows = team.invites.select_related('user', 'invited_by').all()
        return _ok({'invites': [_invite_row(request, i) for i in rows],
                    'count': rows.count()}, 'Invites retrieved.')

    kind = (request.data.get('kind') or 'direct').strip().lower()
    role = (request.data.get('role') or 'member').strip().lower()
    if role not in perms.ROLE_PERMISSIONS or role == 'owner':
        return _err('That is not a role you can invite somebody as.', 'BAD_ROLE')

    needed = perms.MANAGE_LINKS if kind == 'link' else perms.INVITE
    _, denied = _require(team, user, needed)
    if denied:
        return denied

    if kind == 'link':
        try:
            max_uses = int(request.data.get('max_uses') or 0)
        except (TypeError, ValueError):
            return _err('The number of uses must be a number.', 'VALIDATION_ERROR')
        if max_uses < 0:
            return _err('The number of uses cannot be negative.', 'VALIDATION_ERROR')

        expires_at = None
        try:
            days = int(request.data.get('expires_in_days') or 0)
        except (TypeError, ValueError):
            return _err('The expiry must be a number of days.', 'VALIDATION_ERROR')
        if days > 0:
            expires_at = timezone.now() + timezone.timedelta(days=days)

        invite = TeamInvite.objects.create(
            team=team, kind='link', invited_by=user, role=role,
            message=(request.data.get('message') or '').strip()[:280],
            max_uses=max_uses, expires_at=expires_at,
        )
        return _ok({'invite': _invite_row(request, invite)}, 'Link created.',
                   status.HTTP_201_CREATED)

    username = (request.data.get('username') or '').strip()
    target = Users.objects.filter(username__iexact=username).first()
    if target is None:
        return _err(f'No player called "{username}".', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if role_of(team, target) is not None:
        return _err(f'{target.username} is already in this team.', 'ALREADY_A_MEMBER',
                    status.HTTP_409_CONFLICT)

    existing = TeamInvite.objects.filter(team=team, user=target, kind='direct',
                                         status='pending').first()
    if existing is not None:
        # Asking twice is a reminder, not a second row in their list.
        _notify(target, f'{team.team_name} invited you to join', team)
        return _ok({'invite': _invite_row(request, existing)},
                   f'{target.username} has already been invited; they have been reminded.')

    invite = TeamInvite.objects.create(
        team=team, kind='direct', invited_by=user, user=target, role=role,
        message=(request.data.get('message') or '').strip()[:280],
    )
    _notify(target, f'{team.team_name} invited you to join', team)
    return _ok({'invite': _invite_row(request, invite)}, 'Invitation sent.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def revoke_invite(request, team_id, invite_id):
    """Withdraw an invitation, or switch off a link."""
    user, err = _authenticate(request)
    if err:
        return err
    team = _team_by_ref(team_id)
    if team is None:
        return _err('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    invite = TeamInvite.objects.filter(team=team, id=invite_id).first()
    if invite is None:
        return _err('Invitation not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    needed = perms.MANAGE_LINKS if invite.kind == 'link' else perms.INVITE
    _, denied = _require(team, user, needed)
    if denied:
        return denied

    invite.status = 'revoked'
    invite.answered_at = timezone.now()
    invite.save(update_fields=['status', 'answered_at'])
    return _ok({'invite': _invite_row(request, invite)}, 'Withdrawn.')


@api_view(['GET'])
def my_invites(request):
    """The invitations waiting for the person asking."""
    user, err = _authenticate(request)
    if err:
        return err
    rows = (TeamInvite.objects.filter(user=user, kind='direct', status='pending')
            .select_related('team', 'invited_by', 'user'))
    return _ok({'invites': [_invite_row(request, i) for i in rows],
                'count': rows.count()}, 'Invites retrieved.')


@api_view(['POST'])
def respond_to_invite(request, invite_id):
    """Accept or decline an invitation addressed to you."""
    user, err = _authenticate(request)
    if err:
        return err

    invite = (TeamInvite.objects.select_related('team', 'user')
              .filter(id=invite_id, kind='direct').first())
    if invite is None:
        return _err('Invitation not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if invite.user_id != user.user_id:
        return _err('That invitation is not yours.', 'FORBIDDEN',
                    status.HTTP_403_FORBIDDEN)
    if invite.status != 'pending':
        return _err(f'That invitation was already {invite.status}.',
                    'STATE_CONFLICT', status.HTTP_409_CONFLICT)

    accept = bool(request.data.get('accept'))
    with transaction.atomic():
        invite.status = 'accepted' if accept else 'declined'
        invite.answered_at = timezone.now()
        invite.save(update_fields=['status', 'answered_at'])

        if accept:
            TeamMembers.objects.get_or_create(
                team=invite.team, user=user,
                defaults={'role': invite.role,
                          'is_captain': invite.role in ('owner', 'captain')},
            )
            invite.team.number_of_members = TeamMembers.objects.filter(
                team=invite.team).count()
            invite.team.save(update_fields=['number_of_members'])

    if accept:
        _notify(invite.invited_by, f'{user.username} joined {invite.team.team_name}',
                invite.team)
    return _ok({'invite': _invite_row(request, invite),
                'joined': accept}, 'Joined.' if accept else 'Declined.')


@api_view(['GET', 'POST'])
def join_by_link(request, token):
    """Look at a join link, or use it.

    GET is public on purpose: somebody following a link from a group chat sees
    which team it is before being asked to sign in, and a sign-in wall in front
    of "which team is this" is how a link stops working.
    """
    invite = (TeamInvite.objects.select_related('team', 'invited_by')
              .filter(token=token, kind='link').first())
    if invite is None:
        return _err('That link is not valid.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return _ok({
            'team': {'id': invite.team.team_id, 'slug': invite.team.slug,
                     'name': invite.team.team_name},
            'role': invite.role,
            'message': invite.message,
            'spent': invite.is_spent,
        }, 'Link read.')

    user, err = _authenticate(request)
    if err:
        return err

    # Checked at the moment it is used, not at the moment it was made: a link
    # that expired while somebody had the page open must not still work.
    if invite.is_spent:
        return _err('That link has expired or been used up.', 'LINK_SPENT',
                    status.HTTP_409_CONFLICT)
    if role_of(invite.team, user) is not None:
        return _err('You are already in this team.', 'ALREADY_A_MEMBER',
                    status.HTTP_409_CONFLICT)

    with transaction.atomic():
        # Locked, because a link with three uses left and ten people following
        # it at once is exactly the case where a read-then-write puts thirteen
        # people in the team.
        locked = TeamInvite.objects.select_for_update().get(pk=invite.pk)
        if locked.is_spent:
            return _err('That link has expired or been used up.', 'LINK_SPENT',
                        status.HTTP_409_CONFLICT)

        TeamMembers.objects.get_or_create(
            team=locked.team, user=user,
            defaults={'role': locked.role,
                      'is_captain': locked.role in ('owner', 'captain')},
        )
        locked.uses += 1
        locked.save(update_fields=['uses'])
        locked.team.number_of_members = TeamMembers.objects.filter(
            team=locked.team).count()
        locked.team.save(update_fields=['number_of_members'])

    _notify(invite.invited_by, f'{user.username} joined {invite.team.team_name} '
            f'through your link', invite.team)
    return _ok({'team': {'id': invite.team.team_id, 'slug': invite.team.slug,
                         'name': invite.team.team_name},
                'role': invite.role}, f'You joined {invite.team.team_name}.')

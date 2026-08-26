"""Organizations - profiles, membership, join requests, followers, verification.

Mounted at the root (`/organization/...`) because that is what the frontend
calls. Permission model:
  owner            - everything, including promote/kick/verification
  admin, manager   - approve or reject join requests, kick members
  member           - belongs to the org
  anyone signed in - apply to join, follow
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Users, Organization, OrgMember, OrgJoinRequest, OrgFollower, Teams, AdminAction,
)

SESSION_TIMEOUT_MINUTES = 120
MANAGE_ROLES = {'owner', 'admin', 'manager'}


def _error(message, code, http_status, extra=None):
    return Response(
        {'status': 'error', 'data': extra or {}, 'message': message, 'code': code},
        status=http_status,
    )


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message}, status=status.HTTP_200_OK)


def _authenticate(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


def _optional_user(request):
    user, err = _authenticate(request)
    return None if err else user


def _abs(request, filefield):
    if not filefield:
        return None
    try:
        return request.build_absolute_uri(filefield.url)
    except ValueError:
        return None


def _role_of(org, user):
    if user is None:
        return None
    if org.org_owner_id == user.user_id:
        return 'owner'
    membership = OrgMember.objects.filter(org=org, user=user).first()
    return membership.role if membership else None


def _tournament_stats(org):
    """Real numbers from the tournaments this org has run."""
    from vent_tournament.models import Tournament, TournamentPrizeDistribution

    tournaments = Tournament.objects.filter(tournament_organization=org, is_draft=False)
    prize_total = 0
    for t in tournaments:
        prize_total += sum(
            int(p.prize) for p in TournamentPrizeDistribution.objects.filter(tournament=t)
        )
    return tournaments, prize_total


def serialize_org(request, org, viewer=None, detail=False):
    tournaments, prize_total = _tournament_stats(org)
    member_count = OrgMember.objects.filter(org=org).count()
    data = {
        'id': org.org_id,
        'org_id': org.org_id,
        'slug': org.slug,
        'name': org.org_name,
        'tag': org.tag or None,
        'bio': org.bio,
        'focus': org.focus or None,
        'location': org.location or None,
        'region': org.region or None,
        'logo': _abs(request, org.logo),
        'banner': _abs(request, org.banner),
        'verified': org.verified,
        'member_count': member_count,
        'team_count': org.teams.count(),
        'tournaments_hosted': tournaments.count(),
        'total_tournaments_hosted': tournaments.count(),
        'prize_pool_awarded_vc': prize_total,
        'total_prize_pool': prize_total,
        'events_hosted': 0,          # events are not org-owned yet
        'owner': org.org_owner.username if org.org_owner else None,
        'follower_count': org.followers.count(),
        # The listing cards need these too: without them a member sees "Join".
        'my_role': _role_of(org, viewer),
        'has_pending_request': bool(
            viewer and OrgJoinRequest.objects.filter(org=org, user=viewer, status='pending').exists()
        ),
    }
    if detail:
        data.update({
            'mission': org.mission,
            'contact_email': org.contact_email or None,
            'founded': org.founded,
            'social_links': org.social_links or {},
            'verification_requested': org.verification_requested,
            'founders': [org.org_creator.username] if org.org_creator else [],
            'is_following': bool(viewer and OrgFollower.objects.filter(org=org, user=viewer).exists()),
            'pending_request_count': OrgJoinRequest.objects.filter(org=org, status='pending').count(),
        })
    return data


def serialize_member(request, m):
    profile = getattr(m.user, 'userprofile_set', None)
    avatar = None
    try:
        pic = m.user.userprofile_set.first()
        avatar = _abs(request, pic.profile_picture) if pic else None
    except Exception:
        avatar = None
    return {
        'id': m.id,
        'user_id': m.user_id,
        'username': m.user.username,
        'full_name': m.user.full_name,
        'role': m.role,
        'joined_at': m.joined_at,
        # The org UI renders member.user.{id,username,full_name}
        'user': {
            'id': m.user_id,
            'user_id': m.user_id,
            'username': m.user.username,
            'full_name': m.user.full_name,
            'avatar': avatar,
        },
    }


# ---------------------------------------------------------------------------
# GET /organization/list/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def org_list(request):
    viewer = _optional_user(request)
    qs = Organization.objects.all().prefetch_related('teams', 'followers')

    search = (request.GET.get('search') or request.GET.get('q') or '').strip()
    if search:
        qs = qs.filter(Q(org_name__icontains=search) | Q(tag__icontains=search) | Q(bio__icontains=search))
    region = (request.GET.get('region') or '').strip()
    if region and region.lower() != 'all':
        qs = qs.filter(region__iexact=region)
    if (request.GET.get('verified') or '').lower() in {'1', 'true', 'yes'}:
        qs = qs.filter(verified=True)

    orgs = [serialize_org(request, o, viewer) for o in qs]
    return _ok({'organizations': orgs, 'count': len(orgs)}, 'Organizations retrieved.')


# ---------------------------------------------------------------------------
# POST /organization/create/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def org_create(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    name = (request.data.get('name') or request.data.get('org_name') or '').strip()
    if not name:
        return _error('An organization name is required.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if Organization.objects.filter(org_name__iexact=name).exists():
        return _error('An organization with that name already exists.',
                      'DUPLICATE', status.HTTP_409_CONFLICT)

    org = Organization.objects.create(
        org_name=name[:148],
        org_creator=user,
        org_owner=user,
        tag=(request.data.get('tag') or '').strip()[:12],
        bio=(request.data.get('bio') or '').strip()[:280],
        mission=(request.data.get('mission') or '').strip(),
        focus=(request.data.get('focus') or '').strip()[:120],
        location=(request.data.get('location') or '').strip()[:120],
        region=(request.data.get('region') or '').strip()[:60],
        contact_email=(request.data.get('contact_email') or '').strip(),
        social_links=request.data.get('social_links') or {},
    )
    OrgMember.objects.create(org=org, user=user, role='owner')

    return Response(
        {'status': 'success', 'data': {'organization': serialize_org(request, org, user, detail=True)},
         'message': f'{org.org_name} created.'},
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# GET /organization/<id>/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def org_detail(request, org_id):
    from vent_auth.slugs import resolve_or_redirect

    org, moved_to = resolve_or_redirect(
        org_id, entity_type='organization', id_field='org_id', model=Organization,
    )
    if moved_to:
        return Response({
            'status': 'moved', 'code': 'SLUG_CHANGED',
            'message': 'This organization has been renamed.',
            'data': {'slug': moved_to, 'url': f'/organizations/{moved_to}'},
        }, status=status.HTTP_200_OK)
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    viewer = _optional_user(request)
    return _ok({'organization': serialize_org(request, org, viewer, detail=True)},
               'Organization retrieved.')


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

@api_view(['GET'])
def org_members(request, org_id):
    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    members = OrgMember.objects.filter(org=org).select_related('user')
    return _ok(
        {'members': [serialize_member(request, m) for m in members], 'count': members.count()},
        'Members retrieved.',
    )


@api_view(['POST'])
def org_promote(request, org_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) != 'owner':
        return _error('Only the organization owner can change roles.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    role = (request.data.get('role') or '').strip()
    if role not in {'admin', 'manager', 'member'}:
        return _error('Role must be admin, manager or member.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    member = OrgMember.objects.filter(
        org=org, user_id=request.data.get('user_id')
    ).select_related('user').first()
    if member is None:
        return _error('That person is not a member.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if member.role == 'owner':
        return _error('The owner\'s role cannot be changed here.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    member.role = role
    member.save(update_fields=['role'])
    return _ok({'member': serialize_member(request, member)}, f'@{member.user.username} is now {role}.')


@api_view(['POST'])
def org_kick(request, org_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) not in MANAGE_ROLES:
        return _error('You do not manage this organization.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    member = OrgMember.objects.filter(org=org, user_id=request.data.get('user_id')).select_related('user').first()
    if member is None:
        return _error('That person is not a member.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if member.role == 'owner':
        return _error('The owner cannot be removed.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    username = member.user.username
    member.delete()
    return _ok({'removed': username}, f'@{username} removed from {org.org_name}.')


# ---------------------------------------------------------------------------
# Join requests
# ---------------------------------------------------------------------------

@api_view(['POST'])
def org_apply(request, org_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user):
        return _error('You are already part of this organization.', 'ALREADY_MEMBER', status.HTTP_400_BAD_REQUEST)
    if OrgJoinRequest.objects.filter(org=org, user=user, status='pending').exists():
        return _error('Your application is already pending.', 'ALREADY_APPLIED', status.HTTP_409_CONFLICT)

    req = OrgJoinRequest.objects.create(
        org=org, user=user, message=(request.data.get('message') or '').strip()[:280],
    )
    try:
        from .views_notifications import create_notification
        create_notification(
            user=org.org_owner, category='system',
            title=f'@{user.username} applied to {org.org_name}',
            body=req.message, link=f'/organizations/manage?id={org.org_id}',
            metadata={'org_id': org.org_id, 'request_id': req.id},
        )
    except Exception:
        pass

    return Response(
        {'status': 'success', 'data': {'request_id': req.id}, 'message': 'Application sent.'},
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET'])
def org_requests(request, org_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) not in MANAGE_ROLES:
        return _error('You do not manage this organization.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    reqs = OrgJoinRequest.objects.filter(org=org, status='pending').select_related('user')
    rows = [
        {
            'id': r.id,
            'user_id': r.user_id,
            'username': r.user.username,
            'full_name': r.user.full_name,
            'message': r.message,
            'created_at': r.created_at,
        }
        for r in reqs
    ]
    return _ok({'requests': rows, 'count': len(rows)}, 'Requests retrieved.')


def _resolve_request(request, org_id, accept):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) not in MANAGE_ROLES:
        return _error('You do not manage this organization.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    req = OrgJoinRequest.objects.filter(
        id=request.data.get('request_id'), org=org, status='pending'
    ).select_related('user').first()
    if req is None:
        return _error('That request is no longer pending.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    req.status = 'accepted' if accept else 'rejected'
    req.resolved_at = timezone.now()
    req.save(update_fields=['status', 'resolved_at'])

    if accept:
        OrgMember.objects.get_or_create(org=org, user=req.user, defaults={'role': 'member'})

    try:
        from .views_notifications import create_notification
        create_notification(
            user=req.user, category='system',
            title=f"{org.org_name} {'accepted' if accept else 'declined'} your application",
            body='', link=f'/organizations/org-profile?id={org.org_id}',
            metadata={'org_id': org.org_id},
        )
    except Exception:
        pass

    return _ok(
        {'request_id': req.id, 'status': req.status},
        f"@{req.user.username} {'joined' if accept else 'was declined'}.",
    )


@api_view(['POST'])
def org_approve_request(request, org_id):
    return _resolve_request(request, org_id, True)


@api_view(['POST'])
def org_reject_request(request, org_id):
    return _resolve_request(request, org_id, False)


# ---------------------------------------------------------------------------
# Follow / verification / related lists
# ---------------------------------------------------------------------------

@api_view(['POST'])
def org_follow(request, org_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    existing = OrgFollower.objects.filter(org=org, user=user).first()
    if existing:
        existing.delete()
        following = False
    else:
        OrgFollower.objects.create(org=org, user=user)
        following = True

    return _ok(
        {'is_following': following, 'follower_count': org.followers.count()},
        f"{'Following' if following else 'Unfollowed'} {org.org_name}.",
    )


@api_view(['POST'])
def org_request_verification(request, org_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) != 'owner':
        return _error('Only the owner can request verification.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if org.verified:
        return _error('This organization is already verified.', 'ALREADY_VERIFIED', status.HTTP_409_CONFLICT)
    if org.verification_requested:
        return _error('Verification is already under review.', 'ALREADY_REQUESTED', status.HTTP_409_CONFLICT)

    org.verification_requested = True
    org.verification_note = (request.data.get('note') or '').strip()
    org.save(update_fields=['verification_requested', 'verification_note'])
    return _ok({'verification_requested': True}, 'Verification requested. An admin will review it.')


@api_view(['GET'])
def org_teams(request, org_id):
    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    rows = [
        {
            'id': t.team_id,
            'team_id': t.team_id,
            'name': t.team_name,
            'game': t.game.game_title if t.game else None,
            'logo': _abs(request, t.team_logo),
            'members': t.number_of_members,
        }
        for t in org.teams.select_related('game')
    ]
    return _ok({'teams': rows, 'count': len(rows)}, 'Teams retrieved.')


@api_view(['POST'])
def org_link_team(request, org_id):
    """Attach a team to this org. Owner-only, and only teams you own."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) != 'owner':
        return _error('Only the organization owner can link teams.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    team = Teams.objects.filter(team_id=request.data.get('team_id')).first()
    if team is None:
        return _error('Team not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if team.team_owner_id != user.user_id:
        return _error('You can only link a team you own.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if team.organization_id and team.organization_id != org.org_id:
        return _error(f'{team.team_name} already belongs to another organization.',
                      'STATE_CONFLICT', status.HTTP_409_CONFLICT)

    team.organization = org
    team.save(update_fields=['organization'])
    return _ok({'team_id': team.team_id, 'linked': True}, f'{team.team_name} linked.')


@api_view(['POST'])
def org_unlink_team(request, org_id):
    """Detach a team from this org. Owner-only."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if _role_of(org, user) != 'owner':
        return _error('Only the organization owner can unlink teams.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    team = Teams.objects.filter(team_id=request.data.get('team_id'), organization=org).first()
    if team is None:
        return _error('That team is not linked to this organization.',
                      'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    team.organization = None
    team.save(update_fields=['organization'])
    return _ok({'team_id': team.team_id, 'linked': False}, f'{team.team_name} unlinked.')


@api_view(['GET'])
def org_linkable_teams(request):
    """Teams the caller owns that are not attached to any organization yet."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    rows = [
        {
            'id': t.team_id,
            'team_id': t.team_id,
            'name': t.team_name,
            'game': t.game.game_title if t.game else None,
            'logo': _abs(request, t.team_logo),
            'members': t.number_of_members,
        }
        for t in Teams.objects.filter(team_owner=user, organization__isnull=True).select_related('game')
    ]
    return _ok({'teams': rows, 'count': len(rows)}, 'Linkable teams retrieved.')


@api_view(['GET'])
def org_tournaments(request, org_id):
    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    from vent_tournament.views import serialize_tournament_card, _card_lookups

    tournaments = list(
        org.tournament_set.filter(is_draft=False).select_related('tournament_game')
        if hasattr(org, 'tournament_set') else []
    )
    if not tournaments:
        from vent_tournament.models import Tournament
        tournaments = list(
            Tournament.objects.filter(tournament_organization=org, is_draft=False)
            .select_related('tournament_game')
        )
    counts, prizes = _card_lookups(tournaments)
    rows = [
        serialize_tournament_card(t, counts.get(t.tournament_id, 0), prizes.get(t.tournament_id, 0))
        for t in tournaments
    ]
    return _ok({'tournaments': rows, 'count': len(rows)}, 'Tournaments retrieved.')


@api_view(['GET'])
def org_events(request, org_id):
    if not Organization.objects.filter(org_id=org_id).exists():
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    # Events are created by a user, not an org, so there is nothing to list yet.
    return _ok({'events': [], 'count': 0}, 'Events retrieved.')


@api_view(['GET'])
def org_activity(request, org_id):
    org = Organization.objects.filter(org_id=org_id).first()
    if org is None:
        return _error('Organization not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    items = []
    for m in OrgMember.objects.filter(org=org).select_related('user').order_by('-joined_at')[:10]:
        items.append({
            'type': 'member_joined',
            'text': f'@{m.user.username} joined as {m.role}',
            'at': m.joined_at,
        })
    from vent_tournament.models import Tournament
    for t in Tournament.objects.filter(tournament_organization=org, is_draft=False)[:10]:
        items.append({
            'type': 'tournament',
            'text': f'Hosted {t.tournament_title}',
            'at': t.start_date_and_time,
        })
    items.sort(key=lambda i: i['at'] or timezone.now(), reverse=True)
    return _ok({'activity': items[:20], 'count': len(items[:20])}, 'Activity retrieved.')

"""Serialization helpers for the vent_team app.

Follows the V-ENT house style (plain functions returning dicts, see
`vent_tournament.views`). Centralizes team shaping so list / my-teams / detail
all emit the same Team object the frontend already reads.

Membership is now a SINGLE table: `vent_auth.TeamMembers` (user-based, with a
`role` field). The team owner always has a row with `role='owner'`. The old
GameAccount-based `vent_team.TeamMembers` has been deleted (teams-consolidation
Part A), so these helpers read only from `vent_auth.TeamMembers`.
"""

from vent_auth.models import (
    TeamProfile,
    TeamMembers as AuthTeamMembers,
    TeamJoinRequest,
    UserProfile,
)


def absolute_media_url(request, file_field):
    if file_field:
        try:
            url = file_field.url
        except ValueError:
            return None
        return request.build_absolute_uri(url) if request else url
    return None


def _owner_block(owner, profile_pic=None):
    return {
        'id': owner.user_id,
        'user_id': owner.user_id,
        'username': owner.username,
        'full_name': owner.full_name,
        'profile_pic': profile_pic,
    }


def serialize_team_card(request, team, profile_map=None):
    """Compact shape for listing/my-teams cards + the registration team picker.

    `profile_map` is an optional {team_id: TeamProfile} dict so a list view can
    bulk-fetch profiles instead of doing one query per row.
    """
    profile = profile_map.get(team.team_id) if profile_map else None
    logo = absolute_media_url(request, team.team_logo)
    banner = absolute_media_url(request, team.team_banner)
    game_title = team.game.game_title if team.game else None
    return {
        'id': team.team_id,
        'team_id': team.team_id,
        'slug': team.slug,
        'name': team.team_name,
        'tag': None,  # no tag column on Teams yet
        'game': game_title,
        'core_game': game_title,
        'logo': logo, 'logo_url': logo, 'team_logo': logo, 'image': logo,
        'banner': banner, 'banner_url': banner, 'team_banner': banner,
        'member_count': team.number_of_members,
        'members': team.number_of_members,  # registration picker reads `members`
        'is_accepting_members': team.allow_membership_requests,
        'open_to_join': team.allow_membership_requests,
        'region': profile.country if profile else None,
        'description': team.description,
        'penalty_points': team.penalty_points,
        'tournaments_played': profile.tournament_played if profile else 0,
        'tournaments_won': 0,  # no team win tracking yet
        'creation_date': team.creation_date,
        'owner': _owner_block(team.team_owner),
    }


def _collect_members(request, team):
    """Members of a team, from the unified `vent_auth.TeamMembers` table.

    The owner always has a `role='owner'` row; we still seed an owner entry up
    front (using the team creation_date as joined_at) and skip the owner's
    membership row so the owner is never duplicated even if the row is missing.
    """
    members = {}

    owner = team.team_owner
    members[owner.user_id] = {
        'user_id': owner.user_id,
        'username': owner.username,
        'full_name': owner.full_name,
        'role': 'owner',
        'joined_at': team.creation_date,
        'win_rate': 0,
        'profile_pic': None,
    }

    for m in AuthTeamMembers.objects.filter(team=team).select_related('user'):
        uid = m.user_id
        if uid == owner.user_id:
            continue  # owner already seeded above
        members[uid] = {
            'user_id': uid,
            'username': m.user.username,
            'full_name': m.user.full_name,
            'role': m.role,
            'joined_at': m.join_date,
            'win_rate': 0,
            'profile_pic': None,
        }

    # Bulk-attach profile pictures (one query for all members).
    pic_map = {}
    for up in UserProfile.objects.filter(user_id__in=list(members.keys())):
        pic_map[up.user_id] = absolute_media_url(request, up.profile_picture)
    for uid, block in members.items():
        block['profile_pic'] = pic_map.get(uid)

    return list(members.values())


def _viewer_state(request, team, members):
    """What this team is to whoever is looking at it.

    The detail payload carried nothing viewer-relative, so the profile page had
    to guess: it showed "Leave team" to strangers, and offered "Request to join"
    to people who had already asked.
    """
    from vent_team.views import _optional_user

    user = _optional_user(request)
    if user is None:
        return {
            'viewer_is_owner': False,
            'viewer_is_member': False,
            'viewer_request_status': 'none',
        }

    is_owner = team.team_owner_id == user.user_id
    is_member = is_owner or any(m.get('user_id') == user.user_id for m in members)

    request_status = 'none'
    if not is_member:
        latest = (
            TeamJoinRequest.objects
            .filter(team=team, user=user)
            .order_by('-created_at')
            .first()
        )
        if latest is not None:
            request_status = latest.status

    return {
        'viewer_is_owner': is_owner,
        'viewer_is_member': is_member,
        'viewer_request_status': request_status,
    }


def serialize_team_detail(request, team):
    """Full shape for the team-profile page (hero/overview/members/stats tabs)."""
    profile = TeamProfile.objects.filter(team=team).first()
    members = _collect_members(request, team)
    logo = absolute_media_url(request, team.team_logo)
    banner = absolute_media_url(request, team.team_banner)
    game_title = team.game.game_title if team.game else None
    owner = team.team_owner

    social = {}
    if profile:
        social = {
            'facebook': profile.facebook_link,
            'twitter': profile.twitter_link,
            'instagram': profile.instagram_link,
            'youtube': profile.youtube_link,
            'twitch': profile.twitch_link,
            'kick': profile.kick_link,
        }
        social = {k: v for k, v in social.items() if v}
    social['discord'] = None  # no discord column yet; FE expects the key

    owner_pic = next((m['profile_pic'] for m in members if m['user_id'] == owner.user_id), None)
    pending_count = TeamJoinRequest.objects.filter(team=team, status='pending').count()

    return {
        'id': team.team_id,
        'team_id': team.team_id,
        'name': team.team_name,
        'tag': None,
        'game': game_title,
        'core_game': game_title,
        'logo': logo, 'logo_url': logo, 'team_logo': logo, 'image': logo,
        'banner': banner, 'banner_url': banner, 'team_banner': banner,
        'is_accepting_members': team.allow_membership_requests,
        'open_to_join': team.allow_membership_requests,
        'region': profile.country if profile else None,
        'max_members': team.max_members,
        'password_protected': team.password_protected,
        'member_count': len(members),
        'bio': team.description,
        'description': team.description,
        'penalty_points': team.penalty_points,
        'creation_date': team.creation_date,
        'social_links': social,
        'owner': _owner_block(owner, owner_pic),
        'members': members,
        'tournaments': [],
        'events': [],
        'stats': {
            'win_rate': 0,
            'tournaments_won': 0,
            'wins': 0,
            'losses': 0,
            'total_prize_pool': 0,
            'rank': None,
            'win_rate_by_month': [],
            'most_played_games': [],
        },
        '_pendingRequestCount': pending_count,
        **_viewer_state(request, team, members),
    }

"""The partner API: read-only, scoped, and paginated.

Everything here answers the same envelope the rest of the platform uses, and
every endpoint names the single scope it needs. Nothing writes. A partner reading
V-ENT data cannot change V-ENT data, which keeps the blast radius of a leaked key
to "somebody read what we publish".
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import Teams, TeamMembers, Users
from vent_event.models import Event
from vent_tournament.models import (
    BracketMatch,
    Tournament,
    TournamentRegistration,
)

from .auth import requires_scope
from .models import SCOPES

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


def _page(request, queryset, serialize):
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    size = max(1, min(MAX_PAGE_SIZE, size))

    total = queryset.count()
    start = (page - 1) * size
    rows = [serialize(obj) for obj in queryset[start:start + size]]
    return {
        'results': rows,
        'page': page,
        'page_size': size,
        'total': total,
        'has_more': start + size < total,
    }


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message})


def _absolute(request, field):
    try:
        return request.build_absolute_uri(field.url) if field else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def api_index(request):
    """What this API offers, and what each scope opens. No key needed."""
    return _ok({
        'version': 'v1',
        'documentation': 'https://v-ent.co/partners/docs',
        'scopes': SCOPES,
        'endpoints': {
            'events': '/api/v1/events/',
            'event': '/api/v1/events/<id>/',
            'tournaments': '/api/v1/tournaments/',
            'tournament': '/api/v1/tournaments/<id>/',
            'participants': '/api/v1/tournaments/<id>/participants/',
            'bracket': '/api/v1/tournaments/<id>/bracket/',
            'teams': '/api/v1/teams/',
            'team': '/api/v1/teams/<id>/',
            'player': '/api/v1/players/<username>/',
            'rankings': '/api/v1/rankings/',
            'whoami': '/api/v1/whoami/',
        },
    }, 'V-ENT partner API')


@api_view(['GET'])
def whoami(request):
    """Which partner this key belongs to and what it may read."""
    from .auth import resolve_key

    key, error = resolve_key(request)
    if error is not None:
        return error
    return _ok({
        'partner': key.partner.name,
        'partner_slug': key.partner.slug,
        'key_name': key.name,
        'scopes': key.scopes,
        'rate_limit_per_minute': key.rate_limit_per_minute,
        'status': key.partner.status,
    }, 'Key is valid.')


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _event_row(request, e):
    return {
        'id': e.event_id,
        'name': e.name,
        'game': e.game.game_title if e.game_id else None,
        'type': e.event_type,
        'description': e.desc,
        'date': e.event_date,
        'start_time': e.start_time,
        'end_time': e.end_time,
        'registration_opens': e.reg_start_date,
        'registration_closes': e.reg_end_date,
        'location': e.location,
        'link': e.event_link,
        'entry_fee': str(e.entry_fee),
        'logo': _absolute(request, e.logo),
        'banner': _absolute(request, e.banner),
    }


@api_view(['GET'])
@requires_scope('events:read')
def events_list(request):
    qs = Event.objects.filter(is_active=True).select_related('game').order_by('-event_date')
    game = request.GET.get('game')
    if game:
        qs = qs.filter(game__game_title__iexact=game)
    return _ok(_page(request, qs, lambda e: _event_row(request, e)), 'Events')


@api_view(['GET'])
@requires_scope('events:read')
def event_detail(request, event_id):
    e = Event.objects.select_related('game').filter(pk=event_id, is_active=True).first()
    if e is None:
        return Response({'status': 'error', 'code': 'EVENT_NOT_FOUND', 'message': 'No such event.',
                         'data': None}, status=status.HTTP_404_NOT_FOUND)
    return _ok(_event_row(request, e), 'Event')


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

def _tournament_row(request, t):
    from vent_tournament.views import bracket_label

    return {
        'id': t.tournament_id,
        'title': t.tournament_title,
        'game': t.tournament_game.game_title if t.tournament_game_id else None,
        'format': t.bracket_type,
        'format_label': bracket_label(t.bracket_type),
        'visibility': t.tournament_visibility,
        'type': t.tournament_type,
        'starts_at': t.start_date_and_time,
        'ends_at': t.end_date_and_time,
        'entry': t.entry_fee,
        'entry_fee_vc': str(t.entry_fee_price),
        'max_participants': t.max_number_of_teams or t.player_size,
        'min_participants': t.min_number_of_teams,
        'team_size': t.team_size,
        'prize_type': t.prize_type,
        'logo': _absolute(request, t.tournament_logo),
        'banner': _absolute(request, t.tournament_banner),
    }


def _public_tournaments():
    """Drafts and private tournaments are nobody else's business."""
    return (
        Tournament.objects
        .filter(is_draft=False)
        .exclude(tournament_visibility='private')
        .select_related('tournament_game')
    )


@api_view(['GET'])
@requires_scope('tournaments:read')
def tournaments_list(request):
    qs = _public_tournaments().order_by('-start_date_and_time')
    game = request.GET.get('game')
    if game:
        qs = qs.filter(tournament_game__game_title__iexact=game)
    fmt = request.GET.get('format')
    if fmt:
        from vent_tournament.views import normalize_bracket_type
        qs = qs.filter(bracket_type=normalize_bracket_type(fmt))
    return _ok(_page(request, qs, lambda t: _tournament_row(request, t)), 'Tournaments')


@api_view(['GET'])
@requires_scope('tournaments:read')
def tournament_detail(request, tournament_id):
    t = _public_tournaments().filter(pk=tournament_id).first()
    if t is None:
        return Response({'status': 'error', 'code': 'TOURNAMENT_NOT_FOUND', 'message': 'No such tournament.',
                         'data': None}, status=status.HTTP_404_NOT_FOUND)
    row = _tournament_row(request, t)
    row['description'] = t.tournament_description
    row['rules'] = t.tournament_rules
    row['prizes'] = [
        {'position': p.position, 'prize_vc': str(p.prize), 'extras': p.extras}
        for p in t.prize_distributions.all().order_by('position')
    ]
    return _ok(row, 'Tournament')


@api_view(['GET'])
@requires_scope('tournaments:participants:read')
def tournament_participants(request, tournament_id):
    t = _public_tournaments().filter(pk=tournament_id).first()
    if t is None:
        return Response({'status': 'error', 'code': 'TOURNAMENT_NOT_FOUND', 'message': 'No such tournament.',
                         'data': None}, status=status.HTTP_404_NOT_FOUND)

    qs = (
        TournamentRegistration.objects
        .filter(tournament=t, status='confirmed')
        .select_related('user', 'team')
        .order_by('registered_at')
    )

    def row(r):
        if r.team_id:
            return {'kind': 'team', 'id': r.team_id, 'name': r.team.team_name,
                    'registered_at': r.registered_at}
        return {'kind': 'player', 'id': r.user_id,
                'username': r.user.username if r.user_id else None,
                'country': r.user.country if r.user_id else None,
                'registered_at': r.registered_at}

    return _ok(_page(request, qs, row), 'Participants')


@api_view(['GET'])
@requires_scope('tournaments:brackets:read')
def tournament_bracket(request, tournament_id):
    t = _public_tournaments().filter(pk=tournament_id).first()
    if t is None:
        return Response({'status': 'error', 'code': 'TOURNAMENT_NOT_FOUND', 'message': 'No such tournament.',
                         'data': None}, status=status.HTTP_404_NOT_FOUND)

    def name_of(reg):
        if reg is None:
            return None
        if reg.team_id:
            return reg.team.team_name
        return reg.user.username if reg.user_id else None

    matches = (
        BracketMatch.objects
        .filter(tournament=t)
        .select_related('participant_1__team', 'participant_1__user',
                        'participant_2__team', 'participant_2__user',
                        'winner__team', 'winner__user')
        .order_by('round_number', 'match_number')
    )

    rounds = {}
    for m in matches:
        rounds.setdefault(m.round_number, []).append({
            'match_number': m.match_number,
            'participant_1': name_of(m.participant_1),
            'participant_2': name_of(m.participant_2),
            'score_1': m.score_p1,
            'score_2': m.score_p2,
            'winner': name_of(m.winner),
            'status': m.status,
            'scheduled_at': m.scheduled_at,
            'completed_at': m.completed_at,
        })

    return _ok({
        'tournament': t.tournament_id,
        'format': t.bracket_type,
        'rounds': [{'round': r, 'matches': rounds[r]} for r in sorted(rounds)],
    }, 'Bracket')


# ---------------------------------------------------------------------------
# Teams and players
# ---------------------------------------------------------------------------

def _team_row(request, team, member_count=None):
    return {
        'id': team.team_id,
        'name': team.team_name,
        'game': team.game.game_title if team.game_id else None,
        'description': team.description,
        'open_to_join': team.allow_membership_requests,
        'member_count': member_count if member_count is not None else team.number_of_members,
        'max_members': getattr(team, 'max_members', None),
        'created': team.creation_date,
        'logo': _absolute(request, team.team_logo),
        'banner': _absolute(request, team.team_banner),
    }


@api_view(['GET'])
@requires_scope('teams:read')
def teams_list(request):
    qs = Teams.objects.select_related('game').order_by('team_name')
    game = request.GET.get('game')
    if game:
        qs = qs.filter(game__game_title__iexact=game)
    return _ok(_page(request, qs, lambda t: _team_row(request, t)), 'Teams')


@api_view(['GET'])
@requires_scope('teams:read')
def team_detail(request, team_id):
    team = Teams.objects.select_related('game').filter(pk=team_id).first()
    if team is None:
        return Response({'status': 'error', 'code': 'TEAM_NOT_FOUND', 'message': 'No such team.',
                         'data': None}, status=status.HTTP_404_NOT_FOUND)

    members = (
        TeamMembers.objects
        .filter(team=team)
        .select_related('user')
        .order_by('join_date')
    )
    row = _team_row(request, team, member_count=members.count())
    row['roster'] = [
        {
            'username': m.user.username,
            'is_captain': m.is_captain,
            'joined': m.join_date,
        }
        for m in members
    ]
    return _ok(row, 'Team')


@api_view(['GET'])
@requires_scope('players:read')
def player_detail(request, username):
    user = Users.objects.filter(username__iexact=username, is_active=True).first()
    if user is None:
        return Response({'status': 'error', 'code': 'PLAYER_NOT_FOUND', 'message': 'No such player.',
                         'data': None}, status=status.HTTP_404_NOT_FOUND)

    profile = getattr(user, 'userprofile', None)
    row = {
        'username': user.username,
        'full_name': user.full_name,
        'country': user.country,
        'city': user.state,
        'joined': user.date_joined,
        'is_founding_member': user.is_founding_member,
        'profile_picture': _absolute(request, profile.profile_picture) if profile else None,
        'description': profile.description if profile else None,
    }

    # Stats are their own scope: a partner may be allowed to see who somebody is
    # without being allowed to see how they have performed.
    if request.partner_key.allows('players:stats:read'):
        played = TournamentRegistration.objects.filter(user=user, status='confirmed').count()
        won = BracketMatch.objects.filter(winner__user=user).count()
        lost = (
            BracketMatch.objects
            .filter(status='completed')
            .filter(**{'participant_1__user': user})
            .exclude(winner__user=user)
            .count()
            + BracketMatch.objects
            .filter(status='completed')
            .filter(**{'participant_2__user': user})
            .exclude(winner__user=user)
            .count()
        )
        row['stats'] = {'tournaments_played': played, 'matches_won': won, 'matches_lost': lost}

    return _ok(row, 'Player')


@api_view(['GET'])
@requires_scope('rankings:read')
def rankings(request):
    """Players ordered by matches won. The same numbers the platform shows."""
    from django.db.models import Count

    rows = (
        BracketMatch.objects
        .filter(status='completed', winner__user__isnull=False)
        .values('winner__user__username', 'winner__user__country')
        .annotate(wins=Count('id'))
        .order_by('-wins')[:100]
    )
    return _ok({
        'generated_at': timezone.now(),
        'results': [
            {'rank': i + 1, 'username': r['winner__user__username'],
             'country': r['winner__user__country'], 'wins': r['wins']}
            for i, r in enumerate(rows)
        ],
    }, 'Rankings')

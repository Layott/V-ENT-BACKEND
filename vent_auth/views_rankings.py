"""Platform rankings - players, teams, organizations.

Backs `GET /ranking/` (root-mounted; the rankings page calls it with no prefix).

Numbers are derived from real bracket results, never seeded:
  wins   = completed bracket matches where the participant is the winner
  losses = completed bracket matches the participant played and did not win
  points = 10 per win + 3 per completed match played (participation)

Response shape (what src/app/rankings/RankingsView.js reads):
  { status, data: { players: [row], teams: [row], organizations: [row] } }
  row = { id, name, avatar, country, region, favorite_game, points, wins,
          losses, win_rate, rank, prev_rank, is_session_user }
"""
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import Users, UserProfile, Teams, Organization, FavoriteGames
from vent_tournament.models import BracketMatch, TournamentRegistration


WIN_POINTS = 10
PLAYED_POINTS = 3


def _session_user(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    if not token:
        return None
    return Users.objects.filter(login_session_token=token).first()


def _match_records(game=None):
    """Return {registration_id: {'wins': n, 'played': n}} from completed matches."""
    matches = BracketMatch.objects.filter(status='completed')
    if game:
        matches = matches.filter(tournament__tournament_game__game_title__iexact=game)

    records = {}
    # values_list keeps this to a single query - no model instances needed.
    for p1, p2, winner in matches.values_list('participant_1_id', 'participant_2_id', 'winner_id'):
        for reg_id in (p1, p2):
            if not reg_id:
                continue
            rec = records.setdefault(reg_id, {'wins': 0, 'played': 0})
            rec['played'] += 1
            if winner == reg_id:
                rec['wins'] += 1
    return records


def _row(entity_id, name, avatar, country, region, favorite_game, wins, played, is_me):
    losses = max(played - wins, 0)
    win_rate = round((wins / played) * 100) if played else 0
    return {
        'id': entity_id,
        'name': name,
        'avatar': avatar,
        'country': country,
        'region': region,
        'favorite_game': favorite_game,
        'points': wins * WIN_POINTS + played * PLAYED_POINTS,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'rank': None,      # filled in after sorting
        'prev_rank': None,  # no historical snapshots yet
        'is_session_user': is_me,
    }


def _rank(rows):
    rows.sort(key=lambda r: (-r['points'], -r['wins'], r['name'] or ''))
    for i, r in enumerate(rows, start=1):
        r['rank'] = i
    return rows


@api_view(['GET'])
def rankings(request):
    try:
        game = request.GET.get('game')
        if game in ('all', 'All Games', ''):
            game = None
        country = request.GET.get('country') or None
        region = request.GET.get('region')
        if region in ('global', ''):
            region = None
        search = (request.GET.get('search') or '').strip()

        me = _session_user(request)
        records = _match_records(game)

        # Map registrations → their user / team so match records can be attributed.
        regs = (
            TournamentRegistration.objects
            .filter(id__in=records.keys())
            .select_related('user', 'team')
        )
        user_stats, team_stats = {}, {}
        for reg in regs:
            rec = records.get(reg.id, {'wins': 0, 'played': 0})
            if reg.user_id:
                agg = user_stats.setdefault(reg.user_id, {'wins': 0, 'played': 0})
                agg['wins'] += rec['wins']
                agg['played'] += rec['played']
            if reg.team_id:
                agg = team_stats.setdefault(reg.team_id, {'wins': 0, 'played': 0})
                agg['wins'] += rec['wins']
                agg['played'] += rec['played']

        # ---- players ----
        user_qs = Users.objects.all()
        if country:
            user_qs = user_qs.filter(country__iexact=country)
        if search:
            user_qs = user_qs.filter(Q(username__icontains=search) | Q(full_name__icontains=search))

        profiles = {p.user_id: p for p in UserProfile.objects.filter(user__in=user_qs)}
        favorites = {}
        for fav in FavoriteGames.objects.filter(user__in=user_qs).select_related('game'):
            favorites.setdefault(fav.user_id, fav.game.game_title if fav.game else None)

        players = []
        for u in user_qs:
            stat = user_stats.get(u.user_id, {'wins': 0, 'played': 0})
            profile = profiles.get(u.user_id)
            avatar = None
            if profile and profile.profile_picture:
                avatar = request.build_absolute_uri(profile.profile_picture.url)
            players.append(_row(
                u.user_id, u.full_name or u.username, avatar, u.country,
                u.state or u.country, favorites.get(u.user_id),
                stat['wins'], stat['played'], bool(me and me.user_id == u.user_id),
            ))

        # ---- teams ----
        team_qs = Teams.objects.select_related('game')
        if search:
            team_qs = team_qs.filter(team_name__icontains=search)
        if game:
            team_qs = team_qs.filter(game__game_title__iexact=game)

        teams = []
        for t in team_qs:
            stat = team_stats.get(t.team_id, {'wins': 0, 'played': 0})
            avatar = request.build_absolute_uri(t.team_logo.url) if t.team_logo else None
            teams.append(_row(
                t.team_id, t.team_name, avatar, None, None,
                t.game.game_title if t.game else None,
                stat['wins'], stat['played'], False,
            ))

        # ---- organizations ----
        org_qs = Organization.objects.all()
        if search:
            org_qs = org_qs.filter(org_name__icontains=search)
        organizations = [
            _row(o.org_id, o.org_name, None, None, None, None, 0, 0, False)
            for o in org_qs
        ]

        return Response({
            'status': 'success',
            'data': {
                'players': _rank(players),
                'teams': _rank(teams),
                'organizations': _rank(organizations),
            },
            'message': 'Rankings retrieved',
        }, status=status.HTTP_200_OK)

    except Exception as e:  # pragma: no cover - defensive
        return Response(
            {'status': 'error', 'message': f'Failed to build rankings: {e}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
def games_list(request):
    """GET /auth/games/ - the real game catalogue.

    There was no games endpoint, so the create-tournament wizard derived its
    dropdown from whatever games existing tournaments happened to use, and the
    profile's favourite-games panel shipped a hardcoded list with invented
    gamertags. Both now read this.
    """
    from .models import Games

    games = Games.objects.all().order_by('game_title')
    data = [
        {
            'id': g.game_id,
            'game_id': g.game_id,
            'name': g.game_title,
            'game_title': g.game_title,
            'description': g.description,
            'logo': request.build_absolute_uri(g.logo.url) if g.logo else None,
        }
        for g in games
    ]
    return Response(
        {'status': 'success', 'data': {'games': data, 'count': len(data)}, 'message': 'Games retrieved'},
        status=status.HTTP_200_OK,
    )

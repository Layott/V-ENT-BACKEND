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
from . import regions
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


def _org_logo(request, org):
    """An organisation's crest, absolute.

    The organisations tab passed None for every row, so the whole leaderboard
    drew blank circles while the same organisations showed their crest on
    every other screen.
    """
    if not getattr(org, 'logo', None):
        return None
    try:
        return request.build_absolute_uri(org.logo.url)
    except ValueError:
        return None


def _row(entity_id, name, avatar, country, region, favorite_game, wins, played,
         is_me, address=None):
    """One row of a leaderboard.

    `address` is how the row is OPENED: a username for a person, a slug for a
    team or an organisation. It used to be missing entirely, so the rankings
    page fell back to the display name and sent people to `/u/Real Name`,
    which is a 404 - and for teams and organisations it sent the primary key,
    against the slug rule. A leaderboard whose rows cannot be clicked is a
    table of names.
    """
    losses = max(played - wins, 0)
    win_rate = round((wins / played) * 100) if played else 0
    return {
        'id': entity_id,
        'name': name,
        # Both spellings, because the page reads `username` for a person and
        # `slug` for everything else, and one row builder serves all three.
        'username': address,
        'slug': address,
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


def _filters(request):
    """What the screen should offer in its two dropdowns.

    Built from the countries that ACTUALLY appear on the platform, intersected
    with the countries we know. The frontend used to carry a hand-typed list of
    seven that included Lagos and Abuja - two Nigerian cities offered as
    countries - while every country outside those five was unreachable.

    A screen cannot know what is in the database, so it must not be the one
    holding this list.
    """
    present = set()
    for value in Users.objects.values_list('country', flat=True):
        if regions.is_country(value):
            present.add(str(value).strip())

    # Anything real that somebody has, plus nothing invented. If nobody on the
    # platform is in Chad, Chad is not offered, because an empty filter result
    # reads as a broken page.
    countries = sorted(present)
    offered = [r for r in regions.ORDER
               if any(regions.region_for(c) == r for c in countries)]
    return {
        'countries': countries,
        'regions': offered,
        # So the screen can say how many it is choosing between rather than
        # rendering an empty select.
        'has_locations': bool(countries),
    }


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

        # A region is a set of countries. This was read off the query string,
        # checked against 'global', and then never used in a single query, so
        # picking West Africa returned the whole world. It filters now.
        region_countries = regions.countries_in(region) if region else []
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
            if reg.squad_id:
                # A squad is assembled for one tournament and is not a club, so
                # it earns no club ranking. Its PLAYERS did play those fixtures
                # though, and before this they counted towards nothing at all:
                # not the club, because a squad is not one, and not themselves,
                # because `reg.user_id` is null. Four fixtures for Nigeria
                # showed as zero.
                #
                # They are credited with the side's record, which is what this
                # endpoint measures for a club's members too. A player's own
                # per-seat record is a different question and lives in
                # `league.player_table`.
                for person in reg.people:
                    agg = user_stats.setdefault(
                        person.user_id, {'wins': 0, 'played': 0})
                    agg['wins'] += rec['wins']
                    agg['played'] += rec['played']

        # ---- players ----
        user_qs = Users.objects.all()
        if country:
            user_qs = user_qs.filter(country__iexact=country)
        elif region_countries:
            # Only when no country is named. A country is the narrower of the
            # two and naming both means the country wins, which is what
            # somebody who picked one expects.
            user_qs = user_qs.filter(country__in=region_countries)
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
                # The REGION, derived from the country. It used to be
                # `u.state or u.country`, so somebody in Lagos had the region
                # "Lagos" and somebody with no state had the region "Nigeria".
                # A state is not a region and a country is not a region, and
                # the filter offering West Africa could never match either.
                regions.region_for(u.country), favorites.get(u.user_id),
                stat['wins'], stat['played'], bool(me and me.user_id == u.user_id),
                address=u.username,
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
                address=t.slug,
            ))

        # ---- organizations ----
        org_qs = Organization.objects.all()
        if search:
            org_qs = org_qs.filter(org_name__icontains=search)
        organizations = [
            _row(o.org_id, o.org_name, _org_logo(request, o), None, None, None,
                 0, 0, False, address=o.slug)
            for o in org_qs
        ]

        return Response({
            'status': 'success',
            'data': {
                'players': _rank(players),
                'teams': _rank(teams),
                'organizations': _rank(organizations),
                # What the two dropdowns should offer, built from the
                # countries that actually appear on the platform. Sent here
                # rather than from a second endpoint so there is one request
                # and nothing to leave uncalled.
                'filters': _filters(request),
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

    # Retired titles stay in the database because tournaments point at them, but
    # they have no business in a picker. `all=1` is for the console, which has to
    # show a retired game in order to bring it back.
    games = Games.objects.prefetch_related('series')
    if request.GET.get('all') not in ('1', 'true'):
        games = games.filter(is_active=True)
    games = games.order_by('sort_order', 'game_title')
    data = [
        {
            'id': g.game_id,
            'game_id': g.game_id,
            'name': g.game_title,
            'game_title': g.game_title,
            'description': g.description,
            'is_active': g.is_active,
            'logo': request.build_absolute_uri(g.logo.url) if g.logo else None,
            # The editions of an annual title, newest first. Empty for a game
            # that does not have editions, which is most of them.
            'series': [
                {
                    'id': sr.series_id,
                    'name': sr.name,
                    'slug': sr.slug,
                    'release_year': sr.release_year,
                    'is_active': sr.is_active,
                }
                for sr in sorted(
                    [x for x in g.series.all()
                     if x.is_active or request.GET.get('all') in ('1', 'true')],
                    key=lambda x: (x.sort_order, -(x.release_year or 0), x.name))
            ],
        }
        for g in games
    ]
    return Response(
        {'status': 'success', 'data': {'games': data, 'count': len(data)}, 'message': 'Games retrieved'},
        status=status.HTTP_200_OK,
    )

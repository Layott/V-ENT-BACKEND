"""Where a player stands, worked out once.

`views_rankings` computed this inline: it read completed bracket matches,
attributed them to the person or the club that played them, and sorted on
points. Nothing else could ask the same question without writing the same loop,
and a second copy of a leaderboard is a second answer to "am I in the top ten".

It moved here when an entry requirement needed to ask it. The view now calls
these functions rather than holding its own, so the rank a requirement enforces
is the rank the rankings page shows. That is the "one model per thing" rule
applied to a number rather than to a table.

The arithmetic is unchanged and deliberately simple: ten points for a win, three
for turning up. Participation counts because a ladder that only rewards winning
tells a new player they are ranked last for ever.
"""
from .models import FavoriteGames, Teams, Users, UserProfile   # noqa: F401
from . import regions

WIN_POINTS = 10
PLAYED_POINTS = 3


def match_records(game=None):
    """{registration_id: {'wins': n, 'played': n}} from completed matches."""
    from vent_tournament.models import BracketMatch

    matches = BracketMatch.objects.filter(status='completed')
    if game:
        matches = matches.filter(
            tournament__tournament_game__game_title__iexact=game)

    records = {}
    # values_list keeps this to a single query: no model instances needed.
    for p1, p2, winner in matches.values_list(
            'participant_1_id', 'participant_2_id', 'winner_id'):
        for reg_id in (p1, p2):
            if not reg_id:
                continue
            rec = records.setdefault(reg_id, {'wins': 0, 'played': 0})
            rec['played'] += 1
            if winner == reg_id:
                rec['wins'] += 1
    return records


def records_by_entity(game=None):
    """(user_stats, team_stats), keyed by user_id and team_id.

    A squad is assembled for one tournament and is not a club, so it earns no
    club ranking. Its PLAYERS did play those fixtures, and before this they
    counted towards nothing at all: not the club, because a squad is not one,
    and not themselves, because the registration carries no user. Four fixtures
    for Nigeria showed as zero.
    """
    from vent_tournament.models import TournamentRegistration

    records = match_records(game)
    regs = (TournamentRegistration.objects
            .filter(id__in=records.keys())
            .select_related('user', 'team'))

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
            for person in reg.people:
                agg = user_stats.setdefault(
                    person.user_id, {'wins': 0, 'played': 0})
                agg['wins'] += rec['wins']
                agg['played'] += rec['played']
    return user_stats, team_stats


def points_for(stat):
    return stat['wins'] * WIN_POINTS + stat['played'] * PLAYED_POINTS


def sort_key(row):
    """The one ordering. Points, then wins, then the name, so it is stable."""
    return (-row['points'], -row['wins'], row['name'] or '')


def player_standings(game=None, region=None):
    """Every player in scope, in rank order, as {user_id, name, points, rank}.

    `region` is a set of countries, not a state and not a country: somebody in
    Lagos is in West Africa, and the rankings page learned that the hard way.
    """
    user_stats, _ = records_by_entity(game)

    people = Users.objects.all().only('user_id', 'username', 'full_name', 'country')
    countries = regions.countries_in(region) if region else []
    if countries:
        people = people.filter(country__in=countries)

    rows = []
    for user in people:
        stat = user_stats.get(user.user_id, {'wins': 0, 'played': 0})
        rows.append({
            'user_id': user.user_id,
            'name': user.full_name or user.username,
            'wins': stat['wins'],
            'played': stat['played'],
            'points': points_for(stat),
        })
    rows.sort(key=sort_key)
    for index, row in enumerate(rows, start=1):
        row['rank'] = index
    return rows


def position_for(user, *, game=None, region=None):
    """(rank, total) for one player in that scope, or (None, total).

    None means UNRANKED, and it covers two cases that behave the same way:

      * they are not in the scope at all. Somebody in Ghana is not ranked 400th
        in West Africa when the tournament asks about Nigeria; they are not in
        that list.
      * they have not completed a match in this scope. The leaderboard shows
        them with zero points and a position, because a table has to put
        everybody somewhere, but a position earned by nothing is not a rank. On
        a platform of eight players it would otherwise make a brand new account
        "in the top ten", which is the exact thing a top-ten condition exists
        to prevent.

    The direction matters both ways: a newcomers' cup asking for players
    OUTSIDE the top ten should admit somebody who has never played, and that
    falls out of the same answer.
    """
    if user is None:
        return None, 0
    rows = player_standings(game=game, region=region)
    for row in rows:
        if row['user_id'] == getattr(user, 'user_id', None):
            return (row['rank'] if row['played'] else None), len(rows)
    return None, len(rows)

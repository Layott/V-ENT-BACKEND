"""Who played best, and the arithmetic that says so.

PRD: "MVP and Performance-Based Indicators: Data on who was selected as MVP and
based on which metrics."

The score is one line: sum of value times weight, over the metrics this
tournament chose to count. Everything else here exists so the answer to "why
him" is a row of numbers rather than an opinion.

Two decisions worth stating.

**Metrics the tournament does not count are ignored, not zeroed.** A stat row
left over from a metric the organiser removed is data about a question nobody
is asking any more. Including it at weight zero and including it not at all
give the same total, but only one of them survives the organiser putting the
metric back.

**Ties break on the organiser's own order.** Two players level on total are
separated by the first metric in the tournament's list, then the second, and so
on. That is the same rule the league table uses, deliberately: two orderings
that behave differently are two orderings somebody has to hold in their head.
It stops where arithmetic stops, and the last tie is left tied rather than
settled by a database id, because a database id is not a reason.
"""
from collections import defaultdict

from .. import metrics as catalogue
from ..models import MatchPlayerStat, TournamentMetric


def counted_metrics(tournament):
    """The tournament's metrics, in the organiser's order.

    Falls back to the game's defaults when the organiser has not chosen. An
    empty table is not the same as "count nothing": nobody sets up a tournament
    intending its MVP screen to be blank, and a default they can edit is more
    use than a blank they have to discover.
    """
    rows = list(TournamentMetric.objects.filter(tournament=tournament))
    if rows:
        return [(r.key, r.weight) for r in rows if catalogue.get(r.key)]

    title = (tournament.tournament_game.game_title
             if tournament.tournament_game_id else '')
    out = []
    for key in catalogue.defaults_for_game(title):
        definition = catalogue.get(key)
        if definition:
            out.append((key, definition.default_weight))
    return out


def table(tournament):
    """Every player who has a recorded stat, ranked. Highest score first.

    Returns one row per player: the score, the per-metric totals behind it, and
    the position. The per-metric totals are the point - a score with no
    breakdown is the same unarguable number the PRD is trying to replace.
    """
    counted = counted_metrics(tournament)
    if not counted:
        return []
    weights = dict(counted)
    order = [key for key, _weight in counted]

    rows = (MatchPlayerStat.objects
            .filter(match__tournament=tournament, key__in=weights)
            .select_related('player', 'registration__team',
                            'registration__user'))

    totals = defaultdict(lambda: defaultdict(float))
    players = {}
    sides = {}
    matches = defaultdict(set)
    for stat in rows:
        totals[stat.player_id][stat.key] += stat.value
        players[stat.player_id] = stat.player
        matches[stat.player_id].add(stat.match_id)
        if stat.registration_id and stat.player_id not in sides:
            sides[stat.player_id] = stat.registration

    def side_name(registration):
        if registration is None:
            return ''
        if registration.team_id:
            return registration.team.team_name
        if registration.user_id:
            return registration.user.full_name or registration.user.username
        return ''

    out = []
    for player_id, per_metric in totals.items():
        score = sum(per_metric.get(key, 0.0) * weights[key] for key in order)
        player = players[player_id]
        out.append({
            'player_id': player_id,
            'username': player.username,
            'full_name': player.full_name or '',
            'side': side_name(sides.get(player_id)),
            'matches': len(matches[player_id]),
            # Rounded at the edge rather than during, so a long tournament does
            # not accumulate a visible drift from float addition.
            'score': round(score, 3),
            'metrics': {key: round(per_metric.get(key, 0.0), 3) for key in order},
        })

    # Score first, then the organiser's metrics in their order. Never an id:
    # a database id is not a reason, and the last tie is left tied.
    out.sort(key=lambda row: (
        -row['score'],
        *(-row['metrics'].get(key, 0.0) for key in order),
    ))

    position = 0
    previous = None
    for index, row in enumerate(out, start=1):
        shape = (row['score'], tuple(row['metrics'].get(k, 0.0) for k in order))
        if shape != previous:
            position = index
            previous = shape
        # Two players who are level on everything counted share a position.
        # Printing 3rd and 4th for an unbroken tie asserts a difference the
        # arithmetic did not find.
        row['position'] = position

    return out


def leader(tournament):
    """The top row, or None. Nobody wins on no data."""
    rows = table(tournament)
    return rows[0] if rows else None

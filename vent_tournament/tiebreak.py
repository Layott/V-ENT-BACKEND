"""Separating two participants on the same points, and saying which rule did it.

Two things matter here and they are equally important.

The first is that the tie-breakers are **ordered**, and the order belongs to the
format. Round robin looks at the result between the tied sides first, then goal
difference. Swiss looks at the strength of the opponents each was given first,
because that is what a Counter-Strike major does. Battle royale looks at total
kills. Applying the wrong order produces a different champion.

The second is that the standings must say **which rule separated them**. An
organiser who cannot answer "why is that team above mine" has an argument on
their hands, and "the system decided" is not an answer. So every row that was
settled by a tie-break carries the name of the rule that settled it, and the
value both sides had.
"""
from . import formats as fmt


def _head_to_head(row, others, table):
    """Wins against the other tied participants only.

    A mini-table between the people who are level, which is what "who beat whom"
    means when three sides are tied rather than two.
    """
    tied = {o.participant_id for o in others}
    return sum(1 for beaten in row.beat if beaten in tied)


def _buchholz(row, others, table):
    """The sum of the wins of everybody this participant faced.

    Beat teams that went on to win and it rises; beat teams that lost and it
    stays low. It is a measure of the draw somebody was given rather than of
    what they did, which is exactly why it is a tie-break and not the standing.
    """
    return sum(table[o].wins for o in row.opponents if o in table)


def _most_recent(row, others, table):
    """The last result against any of the tied participants.

    Deliberately crude. It exists so a battle royale table has something after
    kills and placements rather than falling through to a coin toss.
    """
    tied = {o.participant_id for o in others}
    for opponent in reversed(row.opponents):
        if opponent in tied:
            return 1 if opponent in row.beat else 0
    return 0


VALUES = {
    'head_to_head': _head_to_head,
    'buchholz': _buchholz,
    'most_recent': _most_recent,
    'wins': lambda row, o, t: row.wins,
    'goal_difference': lambda row, o, t: row.goal_difference,
    'goals_for': lambda row, o, t: row.goals_for,
    'aggregate_goals': lambda row, o, t: row.goals_for,
    'rounds_difference': lambda row, o, t: row.goal_difference,
    'maps_won': lambda row, o, t: row.wins,
    'total_kills': lambda row, o, t: row.kills,
    # A lower placement number is better, so it is negated to keep every
    # comparison "higher is better" and stop one rule reading backwards.
    'best_placement': lambda row, o, t: -(row.best_placement or 999),
    'placement_count': lambda row, o, t: row.firsts,
    'coin_toss': lambda row, o, t: 0,
}


def standings(table, tiebreakers, *, points_key='points'):
    """Order a table, and record what separated everybody.

    Returns a list of rows, each with `position`, the numbers, and
    `separated_by` naming the rule that broke the tie plus the value both sides
    were compared on. `separated_by` is None where the points alone were enough,
    which is most of the time and should look different from a tie-break.
    """
    rows = list(table.values())
    if not rows:
        return []

    order = [t for t in tiebreakers if t in VALUES]

    # Group by points first: everything below only ever compares within a group.
    groups = {}
    for row in rows:
        groups.setdefault(getattr(row, points_key), []).append(row)

    out = []
    position = 1
    for points in sorted(groups, reverse=True):
        tied = groups[points]

        if len(tied) == 1:
            row = tied[0].as_row()
            row.update(position=position, separated_by=None)
            out.append(row)
            position += 1
            continue

        # Values are computed against the OTHER tied participants, so
        # head-to-head means "among these", not "against the whole field".
        def key(row):
            return tuple(
                VALUES[t](row, [o for o in tied if o is not row], table)
                for t in order
            )

        ranked = sorted(tied, key=key, reverse=True)

        for index, row in enumerate(ranked):
            others = [o for o in ranked if o is not row]
            settled_by = None
            settled_value = None

            # Which rule actually did the separating: the first one where this
            # row differs from the row immediately around it.
            neighbour = ranked[index - 1] if index else (ranked[1] if len(ranked) > 1 else None)
            if neighbour is not None:
                for t in order:
                    mine = VALUES[t](row, others, table)
                    theirs = VALUES[t](neighbour, [o for o in ranked if o is not neighbour], table)
                    if mine != theirs:
                        settled_by = t
                        settled_value = mine
                        break

            data = row.as_row()
            data.update(
                position=position,
                separated_by=settled_by,
                separated_by_label=fmt.TIEBREAKERS.get(settled_by) if settled_by else None,
                separated_value=settled_value,
                # Said plainly, because this is the sentence an organiser reads
                # out when somebody asks why they finished below another team.
                tied_on_points=True,
            )
            out.append(data)
            position += 1

    return out


def for_format(format_key, table, **kwargs):
    """Order a table using the tie-breakers that format actually uses."""
    definition = fmt.get(format_key)
    order = definition.tiebreakers if definition else ('wins',)
    return standings(table, order, **kwargs)

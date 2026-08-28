"""Turning results into a table.

A scoring method takes the results a participant was involved in and returns the
numbers a standings table is built from. Each one is separate and testable,
because "how many points is that" is the question organisers and players argue
about, and the answer has to be the same every time and explainable afterwards.

Every method here is checked against a real event's published rules, and the
tests carry a worked example from one. Where two games disagree - and PUBG
Mobile and Free Fire do - both tables are here rather than one being treated as
the default.
"""
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Placement tables for battle royale
# --------------------------------------------------------------------------
# PUBG Mobile: 10 for the win, then 6, 5, 4, 3, 2, 1, 1, and nothing from 9th.
# One point a kill. A win outweighs ten kills, which is the whole design: it
# pays for surviving, not only for fighting.
PUBG_PLACEMENT = {1: 10, 2: 6, 3: 5, 4: 4, 5: 3, 6: 2, 7: 1, 8: 1}

# Free Fire: 12 down to 1 across the top ten, nothing for 11th or 12th. One
# point a kill. A flatter, longer table than PUBG's, so a mid-table finish is
# worth more and the gap between first and second is larger.
FREE_FIRE_PLACEMENT = {
    1: 12, 2: 9, 3: 8, 4: 7, 5: 6, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1,
}

PLACEMENT_TABLES = {
    'pubg_mobile': PUBG_PLACEMENT,
    'free_fire': FREE_FIRE_PLACEMENT,
}


@dataclass
class Standing:
    """One row of a table, and everything a tie-break might need to read."""

    participant_id: int
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    points: int = 0

    goals_for: int = 0
    goals_against: int = 0

    kills: int = 0
    placements: list = field(default_factory=list)   # finishing positions
    opponents: list = field(default_factory=list)    # participant ids faced
    beat: list = field(default_factory=list)         # ids this one beat

    @property
    def goal_difference(self):
        return self.goals_for - self.goals_against

    @property
    def best_placement(self):
        return min(self.placements) if self.placements else None

    @property
    def firsts(self):
        return sum(1 for p in self.placements if p == 1)

    def as_row(self):
        return {
            'participant_id': self.participant_id,
            'played': self.played,
            'wins': self.wins,
            'draws': self.draws,
            'losses': self.losses,
            'points': self.points,
            'goals_for': self.goals_for,
            'goals_against': self.goals_against,
            'goal_difference': self.goal_difference,
            'kills': self.kills,
            'best_placement': self.best_placement,
            'firsts': self.firsts,
        }


def _row(table, pid):
    if pid not in table:
        table[pid] = Standing(participant_id=pid)
    return table[pid]


# --------------------------------------------------------------------------
# The methods
# --------------------------------------------------------------------------

def match_win(results):
    """One point a win, nothing for a loss. Knockouts and Swiss.

    `results` is a list of {a, b, score_a, score_b}, where a and b are
    participant ids. A draw is possible in some games and counts for neither.
    """
    table = {}
    for r in results:
        a, b = _row(table, r['a']), _row(table, r['b'])
        sa, sb = int(r.get('score_a') or 0), int(r.get('score_b') or 0)

        for side, other, own_score, their_score in ((a, b, sa, sb), (b, a, sb, sa)):
            side.played += 1
            side.opponents.append(other.participant_id)
            side.goals_for += own_score
            side.goals_against += their_score

        if sa > sb:
            a.wins += 1; a.points += 1; a.beat.append(b.participant_id); b.losses += 1
        elif sb > sa:
            b.wins += 1; b.points += 1; b.beat.append(a.participant_id); a.losses += 1
        else:
            a.draws += 1; b.draws += 1
    return table


def points_3_1_0(results):
    """Three for a win, one for a draw. Round robin and ladders.

    The football convention, and what every league table anybody has read uses.
    """
    table = match_win(results)
    for row in table.values():
        row.points = row.wins * 3 + row.draws
    return table


def battle_royale(results, placement_table='pubg_mobile', per_kill=1):
    """Placement points plus kill points, added across every match.

    `results` is a list of {participant, placement, kills} - one entry per
    participant per match, because everybody plays at once.
    """
    points_for = PLACEMENT_TABLES.get(placement_table, PUBG_PLACEMENT)
    table = {}
    for r in results:
        row = _row(table, r['participant'])
        placement = int(r.get('placement') or 0)
        kills = int(r.get('kills') or 0)

        row.played += 1
        row.kills += kills
        if placement:
            row.placements.append(placement)
        row.points += points_for.get(placement, 0) + kills * per_kill
        if placement == 1:
            row.wins += 1
    return table


def aggregate_goals(results):
    """A tie decided on TOTAL GOALS across the per-player fixtures.

    The EA FC 2v2 league. Winning one fixture 5-0 and losing the other 1-0 wins
    the tie 5-1: it is never a count of which individual fixtures were won, and
    treating it as one is the mistake this comment exists to prevent.

    `results` is a list of {a, b, score_a, score_b} - one per FIXTURE, several
    per tie - and the aggregate falls out of summing them.
    """
    table = {}
    for r in results:
        a, b = _row(table, r['a']), _row(table, r['b'])
        sa, sb = int(r.get('score_a') or 0), int(r.get('score_b') or 0)
        for side, other, own, theirs in ((a, b, sa, sb), (b, a, sb, sa)):
            side.played += 1
            side.opponents.append(other.participant_id)
            side.goals_for += own
            side.goals_against += theirs

    # The tie is settled on the totals, so wins and points are read off the
    # aggregate rather than off the individual fixtures.
    for row in table.values():
        row.points = row.goals_for
    return table


METHODS = {
    'match_win': match_win,
    'points_3_1_0': points_3_1_0,
    'battle_royale': battle_royale,
    'aggregate_goals': aggregate_goals,
}


def score(method, results, **options):
    """Run a named method. Unknown names are a programming error, not a default."""
    fn = METHODS.get(method)
    if fn is None:
        raise ValueError('No scoring method called %r' % method)
    return fn(results, **options)

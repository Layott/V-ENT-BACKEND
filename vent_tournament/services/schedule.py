"""Round robin scheduling: every entrant against every other, exactly once.

The circle method. One entrant is held still and the rest rotate around them,
which produces a schedule where nobody plays twice in a round and every pair
meets exactly once.

An odd number of entrants gets a bye added, so five teams play five rounds of
two ties with one team resting each round. Handing the bye out by rotation
rather than always to the same seat is the point of doing it this way.
"""
from ..models import BracketMatch, TieFixture


def round_robin_pairings(entrants):
    """[[(a, b), ...] per round]. `None` in a pair means a bye that round."""
    people = list(entrants)
    if len(people) < 2:
        return []

    bye = None
    if len(people) % 2:
        people.append(bye)

    n = len(people)
    rounds = []
    # The first seat is fixed; the rest rotate. n - 1 rounds covers every pair.
    order = people[:]
    for _ in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = order[i], order[n - 1 - i]
            pairs.append((a, b))
        rounds.append(pairs)
        order = [order[0]] + [order[-1]] + order[1:-1]
    return rounds


def build_league(tournament, players_per_team=2):
    """Create every tie and its per-player fixtures. Returns the ties made.

    Idempotent by refusal rather than by overwriting: a tournament that already
    has a schedule keeps it. Regenerating silently would throw away results
    somebody has already played and confirmed.
    """
    existing = BracketMatch.objects.filter(tournament=tournament)
    if existing.exists():
        return list(existing)

    regs = list(tournament.registrations.filter(status='confirmed').order_by('seed', 'id'))
    schedule = round_robin_pairings(regs)

    made = []
    for round_index, pairs in enumerate(schedule, start=1):
        match_number = 0
        for a, b in pairs:
            match_number += 1
            if a is None or b is None:
                # A bye. Recorded so the round is complete and the resting team
                # is visible, rather than silently missing from the fixture list.
                present = a or b
                tie = BracketMatch.objects.create(
                    tournament=tournament,
                    round_number=round_index,
                    match_number=match_number,
                    participant_1=present,
                    participant_2=None,
                    status='bye',
                )
                made.append(tie)
                continue

            tie = BracketMatch.objects.create(
                tournament=tournament,
                round_number=round_index,
                match_number=match_number,
                participant_1=a,
                participant_2=b,
                status='scheduled',
            )
            for slot in range(1, players_per_team + 1):
                TieFixture.objects.create(tie=tie, slot=slot, status='scheduled')
            made.append(tie)

    return made

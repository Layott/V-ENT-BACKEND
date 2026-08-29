"""Bracket generation.

Builds the full BracketMatch tree for a tournament and wires the advancement
pointer graph (winner_to/loser_to). Byes (non-power-of-2 fields) are collapsed
immediately so round-2 slots show their advancers, per spec.

Supported bracket types: single_elimination, double_elimination, round_robin.
"""
import math
import random

from django.utils import timezone

from . import advance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_bracket_type(raw):
    """Map any stored/label variant to a canonical key."""
    if not raw:
        return 'single_elimination'
    key = str(raw).strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'single_elimination': 'single_elimination',
        'single_elim': 'single_elimination',
        'single': 'single_elimination',
        'double_elimination': 'double_elimination',
        'double_elim': 'double_elimination',
        'double': 'double_elimination',
        'round_robin': 'round_robin',
        'roundrobin': 'round_robin',
        'rr': 'round_robin',
    }
    return aliases.get(key, key)


def next_power_of_2(n):
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


def standard_seed_positions(bracket_size):
    """Seed numbers (1..bracket_size) in bracket-slot order (standard seeding).

    Ensures seed 1 and seed 2 can only meet in the final, seed 1 vs the lowest
    seed in round 1, etc.
    """
    positions = [1, 2]
    while len(positions) < bracket_size:
        length = len(positions) * 2 + 1
        expanded = []
        for p in positions:
            expanded.append(p)
            expanded.append(length - p)
        positions = expanded
    return positions


def confirmed_registrations(tournament):
    return list(
        tournament.registrations.filter(status='confirmed')
        .select_related('user', 'team')
        .order_by('registered_at', 'id')
    )


def seed_registrations(regs, strategy, manual_order=None):
    """Return registrations ordered best-seed-first per the chosen strategy."""
    strategy = strategy or 'random'
    if strategy == 'registration':
        # confirmed_registrations() already reads in registered_at order, so
        # first come really is first seeded.
        return list(regs)
    if strategy == 'manual_order':
        by_id = {r.id: r for r in regs}
        ordered = [by_id[i] for i in (manual_order or []) if i in by_id]
        # Append any confirmed reg the caller forgot, preserving determinism.
        for r in regs:
            if r not in ordered:
                ordered.append(r)
        return ordered
    if strategy == 'ranked':
        # Placeholder ranking until an ELO/points system lands (M2): existing
        # seed field first, then alphabetical by display name for determinism.
        def name(r):
            if r.user_id:
                return (r.user.username or '').lower()
            if r.team_id:
                return (r.team.team_name or '').lower()
            return ''
        return sorted(regs, key=lambda r: (r.seed if r.seed is not None else 1_000_000, name(r)))
    # random (default)
    shuffled = list(regs)
    random.shuffle(shuffled)
    return shuffled


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class BracketError(Exception):
    """Raised for precondition failures; carries a machine code."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def generate(tournament, generated_by, seed_strategy='random', manual_order=None):
    """Create the bracket for `tournament`. Must run inside transaction.atomic().

    Returns a summary dict. Raises BracketError on precondition failure.
    """
    from ..models import BracketMatch, BracketGeneration

    if tournament.bracket_matches.exists():
        raise BracketError('bracket_already_generated', 'A bracket already exists for this tournament.')

    regs = confirmed_registrations(tournament)
    n = len(regs)
    min_required = tournament.min_number_of_teams or 2
    if n < max(2, min_required):
        raise BracketError(
            'not_enough_participants',
            f'Need at least {max(2, min_required)} confirmed participants; have {n}.',
        )

    btype = normalize_bracket_type(tournament.bracket_type)
    ordered = seed_registrations(regs, seed_strategy, manual_order)

    # Freeze the seed order onto the registrations for auditing / display.
    for i, reg in enumerate(ordered, start=1):
        if reg.seed != i:
            reg.seed = i
            reg.save(update_fields=['seed'])

    with advance.suspend_advance():
        if btype == 'round_robin':
            summary = _generate_round_robin(tournament, ordered)
        elif btype == 'double_elimination':
            summary = _generate_double_elimination(tournament, ordered)
        else:
            btype = 'single_elimination'
            summary = _generate_single_elimination(tournament, ordered)

    gen = BracketGeneration.objects.create(
        tournament=tournament,
        generated_by=generated_by,
        seed_strategy=seed_strategy if seed_strategy in ('random', 'ranked', 'manual_order', 'registration') else 'random',
        seed_payload={'registration_ids': [r.id for r in ordered]},
        match_count=summary['matches_created'],
        rounds_count=summary['rounds_count'],
        notes=f'bracket_type={btype}',
    )

    tournament.status = 'live'
    tournament.save(update_fields=['status'])

    summary.update({
        'tournament_id': tournament.tournament_id,
        'bracket_type': btype,
        'bracket_generation_id': gen.id,
    })
    return summary


# ---------------------------------------------------------------------------
# Single elimination
# ---------------------------------------------------------------------------

def _seed_slots(ordered, bracket_size):
    """Place seeded registrations (padded with None byes) into bracket slots."""
    padded = list(ordered) + [None] * (bracket_size - len(ordered))
    positions = standard_seed_positions(bracket_size)
    return [padded[pos - 1] for pos in positions]


def _generate_single_elimination(tournament, ordered):
    from ..models import BracketMatch

    n = len(ordered)
    bracket_size = next_power_of_2(n)
    rounds = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)

    # Create matches per round.
    per_round = []
    for r in range(1, rounds + 1):
        count = bracket_size // (2 ** r)
        matches = [
            BracketMatch.objects.create(
                tournament=tournament, round_number=r, match_number=m + 1,
                bracket_side='winners',
            )
            for m in range(count)
        ]
        per_round.append(matches)

    # Wire winner pointers R -> R+1.
    for r in range(rounds - 1):
        for m, match in enumerate(per_round[r]):
            tgt = per_round[r + 1][m // 2]
            match.winner_to_match = tgt
            match.winner_to_slot = 1 if m % 2 == 0 else 2
            match.save(update_fields=['winner_to_match', 'winner_to_slot'])
    per_round[-1][0].is_final = True
    per_round[-1][0].save(update_fields=['is_final'])

    # Seed round 1 + resolve seed-level byes.
    round1_terminal = []
    for m, match in enumerate(per_round[0]):
        match.participant_1 = slots[2 * m]
        match.participant_2 = slots[2 * m + 1]
        present = [p for p in (match.participant_1, match.participant_2) if p]
        if len(present) == 2:
            match.save(update_fields=['participant_1', 'participant_2'])
        elif len(present) == 1:
            match.winner = present[0]
            match.status = 'bye'
            match.completed_at = timezone.now()
            match.save(update_fields=['participant_1', 'participant_2', 'winner', 'status', 'completed_at'])
            round1_terminal.append(match)
        else:
            match.status = 'bye'
            match.save(update_fields=['participant_1', 'participant_2', 'status'])
            round1_terminal.append(match)

    for match in round1_terminal:
        advance.cascade(match)

    matches_created = sum(len(mr) for mr in per_round)

    # Third-place match. The two semi-final losers play for the bronze, which
    # is how a prize table with a third position gets a third place at all.
    # Only meaningful once there is a semi-final to lose, so two rounds up.
    from ..options import clean as clean_options
    third_place = None
    if clean_options(tournament.options).get('third_place_match') and rounds >= 2:
        semis = per_round[rounds - 2]
        third_place = BracketMatch.objects.create(
            tournament=tournament,
            round_number=rounds,
            match_number=2,                 # sits beside the final
            bracket_side='winners',
        )
        for m, semi in enumerate(semis[:2]):
            semi.loser_to_match = third_place
            semi.loser_to_slot = m + 1
            semi.save(update_fields=['loser_to_match', 'loser_to_slot'])
        matches_created += 1

    return {
        'rounds_count': rounds,
        'matches_created': matches_created,
        'third_place_match_id': third_place.id if third_place else None,
        'structure_summary': [
            {'round_number': r + 1, 'match_count': len(per_round[r])}
            for r in range(rounds)
        ],
    }


# ---------------------------------------------------------------------------
# Round robin
# ---------------------------------------------------------------------------

def _seats_for(tournament):
    """How many players each side fields inside one fixture.

    1 means a fixture IS the match, which is every ordinary round robin. More
    than 1 means the fixture is a tie made of that many matches, one per seat,
    and it is decided on goals added across them.

    Read from LeagueRules because that is where the organiser sets it. A
    tournament with no LeagueRules row is a plain round robin, which is the
    right default: a format nobody configured should not silently become an
    aggregate league.
    """
    from ..models import LeagueRules

    rules = LeagueRules.objects.filter(tournament=tournament).first()
    if rules is None:
        return 1
    return max(1, int(rules.players_per_team or 1))


def _seat_players(registration, seats):
    """The people sitting in each seat for one entrant, in seat order.

    Returns a list of length `seats`, padded with None. A seat with nobody in
    it is a real state: a fixture is scheduled before both rosters are locked,
    and a forfeited seat has a score with nobody behind it.
    """
    from vent_auth.models import TeamMembers

    if registration is None:
        return [None] * seats

    if registration.user_id:
        # An individual entrant fills seat one and nothing else.
        return [registration.user] + [None] * (seats - 1)

    if not registration.team_id:
        return [None] * seats

    # Join order is the roster order until somebody sets it deliberately. It is
    # at least stable and visible, which a set iteration order is not.
    members = list(
        TeamMembers.objects.filter(team_id=registration.team_id)
        .select_related('user').order_by('-is_captain', 'join_date', 'pk')[:seats]
    )
    people = [m.user for m in members]
    return (people + [None] * seats)[:seats]


def _generate_round_robin(tournament, ordered):
    from ..models import BracketMatch, TieFixture

    seats = _seats_for(tournament)
    players = list(ordered)
    n = len(players)
    # Circle method; add a bye placeholder for odd counts.
    circle = players + ([None] if n % 2 else [])
    size = len(circle)
    rounds = size - 1
    half = size // 2

    matches_created = 0
    arr = list(circle)
    for r in range(1, rounds + 1):
        match_no = 1
        for i in range(half):
            a, b = arr[i], arr[size - 1 - i]
            if a is None or b is None:
                continue
            fixture = BracketMatch.objects.create(
                tournament=tournament, round_number=r, match_number=match_no,
                bracket_side='winners', participant_1=a, participant_2=b,
                status='scheduled',
            )

            # The matches inside the fixture, one per seat. Without these the
            # fixture is an empty shell: the standings read TieFixture rows, so
            # a league generated without them has a schedule and no way to
            # record a score against it.
            #
            # Seat N always faces seat N. That is the whole point of the slot
            # and it is why there is no fixture in which seat 1 plays seat 2.
            if seats > 1:
                left = _seat_players(a, seats)
                right = _seat_players(b, seats)
                for slot in range(1, seats + 1):
                    TieFixture.objects.create(
                        tie=fixture, slot=slot,
                        player_1=left[slot - 1], player_2=right[slot - 1],
                        status='scheduled',
                    )

            match_no += 1
            matches_created += 1
        # Rotate all but the first element.
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]

    return {
        'rounds_count': rounds,
        'matches_created': matches_created,
        'structure_summary': [{
            'total_matches': matches_created,
            'players': n,
            'seats_per_side': seats,
            # What the organiser will actually run. Ten fixtures of two seats is
            # twenty matches on the floor, and the schedule is built from that
            # number rather than from the fixture count.
            'matches_on_the_floor': matches_created * seats,
        }],
    }


# ---------------------------------------------------------------------------
# Double elimination
# ---------------------------------------------------------------------------

def _generate_double_elimination(tournament, ordered):
    """Winners + losers bracket + grand final (with reset).

    Fully correct and auto-advancing for power-of-2 fields. Non-power-of-2 fields
    are padded with byes which the walkover collapse resolves; deep losers-bracket
    seeding for byes is best-effort (flagged in the build report).
    """
    from ..models import BracketMatch

    n = len(ordered)
    bracket_size = next_power_of_2(n)
    k = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)

    def mk(round_number, match_number, side):
        return BracketMatch.objects.create(
            tournament=tournament, round_number=round_number,
            match_number=match_number, bracket_side=side,
        )

    # --- Winners bracket ---------------------------------------------------
    wb = []  # wb[r-1] = list of matches in WB round r
    for r in range(1, k + 1):
        count = bracket_size // (2 ** r)
        wb.append([mk(r, m + 1, 'winners') for m in range(count)])

    for r in range(k - 1):
        for m, match in enumerate(wb[r]):
            tgt = wb[r + 1][m // 2]
            match.winner_to_match = tgt
            match.winner_to_slot = 1 if m % 2 == 0 else 2
            match.save(update_fields=['winner_to_match', 'winner_to_slot'])

    # --- Losers bracket ----------------------------------------------------
    # LB has 2*(k-1) rounds. Minor rounds (odd index) pair LB survivors; major
    # rounds (even index) pair LB survivors against the incoming WB-round losers.
    lb = []
    if k >= 2:
        lb_round = 0
        # LB round 1: pairs of WB round-1 losers.
        lb_round += 1
        count = bracket_size // 4
        lb.append([mk(lb_round, m + 1, 'losers') for m in range(max(count, 1))] if count >= 1
                  else [mk(lb_round, 1, 'losers')])
        # Remaining LB rounds.
        wb_feeder_round = 2  # WB round whose losers drop into the next LB major round
        prev = lb[0]
        while len(prev) > 1 or wb_feeder_round <= k:
            # Major round: prev LB winners vs WB round `wb_feeder_round` losers.
            lb_round += 1
            count = len(prev)
            major = [mk(lb_round, m + 1, 'losers') for m in range(count)]
            lb.append(major)
            wb_feeder_round += 1
            prev = major
            if len(prev) == 1:
                break
            # Minor round: pair up LB survivors.
            lb_round += 1
            count = len(prev) // 2
            minor = [mk(lb_round, m + 1, 'losers') for m in range(count)]
            lb.append(minor)
            prev = minor

    # Wire LB winner pointers (survivor advances to the next LB round).
    for r in range(len(lb) - 1):
        cur, nxt = lb[r], lb[r + 1]
        for m, match in enumerate(cur):
            if len(nxt) == len(cur):
                tgt, slot = nxt[m], 1  # major round: survivor takes slot 1
            else:
                tgt, slot = nxt[m // 2], (1 if m % 2 == 0 else 2)
            match.winner_to_match = tgt
            match.winner_to_slot = slot
            match.save(update_fields=['winner_to_match', 'winner_to_slot'])

    # Wire WB losers dropping into LB.
    if lb:
        # WB round 1 losers -> LB round 1 (two per LB match).
        for m, match in enumerate(wb[0]):
            tgt = lb[0][m // 2]
            match.loser_to_match = tgt
            match.loser_to_slot = 1 if m % 2 == 0 else 2
            match.save(update_fields=['loser_to_match', 'loser_to_slot'])
        # WB round r>=2 losers -> the LB major round that consumes them (slot 2).
        major_rounds = [lb[i] for i in range(1, len(lb), 2)]  # lb indices 1,3,5.. are majors
        for idx, wb_round in enumerate(wb[1:], start=0):
            if idx >= len(major_rounds):
                break
            major = major_rounds[idx]
            for m, match in enumerate(wb_round):
                tgt = major[m] if m < len(major) else major[-1]
                match.loser_to_match = tgt
                match.loser_to_slot = 2
                match.save(update_fields=['loser_to_match', 'loser_to_slot'])

    # --- Grand final ------------------------------------------------------
    # M1 uses a single decisive grand final (WB champion vs LB champion). The
    # true double-elim "bracket reset" (a second GF when the LB player wins) is
    # deferred to M2 - flagged in the build report.
    gf = mk(k + 1, 1, 'grand_final')
    wb[-1][0].winner_to_match = gf
    wb[-1][0].winner_to_slot = 1
    wb[-1][0].save(update_fields=['winner_to_match', 'winner_to_slot'])
    if lb:
        lb[-1][0].winner_to_match = gf
        lb[-1][0].winner_to_slot = 2
        lb[-1][0].save(update_fields=['winner_to_match', 'winner_to_slot'])
    gf.is_final = True
    gf.save(update_fields=['is_final'])

    # --- Seed WB round 1 + resolve byes -----------------------------------
    round1_terminal = []
    for m, match in enumerate(wb[0]):
        match.participant_1 = slots[2 * m]
        match.participant_2 = slots[2 * m + 1]
        present = [p for p in (match.participant_1, match.participant_2) if p]
        if len(present) == 2:
            match.save(update_fields=['participant_1', 'participant_2'])
        elif len(present) == 1:
            match.winner = present[0]
            match.status = 'bye'
            match.completed_at = timezone.now()
            match.save(update_fields=['participant_1', 'participant_2', 'winner', 'status', 'completed_at'])
            round1_terminal.append(match)
        else:
            match.status = 'bye'
            match.save(update_fields=['participant_1', 'participant_2', 'status'])
            round1_terminal.append(match)

    for match in round1_terminal:
        advance.cascade(match)

    matches_created = tournament.bracket_matches.count()
    rounds_count = k + 1
    return {
        'rounds_count': rounds_count,
        'matches_created': matches_created,
        'structure_summary': [
            {'bracket': 'winners', 'rounds': k},
            {'bracket': 'losers', 'rounds': len(lb)},
            {'bracket': 'grand_final', 'matches': 2},
        ],
    }

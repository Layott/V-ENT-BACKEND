"""Bracket generation.

Builds the full BracketMatch tree for a tournament and wires the advancement
pointer graph (winner_to/loser_to). Byes (non-power-of-2 fields) are collapsed
immediately so round-2 slots show their advancers, per spec.

Supported: single and double elimination (with a third-place match and a
grand-final reset), round robin in one field or in groups and home and away,
GSL groups, and Swiss one round at a time. A tournament with stages draws
each stage through `draw_into`.
"""
import math
import random

from django.utils import timezone

from . import advance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_bracket_type(raw):
    """Map any stored/label variant to a canonical key.

    Resolved through the format catalogue, which is the only alias list. A
    value the catalogue does not know passes through unchanged so a caller can
    still see what it was handed; `generate_bracket` treats it as a knockout.
    """
    from .. import formats
    if not raw:
        return 'single_elimination'
    definition = formats.get(raw)
    if definition:
        return definition.key
    return str(raw).strip().lower().replace('-', '_').replace(' ', '_')


def decided_by_table(btype):
    """Whether this format is a table (round robin, aggregate ties, ladder).

    Those all schedule as a round robin; what differs between them is how a tie
    is scored, and that lives in the league rules and the scoring module, not
    here. The branch below used to test `== 'round_robin'` alone, so an
    aggregate league or a ladder would have been drawn as a knockout.
    """
    from .. import formats
    definition = formats.get(btype)
    return bool(definition and definition.advancement == 'table')


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


def _display_name(reg):
    # `entrant_name` covers all three kinds. Branching here by hand is how a
    # squad sorted as the empty string and drifted to the front of the draw.
    return (getattr(reg, 'entrant_name', '') or '').lower()


def _seed_by_record(regs):
    """Best first, from what has actually been played.

    "Automatic seeding based off result entry" has to mean the results, not the
    alphabet. This used to sort by an unset seed field and then by name, which
    is alphabetical order wearing the word "ranked" - and an organiser pressing
    a button labelled that would have had no way to know.

    Three sources, in order of how much they are worth:

      1. The standings, when anything has been played. A group stage feeding a
         knockout is the case this exists for: the bracket is seeded by how the
         groups actually went.
      2. The organiser's own `seed` number, where they set one.
      3. Alphabetical, which decides nothing but decides it the same way twice.

    Deterministic at every level, because a bracket that comes out differently
    on a retry is a bracket nobody can check.
    """
    regs = list(regs)
    if not regs:
        return regs

    tournament = regs[0].tournament

    table = {}
    try:
        from . import league

        for row in league.team_table(tournament):
            reg_id = row.get('registration_id')
            if reg_id is not None:
                table[reg_id] = row
    except Exception:
        # A standings failure must not stop a bracket being drawn; it just
        # means seeding falls back to what the organiser set.
        table = {}

    played = any((row.get('played') or 0) > 0 for row in table.values())

    def key(reg):
        row = table.get(reg.id) if played else None
        if row:
            # Negated so "more is better" sorts first without reversing the
            # whole key and flipping the name tiebreak with it.
            return (0,
                    -int(row.get('points') or 0),
                    -int(row.get('goal_difference') or 0),
                    -int(row.get('goals_for') or 0),
                    _display_name(reg))
        return (1,
                reg.seed if reg.seed is not None else 1_000_000,
                0, 0,
                _display_name(reg))

    return sorted(regs, key=key)


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
        return _seed_by_record(regs)
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


#: Formats this module can draw, and the one it cannot. Battle royale is a
#: points table across lobbies, not a bracket; it is refused with a code rather
#: than drawn as something else. Swiss and GSL used to fall through to single
#: elimination silently, which is the fault this list exists to prevent.
DRAWABLE = ('single_elimination', 'double_elimination', 'round_robin', 'ladder',
            'aggregate_2v2', 'swiss', 'gsl')


def generate(tournament, generated_by, seed_strategy='random', manual_order=None):
    """Create the bracket for `tournament`. Must run inside transaction.atomic().

    For a tournament that runs as one format. A tournament with stages draws
    each stage through `stage_engine.draw`, which calls `draw_into` below.

    Returns a summary dict. Raises BracketError on precondition failure.
    """
    from ..models import BracketGeneration
    from .. import stage_settings

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
    if btype not in DRAWABLE:
        raise BracketError('format_has_no_bracket',
                           'This format is scored as a table, not drawn as a bracket.')
    ordered = seed_registrations(regs, seed_strategy, manual_order)

    # Freeze the seed order onto the registrations for auditing / display.
    for i, reg in enumerate(ordered, start=1):
        if reg.seed != i:
            reg.seed = i
            reg.save(update_fields=['seed'])

    settings = stage_settings.for_tournament(tournament)
    summary = draw_into(tournament, None, btype, ordered, settings, groups=0)

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


def draw_into(tournament, stage, btype, ordered, settings, groups=0):
    """Draw `ordered` (best seed first) as `btype`, into `stage` or none.

    The single entry every generator is reached through, for a whole
    tournament and for one stage of one.
    """
    if btype not in DRAWABLE:
        raise BracketError('format_has_no_bracket',
                           'This format is scored as a table, not drawn as a bracket.')
    with advance.suspend_advance():
        if btype == 'gsl':
            return _generate_gsl(tournament, stage, ordered, settings)
        if btype == 'swiss':
            return _generate_swiss_first_round(tournament, stage, ordered, settings)
        if decided_by_table(btype):
            if groups and groups > 1:
                return _generate_groups(tournament, stage, ordered, settings, groups)
            return _generate_round_robin(tournament, ordered, stage=stage,
                                         settings=settings)
        if btype == 'double_elimination':
            return _generate_double_elimination(tournament, ordered, stage=stage,
                                                settings=settings)
        return _generate_single_elimination(tournament, ordered, stage=stage,
                                            settings=settings)


def _new_match(tournament, stage, settings, round_number, match_number, side,
               *, rounds_total=1, is_final=False, group=None, table=False, **extra):
    """Create one match with how it is played fixed on it."""
    from ..models import BracketMatch
    from .. import stage_settings

    settings = settings or {}
    best_of = stage_settings.best_of_for(settings, round_number, rounds_total,
                                         is_final=is_final)
    legs = 1 if table else int(settings.get('knockout_legs') or 1)
    return BracketMatch.objects.create(
        tournament=tournament, stage=stage, group_number=group,
        round_number=round_number, match_number=match_number,
        bracket_side=side, best_of=best_of, legs=legs,
        draw_allowed=(settings.get('draws') == 'allowed'),
        is_final=is_final, **extra,
    )


def _seat_round_one(matches, slots):
    """Put seeded slots into round-one matches and collapse the byes."""
    terminal = []
    for m, match in enumerate(matches):
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
            terminal.append(match)
        else:
            match.status = 'bye'
            match.save(update_fields=['participant_1', 'participant_2', 'status'])
            terminal.append(match)
    return terminal


# ---------------------------------------------------------------------------
# Single elimination
# ---------------------------------------------------------------------------

def _seed_slots(ordered, bracket_size):
    """Place seeded registrations (padded with None byes) into bracket slots."""
    padded = list(ordered) + [None] * (bracket_size - len(ordered))
    positions = standard_seed_positions(bracket_size)
    return [padded[pos - 1] for pos in positions]


def _generate_single_elimination(tournament, ordered, stage=None, settings=None):
    settings = settings or {}
    n = len(ordered)
    bracket_size = next_power_of_2(n)
    rounds = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)

    per_round = []
    for r in range(1, rounds + 1):
        count = bracket_size // (2 ** r)
        per_round.append([
            _new_match(tournament, stage, settings, r, m + 1, 'winners',
                       rounds_total=rounds, is_final=(r == rounds))
            for m in range(count)
        ])

    # Wire winner pointers R -> R+1.
    for r in range(rounds - 1):
        for m, match in enumerate(per_round[r]):
            tgt = per_round[r + 1][m // 2]
            match.winner_to_match = tgt
            match.winner_to_slot = 1 if m % 2 == 0 else 2
            match.save(update_fields=['winner_to_match', 'winner_to_slot'])

    matches_created = sum(len(mr) for mr in per_round)

    # Third-place match. The two semi-final losers play for the bronze, which
    # is how a prize table with a third position gets a third place at all.
    # Only meaningful once there is a semi-final to lose, so two rounds up.
    # Created BEFORE round one is seated, so a semi-final settled by a bye
    # already has somewhere to send its loser.
    third_place = None
    if settings.get('third_place') and rounds >= 2:
        semis = per_round[rounds - 2]
        third_place = _new_match(tournament, stage, settings, rounds, 2,
                                 'winners', rounds_total=rounds)
        for m, semi in enumerate(semis[:2]):
            semi.loser_to_match = third_place
            semi.loser_to_slot = m + 1
            semi.save(update_fields=['loser_to_match', 'loser_to_slot'])
        matches_created += 1

    for match in _seat_round_one(per_round[0], slots):
        advance.cascade(match)

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
# Round robin, and groups of it
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

    if registration.squad_id:
        # A squad is a side assembled for this tournament out of people from
        # several clubs. Its members sit the seats in the order the organiser
        # built it, captain first. Without this branch every seat of every
        # nation in a Rivalry Series was empty, the player table had no rows,
        # and the results desk could not say who was playing.
        members = list(
            registration.squad.members.select_related('user')
            .order_by('-is_captain', 'added_at', 'pk')[:seats])
        people = [m.user for m in members]
        return (people + [None] * seats)[:seats]

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


def round_robin_pairings(players, legs=1):
    """[(round, a, b)] for everybody against everybody, `legs` times.

    Circle method with a None bye for an odd count. The second leg repeats the
    first with home and away swapped, which is what a double round robin is.
    """
    circle = list(players) + ([None] if len(players) % 2 else [])
    size = len(circle)
    if size < 2:
        return []
    rounds = size - 1
    half = size // 2
    out = []
    arr = list(circle)
    for r in range(1, rounds + 1):
        for i in range(half):
            a, b = arr[i], arr[size - 1 - i]
            if a is None or b is None:
                continue
            # Alternate who is listed first so nobody is always "home".
            out.append((r, a, b) if r % 2 else (r, b, a))
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    if legs > 1:
        out += [(r + rounds, b, a) for (r, a, b) in list(out)]
    return out


def _generate_round_robin(tournament, ordered, stage=None, settings=None, group=None):
    from ..models import TieFixture

    settings = settings or {}
    seats = _seats_for(tournament)
    legs = int(settings.get('legs') or 1)
    pairings = round_robin_pairings(list(ordered), legs)
    rounds_total = max((r for r, _a, _b in pairings), default=0)

    match_no = {}
    matches_created = 0
    for r, a, b in pairings:
        match_no[r] = match_no.get(r, 0) + 1
        fixture = _new_match(tournament, stage, settings, r, match_no[r], 'winners',
                             rounds_total=rounds_total, group=group, table=True,
                             participant_1=a, participant_2=b, status='scheduled')
        if not settings:
            # A tournament drawn without stage settings is a table: level is a
            # result there, and always has been in principle.
            fixture.draw_allowed = True
            fixture.save(update_fields=['draw_allowed'])

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
        matches_created += 1

    return {
        'rounds_count': rounds_total,
        'matches_created': matches_created,
        'structure_summary': [{
            'total_matches': matches_created,
            'players': len(ordered),
            'seats_per_side': seats,
            # What the organiser will actually run. Ten fixtures of two seats is
            # twenty matches on the floor, and the schedule is built from that
            # number rather than from the fixture count.
            'matches_on_the_floor': matches_created * seats,
        }],
    }


def split_into_groups(ordered, groups):
    """Seeds dealt across groups the way a draw from pots does it.

    Snake order: seed 1 to group A, 2 to B ... then back from the last group,
    so each group gets one of the strongest, one of the next, and so on, and
    no group is stacked. Returns a list of lists, group 1 first.
    """
    groups = max(1, int(groups))
    out = [[] for _ in range(groups)]
    for index, reg in enumerate(ordered):
        pot, pos = divmod(index, groups)
        target = pos if pot % 2 == 0 else groups - 1 - pos
        out[target].append(reg)
    return out


def _generate_groups(tournament, stage, ordered, settings, groups):
    buckets = split_into_groups(ordered, groups)
    if any(len(b) < 2 for b in buckets):
        raise BracketError('group_too_small',
                           'Every group needs at least two entrants.')
    created = 0
    rounds = 0
    summary = []
    for number, bucket in enumerate(buckets, start=1):
        part = _generate_round_robin(tournament, bucket, stage=stage,
                                     settings=settings, group=number)
        created += part['matches_created']
        rounds = max(rounds, part['rounds_count'])
        summary.append({'group': number, 'entrants': len(bucket),
                        'matches': part['matches_created']})
    return {'rounds_count': rounds, 'matches_created': created,
            'structure_summary': summary}


# ---------------------------------------------------------------------------
# GSL groups: four per group, double elimination inside it
# ---------------------------------------------------------------------------

def _generate_gsl(tournament, stage, ordered, settings):
    """Groups of four. Opening matches 1v4 and 2v3; the winners meet for first
    place; the losers meet and the loser of that is out in fourth; the loser of
    the winners' match and the winner of the losers' match play a decider for
    second. Five matches a group, two go through.
    """
    n = len(ordered)
    if n < 8 or n % 4:
        raise BracketError('gsl_needs_groups_of_four',
                           'GSL groups need a field that divides into fours, eight or more.')
    groups = n // 4
    buckets = split_into_groups(ordered, groups)
    created = 0
    for number, four in enumerate(buckets, start=1):
        s1, s2, s3, s4 = four
        mk = lambda r, m: _new_match(tournament, stage, settings, r, m, 'winners',
                                     rounds_total=3, group=number)
        open_a, open_b = mk(1, 1), mk(1, 2)
        winners, losers = mk(2, 1), mk(2, 2)
        decider = mk(3, 1)
        open_a.participant_1, open_a.participant_2 = s1, s4
        open_b.participant_1, open_b.participant_2 = s2, s3
        for opening, slot in ((open_a, 1), (open_b, 2)):
            opening.winner_to_match, opening.winner_to_slot = winners, slot
            opening.loser_to_match, opening.loser_to_slot = losers, slot
            opening.save()
        winners.loser_to_match, winners.loser_to_slot = decider, 1
        winners.save(update_fields=['loser_to_match', 'loser_to_slot'])
        losers.winner_to_match, losers.winner_to_slot = decider, 2
        losers.save(update_fields=['winner_to_match', 'winner_to_slot'])
        created += 5
    return {'rounds_count': 3, 'matches_created': created,
            'structure_summary': [{'group': g + 1, 'matches': 5} for g in range(groups)]}


# ---------------------------------------------------------------------------
# Swiss: one round at a time, paired on record
# ---------------------------------------------------------------------------

def swiss_rounds_for(n, settings):
    """How many rounds: what the organiser set, else enough to separate the field."""
    set_rounds = int((settings or {}).get('rounds') or 0)
    if set_rounds:
        return set_rounds
    return max(1, math.ceil(math.log2(max(2, n))))


def pair_swiss(players, record, played, had_bye):
    """Pair one Swiss round.

    `players` in standing order (best first); `record[id]` = (wins, losses);
    `played` = set of frozenset({a, b}) already met; `had_bye` = ids that have
    had a bye. Returns (pairs, bye_player_or_None).

    Pairs within the same record where possible, never a rematch if any other
    pairing exists, found by backtracking from the top of the table. The bye,
    for an odd field, goes to the lowest-ranked entrant who has not had one.
    """
    pool = list(players)
    bye = None
    if len(pool) % 2:
        for candidate in reversed(pool):
            if candidate.id not in had_bye:
                bye = candidate
                break
        if bye is None:
            bye = pool[-1]
        pool.remove(bye)

    def solve(remaining):
        if not remaining:
            return []
        first = remaining[0]
        rest = remaining[1:]
        # Closest record first, then table order: that is what "paired
        # against somebody on the same record" means when records run out.
        candidates = sorted(
            rest,
            key=lambda p: (abs(record[p.id][0] - record[first.id][0])
                           + abs(record[p.id][1] - record[first.id][1]),
                           rest.index(p)))
        for other in candidates:
            if frozenset((first.id, other.id)) in played:
                continue
            tail = solve([p for p in rest if p is not other])
            if tail is not None:
                return [(first, other)] + tail
        return None

    pairs = solve(pool)
    if pairs is None:
        # Every pairing would be a rematch somewhere. Better a rematch than a
        # round that cannot be drawn; pair straight down the table.
        pairs = [(pool[i], pool[i + 1]) for i in range(0, len(pool) - 1, 2)]
    return pairs, bye


def _swiss_round(tournament, stage, settings, round_number, pairs, bye, rounds_total):
    created = 0
    for m, (a, b) in enumerate(pairs, start=1):
        _new_match(tournament, stage, settings, round_number, m, 'winners',
                   rounds_total=rounds_total, table=True,
                   participant_1=a, participant_2=b, status='scheduled')
        created += 1
    if bye is not None:
        match = _new_match(tournament, stage, settings, round_number, len(pairs) + 1,
                           'winners', rounds_total=rounds_total, table=True,
                           participant_1=bye, winner=bye, status='bye',
                           completed_at=timezone.now())
        created += 1
    return created


def _generate_swiss_first_round(tournament, stage, ordered, settings):
    """Round one pairs the top half against the bottom half (1 v n/2+1), the
    usual Swiss opening, so the strongest do not meet each other first."""
    n = len(ordered)
    if n < 4:
        raise BracketError('not_enough_participants', 'Swiss needs at least four.')
    players = list(ordered)
    bye = None
    if n % 2:
        bye = players.pop()        # the lowest seed sits out round one
    half = len(players) // 2
    pairs = [(players[i], players[i + half]) for i in range(half)]
    rounds_total = swiss_rounds_for(n, settings)
    created = _swiss_round(tournament, stage, settings, 1, pairs, bye, rounds_total)
    return {'rounds_count': rounds_total, 'matches_created': created,
            'structure_summary': [{'round_number': 1, 'match_count': created}]}


# ---------------------------------------------------------------------------
# Double elimination
# ---------------------------------------------------------------------------

def _generate_double_elimination(tournament, ordered, stage=None, settings=None):
    """Winners + losers bracket + grand final, with or without a reset.

    Fully correct and auto-advancing for power-of-2 fields. Non-power-of-2 fields
    are padded with byes which the walkover collapse resolves.
    """
    settings = settings or {}
    n = len(ordered)
    bracket_size = next_power_of_2(n)
    k = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)
    created = []

    def mk(round_number, match_number, side, is_final=False):
        match = _new_match(tournament, stage, settings, round_number, match_number,
                           side, rounds_total=k + 1, is_final=is_final)
        created.append(match)
        return match

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
        lb_round = 1
        count = bracket_size // 4
        lb.append([mk(lb_round, m + 1, 'losers') for m in range(max(count, 1))])
        wb_feeder_round = 2
        prev = lb[0]
        while len(prev) > 1 or wb_feeder_round <= k:
            lb_round += 1
            major = [mk(lb_round, m + 1, 'losers') for m in range(len(prev))]
            lb.append(major)
            wb_feeder_round += 1
            prev = major
            if len(prev) == 1:
                break
            lb_round += 1
            minor = [mk(lb_round, m + 1, 'losers') for m in range(len(prev) // 2)]
            lb.append(minor)
            prev = minor

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

    if lb:
        for m, match in enumerate(wb[0]):
            tgt = lb[0][m // 2]
            match.loser_to_match = tgt
            match.loser_to_slot = 1 if m % 2 == 0 else 2
            match.save(update_fields=['loser_to_match', 'loser_to_slot'])
        major_rounds = [lb[i] for i in range(1, len(lb), 2)]
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
    # The winners' champion has not lost; the losers' champion has, once. With
    # a reset, a win for the losers' side in the first grand final forces a
    # second, because otherwise the winners' side is out after ONE loss in a
    # format whose whole promise is two. Without a reset the first is final.
    reset = settings.get('grand_final', 'single') == 'reset'
    gf = mk(k + 1, 1, 'grand_final', is_final=not reset)
    wb[-1][0].winner_to_match = gf
    wb[-1][0].winner_to_slot = 1
    wb[-1][0].save(update_fields=['winner_to_match', 'winner_to_slot'])
    if lb:
        lb[-1][0].winner_to_match = gf
        lb[-1][0].winner_to_slot = 2
        lb[-1][0].save(update_fields=['winner_to_match', 'winner_to_slot'])
    if reset:
        gf2 = mk(k + 2, 1, 'grand_final', is_final=True)
        # Routing into the reset is decided in advance.py: it is played only
        # when the losers' champion wins the first grand final.
        gf.winner_to_match = gf2
        gf.winner_to_slot = 1
        gf.save(update_fields=['winner_to_match', 'winner_to_slot'])

    for match in _seat_round_one(wb[0], slots):
        advance.cascade(match)

    return {
        'rounds_count': k + (2 if reset else 1),
        'matches_created': len(created),
        'structure_summary': [
            {'bracket': 'winners', 'rounds': k},
            {'bracket': 'losers', 'rounds': len(lb)},
            {'bracket': 'grand_final', 'matches': 2 if reset else 1},
        ],
    }

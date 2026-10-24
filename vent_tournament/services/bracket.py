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
from .. import match_slots


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
DRAWABLE = ('single_elimination', 'double_elimination', 'round_robin', 'league', 'ladder',
            'aggregate_2v2', 'swiss', 'gsl', 'bracket_groups', 'custom_bracket', 'stepladder',
            'page_playoff', 'winner_stays_on', 'league_phase')


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


def draw_into(tournament, stage, btype, ordered, settings, groups=0, lower=None, early=False):
    """Draw `ordered` (best seed first) as `btype`, into `stage` or none.

    The single entry every generator is reached through, for a whole
    tournament and for one stage of one. `lower` are entrants a progression
    line sends into a double elimination's lower bracket (P1b).
    """
    lower = list(lower or [])
    if lower and btype != 'double_elimination':
        ordered, lower = list(ordered) + lower, []
    if btype not in DRAWABLE:
        raise BracketError('format_has_no_bracket',
                           'This format is scored as a table, not drawn as a bracket.')
    from .. import formats
    definition = formats.get(btype)
    problem = definition.count_problem(len(ordered)) if definition else None
    if problem == 'at_most':
        raise BracketError('too_many_entrants',
                           'This format holds at most %s.' % definition.max_participants)
    if problem == 'at_least':
        raise BracketError('not_enough_entrants',
                           'This format needs at least %s.' % definition.min_participants)
    with advance.suspend_advance():
        summary = _draw(tournament, stage, btype, ordered, settings, groups, lower, early)
    # The seats the generator filled (entrants, byes) get their slot rows here,
    # after any bye has moved somebody on; the links wrote their own.
    scope = (stage.matches.all() if stage is not None
             else tournament.bracket_matches.filter(stage__isnull=True))
    match_slots.complete(scope, {r.id: i for i, r in enumerate(list(ordered) + lower, start=1)
                                 if hasattr(r, 'id')})
    return summary


def _draw(tournament, stage, btype, ordered, settings, groups, lower=None, early=False):
    """The generator for `btype`, run inside `draw_into`."""
    if btype == 'stepladder':
        return _generate_stepladder(tournament, stage, ordered, settings)
    if btype == 'page_playoff':
        return _generate_page_playoff(tournament, stage, ordered, settings)
    if btype == 'winner_stays_on':
        return _generate_winner_stays_on(tournament, stage, ordered, settings)
    if btype == 'single_elimination' and (settings or {}).get('every_place'):
        return _generate_every_place(tournament, stage, ordered, settings, early=early)
    if btype == 'custom_bracket':
        from .. import bracket_schema
        return bracket_schema.compile_into(tournament, stage, ordered, settings)
    if btype in ('gsl', 'bracket_groups'):
        return _generate_bracket_groups(tournament, stage, ordered, settings, groups,
                                        gsl=btype == 'gsl')
    if btype == 'swiss':
        return _generate_swiss_first_round(tournament, stage, ordered, settings)
    if btype == 'league_phase':
        settings = settings or {}
        pairings = league_phase_pairings(list(ordered), int(settings.get('pots') or 4),
                                         int(settings.get('per_pot') or 2))
        return _generate_round_robin(tournament, ordered, stage=stage, settings=settings,
                                     pairings=pairings)
    if decided_by_table(btype):
        if groups and groups > 1:
            return _generate_groups(tournament, stage, ordered, settings, groups)
        return _generate_round_robin(tournament, ordered, stage=stage,
                                     settings=settings)
    if btype == 'double_elimination':
        return _generate_double_elimination(tournament, ordered, stage=stage,
                                            settings=settings, lower=lower, early=early)
    return _generate_single_elimination(tournament, ordered, stage=stage,
                                        settings=settings, early=early)


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


def _seat_item(match, side, item):
    """One seat of a bracket drawn early: a known entrant, a bye, or a token
    waiting for a place in a stage that feeds this one (P1d)."""
    if item is None:
        match_slots.mark_bye(match, side)
    elif isinstance(item, dict):
        match_slots.wait_for(match, side, item['line'], item['group'], item['place'])
    else:
        setattr(match, 'participant_%d' % side, item)
        match.save(update_fields=['participant_%d' % side])


def _place_tokens(matches, slots):
    """Round one of a bracket drawn early. A seat against a bye resolves now
    only when its entrant is known; a token resolves when its link sends."""
    settled = []
    for m, match in enumerate(matches):
        pair = (slots[2 * m], slots[2 * m + 1])
        for side, item in ((1, pair[0]), (2, pair[1])):
            _seat_item(match, side, item)
        waiting = any(isinstance(item, dict) for item in pair)
        known = [item for item in pair if item is not None and not isinstance(item, dict)]
        if waiting or len(known) == 2:
            continue
        match.status = 'bye'
        if known:
            match.winner = known[0]
            match.completed_at = timezone.now()
            match.save(update_fields=['status', 'winner', 'completed_at'])
        else:
            match.save(update_fields=['status'])
        settled.append(match)
    return settled


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


def _threshold(settings, bracket_size):
    """The stage's threshold, refused when the bracket is too small for it.

    A threshold keeps whole rounds, so it may be at most half the bracket:
    sixteen seats can stop at eight, five entrants (eight seats) at four.
    """
    from .. import stage_settings
    threshold = stage_settings.threshold_of(settings)
    if threshold and threshold * 2 > bracket_size:
        raise BracketError('threshold_too_big',
                           'A bracket of %s stops at %s at most.' % (bracket_size, bracket_size // 2))
    return threshold


def _generate_single_elimination(tournament, ordered, stage=None, settings=None, early=False,
                                 group=None):
    settings = settings or {}
    n = len(ordered)
    bracket_size = next_power_of_2(n)
    rounds = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)
    # A threshold stops when that many are left (P3 W1): the rounds that would
    # decide between them are not drawn, and no match is the final.
    threshold = _threshold(settings, bracket_size)
    kept = rounds - int(math.log2(threshold)) if threshold else rounds

    per_round = []
    for r in range(1, kept + 1):
        count = bracket_size // (2 ** r)
        per_round.append([
            _new_match(tournament, stage, settings, r, m + 1, 'winners',
                       rounds_total=rounds, is_final=(r == rounds), group=group)
            for m in range(count)
        ])
    rounds = kept

    # Wire winner pointers R -> R+1. A reseeded knockout has none after round
    # one: each seat waits for a rank among the survivors, match m of M
    # taking ranks m and 2M+1-m (P3 W1, `match_slots.sync_reseeded`).
    for r in range(rounds - 1):
        if settings.get('reseed'):
            size = len(per_round[r + 1])
            for m, tgt in enumerate(per_round[r + 1]):
                match_slots.wait_reseed(tgt, 1, m + 1)
                match_slots.wait_reseed(tgt, 2, 2 * size - m)
            continue
        for m, match in enumerate(per_round[r]):
            tgt = per_round[r + 1][m // 2]
            match_slots.link(match, 'winner', tgt, 1 if m % 2 == 0 else 2)

    matches_created = sum(len(mr) for mr in per_round)

    # Third-place match. The two semi-final losers play for the bronze, which
    # is how a prize table with a third position gets a third place at all.
    # Only meaningful once there is a semi-final to lose, so two rounds up.
    # Created BEFORE round one is seated, so a semi-final settled by a bye
    # already has somewhere to send its loser.
    third_place = None
    if settings.get('third_place') and rounds >= 2 and not threshold:
        semis = per_round[rounds - 2]
        third_place = _new_match(tournament, stage, settings, rounds, 2,
                                 'winners', rounds_total=rounds, group=group)
        for m, semi in enumerate(semis[:2]):
            match_slots.link(semi, 'loser', third_place, m + 1)
        matches_created += 1

    seat = _place_tokens if early else _seat_round_one
    for match in seat(per_round[0], slots):
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


def _berger(players, legs):
    """FIDE's Berger tables (C.05 Annex 1, research catalogue 3.1).

    Players are numbered 1 to n in seed order, with a bye as the highest
    number for an odd field. In round r the last player meets p = (r-1) * n/2
    mod (n-1) + 1, at home in even rounds; every other pair is p+k against
    p-k, counted round the other n-1. Six players, round 1: 1-6, 2-5, 3-4;
    round 2: 6-4, 5-3, 1-2. A double round robin reverses the last two rounds
    of the first cycle, as FIDE recommends, so nobody has the same side three
    rounds running, and the second cycle swaps every side.
    """
    table = list(players) + ([None] if len(players) % 2 else [])
    n = len(table)
    if n < 2:
        return []
    wrap = lambda x: (x - 1) % (n - 1) + 1  # noqa: E731
    rounds = []
    for r in range(1, n):
        p = ((r - 1) * (n // 2)) % (n - 1) + 1
        pairs = [(n, p) if r % 2 == 0 else (p, n)]
        pairs += [(wrap(p + k), wrap(p - k)) for k in range(1, n // 2)]
        rounds.append(pairs)
    if legs > 1 and len(rounds) >= 2:
        first = rounds[:-2] + [rounds[-1], rounds[-2]]
        second = [[(b, a) for a, b in pairs] for pairs in rounds]
        rounds = first + second
    out = []
    for r, pairs in enumerate(rounds, start=1):
        for a, b in pairs:
            left, right = table[a - 1], table[b - 1]
            if left is not None and right is not None:
                out.append((r, left, right))
    return out


def round_robin_pairings(players, legs=1, method='circle'):
    """[(round, a, b)] for everybody against everybody, `legs` times.

    Circle method with a None bye for an odd count. The second leg repeats the
    first with home and away swapped, which is what a double round robin is.
    `method='berger'` follows FIDE's published tables instead (P3 W1).
    """
    if method == 'berger':
        return _berger(players, legs)
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


def league_phase_pairings(players, pots, per_pot):
    """[(round, home, away)] for a league phase drawn from pots (P3 W3a,
    research catalogue 3.3, the shape UEFA plays from 2024).

    The field is dealt into `pots` by seed, best first, and everybody meets
    `per_pot` opponents from every pot, their own included, about half of them
    at home. Inside a pot the opponents are the next seeds round a circle;
    between two pots seed i meets seeds i to i + per_pot - 1 of the other, so
    nobody can meet anybody twice. A match goes in the first round where
    neither side already plays.
    """
    n = len(players)
    if pots < 2 or n % pots:
        raise BracketError('league_phase_pots',
                           '%d entrants cannot be dealt into %d equal pots.' % (n, pots))
    size = n // pots
    if per_pot < 1 or per_pot > size - 1 or (per_pot % 2 and size % 2):
        raise BracketError('league_phase_per_pot',
                           'A pot of %d cannot give everybody %d opponents.' % (size, per_pot))
    pot = [players[p * size:(p + 1) * size] for p in range(pots)]

    pairs = []
    seen = set()

    def add(home, away):
        key = frozenset((id(home), id(away)))
        if key not in seen:
            seen.add(key)
            pairs.append((home, away))

    for p in range(pots):
        for a in range(p + 1, pots):
            for i in range(size):
                for k in range(per_pot):
                    j = (i + k) % size
                    if (k + p + a) % 2 == 0:
                        add(pot[p][i], pot[a][j])
                    else:
                        add(pot[a][j], pot[p][i])
        for i in range(size):
            # The next seeds round the circle: home to the ones after, away
            # to the ones before, so each side is home exactly half the time.
            for d in range(1, per_pot // 2 + 1):
                add(pot[p][i], pot[p][(i + d) % size])
        if per_pot % 2:
            for i in range(size // 2):
                a, b = pot[p][i], pot[p][i + size // 2]
                add(a, b) if i % 2 == 0 else add(b, a)

    busy = {}
    out = []
    for home, away in pairs:
        r = 1
        while id(home) in busy.get(r, ()) or id(away) in busy.get(r, ()):
            r += 1
        busy.setdefault(r, set()).update((id(home), id(away)))
        out.append((r, home, away))
    out.sort(key=lambda row: row[0])
    return out


def _generate_round_robin(tournament, ordered, stage=None, settings=None, group=None,
                          pairings=None):
    from ..models import TieFixture

    settings = settings or {}
    seats = _seats_for(tournament)
    legs = int(settings.get('legs') or 1)
    if pairings is None:
        pairings = round_robin_pairings(list(ordered), legs, settings.get('pairing') or 'circle')
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
# Bracket groups, and GSL as their preset
# ---------------------------------------------------------------------------

def _generate_bracket_groups(tournament, stage, ordered, settings, groups, gsl=False):
    """Several small knockouts (P3 W1, toornament's bracket groups, research
    section 4.5): the field dealt into groups by snake, each group drawn as a
    single or double elimination of its own with the stage's threshold.

    GSL is the preset: groups of four, double elimination, two through. In a
    group of four that is the two openers (1 v 4, 2 v 3), the winners match,
    the losers match and the decider: the same five matches GSL was drawn with
    by hand before P3, the decider now in the losers bracket with the losers
    match's winner listed first. A GSL drawn the old way keeps its own
    standings reader (`stage_engine._gsl_standings`).

    With no group count of its own, the groups come from `group_size`.
    """
    settings = dict(settings or {})
    n = len(ordered)
    if gsl:
        if n < 8 or n % 4:
            raise BracketError('gsl_needs_groups_of_four',
                               'GSL groups need a field that divides into fours, eight or more.')
        groups = n // 4
        settings.update(bracket='double', threshold=2, grand_final='none')
    elif not groups or groups < 2:
        groups = max(1, math.ceil(n / int(settings.get('group_size') or 4)))
    double = settings.get('bracket', 'double') == 'double'
    buckets = split_into_groups(ordered, groups)
    if any(len(b) < (3 if double else 2) for b in buckets):
        raise BracketError('group_too_small',
                           'Every group needs at least %s entrants.' % (3 if double else 2))
    generate_one = _generate_double_elimination if double else _generate_single_elimination
    created, rounds, summary = 0, 0, []
    for number, bucket in enumerate(buckets, start=1):
        part = generate_one(tournament, bucket, stage=stage, settings=settings, group=number)
        created += part['matches_created']
        rounds = max(rounds, part['rounds_count'])
        summary.append({'group': number, 'entrants': len(bucket),
                        'matches': part['matches_created']})
    return {'rounds_count': rounds, 'matches_created': created, 'groups': groups,
            'structure_summary': summary}


# ---------------------------------------------------------------------------
# Swiss: one round at a time, paired on record
# ---------------------------------------------------------------------------

def swiss_rounds_for(n, settings):
    """How many rounds: what the organiser set, else enough to separate the field."""
    set_rounds = int((settings or {}).get('rounds') or 0)
    if set_rounds:
        return set_rounds
    return max(1, math.ceil(math.log2(max(2, n))))


SWISS_PAIRINGS = ('monrad', 'dutch', 'valve')


def _swiss_candidates(first, rest, record, method):
    """Who `first` (the best remaining) would rather meet, best choice first.

    monrad (what V-ENT has always drawn): the closest record, then table
    order, so a score group pairs 1 v 2, 3 v 4. dutch (FIDE C.04.3): in its
    score group the top player meets the player half way down, top half
    against bottom half (1 v 5, 2 v 6 in a group of eight), then the rest of
    the bottom half, then the top half; an odd player floats down. valve
    (Counter-Strike Major rulebook): in its record group the highest meets the
    lowest available. Research catalogue 4.2 and 4.4.
    """
    def closeness(p):
        return (abs(record[p.id][0] - record[first.id][0])
                + abs(record[p.id][1] - record[first.id][1]))
    if method == 'dutch':
        group = [first] + [p for p in rest if record[p.id][0] == record[first.id][0]]
        half = max(len(group) // 2, 1)       # never `first` itself
        own = group[half:] + list(reversed(group[1:half]))
        others = sorted((p for p in rest if p not in group),
                        key=lambda p: (abs(record[p.id][0] - record[first.id][0]), rest.index(p)))
        return own + others
    if method == 'valve':
        group = [p for p in rest if record[p.id] == record[first.id]]
        others = sorted((p for p in rest if p not in group),
                        key=lambda p: (closeness(p), -rest.index(p)))
        return list(reversed(group)) + others
    return sorted(rest, key=lambda p: (closeness(p), rest.index(p)))


def mcmahon_starts(ids, bands):
    """{id: starting score} for a McMahon Swiss: `ids` in seed order split
    into `bands` as even as they go, the top band on 0, the next on -1.
    Empty when `bands` is 0 or 1, which is an ordinary Swiss."""
    ids = list(ids)
    bands = int(bands or 0)
    if bands < 2 or not ids:
        return {}
    bands = min(bands, len(ids))
    return {rid: -(i * bands // len(ids)) for i, rid in enumerate(ids)}


def pair_swiss(players, record, played, had_bye, method='monrad'):
    """Pair one Swiss round.

    `players` in standing order (best first); `record[id]` = (wins, losses);
    `played` = set of frozenset({a, b}) already met; `had_bye` = ids that have
    had a bye. Returns (pairs, bye_player_or_None).

    Pairs within the same record where possible, never a rematch if any other
    pairing exists, found by backtracking from the top of the table, each
    entrant trying opponents in the order its `method` prefers (P3 W1,
    `_swiss_candidates`). The bye, for an odd field, goes to the lowest-ranked
    entrant who has not had one. The Valve rulebook's priority table for six
    team record groups is not encoded beyond its first row (1 v 6, 2 v 5,
    3 v 4), which is the highest against the lowest this draws.
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
        for other in _swiss_candidates(first, rest, record, method):
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


def _decisive(settings, record, *ids):
    """Whether a Swiss match can send one of its sides through or out: one
    of them is a win from the target or a loss from the limit (Valve: "All
    elimination and advancement matches are to be best of three")."""
    target, limit = int(settings.get('win_target') or 0), int(settings.get('loss_limit') or 0)
    for rid in ids:
        wins, losses = (record or {}).get(rid, (0, 0))
        if (target and wins == target - 1) or (limit and losses == limit - 1):
            return True
    return False


def _swiss_round(tournament, stage, settings, round_number, pairs, bye, rounds_total, record=None):
    created = 0
    decisive_best_of = int((settings or {}).get('decisive_best_of') or 0)
    for m, (a, b) in enumerate(pairs, start=1):
        match = _new_match(tournament, stage, settings, round_number, m, 'winners',
                           rounds_total=rounds_total, table=True,
                           participant_1=a, participant_2=b, status='scheduled')
        if decisive_best_of and _decisive(settings, record, a.id, b.id):
            match.best_of = decisive_best_of
            match.save(update_fields=['best_of'])
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
    starts = mcmahon_starts([p.id for p in players], (settings or {}).get('mcmahon_bands'))
    if starts:
        # McMahon: round one pairs inside each band, like any later round.
        pairs, bye = pair_swiss(players, {rid: (s, 0) for rid, s in starts.items()},
                                set(), set(), method=(settings or {}).get('pairing') or 'monrad')
        rounds_total = swiss_rounds_for(n, settings)
        created = _swiss_round(tournament, stage, settings, 1, pairs, bye, rounds_total)
        return {'rounds_count': rounds_total, 'matches_created': created,
                'structure_summary': [{'round_number': 1, 'match_count': created}]}
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

# ---------------------------------------------------------------------------
# The order losers drop into the lower bracket
# ---------------------------------------------------------------------------
# In natural order the lower bracket paired people who had just met: 3 of 6
# lower matches at 8 entrants were rematches under expected results, 31 of 62
# at 64 (docs/research/de_rematch.py). These are brackets-manager's published
# orders, replayed in docs/research/de_orders.py: item 0 is round one seeding
# and is not used here, item 1 orders the round one losers into lower round
# one, and each item after that orders the losers of the next upper round into
# the lower round they drop into. Expected results then leave 1 rematch up to
# 16 entrants and 3 from 32 up, the ones no order avoids. CEO, 16 October
# 2026, on fixing this first: "these are fine."

DROP_ORDERS = {
    4: ['natural', 'natural'],
    8: ['natural', 'reverse', 'natural'],
    16: ['natural', 'reverse_half_shift', 'reverse', 'natural'],
    32: ['natural', 'reverse', 'half_shift', 'natural', 'natural'],
    64: ['natural', 'reverse', 'half_shift', 'reverse', 'natural', 'natural'],
    128: ['natural', 'reverse', 'half_shift', 'pair_flip', 'pair_flip', 'pair_flip', 'natural'],
}


def drop_order(bracket_size):
    """The orders for a bracket of this size; past 128, one more pair flip per doubling."""
    if bracket_size in DROP_ORDERS:
        return list(DROP_ORDERS[bracket_size])
    if bracket_size < 4:
        return ['natural', 'natural']
    order = list(DROP_ORDERS[128])
    size = 128
    while size < bracket_size:
        order.insert(-1, 'pair_flip')
        size *= 2
    return order


def reorder(name, items):
    """Apply one of the named orders to a list, returning a new list."""
    items = list(items)
    half = len(items) // 2
    if name == 'reverse':
        return items[::-1]
    if name == 'half_shift':
        return items[half:] + items[:half]
    if name == 'reverse_half_shift':
        return items[:half][::-1] + items[half:][::-1]
    if name == 'pair_flip' and len(items) > 1:
        return [x for i in range(0, len(items), 2) for x in (items[i + 1], items[i])]
    return items


def _generate_double_elimination(tournament, ordered, stage=None, settings=None, lower=None,
                                 early=False, group=None):
    """Winners + losers bracket + grand final, with or without a reset.

    Fully correct and auto-advancing for power-of-2 fields. Non-power-of-2 fields
    are padded with byes which the walkover collapse resolves.

    `lower` are entrants who start in the lower bracket (a group's third place,
    a qualifier: P1b, design section 6, "qualifiers join a double elimination
    in the lower bracket"). They get a lower round of their own in front: each
    upper round one loser meets one of them, best lower entrant first, a bye
    where there are fewer of them, and the lower bracket carries on from there.
    """
    settings = settings or {}
    lower = list(lower or [])
    n = len(ordered)
    bracket_size = next_power_of_2(n)
    k = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)
    # A threshold, or no grand final (which is a threshold of two), stops
    # before the brackets meet (P3 W1, toornament's threshold and grand final
    # none): half go through from the upper bracket and half from the lower.
    threshold = _threshold(settings, bracket_size)
    created = []

    def mk(round_number, match_number, side, is_final=False):
        match = _new_match(tournament, stage, settings, round_number, match_number,
                           side, rounds_total=k + 1, is_final=is_final, group=group)
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
            match_slots.link(match, 'winner', tgt, 1 if m % 2 == 0 else 2)

    # --- Losers bracket ----------------------------------------------------
    # LB has 2*(k-1) rounds. Minor rounds (odd index) pair LB survivors; major
    # rounds (even index) pair LB survivors against the incoming WB-round losers.
    entry = []
    if lower:
        if k < 2:
            raise BracketError('lower_needs_four_upper',
                               'A lower bracket entry needs at least four in the upper bracket.')
        if len(lower) > len(wb[0]):
            raise BracketError('lower_too_many',
                               'More come in on the lower side than there are upper round one losers.')
        entry = [mk(1, m + 1, 'losers') for m in range(len(wb[0]))]

    lb = []
    if k >= 2:
        lb_round = 2 if entry else 1
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
            match_slots.link(match, 'winner', tgt, slot)

    if lb:
        orders = drop_order(bracket_size)
        if entry:
            # Upper round one losers into the entry round's first seats; the
            # entry round's winners on into the lower bracket as round one's
            # losers would have gone.
            for m, match in enumerate(reorder(orders[1], wb[0])):
                match_slots.link(match, 'loser', entry[m], 1)
            for m, match in enumerate(entry):
                match_slots.link(match, 'winner', lb[0][m // 2], 1 if m % 2 == 0 else 2)
            padded = lower + [None] * (len(entry) - len(lower))
            for match, reg in zip(entry, padded):
                if early:
                    _seat_item(match, 2, reg)
                elif reg is not None:
                    match.participant_2 = reg
                    match.save(update_fields=['participant_2'])
                else:
                    match_slots.mark_bye(match, 2)
        else:
            for m, match in enumerate(reorder(orders[1], wb[0])):
                tgt = lb[0][m // 2]
                match_slots.link(match, 'loser', tgt, 1 if m % 2 == 0 else 2)
        major_rounds = [lb[i] for i in range(1, len(lb), 2)]
        for idx, wb_round in enumerate(wb[1:], start=0):
            if idx >= len(major_rounds):
                break
            major = major_rounds[idx]
            name = orders[2 + idx] if 2 + idx < len(orders) else 'natural'
            for m, match in enumerate(reorder(name, wb_round)):
                tgt = major[m] if m < len(major) else major[-1]
                match_slots.link(match, 'loser', tgt, 2)

    # --- Grand final ------------------------------------------------------
    # The winners' champion has not lost; the losers' champion has, once. With
    # a reset, a win for the losers' side in the first grand final forces a
    # second, because otherwise the winners' side is out after ONE loss in a
    # format whose whole promise is two. Without a reset the first is final.
    reset = settings.get('grand_final', 'single') == 'reset' and not threshold
    if not threshold:
        gf = mk(k + 1, 1, 'grand_final', is_final=not reset)
        match_slots.link(wb[-1][0], 'winner', gf, 1)
        if lb:
            match_slots.link(lb[-1][0], 'winner', gf, 2)
        if reset:
            gf2 = mk(k + 2, 1, 'grand_final', is_final=True)
            # Routing into the reset is decided in advance.py: it is played only
            # when the losers' champion wins the first grand final.
            match_slots.link_reset(gf, gf2)
    else:
        # Threshold 2**j keeps upper rounds 1 .. k-j+1 (threshold / 2 left
        # there) and the lower rounds up to the one the last kept upper
        # round's losers drop into (threshold / 2 left there too). The rest
        # is drawn whole and cut before anybody is seated: what is cut only
        # ever feeds what is cut, so the pointers into it go null with it.
        last_upper = k - int(math.log2(threshold)) + 1
        cut = [m for r in wb[last_upper:] for m in r] + [m for r in lb[2 * last_upper - 2:] for m in r]
        if cut:
            from ..models import BracketMatch
            gone = {m.pk for m in cut}
            BracketMatch.objects.filter(pk__in=gone).delete()
            created = [m for m in created if m.pk not in gone]
            wb, lb = wb[:last_upper], lb[:2 * last_upper - 2]
            # Read round one again: its in-memory pointers predate the cut.
            wb[0] = list(BracketMatch.objects.filter(pk__in=[m.pk for m in wb[0]])
                         .order_by('match_number'))

    seat = _place_tokens if early else _seat_round_one
    for match in seat(wb[0], slots):
        advance.cascade(match)

    # Without a grand final the last lower round still comes after the last
    # upper round (it takes that round's losers), so it is a round of its own.
    finals = (1 if lb else 0) if threshold else (2 if reset else 1)
    return {
        'rounds_count': len(wb) + finals,
        'matches_created': len(created),
        'structure_summary': [
            {'bracket': 'winners', 'rounds': len(wb)},
            {'bracket': 'losers', 'rounds': len(lb)},
            {'bracket': 'grand_final', 'matches': finals},
        ],
    }


# ---------------------------------------------------------------------------
# Matches for every place
# ---------------------------------------------------------------------------
# CEO, 28 September 2026: "Finish up the matches for every place". A knockout
# where the losers keep playing: the four quarter-final losers play for fifth
# to eighth, their winners for fifth, their losers for seventh, and so on down,
# so every entrant finishes in a place of their own and a prize table with
# eight rows has eight names to pay. Each match states the place it settles
# (`winner_place`, `loser_place`), so final positions are read off matches.

def _generate_every_place(tournament, stage, ordered, settings, early=False):
    settings = settings or {}
    bracket_size = next_power_of_2(len(ordered))
    rounds = int(math.log2(bracket_size))
    slots = _seed_slots(ordered, bracket_size)
    numbers = {}
    created = []

    def mk(round_number, first_place, last):
        numbers[round_number] = numbers.get(round_number, 0) + 1
        title = first_place == 1
        match = _new_match(tournament, stage, settings, round_number,
                           numbers[round_number], 'winners' if title else 'placement',
                           rounds_total=rounds, is_final=title and last)
        created.append(match)
        return match

    def build(size, first_place, round_number):
        """`size` sides play for places first_place .. first_place+size-1."""
        last = size == 2
        matches = [mk(round_number, first_place, last) for _ in range(size // 2)]
        if last:
            m = matches[0]
            m.winner_place, m.loser_place = first_place, first_place + 1
            m.save(update_fields=['winner_place', 'loser_place'])
            return matches
        upper = build(size // 2, first_place, round_number + 1)
        lower = build(size // 2, first_place + size // 2, round_number + 1)
        for i, m in enumerate(matches):
            slot = 1 if i % 2 == 0 else 2
            match_slots.link(m, 'winner', upper[i // 2], slot)
            match_slots.link(m, 'loser', lower[i // 2], slot)
        return matches

    first_round = build(bracket_size, 1, 1)
    seat = _place_tokens if early else _seat_round_one
    for match in seat(first_round, slots):
        advance.cascade(match)
    return {
        'rounds_count': rounds,
        'matches_created': len(created),
        'structure_summary': [{'round_number': r, 'match_count': numbers[r]}
                              for r in sorted(numbers)],
    }


# ---------------------------------------------------------------------------
# Stepladder
# ---------------------------------------------------------------------------
# The two lowest seeds play; the winner meets the next seed up; and so on until
# the top seed, who waits in the final. Round r is the r-th rung. The loser of
# every rung finishes in a known place: the first rung's loser last.

def _generate_stepladder(tournament, stage, ordered, settings):
    settings = settings or {}
    n = len(ordered)
    rungs = n - 1
    matches = []
    for r in range(1, rungs + 1):
        m = _new_match(tournament, stage, settings, r, 1, 'winners',
                       rounds_total=rungs, is_final=(r == rungs))
        m.loser_place = n - r + 1
        if r == rungs:
            m.winner_place = 1
        # The waiting seed takes slot 1: the higher seed is the home side.
        m.participant_1 = ordered[n - 1 - r]
        if r == 1:
            m.participant_2 = ordered[n - 1]
        m.save(update_fields=['loser_place', 'winner_place', 'participant_1',
                              'participant_2'])
        matches.append(m)
    for prev, nxt in zip(matches, matches[1:]):
        match_slots.link(prev, 'winner', nxt, 2)
    return {
        'rounds_count': rungs,
        'matches_created': rungs,
        'structure_summary': [{'round_number': r, 'match_count': 1}
                              for r in range(1, rungs + 1)],
    }


# ---------------------------------------------------------------------------
# Page playoff
# ---------------------------------------------------------------------------
# Four sides, four matches. 1 v 2: the winner goes to the final, the loser gets
# a second chance. 3 v 4: the loser is out in fourth. The loser of 1 v 2 meets
# the winner of 3 v 4 for the other place in the final; its loser is third.

def _generate_page_playoff(tournament, stage, ordered, settings):
    settings = settings or {}
    s1, s2, s3, s4 = ordered[:4]
    top = _new_match(tournament, stage, settings, 1, 1, 'winners', rounds_total=3,
                     participant_1=s1, participant_2=s2)
    bottom = _new_match(tournament, stage, settings, 1, 2, 'losers', rounds_total=3,
                        participant_1=s3, participant_2=s4)
    semi = _new_match(tournament, stage, settings, 2, 1, 'losers', rounds_total=3)
    final = _new_match(tournament, stage, settings, 3, 1, 'winners', rounds_total=3,
                       is_final=True)
    match_slots.link(top, 'winner', final, 1)
    match_slots.link(top, 'loser', semi, 1)
    match_slots.link(bottom, 'winner', semi, 2)
    bottom.loser_place = 4
    bottom.save(update_fields=['loser_place'])
    match_slots.link(semi, 'winner', final, 2)
    semi.loser_place = 3
    semi.save(update_fields=['loser_place'])
    final.winner_place, final.loser_place = 1, 2
    final.save(update_fields=['winner_place', 'loser_place'])
    return {
        'rounds_count': 3,
        'matches_created': 4,
        'structure_summary': [{'round_number': 1, 'match_count': 2},
                              {'round_number': 2, 'match_count': 1},
                              {'round_number': 3, 'match_count': 1}],
    }


# ---------------------------------------------------------------------------
# Winner stays on
# ---------------------------------------------------------------------------
# Seeds 1 and 2 open; the winner stays on and meets the next in the queue.
# With no streak target it is one pass: every challenger gets one go. With a
# target, a loser goes to the back of the queue and it runs until somebody wins
# that many in a row, or the match limit is reached. One match exists at a
# time; the next is drawn when the last is decided (`next_challenge`).

def _generate_winner_stays_on(tournament, stage, ordered, settings):
    settings = settings or {}
    _new_match(tournament, stage, settings, 1, 1, 'winners', rounds_total=1,
               participant_1=ordered[0], participant_2=ordered[1])
    return {'rounds_count': 1, 'matches_created': 1,
            'structure_summary': [{'round_number': 1, 'match_count': 1}]}


def match_limit(settings, entrants):
    settings = settings or {}
    if not settings.get('streak_target'):
        return max(1, entrants - 1)
    return int(settings.get('max_matches') or 0) or min(200, 3 * entrants)


def streak_state(matches, seeds, settings):
    """Replay a winner-stays-on run: who holds the spot, their streak, who is
    queueing, everybody's wins and longest streak, and whether it is over."""
    settings = settings or {}
    target = int(settings.get('streak_target') or 0)
    queue = list(seeds[2:])
    holder, streak = None, 0
    wins = {i: 0 for i in seeds}
    losses = {i: 0 for i in seeds}
    best = {i: 0 for i in seeds}
    played = {i: 0 for i in seeds}
    current = None
    decided = 0
    for m in sorted(matches, key=lambda x: (x.round_number, x.match_number)):
        sides = [x for x in (m.participant_1_id, m.participant_2_id) if x]
        # The challenger has left the queue to play. Without this the same
        # challenger was drawn again and again (found by the streak test on
        # 28 September 2026: seed 3 was drawn against itself).
        for x in sides:
            if x != holder and x in queue:
                queue.remove(x)
        if m.status not in ('completed', 'walkover_p1', 'walkover_p2', 'bye', 'cancelled'):
            current = m
            break
        decided += 1
        if m.winner_id is None:
            continue
        loser = next((x for x in sides if x != m.winner_id), None)
        streak = streak + 1 if m.winner_id == holder else 1
        holder = m.winner_id
        for x in sides:
            played[x] = played.get(x, 0) + 1
        wins[holder] = wins.get(holder, 0) + 1
        best[holder] = max(best.get(holder, 0), streak)
        if loser is not None:
            losses[loser] = losses.get(loser, 0) + 1
            if target:
                queue.append(loser)
    limit = match_limit(settings, len(seeds))
    over = current is None and (
        (target and streak >= target) or not queue or decided >= limit)
    return {'holder': holder, 'streak': streak, 'queue': queue, 'wins': wins,
            'losses': losses, 'best_streak': best, 'played': played,
            'current': current, 'finished': bool(over), 'target': target,
            'match_limit': limit, 'decided': decided}


def next_challenge(tournament, stage, settings, seeds, matches):
    """Draw the next winner-stays-on match, if the run is not over."""
    state = streak_state(matches, seeds, settings)
    if state['current'] is not None or state['finished'] or state['holder'] is None:
        return None
    from ..models import TournamentRegistration
    regs = TournamentRegistration.objects.in_bulk([state['holder'], state['queue'][0]])
    round_number = max((m.round_number for m in matches), default=0) + 1
    with advance.suspend_advance():
        m = _new_match(tournament, stage, settings, round_number, 1, 'winners',
                       rounds_total=round_number,
                       participant_1=regs[state['holder']],
                       participant_2=regs[state['queue'][0]])
    match_slots.complete([m], {rid: i for i, rid in enumerate(seeds, start=1)})
    return m


def places_from_matches(matches):
    """Final places read off matches that state them, compacted to 1, 2, 3 ...
    None when no match states a place (a plain knockout)."""
    stated = [m for m in matches if m.winner_place or m.loser_place]
    if not stated:
        return None
    place = {}
    for m in stated:
        if m.winner_id and m.winner_place and m.status in (
                'completed', 'bye', 'walkover_p1', 'walkover_p2'):
            place.setdefault(m.winner_id, m.winner_place)
        if m.loser_place and m.winner_id and m.status in ('completed', 'walkover_p1', 'walkover_p2'):
            loser = m.participant_2_id if m.winner_id == m.participant_1_id else m.participant_1_id
            if loser:
                place.setdefault(loser, m.loser_place)
    ranked = sorted(place.items(), key=lambda kv: kv[1])
    return {rid: i + 1 for i, (rid, _p) in enumerate(ranked)}

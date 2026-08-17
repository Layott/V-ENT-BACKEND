"""Bracket advancement engine.

A single mechanism drives every "a match resolved -> move players forward" event,
no matter which app triggered it (participant confirm, organizer override in
vent_tournament, or admin override/resolve in vent_auth). We hang a `post_save`
signal on `BracketMatch` (wired in apps.py) so that ANY code path which sets a
match to `completed`/`bye` triggers the cascade - this keeps the vent_auth admin
views working without editing them (they are outside this app's scope).

Advancement is driven by the pointer graph built at generation time
(`winner_to_match`/`winner_to_slot`, `loser_to_match`/`loser_to_slot`). When a
match resolves we push its winner (and, for double elim, its loser) into the
target slot(s), then walkover-collapse any target that can no longer fill up.

Everything here is idempotent: re-running a cascade never double-advances.
"""
import threading

from django.db import transaction
from django.utils import timezone

TERMINAL_STATUSES = ('completed', 'bye')
NON_TERMINAL_STATUSES = ('scheduled', 'in_progress', 'pending_opponent_confirm', 'disputed')

# Re-entrancy guard. While a cascade runs we suspend the post_save signal so the
# internal saves it performs don't recursively re-fire it - the cascade already
# walks the whole downstream subtree explicitly.
_state = threading.local()


def _suspended():
    return getattr(_state, 'suspend_depth', 0) > 0


class suspend_advance:
    """Context manager: pause the auto-advance signal (used during generation)."""
    def __enter__(self):
        _state.suspend_depth = getattr(_state, 'suspend_depth', 0) + 1
        return self

    def __exit__(self, *exc):
        _state.suspend_depth -= 1
        return False


def handle_match_saved(sender, instance, created, **kwargs):
    """post_save receiver for BracketMatch."""
    if _suspended():
        return
    if instance.status not in TERMINAL_STATUSES:
        return
    # A completed match must have a winner; a bye may be a dead walkover (no winner).
    with suspend_advance():
        with transaction.atomic():
            cascade(instance)


def cascade(match):
    """Route a freshly-resolved match forward and check for tournament completion."""
    _route(match)
    _maybe_complete(match.tournament_id)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _loser_of(match):
    """The participant that did not win a completed 2-player match, else None."""
    if match.status != 'completed' or match.winner_id is None:
        return None
    if match.participant_1_id and match.participant_1_id != match.winner_id:
        return match.participant_1
    if match.participant_2_id and match.participant_2_id != match.winner_id:
        return match.participant_2
    return None


def _route(match):
    from ..models import BracketMatch

    # Winner advances.
    if match.winner_id and match.winner_to_match_id:
        tgt = BracketMatch.objects.select_for_update().get(pk=match.winner_to_match_id)
        _place(tgt, match.winner_to_slot, match.winner)
        _check_walkover(tgt)

    # Loser drops (double elimination only - single/round-robin leave these null).
    if match.loser_to_match_id:
        tgt = BracketMatch.objects.select_for_update().get(pk=match.loser_to_match_id)
        loser = _loser_of(match)
        if loser is not None:
            _place(tgt, match.loser_to_slot, loser)
        _check_walkover(tgt)


def _place(match, slot, registration):
    """Idempotently put a registration into participant slot 1 or 2 of a match."""
    field = 'participant_1' if slot == 1 else 'participant_2'
    if getattr(match, f'{field}_id') == registration.id:
        return
    setattr(match, field, registration)
    match.save(update_fields=[field])


def _feeders(match):
    from django.db.models import Q
    from ..models import BracketMatch
    return list(
        BracketMatch.objects.filter(
            Q(winner_to_match_id=match.id) | Q(loser_to_match_id=match.id)
        )
    )


def _check_walkover(match):
    """If a match can no longer reach 2 players, resolve it as a walkover.

    Only fires once every feeder that routes into `match` is terminal. With one
    player present the lone player advances (status='bye'); with none present the
    match is a dead slot (status='bye', no winner) so completion accounting still
    works. A fully-populated match is left `scheduled` to be played.
    """
    if match.status in TERMINAL_STATUSES:
        return
    feeders = _feeders(match)
    if not feeders:
        return  # seeded (round-1) matches are handled at generation time
    if any(f.status not in TERMINAL_STATUSES for f in feeders):
        return  # still waiting on a real match upstream

    present = [p for p in (match.participant_1, match.participant_2) if p is not None]
    if len(present) >= 2:
        return  # playable - leave it scheduled
    if len(present) == 1:
        match.winner = present[0]
        match.status = 'bye'
        match.completed_at = timezone.now()
        match.save(update_fields=['winner', 'status', 'completed_at'])
    else:
        match.status = 'bye'
        match.save(update_fields=['status'])
    cascade(match)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def _maybe_complete(tournament_id):
    from ..models import Tournament

    tournament = Tournament.objects.select_for_update().get(pk=tournament_id)
    if tournament.completed_at is not None:
        return
    still_open = tournament.bracket_matches.filter(status__in=NON_TERMINAL_STATUSES).exists()
    if still_open:
        return
    if not tournament.bracket_matches.exists():
        return

    assign_final_positions(tournament)
    tournament.status = 'completed'
    tournament.completed_at = timezone.now()
    tournament.save(update_fields=['status', 'completed_at'])


def assign_final_positions(tournament):
    """Write TournamentRegistration.final_position (1 = winner) for a finished bracket."""
    from ..services.bracket import normalize_bracket_type

    btype = normalize_bracket_type(tournament.bracket_type)
    if btype == 'round_robin':
        _positions_round_robin(tournament)
    else:
        _positions_elimination(tournament)


def _positions_elimination(tournament):
    matches = list(tournament.bracket_matches.all())
    if not matches:
        return
    final = next((m for m in matches if m.is_final), None)
    if final is None:
        # Fall back to the highest winners-bracket round's single match.
        final = max(matches, key=lambda m: (m.round_number, m.match_number))

    ordered = {}
    if final.winner_id:
        ordered[final.winner_id] = 1
    final_loser = _loser_of(final)
    if final_loser:
        ordered[final_loser.id] = 2

    # Everyone else: the later they were eliminated, the better the placing.
    next_pos = 3
    losers_by_round = sorted(
        (m for m in matches if m is not final and m.status == 'completed'),
        key=lambda m: (-m.round_number, m.bracket_side, m.match_number),
    )
    for m in losers_by_round:
        loser = _loser_of(m)
        if loser and loser.id not in ordered:
            ordered[loser.id] = next_pos
            next_pos += 1

    _write_positions(tournament, ordered)


def _positions_round_robin(tournament):
    from ..models import TournamentRegistration

    regs = list(tournament.registrations.filter(status='confirmed'))
    wins = {r.id: 0 for r in regs}
    for m in tournament.bracket_matches.filter(status='completed'):
        if m.winner_id in wins:
            wins[m.winner_id] += 1
    ranked = sorted(regs, key=lambda r: (-wins[r.id], r.id))
    ordered = {r.id: i + 1 for i, r in enumerate(ranked)}
    _write_positions(tournament, ordered)


def _write_positions(tournament, ordered):
    from ..models import TournamentRegistration

    for reg_id, pos in ordered.items():
        TournamentRegistration.objects.filter(pk=reg_id).update(final_position=pos)

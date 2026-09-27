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
import logging

import threading

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ('completed', 'bye', 'walkover_p1', 'walkover_p2')
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
    # A walkover names its winner in the status. Written without one, which is
    # how the league desk writes it, the side that turned up was never sent on
    # in a bracket: the walkover statuses were not terminal here at all.
    if instance.status in ('walkover_p1', 'walkover_p2') and instance.winner_id is None:
        side = instance.participant_1 if instance.status == 'walkover_p1' else instance.participant_2
        if side is not None:
            instance.winner = side
            type(instance).objects.filter(pk=instance.pk).update(winner=side)
    # A completed match must have a winner; a bye may be a dead walkover (no winner).
    with suspend_advance():
        with transaction.atomic():
            cascade(instance)


def cascade(match):
    """Route a freshly-resolved match forward and check for tournament completion."""
    _route(match)
    _award_reward_tickets(match)
    if match.stage_id:
        # A Swiss stage draws its next round when the last match of this one
        # is in; nothing else in a stage moves on by itself.
        from .. import stage_engine
        stage_engine.on_match_resolved(match)
    _maybe_complete(match.tournament_id)


def _award_reward_tickets(match):
    """Reaching a round can earn a ticket to the event this runs inside.

    CEO: "if them getting to like the finals gets the players that got there
    automatic tickets or not and what level of tickets."

    Awarded on ARRIVAL in the round, not on winning it: "everyone who makes the
    semi-finals gets a pass" means the four who got there, including the two who
    then lose. So the trigger is the winner of a round-N match, who has thereby
    reached round N+1.

    Silent on failure. A ticket that fails to mint is a support question; a
    scoring path that raises because of one is a tournament that cannot record
    results.
    """
    try:
        if match.status != 'completed' or match.winner_id is None:
            return

        from vent_event.models import EventTournamentLink, Ticket
        from vent_event.views_tickets import _new_code

        link = EventTournamentLink.objects.select_related('event', 'reward_tier').filter(
            tournament_id=match.tournament_id).first()
        if link is None or not link.reward_from_round or link.reward_tier_id is None:
            return

        reached = (match.round_number or 0) + 1
        if reached < link.reward_from_round:
            return

        winner = match.winner
        holder = winner.user if winner and winner.user_id else None
        if holder is None:
            # A team entry has no single person to hand a ticket to. Awarding
            # one to the captain silently would be a decision nobody made.
            return

        # Idempotent: cascade can run more than once for the same match, and two
        # tickets for one achievement is two people through one door.
        already = Ticket.objects.filter(
            event_id=link.event_id, user=holder, tier_id=link.reward_tier_id,
            payment_reference='reward:%s' % match.pk).exists()
        if already:
            return

        Ticket.objects.create(
            event_id=link.event_id, tier_id=link.reward_tier_id, user=holder,
            code=_new_code(), price_vc=0, price_ngn=0,
            attendee_name=(holder.full_name or holder.username or '')[:120],
            attendee_email=(holder.email or '')[:254],
            payment_reference='reward:%s' % match.pk,
        )
    except Exception:
        logger.exception('could not award a reward ticket for match %s', match.pk)


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


def _grand_final_reset(match, target):
    """The first grand final of a double elimination with a reset.

    Slot 1 is the winners' champion, who has not lost. If they win, it is over
    and the reset is not played. If the losers' champion wins, both have lost
    once and the reset decides it. Routing the winner into slot 1 like any
    other match would have put one player in a final against nobody.
    """
    if match.winner_id is None:
        return
    if match.winner_id == match.participant_1_id:
        target.participant_1 = match.winner
        target.winner = match.winner
        target.status = 'bye'
        target.completed_at = timezone.now()
        target.save(update_fields=['participant_1', 'winner', 'status', 'completed_at'])
        cascade(target)
    else:
        target.participant_1 = match.participant_1
        target.participant_2 = match.participant_2
        target.save(update_fields=['participant_1', 'participant_2'])
        _arm(target)


def _arm(match):
    """Once both sides are known: give the match its time (the break after
    their last matches) and start its check-in clock."""
    if match.participant_1_id and match.participant_2_id:
        from .. import stage_engine
        if match.stage_id:
            stage_engine.arm_check_in(match)
        else:
            stage_engine.schedule(match)


def _route(match):
    from ..models import BracketMatch

    if (match.bracket_side == 'grand_final' and match.winner_to_match_id):
        tgt = BracketMatch.objects.select_for_update().get(pk=match.winner_to_match_id)
        if tgt.bracket_side == 'grand_final':
            _grand_final_reset(match, tgt)
            return

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
    _arm(match)


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

    # A tournament in stages is over when its LAST stage has been drawn and
    # played, not when the matches that exist so far are finished. Without
    # this the group stage finishing would have ended the whole tournament
    # before the playoff was drawn.
    stages = list(tournament.stages.all())
    if stages:
        last = stages[-1]
        if last.drawn_at is None:
            return
        from .. import stage_engine
        if last.format == 'swiss' and not stage_engine.swiss_finished(last):
            return
        stage_engine.assign_final_positions(tournament)
        last.status = 'complete'
        last.completed_at = timezone.now()
        last.save(update_fields=['status', 'completed_at'])
        tournament.status = 'completed'
        tournament.completed_at = timezone.now()
        tournament.save(update_fields=['status', 'completed_at'])
        return

    assign_final_positions(tournament)
    tournament.status = 'completed'
    tournament.completed_at = timezone.now()
    tournament.save(update_fields=['status', 'completed_at'])


def assign_final_positions(tournament):
    """Write TournamentRegistration.final_position (1 = winner) for a finished bracket."""
    from ..services.bracket import decided_by_table, normalize_bracket_type

    btype = normalize_bracket_type(tournament.bracket_type)
    if decided_by_table(btype):
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

    # When a third-place match was played, it decides third and fourth outright.
    # Without this the loop below would hand third to whoever lost it, which is
    # the opposite of what the match was for.
    next_pos = 3
    third_place = next(
        (m for m in matches
         if m is not final
         and m.round_number == final.round_number
         and m.bracket_side == final.bracket_side
         and m.status == 'completed'),
        None,
    )
    skip = {final}
    if third_place is not None:
        skip.add(third_place)
        if third_place.winner_id:
            ordered.setdefault(third_place.winner_id, 3)
        bronze_loser = _loser_of(third_place)
        if bronze_loser:
            ordered.setdefault(bronze_loser.id, 4)
        next_pos = 5

    # Everyone else: the later they were eliminated, the better the placing.
    losers_by_round = sorted(
        (m for m in matches if m not in skip and m.status == 'completed'),
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

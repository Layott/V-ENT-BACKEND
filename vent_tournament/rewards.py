"""Tickets handed out as a reward rather than sold (P2d).

Two things in a tournament earn an event ticket: reaching a round the
organiser named (EventTournamentLink.reward_from_round, inbox 307) and being
sent on by a feed whose line carries a tier (Progression.ticket_tier, design
section 7: "an event ticket as a qualifying reward"). Both issue through
`issue`, so a reward ticket is made one way: free, in the holder's name, and
never twice for the same achievement (the `reference` says which one).
"""
import logging

logger = logging.getLogger(__name__)


def issue(event_id, tier_id, holder, reference):
    """A free ticket of `tier_id` for `holder`, once per `reference`. None when
    there is nobody to hand it to (a team entry has no single person), or it
    already exists."""
    from vent_event.models import Ticket
    from vent_event.views_tickets import _new_code

    if holder is None:
        return None
    if Ticket.objects.filter(event_id=event_id, user=holder, tier_id=tier_id,
                             payment_reference=reference).exists():
        return None
    return Ticket.objects.create(
        event_id=event_id, tier_id=tier_id, user=holder,
        code=_new_code(), price_vc=0, price_ngn=0,
        attendee_name=(holder.full_name or holder.username or '')[:120],
        attendee_email=(holder.email or '')[:254],
        payment_reference=reference,
    )


def withdraw(reference):
    """Cancel the unused reward tickets made for `reference`, when the place
    that earned them is given up. A ticket already used at the door stays."""
    from vent_event.models import Ticket

    return Ticket.objects.filter(payment_reference=reference, status='valid').update(status='cancelled')

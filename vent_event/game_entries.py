"""Who is playing which party game, and who is waiting (inbox 434, 435, 439).

One place for the rules, because three different things move a ticket in or
out of a game: the person pressing "I want to play" or "Leave", the organiser
changing how many places there are, and the ticket itself being refunded or
voided. If each kept its own copy of "who moves up from the waiting list", the
list would be right after a leave and wrong after a refund.

The rules:

- Only a live ticket for THIS event plays (inbox 435). A refunded or cancelled
  ticket is not one, and the moment a ticket becomes one it leaves every game
  it was in (`drop_ticket`, called from the Ticket post_save signal, so every
  path that refunds or voids a ticket is covered, including ones written later).
- A game with places fills in the order people joined; past that, a waiting
  list in the same order. A place freeing up goes to the first person waiting.
- Leaving keeps the row (state `left`), so the organiser's list can say what
  happened and, once fees exist (inbox 449), so can the money.
"""
from django.db import transaction
from django.utils import timezone

from .models import EventGame, EventGameEntry

LIVE = (EventGameEntry.IN, EventGameEntry.WAITING)


def counts(game):
    """(playing, waiting) for one game."""
    rows = EventGameEntry.objects.filter(game=game, state__in=LIVE)
    playing = rows.filter(state=EventGameEntry.IN).count()
    return playing, rows.count() - playing


def _promote(game):
    """Fill free places from the front of the waiting list. Returns who moved.

    Called inside the caller's transaction with the game row locked, so two
    people leaving at once cannot both hand their place to the same person,
    and nobody is moved past the number of places.
    """
    moved = []
    if game.places is None:
        waiting = list(EventGameEntry.objects.filter(
            game=game, state=EventGameEntry.WAITING))
    else:
        playing = EventGameEntry.objects.filter(
            game=game, state=EventGameEntry.IN).count()
        free = max(0, game.places - playing)
        if not free:
            return moved
        waiting = list(EventGameEntry.objects.filter(
            game=game, state=EventGameEntry.WAITING).order_by('joined_at', 'id')[:free])
    for entry in waiting:
        entry.state = EventGameEntry.IN
        entry.save(update_fields=['state'])
        moved.append(entry)
    return moved


def join(game, ticket):
    """Put this ticket in the game, or on its waiting list. Returns the entry.

    Joining again while in or waiting changes nothing, so a double press or a
    retried request cannot move somebody to the back of the queue. Joining
    after leaving starts again at the back, which is the only fair reading.
    """
    with transaction.atomic():
        locked = EventGame.objects.select_for_update().get(pk=game.pk)
        entry = EventGameEntry.objects.filter(game=locked, ticket=ticket).first()
        if entry is not None and entry.state in LIVE:
            return entry
        playing = EventGameEntry.objects.filter(
            game=locked, state=EventGameEntry.IN).count()
        state = (EventGameEntry.IN
                 if locked.places is None or playing < locked.places
                 else EventGameEntry.WAITING)
        if entry is None:
            entry = EventGameEntry.objects.create(game=locked, ticket=ticket, state=state)
        else:
            entry.state = state
            entry.left_at = None
            entry.joined_at = timezone.now()
            entry.save(update_fields=['state', 'left_at', 'joined_at'])
        return entry


def leave(game, ticket):
    """Take this ticket out. Returns the entry, or None if it was never in."""
    with transaction.atomic():
        locked = EventGame.objects.select_for_update().get(pk=game.pk)
        entry = EventGameEntry.objects.filter(game=locked, ticket=ticket).first()
        if entry is None or entry.state == EventGameEntry.LEFT:
            return entry
        was_playing = entry.state == EventGameEntry.IN
        entry.state = EventGameEntry.LEFT
        entry.left_at = timezone.now()
        entry.save(update_fields=['state', 'left_at'])
        if was_playing:
            _promote(locked)
        return entry


def places_changed(game):
    """More places, or no limit any more: move people up from the waiting list.

    Fewer places never throws anybody out. Somebody who was told they are in
    stays in; the organiser lowering the number only stops new people joining.
    """
    with transaction.atomic():
        locked = EventGame.objects.select_for_update().get(pk=game.pk)
        return _promote(locked)


def drop_ticket(ticket):
    """A refunded or voided ticket leaves every game it was in.

    Each game it was playing in hands the place to the first person waiting.
    """
    entries = list(EventGameEntry.objects.filter(ticket=ticket, state__in=LIVE)
                   .values_list('game_id', flat=True))
    for game_id in entries:
        game = EventGame.objects.filter(pk=game_id).first()
        if game is not None:
            leave(game, ticket)


def position_on_list(entry):
    """1 for the first person waiting, or None when not waiting."""
    if entry is None or entry.state != EventGameEntry.WAITING:
        return None
    ahead = EventGameEntry.objects.filter(
        game_id=entry.game_id, state=EventGameEntry.WAITING,
    ).filter(joined_at__lt=entry.joined_at).count()
    same_moment = EventGameEntry.objects.filter(
        game_id=entry.game_id, state=EventGameEntry.WAITING,
        joined_at=entry.joined_at, id__lt=entry.id).count()
    return ahead + same_moment + 1


def on_ticket_saved(sender, instance, created, **kwargs):
    """post_save on Ticket: a ticket that stops being live leaves its games."""
    if created:
        return
    if instance.status in ('refunded', 'cancelled'):
        drop_ticket(instance)

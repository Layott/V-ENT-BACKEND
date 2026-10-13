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


#: Picks carried by one purchase. More than any event runs at once (60 games),
#: low enough that a body cannot ask for thousands of joins.
MAX_PICKS = 20


def read_picks(data):
    """The games ticked at checkout, as game ids. [] when none were sent.

    Read before any money moves, so a malformed list is refused while
    refusing is still free (inputs.BadInput answers 400 with the field).
    """
    from vent_auth import inputs
    picks = inputs.read_ids(data, 'games', max_items=MAX_PICKS)
    return list(dict.fromkeys(p for p in picks if p > 0))


def join_picks(ticket, picks):
    """Sign `ticket` up for each picked game that is open on its event.

    Called once the ticket exists (inbox 434, piece G2): the wallet buy, a
    free guest ticket, and the card payment confirmed by the browser or the
    webhook. A game that closed or came off between the pick and the payment
    is skipped rather than refused; the ticket is paid for, and the Games tab
    shows what happened. Never raises: a games sign-up must not undo a sale.
    """
    if not picks or ticket is None:
        return []
    joined = []
    for game in EventGame.objects.filter(event_id=ticket.event_id, pk__in=picks,
                                         status=EventGame.OPEN):
        try:
            entry = join(game, ticket)
        except Exception:
            continue
        joined.append({'game': game.id, 'state': entry.state})
    return joined


def ticket_games(ticket):
    """What `ticket` is down for now, with what a page needs to name each game,
    named the way serialize_game names it (`catalogue_key`, or `custom.name`
    for an organiser's own game), so one helper reads both:
    [{'game', 'state', 'place_in_line', 'catalogue_key', 'custom'}]."""
    if ticket is None:
        return []
    return [{'game': e.game_id, 'state': e.state, 'place_in_line': position_on_list(e),
             'catalogue_key': e.game.catalogue_key or None,
             'custom': None if e.game.catalogue_key else {'name': e.game.custom_name}}
            for e in EventGameEntry.objects.filter(ticket=ticket, state__in=LIVE)
            .select_related('game').order_by('joined_at', 'id')]


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

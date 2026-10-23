"""What V-ENT tells a studio, and the one list it comes from.

Design section 14b: "One event list feeds overlays, the run of show and
studios ... The same list becomes the signed webhooks for game studios in P6,
so there is one catalogue, not two (P1 publishes it inside V-ENT, P6 sends it
outside)."

The tournament events are exactly the kinds `vent_tournament.happenings`
records (P1e). An event is added here the day happenings records it, and never
before: a catalogue promising an event nothing sends is documentation that
lies, and a studio would wait for it for ever.
"""
import logging

from django.db import transaction

from . import studio_delivery

logger = logging.getLogger(__name__)

#: The five kinds P1e records, in its own order.
TOURNAMENT_EVENTS = {
    'result_confirmed': 'A match result was recorded: the two sides, the score, the winner and where the match sits',
    'stage_started': 'A stage was drawn or first filled',
    'stage_closed': 'A stage finished and its places are final',
    'line_sent': 'Places from one stage were sent on to another stage',
    'tournament_won': 'A tournament finished, with its champion',
}

#: About the studio's own players, not a tournament.
ACCOUNT_EVENTS = {
    'account_linked': 'A player linked their game account to their V-ENT account',
    'account_unlinked': 'A link between a game account and a V-ENT account was removed',
}

#: Sent when a studio presses "Send a test event", to prove their signature check.
TEST_EVENTS = {
    'ping': 'A test event, sent when you ask for one',
}

EVENTS = {**TOURNAMENT_EVENTS, **ACCOUNT_EVENTS, **TEST_EVENTS}

#: What a studio may subscribe to. A ping always arrives, because it is asked for.
SUBSCRIBABLE = [kind for kind in EVENTS if kind not in TEST_EVENTS]


def clean_subscriptions(values):
    """Known events only, in catalogue order. Unknown names are the caller's to refuse."""
    wanted = {str(v).strip() for v in (values or [])}
    return [kind for kind in SUBSCRIBABLE if kind in wanted]


def unknown_subscriptions(values):
    return sorted({str(v).strip() for v in (values or [])} - set(SUBSCRIBABLE))


def _wants(game, kind):
    if kind in TEST_EVENTS:
        return True
    chosen = game.subscribed_events or []
    return not chosen or kind in chosen


def publish(kind, tournament_id, data, *, match_id=None):
    """Tell every studio running this tournament what just happened.

    Called from `vent_tournament.happenings.record`, inside the transaction
    that recorded the fact, so the deliveries are rolled back with it and the
    first try is made only after the commit. Returns how many were queued.

    It never raises, and a failure inside it rolls back only its own
    savepoint: a studio's trouble must never cost anybody their result.
    """
    from .models import StudioTournamentLink

    if kind not in TOURNAMENT_EVENTS:
        return 0
    try:
        refs = []
        with transaction.atomic():
            links = (StudioTournamentLink.objects
                     .filter(tournament_id=tournament_id, active=True)
                     .select_related('game', 'tournament'))
            for link in links:
                if not _wants(link.game, kind):
                    continue
                payload = dict(data or {})
                payload['tournament'] = {'slug': link.tournament.slug,
                                         'title': link.tournament.tournament_title}
                sessions = _sessions_of(match_id, link.game)
                if sessions:
                    payload['sessions'] = sessions
                delivery = studio_delivery.queue(link.game, 'event', payload, event=kind)
                if delivery is not None:
                    refs.append(delivery.ref)
        studio_delivery.kick(refs)
        return len(refs)
    except Exception:
        logger.warning('studio publish of %s failed', kind, exc_info=True)
        return 0


def _sessions_of(match_id, game):
    """The game session refs of a match, so a studio can tie an event to its
    own lobbies. Empty until game sessions exist (P6d)."""
    if not match_id:
        return []
    try:
        from .models import StudioSession
    except ImportError:
        return []
    return list(StudioSession.objects.filter(match_id=match_id, game=game)
                .order_by('game_number', 'attempt').values_list('ref', flat=True))


def publish_to_game(game, kind, data):
    """An event about a game's own players (linking) or a test ping."""
    if kind not in ACCOUNT_EVENTS and kind not in TEST_EVENTS:
        return None
    if not _wants(game, kind):
        return None
    try:
        delivery = studio_delivery.queue(game, 'event', dict(data or {}), event=kind)
    except Exception:
        logger.warning('studio event %s failed', kind, exc_info=True)
        return None
    if delivery is not None:
        studio_delivery.kick([delivery.ref])
    return delivery

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

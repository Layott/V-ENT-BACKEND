"""Who may do what to a circuit (P5), asked in one place.

A circuit is managed by its owner, or an admin holding manage_tournaments: the
same two who run a tournament (vent_tournament.access.may_manage). Linking a
tournament needs somebody who runs both, the "manager of both ends" a feed
between two tournaments already asks for (P2).
"""
from vent_auth.actors import may_override


def may_manage(user, circuit):
    if user is None or circuit is None:
        return False
    if circuit.owner_id == getattr(user, 'user_id', None):
        return True
    return may_override(user, 'manage_tournaments')


def may_read(user, circuit):
    """A published circuit is anybody's to read; a draft is its manager's."""
    return circuit is not None and (circuit.is_published or may_manage(user, circuit))


def may_link(user, circuit, tournament):
    from vent_tournament.access import may_manage as may_manage_tournament
    return may_manage(user, circuit) and may_manage_tournament(user, tournament)

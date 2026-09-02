"""Finding a tournament from whatever the address carried.

CEO, 2 September 2026, with a screenshot of his own tournament console:

    Pending BE deploy - this action activates once the backend endpoint ships.
    (Cancel & Refund)

The endpoint was not missing. `<int:tournament_id>/cancel/` had existed for
months. The console addresses a tournament by SLUG, because the slug rule says
no numeric id appears in an address a person can see, and an `<int:>` route
does not match a slug. Django answered 404, and the frontend's `isPendingBackend`
treats any 404 as "the backend has not shipped this yet".

Measured on production before the fix:

    POST /tournament/rivalvry-series-s2/cancel/   404
    POST /tournament/26/cancel/                   409   (a real answer)

So the organiser was told to wait for a deploy that had already happened, on a
feature that worked. Twenty-six routes were `<int:tournament_id>` against
thirty-one that were `<str:>`, so it was a class rather than one banner: every
one of those actions was unreachable from the console that offers it.

One function, used by every view, so the two spellings cannot diverge again.
"""
from .models import Tournament


def find(key):
    """The tournament that address means, or None.

    A digit is tried as an id first and then as a slug, because a slug is free
    text and could in principle be all digits. Trying the id first and falling
    through costs one query in the rare case and none in the common one.
    """
    raw = str(key or '').strip()
    if not raw:
        return None

    if raw.isdigit():
        found = Tournament.objects.filter(tournament_id=int(raw)).first()
        if found is not None:
            return found

    return Tournament.objects.filter(slug=raw).first()

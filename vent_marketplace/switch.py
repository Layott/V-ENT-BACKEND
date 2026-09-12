"""Whether Vermillion City is open, decided in one place, and closed by default.

CEO, 9 September 2026, and this is the instruction that governs the whole
module:

    "But please make sure it is built, but still gated, we dont want to release
    the marketplace yet to the public."

So the marketplace is built to work and shipped switched OFF.

    MARKETPLACE_ENABLED=0   (the default) every endpoint refuses with
                            MARKETPLACE_OFF, no link renders, the routes are
                            noindex and in the robots disallow list
    MARKETPLACE_ENABLED=1   it works

## Why the default is the opposite of billing's

`vent_billing`'s switch defaults to ON, because it was added to a feature that
was already live and taking it away without being asked would have been the
wrong move. This one has never been live, nobody has ever used it, and the
instruction is explicit. A feature that is off unless somebody says otherwise
cannot be turned on by an unrelated deploy, which is exactly how billing ended
up live by accident on 8 September.

## Reading, not writing

Turning it off does not delete a listing, cancel a bid or take anybody's money.
It stops the ENDPOINTS. Rows stay as they are, so turning it back on returns to
where it was, and a purchase that was mid-flight when it closed is still there
when it opens.

There is one deliberate exception, and it is the important one: an endpoint that
would leave money in limbo is not simply refused. See `holds.py` for what
happens to an escrow hold when the marketplace closes underneath it.
"""
import functools

from django.conf import settings
from django.http import JsonResponse

#: The code the frontend branches on. A code, never a sentence: a refusal is
#: read by a screen that may be in French or Portuguese.
OFF_CODE = 'MARKETPLACE_OFF'


def marketplace_is_on():
    """One reader. Defaults to OFF.

    `getattr` with a default rather than a settings key that must exist,
    because a box whose `.env` predates this module must read as closed rather
    than raising on every request.
    """
    return bool(getattr(settings, 'MARKETPLACE_ENABLED', False))


def _refusal():
    """The answer every marketplace endpoint gives while it is closed.

    A plain `JsonResponse` rather than a DRF one on purpose: this is returned
    from a wrapper OUTSIDE `@api_view`, where a DRF `Response` has no renderer
    attached and raises instead of rendering. Same envelope either way, which
    is what the frontend reads.

    503 rather than 404: the address exists and will work later, and a 404
    would tell a crawler to forget it.
    """
    return JsonResponse(
        {
            'status': 'error',
            'data': {},
            'message': 'Vermillion City is not open yet.',
            'code': OFF_CODE,
        },
        status=503,
    )


def gated(view):
    """Wrap a marketplace view so it refuses while the switch is off.

    Applied at the urlconf, once per route, rather than as a decorator on each
    view. A rule that has to be remembered at every view is a rule that holds
    everywhere except the one added last, and that one ships open.
    """
    @functools.wraps(view)
    def guarded(request, *args, **kwargs):
        if not marketplace_is_on():
            return _refusal()
        return view(request, *args, **kwargs)

    # An explicit mark, because `functools.wraps` is not one: DRF's `@api_view`
    # sets `__wrapped__` on every view, so a coverage test that looked for that
    # passed with a route deliberately left open. That exact fault was found on
    # the billing switch by unwrapping a route on purpose.
    guarded.marketplace_gated = True
    return guarded

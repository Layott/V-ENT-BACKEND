"""Whether the anime module is open, decided in one place, and closed by default.

CEO, 10 September 2026, and this is the instruction that governs the whole
module:

    "i want to build the following behind the gating, still locked and not open
    to public."

So it is built to work and shipped switched OFF.

    ANIME_ENABLED=0   (the default) every endpoint refuses with ANIME_OFF, the
                      nav still says Coming Soon, the routes are noindex and in
                      the robots disallow list
    ANIME_ENABLED=1   it works

Copied deliberately from `vent_marketplace/switch.py` rather than generalised
into something both import. Two modules that must be able to open independently
should not share a switch: the day somebody makes the shared one configurable
per module is the day one of them opens by accident. The duplication is eleven
lines and it is the point.

## What being off does NOT do

It stops the ENDPOINTS. It does not delete a series, a chapter somebody paid
for, a reading room or a vote. Rows stay as they are, so turning it back on
returns to exactly where it was.

The one thing worth naming: a coin that has already left a wallet stays gone
while it is off, because a purchase is settled at the moment it is made and
there is nothing in flight to strand. That is the difference from the
marketplace, where an escrow hold can be caught mid-air, and it is why there is
no `holds.py` equivalent here.
"""
import functools

from django.conf import settings
from django.http import JsonResponse

#: The code the frontend branches on. A code, never a sentence: a refusal is
#: read by a screen that may be in French or Portuguese.
OFF_CODE = 'ANIME_OFF'


def anime_is_on():
    """One reader. Defaults to OFF.

    `getattr` with a default rather than a settings key that must exist, because
    a box whose `.env` predates this module must read as closed rather than
    raising on every request.
    """
    return bool(getattr(settings, 'ANIME_ENABLED', False))


def _refusal():
    """The answer every anime endpoint gives while it is closed.

    A plain `JsonResponse` rather than a DRF one on purpose: this is returned
    from a wrapper OUTSIDE `@api_view`, where a DRF `Response` has no renderer
    attached and raises instead of rendering.

    503 rather than 404: the address exists and will work later, and a 404 would
    tell a crawler to forget it.
    """
    return JsonResponse(
        {
            'status': 'error',
            'data': {},
            'message': 'The anime module is not open yet.',
            'code': OFF_CODE,
        },
        status=503,
    )


def gated(view):
    """Wrap an anime view so it refuses while the switch is off.

    Applied at the urlconf, once per route, rather than as a decorator on each
    view. A rule that has to be remembered at every view is a rule that holds
    everywhere except the one added last, and that one ships open.
    """
    @functools.wraps(view)
    def guarded(request, *args, **kwargs):
        if not anime_is_on():
            return _refusal()
        return view(request, *args, **kwargs)

    # An explicit mark, because `functools.wraps` is not one: DRF's `@api_view`
    # sets `__wrapped__` on every view, so a coverage test that looked for that
    # passed with a route deliberately left open. That exact fault was found on
    # the billing switch by unwrapping a route on purpose.
    guarded.anime_gated = True
    return guarded

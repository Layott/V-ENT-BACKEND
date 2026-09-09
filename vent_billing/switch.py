"""Whether subscriptions are on, as a decision rather than an accident.

Gate D2. `vent_billing` shipped on 8 September with no switch at all, and it is
live because it deployed. Nothing charges only because production carries no
Paystack key and nothing schedules the renewal run - two accidents standing in
for a decision, either of which could be undone by somebody fixing an unrelated
thing.

The marketplace spec names this exact case as the fault not to repeat:

    "vent_billing shipped without a flag on 8 September and is live by
    accident; this must not repeat that."

So there is a switch now, it is read in one place, and its current value is a
sentence anybody can read rather than a property of what happens to be
configured.

    BILLING_ENABLED=1   subscriptions work, which is today's behaviour
    BILLING_ENABLED=0   every billing endpoint refuses with BILLING_OFF, and
                        the site shows no plan and no membership anywhere

**Reading, not writing.** Turning it off must never make somebody's existing
membership vanish or their access silently lapse - that would be taking
something away from people who paid. What it stops is the endpoints: nobody can
subscribe, cancel, change plan or be charged while it is off, and the renewal
command refuses to run. The rows stay exactly as they are, so turning it back
on returns to where it was.
"""
import functools

from django.conf import settings
from django.http import JsonResponse

#: The code the frontend branches on. A code, never a sentence: a refusal is
#: read by a screen that may be in French.
OFF_CODE = 'BILLING_OFF'


def billing_is_on():
    """One reader. Defaults to ON, which is what production does today.

    Defaulting to on is deliberate: this switch was added to make the state
    explicit, not to take a live feature away from anybody without being asked.
    """
    return bool(getattr(settings, 'BILLING_ENABLED', True))


def _refusal():
    """The answer every billing endpoint gives while it is off.

    A plain `JsonResponse` rather than a DRF one on purpose: this is returned
    from a wrapper OUTSIDE `@api_view`, where a DRF Response has no renderer
    attached and raises instead of rendering. Same envelope either way, which
    is what the frontend reads.
    """
    return JsonResponse(
        {
            'status': 'error',
            'data': {},
            'message': 'Subscriptions are not open on V-ENT yet.',
            'code': OFF_CODE,
        },
        status=503,
    )


def gated(view):
    """Wrap a billing view so it refuses while the switch is off.

    Applied at the urlconf, once per route, rather than as a decorator on each
    of the 24 views. A rule that has to be remembered at 24 sites is a rule
    that holds at 23, and the twenty-fifth view is the one that ships open.
    """
    @functools.wraps(view)
    def guarded(request, *args, **kwargs):
        if not billing_is_on():
            return _refusal()
        return view(request, *args, **kwargs)

    # An explicit mark, because `functools.wraps` is not one: DRF's own
    # `@api_view` sets `__wrapped__` on every view here, so a test that looked
    # for it passed with a route deliberately left open. Found by unwrapping a
    # route on purpose and watching the test still say OK.
    guarded.billing_gated = True
    return guarded

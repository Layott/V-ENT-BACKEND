"""One endpoint, and it counts. Public, because the people being counted are
by definition the ones without accounts.

CEO, 7 September 2026: "how many clicks, how many people opened it up, how many
tapped buy, how many check out vendor, stuff like that."

Built to the same shape as the referral arrival endpoint next door, which is
the closest existing thing: unauthenticated, dull, and answering 200 whether or
not anything was recorded. An unknown event or an unknown step is far more
likely to be a page from a different deploy than an attack, and answering an
error to a beacon nobody awaits only fills a console with red.

Nothing here is a permission check, because nothing here reads anything. The
counts it writes are readable only through the metrics endpoint, which is
organiser and door staff only.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import funnel
from .models import Event


def _ok(data):
    return Response({'status': 'success', 'message': '', 'data': data},
                    status=status.HTTP_200_OK)


def _event(event_id):
    """By slug, or by id for a link shared before a rename."""
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


@api_view(['POST'])
@permission_classes([AllowAny])
def track(request, event_id):
    """`{step, first_time, ref}` -> one more on today's count.

    `first_time` is the browser saying it had not done this step on this event
    before. It is not verified and cannot be: verifying it would mean storing
    who did what, which is the thing this design exists to avoid.
    """
    event = _event(event_id)
    if event is None:
        return _ok({'recorded': False})

    step = str(request.data.get('step') or '').strip()
    row = funnel.record(
        event, step,
        first_time=bool(request.data.get('first_time')),
        ref=request.data.get('ref') or '')
    return _ok({'recorded': row is not None})

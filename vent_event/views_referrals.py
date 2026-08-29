"""The two endpoints an influencer link needs: recording an arrival, and
reporting what the links did.

The arrival endpoint is public and unauthenticated, because the person arriving
through an influencer's link is by definition somebody who has never been here.
It is deliberately dull: it takes an event and a code, adds one to a daily
count, and answers with nothing worth stealing.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import referrals as refs
from .models import Event


def _ok(data, message='OK'):
    return Response({'status': 'success', 'message': message, 'data': data},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _event(event_id):
    """By slug, or by id for a link shared before a rename."""
    if str(event_id).isdigit():
        return Event.objects.filter(event_id=int(event_id)).first()
    return Event.objects.filter(slug=str(event_id)).first()


@api_view(['POST'])
@permission_classes([AllowAny])
def referral_visit(request, event_id, code):
    """One arrival through one link.

    Answers 200 whether or not the code is real. A wrong code is far more
    likely to be a stale link off an old post than an attack, and telling the
    caller which codes exist turns this into a way to enumerate an organiser's
    influencer list from outside.
    """
    event = _event(event_id)
    if event is None:
        return _ok({'recorded': False})

    referral = refs.resolve(event, code)
    if referral is None:
        return _ok({'recorded': False})

    first_time = bool(request.data.get('first_time'))
    refs.record_visit(referral, first_time=first_time)
    return _ok({'recorded': True})

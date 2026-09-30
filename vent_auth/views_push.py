"""Browser push: a person's devices subscribe, unsubscribe, and test.

POST /auth/push/subscribe/     {endpoint, keys: {p256dh, auth}}  this browser
POST /auth/push/unsubscribe/   {endpoint}                        this browser
POST /auth/push/test/          send a test to every device of mine

Every door acts on the signed-in person's own subscriptions only: an endpoint
belonging to somebody else is never moved or removed (a browser re-subscribing
after a sign-out under a new account takes it over only by subscribing again,
which proves it holds the keys).
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import push
from .models import PushSubscription
from .views_profile import _user_from_bearer
from . import inputs


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'data': {}, 'message': message}, status=http)


@api_view(['POST'])
def push_subscribe(request):
    user, err = _user_from_bearer(request)
    if err:
        return err
    if not push.configured():
        return _err('Push notifications are not set up yet.', 'PUSH_UNAVAILABLE',
                    status.HTTP_503_SERVICE_UNAVAILABLE)
    body = request.data if isinstance(request.data, dict) else {}
    endpoint = inputs.read_text(body, 'endpoint', max_length=600)
    keys = body.get('keys') if isinstance(body.get('keys'), dict) else {}
    p256dh = inputs.read_text(keys, 'p256dh', max_length=200)
    auth = inputs.read_text(keys, 'auth', max_length=100)
    if not endpoint.startswith('https://') or len(endpoint) > 600 or not p256dh or not auth \
            or len(p256dh) > 200 or len(auth) > 100:
        return _err('That browser subscription could not be read.', 'PUSH_BAD_SUBSCRIPTION')
    # The browser that holds these keys is the one subscribing: the row is
    # this person's now, whoever it belonged to before.
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={'user': user, 'p256dh': p256dh, 'auth': auth,
                  'user_agent': str(request.META.get('HTTP_USER_AGENT') or '')[:300]})
    return Response({'status': 'success', 'code': 'OK',
                     'data': {'devices': user.push_subscriptions.count()},
                     'message': 'Push is on for this browser.'})


@api_view(['POST'])
def push_unsubscribe(request):
    user, err = _user_from_bearer(request)
    if err:
        return err
    endpoint = inputs.read_text(request.data, 'endpoint', max_length=600)
    removed, _ = PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
    return Response({'status': 'success', 'code': 'OK',
                     'data': {'removed': removed, 'devices': user.push_subscriptions.count()},
                     'message': 'Push is off for this browser.'})


@api_view(['POST'])
def push_test(request):
    user, err = _user_from_bearer(request)
    if err:
        return err
    if not push.configured():
        return _err('Push notifications are not set up yet.', 'PUSH_UNAVAILABLE',
                    status.HTTP_503_SERVICE_UNAVAILABLE)
    import json
    from vent.settings import FRONTEND_URL
    sent = push.send_to_user(user.user_id, json.dumps({
        'title': 'V-ENT push is working',
        'body': 'This is how your notifications will arrive on this device.',
        'url': '%s/settings?panel=notifications' % FRONTEND_URL,
        'tag': 'vent-test',
    }))
    if not sent:
        return _err('No device of yours took it. Turn push on in this browser first.',
                    'PUSH_NO_DEVICE', status.HTTP_409_CONFLICT)
    return Response({'status': 'success', 'code': 'OK', 'data': {'sent': sent},
                     'message': 'Sent to %d device(s).' % sent})

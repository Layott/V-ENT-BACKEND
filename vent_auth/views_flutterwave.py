"""POST /auth/flutterwave/webhook/ - Flutterwave telling us a payment moved.

Set in the Flutterwave dashboard (Settings, Webhooks) for test and live, with
the secret hash that is also `FLW_SECRET_HASH` in the backend environment.

Why it exists: a payer who pays and closes the tab never comes back to the
return page, and without this the money would sit at Flutterwave with no coins
credited and no ticket issued. It is the second arrival for the same payment,
never a different path: it only names a reference, and the same settle
functions the return page uses ask Flutterwave what happened (the body is never
trusted, 29 September 2026; Flutterwave's own guidance is to re-query).

Answers: 401 for a bad signature; 503 when Flutterwave could not be asked, so
Flutterwave retries (3 times, 30 minutes apart); 200 for everything handled or
not ours, so it stops retrying something that will never change.
"""
import json
import logging

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import flutterwave

log = logging.getLogger(__name__)


def _answer(code, http=status.HTTP_200_OK):
    return Response({'status': 'success' if http < 400 else 'error', 'code': code,
                     'data': {}, 'message': code}, status=http)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def webhook(request):
    if not flutterwave.signature_ok(request):
        log.warning('flutterwave webhook with a bad or missing verif-hash')
        return _answer('BAD_SIGNATURE', status.HTTP_401_UNAUTHORIZED)
    try:
        body = request.data if isinstance(request.data, dict) else json.loads(request.body or b'{}')
    except ValueError:
        return _answer('NOT_JSON', status.HTTP_400_BAD_REQUEST)
    data = body.get('data') or {}
    reference = str(data.get('tx_ref') or data.get('txRef') or '')
    if not flutterwave.owns(reference):
        return _answer('NOT_OURS')

    if reference.startswith(flutterwave.PREFIX + 'TOP-'):
        from .views_wallet import settle_flutterwave_topup
        code, _txn, _balance = settle_flutterwave_topup(reference)
    elif reference.startswith(flutterwave.PREFIX + 'TKT-'):
        from vent_event.views_guest import fulfil_flutterwave
        code, _tickets = fulfil_flutterwave(reference)
    else:
        code = 'not_found'
    log.info('flutterwave webhook %s: %s', reference, code)
    if code == 'unreachable':
        return _answer('RETRY', status.HTTP_503_SERVICE_UNAVAILABLE)
    return _answer(code.upper())

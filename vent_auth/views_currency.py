"""GET /auth/pay/currencies/?amount_ngn=5000 - what a payment costs in each
currency the account can take (inbox 361).

Public, because a guest buying a ticket has no account. Every currency proven
on the live account is listed with the methods it allows, the amount it would
cost, the rate, and a signed quote the checkout will charge exactly. `default`
is the payer's own currency: the country on their profile when they are
signed in, else the country their address places them in, else naira.
"""
import logging
from decimal import Decimal

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import flutterwave, flutterwave_currency as fx, inputs
from .throttle import limited
from .models import Users

log = logging.getLogger(__name__)

#: The largest naira amount anybody is quoted for: a price bigger than this is
#: not a ticket or a top-up, it is somebody probing the rate endpoint.
MAX_NGN = Decimal('50000000')


def _viewer(request):
    """Who is asking, when anybody is. Only chooses the default currency;
    nothing here is gated on it."""
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


@api_view(['GET'])
@limited('pay-currencies', 60)
def currencies(request):
    try:
        amount_ngn = inputs.read_decimal(request.GET, 'amount_ngn', required=True,
                                         minimum='0.01', maximum=MAX_NGN)
    except inputs.BadInput:
        amount_ngn = None
    if amount_ngn is None:
        return Response({'status': 'error', 'code': 'INVALID_INPUT', 'field': 'amount_ngn',
                         'message': 'Say how much, in naira, is being paid.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not flutterwave.configured():
        return Response({'status': 'success', 'message': 'Currencies',
                         'data': {'default': 'NGN', 'currencies': [fx.quote(amount_ngn, 'NGN')]}})

    options = []
    for code in fx.enabled():
        try:
            options.append(fx.quote(amount_ngn, code))
        except (fx.CurrencyError, flutterwave.Unreachable, flutterwave.Refused):
            # A currency whose rate cannot be fetched just now is left out of
            # this answer rather than offered at a guessed price.
            log.info('no quote for %s just now', code)

    country = ''
    viewer = _viewer(request)
    if viewer is not None:
        country = viewer.country or ''
    if not country:
        try:
            from .geo import locate_request
            country = locate_request(request)[0] or ''
        except Exception:                                    # noqa: BLE001
            country = ''
    default = fx.for_country(country)
    if default not in {o['code'] for o in options}:
        default = 'NGN'

    return Response({'status': 'success', 'message': 'Currencies',
                     'data': {'default': default, 'currencies': options}})

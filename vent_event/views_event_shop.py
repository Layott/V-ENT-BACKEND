"""The event's own shop (inbox 550, CEO 25 October 2026).

"As a premium organizer, I want to be able to add a temporary product shop to
my event. In which users can buy things (can be at a discount) and get them
only at the venue and only on the event day (Only physical products)."

It is a `Vendor` with `kind='organiser'`, so the storefront, the checkout, the
order code and collecting at the venue are the ones every stall already uses.
This file only opens it: one per event, for whoever runs the event, on a
Premium event. Everything after that goes through the stall doors in
views_vendor_shop.py, which treat the event's runners as its owner.

    GET  /event/<event>/shop/   the shop, or null, and whether it may be opened
    POST /event/<event>/shop/   open it
"""
from rest_framework import status
from rest_framework.decorators import api_view

from vent_auth import inputs
from vent_auth.premium import has_premium, refuse

from .models import Vendor
from .permissions import may_run_event
from .views_tickets import _authenticate, _error, _ok
from .views_vendors import _event_by_ref


def _summary(shop):
    if shop is None:
        return None
    return {'id': shop.id, 'slug': shop.slug, 'name': shop.name, 'status': shop.status,
            'kind': shop.kind, 'refund_policy': shop.refund_policy,
            'pickup_location': shop.pickup_location or None,
            'pickup_opens_at': shop.pickup_opens_at,
            'pickup_closes_at': shop.pickup_closes_at,
            'product_count': shop.products.filter(is_active=True).count()}


@api_view(['GET', 'POST'])
def event_shop(request, event_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error
    event = _event_by_ref(event_id)
    if event is None:
        return _error('No such event.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not may_run_event(user, event):
        return _error('Only the people running this event can open its shop.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    shop = Vendor.objects.filter(event=event, kind=Vendor.KIND_ORGANISER).first()
    premium = has_premium(event)
    if request.method == 'GET':
        return _ok({'shop': _summary(shop), 'premium': premium}, 'The event shop.')

    if shop is not None:
        # Opening twice is one intention; the second press is not an error.
        return _ok({'shop': _summary(shop), 'premium': premium}, 'The shop is already open.')
    if not premium:
        body = refuse('event_shop')
        from rest_framework.response import Response
        return Response(body, status=status.HTTP_402_PAYMENT_REQUIRED)

    name = inputs.read_text(request.data, 'name', max_length=120) or ('%s shop' % event.name)[:120]
    shop = Vendor.objects.create(
        event=event, owner=user, name=name, kind=Vendor.KIND_ORGANISER,
        # The organiser approves their own shop; it trades at once.
        status='live',
        category='Merchandise',
        pickup_location=inputs.read_text(request.data, 'pickup_location', max_length=160),
    )
    from rest_framework.response import Response
    return Response({'status': 'success', 'data': {'shop': _summary(shop), 'premium': premium},
                     'message': 'Shop opened.'}, status=status.HTTP_201_CREATED)

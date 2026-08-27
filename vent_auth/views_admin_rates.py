"""Admins keep the exchange rates.

Rates decide what a price looks like to a reader in Accra or Nairobi, so they
sit with the other structural powers rather than with day-to-day moderation.

Two ways to set them: pull the published feed, or type a number. The feed is
the normal path and runs nightly from cron; typing one is for when the feed is
wrong, unreachable, or a rate has to be pinned deliberately.

Money still moves in naira. These numbers change what somebody reads, never
what they are charged.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import admin_role_required
from .models import Currency
from .rates import FEED_URL, refresh_rates

# Adding a currency or moving a rate changes what every reader sees a price as,
# which is a platform-shaping decision rather than a moderation one.
RATE_ADMIN_ROLES = ['super_admin', 'finance_admin']


def _row(c):
    return {
        'code': c.code,
        'name': c.name,
        'symbol': c.symbol,
        'rate_from_ngn': str(c.rate_from_ngn),
        'rate_updated': c.rate_updated,
        'is_active': c.is_active,
        'sort_order': c.sort_order,
    }


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message, 'code': code},
                    status=http_status)


@api_view(['GET'])
@admin_role_required(RATE_ADMIN_ROLES)
def admin_rates(request):
    """GET /auth/admin/rates/ - every currency and how fresh its rate is."""
    rows = Currency.objects.all()
    return _ok({
        'results': [_row(c) for c in rows],
        'count': rows.count(),
        'base': 'NGN',
        'feed': FEED_URL,
    })


@api_view(['PATCH'])
@admin_role_required(RATE_ADMIN_ROLES)
def admin_rate_detail(request, code):
    """PATCH /auth/admin/rates/{code}/ - set a rate by hand, or retire a currency.

    For when the feed is wrong, unreachable, or a rate has to be pinned.
    """
    currency = get_object_or_404(Currency, code=str(code).upper())
    updated = []

    if 'rate_from_ngn' in request.data:
        if currency.code == 'NGN':
            return _err('The naira is the base and is always 1.', 'BASE_RATE_FIXED')
        try:
            value = float(request.data.get('rate_from_ngn'))
        except (TypeError, ValueError):
            return _err('The rate must be a number.', 'VALIDATION_FAILED')
        if value <= 0:
            return _err('A rate has to be more than zero.', 'VALIDATION_FAILED')
        currency.rate_from_ngn = value
        # Typed by hand counts as fresh: somebody looked at it just now.
        from django.utils import timezone

        currency.rate_updated = timezone.now()
        updated += ['rate_from_ngn', 'rate_updated']

    if 'is_active' in request.data:
        if currency.code == 'NGN':
            return _err('The naira cannot be switched off.', 'BASE_CURRENCY_REQUIRED')
        currency.is_active = str(request.data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if 'sort_order' in request.data:
        try:
            currency.sort_order = int(request.data.get('sort_order') or 0)
        except (TypeError, ValueError):
            return _err('sort_order must be a number.', 'VALIDATION_FAILED')
        updated.append('sort_order')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    currency.save(update_fields=updated)
    return _ok(_row(currency), 'Rate updated.')


@api_view(['POST'])
@admin_role_required(RATE_ADMIN_ROLES)
def admin_refresh_rates(request):
    """POST /auth/admin/rates/refresh/ - pull the feed now.

    A failure leaves every existing rate exactly as it was. Stale figures make a
    guide slightly wrong; blank ones would make prices unreadable.
    """
    updated, skipped, error = refresh_rates()

    if error:
        return _err(error, 'RATES_FEED_UNAVAILABLE', status.HTTP_502_BAD_GATEWAY)

    rows = Currency.objects.all()
    return _ok({
        'updated': updated,
        'skipped': skipped,
        'results': [_row(c) for c in rows],
    }, 'Updated %d rates.' % updated)

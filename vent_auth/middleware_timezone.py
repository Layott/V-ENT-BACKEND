"""The browser's zone, for every datetime that arrives without one.

`DateField` (and any `datetime-local` input) hands back "2026-09-26T10:30" with
no zone on it. The server runs on UTC, so a naive value read with
`timezone.get_current_timezone()` was UTC, and an organiser in Lagos typing
10:30 for a programme session saw it come back as 11:30 (walk, 18 September
2026). The wizard converts its two dates in the browser; nine other screens
with a timed DateField did not, and each was the same fault waiting.

The browser is the only thing that knows which zone the person typed in, so
it says so on every request to the API (`X-Client-Timezone`, set once by
`components/timing/ClientTimezone.js`), and this activates that zone for the
request. Every `make_aware(naive, get_current_timezone())` on every screen is
then right, including the ones written next month.

A missing or unknown zone leaves UTC, which is what happened before, so a
client that does not send the header loses nothing.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

HEADER = 'HTTP_X_CLIENT_TIMEZONE'


def zone_from(request):
    name = (request.META.get(HEADER) or '').strip()
    if not name or len(name) > 64:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


class ClientTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        zone = zone_from(request)
        if zone is None:
            return self.get_response(request)
        timezone.activate(zone)
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()

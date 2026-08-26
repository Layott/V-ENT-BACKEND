"""Keep a session alive while it is being used.

The reported fault: somebody signed in and working was thrown out to
"Your session expired. Please sign in again." in the middle of what they were
doing. The cause is that `login_session_created_at` was stamped once, at login,
and never touched again - so the window counted down from the moment you signed
in regardless of whether you were using the account. Fourteen days of solid work
and you were still logged out on day fourteen, mid-sentence.

The fix redefines the field rather than adding another one. It now means "when
this session was last used", and this middleware moves it forward while somebody
is active. Sixty-seven places already compare that field against the timeout;
changing what it means turns every one of them into an inactivity check without
touching any of them, and there is no window where half the codebase reads one
field and half reads another.

An idle session still expires, on exactly the same timeout as before. What
changed is only that using the account counts as being there.
"""
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

# How stale the stamp has to be before it is worth a write.
#
# Without this, every authenticated request writes a row - and the app makes
# several per page. The stamp only has to be accurate to within a few minutes
# for a timeout measured in days, so this trades precision nobody can perceive
# for roughly a hundredfold fewer writes.
TOUCH_AFTER_MINUTES = 5


class SessionActivityMiddleware:
    """Move a live session's last-used stamp forward as somebody works."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._touch(request)
        return self.get_response(request)

    def _touch(self, request):
        header = request.headers.get('Authorization') or ''
        if not header.startswith('Bearer '):
            return

        token = header.split(' ', 1)[1].strip()
        if not token:
            return

        # Imported here rather than at module scope: middleware is constructed
        # while the app registry is still loading.
        from vent_auth.models import Users
        from vent_auth.views_helpers import session_timeout_minutes

        try:
            user = Users.objects.filter(login_session_token=token).only(
                'user_id', 'login_session_created_at',
            ).first()
            if user is None or user.login_session_created_at is None:
                return

            now = timezone.now()
            idle = now - user.login_session_created_at

            # Never revive a session that has already lapsed. The request that
            # carried this token is about to be refused by the view, and moving
            # the stamp would hand back an account the timeout had closed.
            if idle > timedelta(minutes=session_timeout_minutes()):
                return

            if idle < timedelta(minutes=TOUCH_AFTER_MINUTES):
                return

            Users.objects.filter(pk=user.pk).update(login_session_created_at=now)
        except Exception:
            # Nothing here is worth failing a request over. A missed touch costs
            # somebody a re-login at worst; a raised exception costs them the
            # page they asked for.
            logger.exception('session activity touch failed')

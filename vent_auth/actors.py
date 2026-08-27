"""Who is making this request, and may they overrule the owner.

Both questions were answered inside `vent_tournament/views.py`, which is fine
until a second module needs the same answer. Events need it now, and a second
copy of authentication logic is how two endpoints quietly start disagreeing
about who is signed in.

The console and the website hold SEPARATE sessions on purpose
(`admin_session_token` vs `login_session_token`), so signing out of one does not
sign you out of the other. That separation stays. What lives here is only the
recognition that a request carrying either one is somebody.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import Users


def _error(code, message, http_status):
    return Response({'code': code, 'status': 'error', 'message': message},
                    status=http_status)


def actor_from_request(request):
    """The account behind this request. Returns (user, error_response).

    Exactly one of the two is not None.

    The website session is tried first because that is the ordinary case; the
    console grant is the admin console calling an owner's own endpoint. Each is
    checked against its own clock, because they are separate sessions and always
    were - this is not the old mistake of making the two tokens equal.
    """
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _error('AUTHORIZATION_HEADER_REQUIRED',
                            'Authorization header is required',
                            status.HTTP_400_BAD_REQUEST)

    token = header.split(' ', 1)[1]

    user = Users.objects.filter(login_session_token=token).first()
    if user is not None:
        from .views_helpers import session_timeout_minutes

        if user.login_session_created_at is None or \
                timezone.now() - user.login_session_created_at > timedelta(
                    minutes=session_timeout_minutes()):
            return None, _error('SESSION_TOKEN_EXPIRED',
                                'Session token has expired',
                                status.HTTP_401_UNAUTHORIZED)
        return user, None

    from .decorators import resolve_admin

    admin, _admin_error = resolve_admin(request)
    if admin is not None:
        return admin, None

    return None, _error('INVALID_EXPIRED_SESSION_TOKEN',
                        'Invalid or expired session token',
                        status.HTTP_401_UNAUTHORIZED)


def may_override(user, permission):
    """Whether this account may act on something somebody else owns.

    Named by the permission rather than hardcoded, because "who may correct a
    tournament" and "who may correct an event" are allowed to be different
    answers, and the caller is the one that knows which it is asking.
    """
    from .decorators import ROLE_PERMISSIONS, effective_admin_role

    if not getattr(user, 'is_staff', False):
        return False
    return effective_admin_role(user) in ROLE_PERMISSIONS.get(permission, set())

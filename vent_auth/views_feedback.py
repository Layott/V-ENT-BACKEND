# -*- coding: utf-8 -*-
"""Somewhere to say what is wrong.

CEO, 7 September 2026: "Let'salso have a place for feedbck."

Asked for beside the pricing page, and they belong together: while everything
is free, what is being asked of people is that they use it hard and say what
broke. Asking for that with nowhere to put it is the same as not asking.

**Open to anybody.** The most useful report comes from somebody who hit a wall,
and the wall is sometimes the sign-in page. So this takes a POST with no Bearer
token; a signed-in person is recorded as themselves, and anybody else may leave
an address or not.

Which means it needs a rate limit, because an open write endpoint without one is
a spam target. One per address per minute, and twenty an hour, counted off the
rows rather than a cache: the numbers are small, the table is indexed by time,
and a limiter that forgets when the process restarts is not a limiter.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Feedback, Users

# Long enough to say what happened, short enough that nobody pastes a log file.
MAX_MESSAGE = 4000
# The shortest thing that could possibly be actionable.
MIN_MESSAGE = 10


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _too_fast(user, ip):
    """Whether this sender has already said enough for now.

    Counted against whichever identity we have. An anonymous sender is held to
    their address, which is coarse behind a shared connection at a venue, so the
    minute window is short and the hourly one is generous.
    """
    now = timezone.now()
    recent = Feedback.objects.filter(created_at__gte=now - timedelta(minutes=1))
    hourly = Feedback.objects.filter(created_at__gte=now - timedelta(hours=1))
    if user is not None:
        return (recent.filter(user=user).exists()
                or hourly.filter(user=user).count() >= 20)
    if not ip:
        return False
    recent = recent.filter(page__startswith='ip:%s|' % ip)
    hourly = hourly.filter(page__startswith='ip:%s|' % ip)
    return recent.exists() or hourly.count() >= 20


def _ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


@api_view(['GET', 'POST'])
def feedback(request):
    """GET: what may be said about what. POST: say it.

    GET so the form draws its own choices from the server rather than keeping a
    second copy of the list. Two copies of a list is how a form ends up offering
    an area the database refuses.
    """
    if request.method == 'GET':
        return _ok({
            'areas': [{'value': v, 'label': label} for v, label in Feedback.AREAS],
            'kinds': [{'value': v, 'label': label} for v, label in Feedback.KINDS],
            'max_message': MAX_MESSAGE,
        })

    user = _viewer(request)
    ip = _ip(request)

    message = str(request.data.get('message') or '').strip()
    if len(message) < MIN_MESSAGE:
        return _error('Tell us a little more than that.', 'MESSAGE_TOO_SHORT',
                      field='message')
    if len(message) > MAX_MESSAGE:
        return _error('That is longer than this form takes.', 'MESSAGE_TOO_LONG',
                      field='message')

    if _too_fast(user, ip):
        return _error('You have just sent one. Give it a minute.',
                      'TOO_MANY', status.HTTP_429_TOO_MANY_REQUESTS)

    area = str(request.data.get('area') or 'other')
    if area not in dict(Feedback.AREAS):
        area = 'other'
    kind = str(request.data.get('kind') or 'broken')
    if kind not in dict(Feedback.KINDS):
        kind = 'broken'

    email = str(request.data.get('email') or '').strip()[:254]

    # Where they were, and the sender's address alongside it so an anonymous
    # rate limit has something to count. Kept in one column rather than adding
    # a second: the page is the useful half and the address only exists to stop
    # a flood.
    page = str(request.data.get('page') or '').strip()[:150]
    stored_page = ('ip:%s|%s' % (ip, page)) if ip else page

    row = Feedback.objects.create(
        user=user, email=email if not user else (email or user.email),
        area=area, kind=kind, message=message, page=stored_page[:200])

    return _ok({'id': row.id}, 'Thank you. That is logged.')

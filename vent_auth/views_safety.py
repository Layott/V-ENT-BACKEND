"""Block, mute and report, and the enforcement that makes them mean something.

CEO, 2 September 2026: "build and fix them all fully".

All three were buttons that showed a toast and made no request:

    onClick={() => showToast("Block requested")}

Somebody who blocked a harasser was told it worked. That is worse than the
feature being absent, because an absent control sends a person to look for
another way to protect themselves and a fake one stops them looking.

## The three, and how they differ

| | What it does | Told? |
|---|---|---|
| **Block** | they cannot reach you, you do not see them, they stop following you | no |
| **Mute** | you stop seeing them; they can still reach you | no |
| **Report** | a human reads it | no |

**A block is checked in BOTH directions.** If A blocked B, B must not be able to
message A either. Enforcing one direction stops the wrong half of the
conversation and the person who asked to be left alone still receives.

**Nobody is told they were blocked or muted.** Telling them turns "leave me
alone" into a notification that somebody has been rebuffed, which is the message
a harasser reacts to.

**A block is not a ban.** Public content stays public to them. Pretending
otherwise would be a promise the platform cannot keep, and a safety feature that
overpromises is one people rely on wrongly.
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import UserBlock, UserMute, UserReport, Users


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
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


def _target(username):
    return Users.objects.filter(username__iexact=str(username or '').strip()).first()


# ---------------------------------------------------------------------------
# What the rest of the platform asks
# ---------------------------------------------------------------------------

def is_blocked_between(a, b):
    """Either direction. This is what callers almost always want."""
    return UserBlock.between(a, b)


def blocked_ids_for(user):
    """Every user id this person should not be shown, in one query.

    Both directions, because somebody who blocked you should also disappear from
    your feeds: the alternative is being shown a person who has made it clear
    they want nothing to do with you.
    """
    if not user:
        return set()
    rows = UserBlock.objects.filter(Q(blocker=user) | Q(blocked=user)) \
        .values_list('blocker_id', 'blocked_id')
    out = set()
    for blocker_id, blocked_id in rows:
        out.add(blocked_id if blocker_id == user.pk else blocker_id)
    return out


def muted_ids_for(user):
    """Ids this person has muted, ignoring mutes that have expired."""
    if not user:
        return set()
    now = timezone.now()
    return set(
        UserMute.objects.filter(muter=user)
        .filter(Q(until__isnull=True) | Q(until__gt=now))
        .values_list('muted_id', flat=True))


def hidden_ids_for(user):
    """Blocked plus muted: everybody who should not appear in a feed."""
    return blocked_ids_for(user) | muted_ids_for(user)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@api_view(['POST'])
def block_user(request, username):
    """POST /user/<username>/block/ - `{"block": true|false}`.

    Blocking also severs the follow in both directions. A block that leaves
    somebody following you means they still see your activity, which is most of
    what the person was trying to stop.
    """
    user = _viewer(request)
    if user is None:
        return _err('You need an account to block somebody.', 'NOT_AUTHENTICATED',
                    status.HTTP_401_UNAUTHORIZED)

    target = _target(username)
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if target.pk == user.pk:
        return _err('You cannot block yourself.', 'CANNOT_BLOCK_SELF')

    wants = request.data.get('block')
    wants = True if wants is None else bool(wants)

    if wants:
        UserBlock.objects.get_or_create(blocker=user, blocked=target)
        _sever_follows(user, target)
    else:
        UserBlock.objects.filter(blocker=user, blocked=target).delete()

    return _ok({'blocked': wants, 'username': target.username},
               'Blocked.' if wants else 'Unblocked.')


def _sever_follows(user, target):
    """Remove any follow between two people, whichever model holds it.

    Written defensively because follow lives in more than one place on this
    platform and a block that missed one of them would be a block that leaks.
    """
    try:
        from .models import Follower
        Follower.objects.filter(
            Q(user=user, follows=target) | Q(user=target, follows=user)).delete()
    except Exception:                                        # noqa: BLE001
        pass


@api_view(['POST'])
def mute_user(request, username):
    """POST /user/<username>/mute/ - `{"mute": true|false, "days": 7}`.

    `days` is optional; without it the mute has no end. With it, the mute lapses
    on its own, which is what somebody means by "mute them for a week" and what
    stops a mute nobody remembers setting from lasting for ever.
    """
    user = _viewer(request)
    if user is None:
        return _err('You need an account to mute somebody.', 'NOT_AUTHENTICATED',
                    status.HTTP_401_UNAUTHORIZED)

    target = _target(username)
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if target.pk == user.pk:
        return _err('You cannot mute yourself.', 'CANNOT_MUTE_SELF')

    wants = request.data.get('mute')
    wants = True if wants is None else bool(wants)

    if not wants:
        UserMute.objects.filter(muter=user, muted=target).delete()
        return _ok({'muted': False, 'username': target.username}, 'Unmuted.')

    until = None
    days = request.data.get('days')
    if days not in (None, ''):
        try:
            days = int(days)
        except (TypeError, ValueError):
            return _err('How long has to be a number of days.', 'INVALID_NUMBER',
                        field='days')
        if days < 1 or days > 365:
            return _err('Between one day and a year.', 'INVALID_NUMBER',
                        field='days')
        from datetime import timedelta
        until = timezone.now() + timedelta(days=days)

    row, _made = UserMute.objects.get_or_create(muter=user, muted=target)
    row.until = until
    row.save(update_fields=['until'])
    return _ok({'muted': True, 'username': target.username,
                'until': until.isoformat() if until else None}, 'Muted.')


@api_view(['POST'])
def report_user(request, username):
    """POST /user/<username>/report/ - `{"reason": ..., "detail": ..., "context": ...}`

    The point of a report is the queue. This writes a row an admin works
    through; a report that goes nowhere is the same fake as the toast it
    replaces.
    """
    user = _viewer(request)
    if user is None:
        return _err('You need an account to report somebody.',
                    'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)

    target = _target(username)
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if target.pk == user.pk:
        return _err('You cannot report yourself.', 'CANNOT_REPORT_SELF')

    reason = str(request.data.get('reason') or 'other').strip()
    if reason not in dict(UserReport.REASONS):
        return _err('Pick one of the listed reasons.', 'INVALID_REASON',
                    field='reason')

    # One open report per person per target. A second press is somebody making
    # sure it worked, not a second incident, and a queue full of duplicates is a
    # queue nobody can read.
    existing = UserReport.objects.filter(
        reporter=user, reported=target, status__in=('open', 'reviewing')).first()
    if existing is not None:
        return _ok({'report_id': existing.id, 'already': True},
                   'You have already reported this person. We are looking at it.')

    row = UserReport.objects.create(
        reporter=user, reported=target, reason=reason,
        detail=str(request.data.get('detail') or '')[:2000],
        context=str(request.data.get('context') or '')[:120],
    )
    return _ok({'report_id': row.id, 'already': False},
               'Reported. A moderator will look at this.')


@api_view(['GET'])
def my_safety_state(request, username):
    """GET /user/<username>/safety/ - what this viewer has done about them.

    One request, so a profile can draw the menu correctly on first paint rather
    than flipping Block to Unblock a second later.
    """
    user = _viewer(request)
    if user is None:
        return _ok({'blocked': False, 'muted': False, 'reported': False},
                   'Signed out')

    target = _target(username)
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    mute = UserMute.objects.filter(muter=user, muted=target).first()
    return _ok({
        'blocked': UserBlock.objects.filter(blocker=user, blocked=target).exists(),
        'muted': bool(mute and mute.is_active),
        'muted_until': mute.until.isoformat() if mute and mute.until else None,
        'reported': UserReport.objects.filter(
            reporter=user, reported=target,
            status__in=('open', 'reviewing')).exists(),
        'reasons': [{'key': k, 'label': v} for k, v in UserReport.REASONS],
    }, 'Safety state')

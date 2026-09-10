"""Reading rooms: joining, turning the page together, and the one feed.

## Why polling and not a socket

The platform has no websocket layer in production: the studio, which is the
other thing on here that has to be live, runs on a polled feed with a cursor and
exponential backoff, and it works through nginx, through a phone changing
network, and through a backend restart. Adding Channels for this would mean a
second live path with its own failure modes, and the one that exists is proven.

So: one endpoint, `feed`, and one cursor, `ReadingRoom.version`. Everything a
participant needs since the last time they asked comes back in one answer, and
the client backs off when nothing changes. `check-pollers` fails the build for a
poller with no backoff, which is the rule this follows rather than works around.

## What the version means

It goes up when anything a participant would want to see changes: the page, a
message, an annotation, somebody joining or leaving, control changing hands. It
does NOT go up when somebody merely polls, or the feed would never settle and
the backoff would never engage.

## Voice

Signalling only. `RoomSignal` rows carry the offer, the answer and the ICE
candidates between two participants until their browsers connect directly; the
audio never touches V-ENT. Rows are deleted as they are read, so the table is a
letterbox rather than a log.
"""
from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.utils import timezone

from .models import (ReadingRoom, RoomAnnotation, RoomMember, RoomMessage,
                     RoomSignal)


class RoomError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def set_password(room, raw):
    """Hash a room password, or clear it. Never stores the password itself."""
    room.password_hash = make_password(raw) if raw else ''
    return room.password_hash


def may_join(room, user, password=''):
    """None if they may join, or a code saying why not."""
    if not room.is_open:
        return 'ROOM_CLOSED'
    if room.host_id == user.user_id:
        return None

    member = RoomMember.objects.filter(room=room, user=user).first()
    if member and member.was_removed:
        # Removed by the host. Rejoining by knowing the address would make the
        # removal decorative.
        return 'REMOVED_FROM_ROOM'

    if room.privacy == 'public':
        return None
    if room.privacy == 'password':
        if not room.password_hash:
            return None
        return None if check_password(password or '', room.password_hash) \
            else 'WRONG_PASSWORD'
    if room.privacy == 'private':
        # Invited, or already a member from an earlier session.
        if member is not None:
            return None
        if room.invites.filter(user=user, used_at__isnull=True).exists():
            return None
        return 'INVITE_ONLY'
    return 'ROOM_CLOSED'


def join(room, user, password=''):
    """Put somebody in the room, or raise with a code."""
    problem = may_join(room, user, password)
    if problem:
        raise RoomError(problem, 'You cannot join that room.')

    member, created = RoomMember.objects.get_or_create(room=room, user=user)
    if member.left_at:
        member.left_at = None
        member.save(update_fields=['left_at'])
    room.invites.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now())
    if created or member.left_at is None:
        room.bump()
    return member


def leave(room, user):
    member = RoomMember.objects.filter(room=room, user=user).first()
    if member and not member.left_at:
        member.left_at = timezone.now()
        member.save(update_fields=['left_at'])
        room.bump()
    return member


def may_turn_page(room, user):
    """Who is allowed to move everybody's view."""
    if room.control == 'anyone':
        return RoomMember.objects.filter(room=room, user=user,
                                         left_at__isnull=True,
                                         was_removed=False).exists() \
            or room.host_id == user.user_id
    driver_id = room.driver_id or room.host_id
    return driver_id == user.user_id


def turn_page(room, user, page_number):
    if not may_turn_page(room, user):
        raise RoomError('NOT_THE_DRIVER',
                        'Somebody else is turning the pages.')
    page_number = max(1, int(page_number or 1))
    room.page_number = page_number
    room.save(update_fields=['page_number'])
    room.bump()

    # Counted for the analytics the spec asks for: pages seen is what
    # "engagement" means for somebody who is reading rather than typing.
    RoomMember.objects.filter(room=room, left_at__isnull=True,
                              was_removed=False).update(
        pages_seen=F('pages_seen') + 1)
    return room.page_number


def hand_over(room, user, to_user):
    """Give the page control to somebody else. The host, or the driver."""
    driver_id = room.driver_id or room.host_id
    if user.user_id not in (room.host_id, driver_id):
        raise RoomError('NOT_YOURS', 'That is not yours to hand over.')
    if not RoomMember.objects.filter(room=room, user=to_user,
                                     left_at__isnull=True,
                                     was_removed=False).exists() \
            and to_user.user_id != room.host_id:
        raise RoomError('NOT_IN_ROOM', 'They are not in the room.')
    room.driver = to_user
    room.save(update_fields=['driver'])
    room.bump()
    return room


def remove(room, user, target):
    """The host removes somebody, and they cannot walk back in."""
    if room.host_id != user.user_id:
        raise RoomError('NOT_THE_HOST', 'Only the host can do that.')
    if target.user_id == room.host_id:
        raise RoomError('NOT_THE_HOST', 'The host cannot remove themselves.')
    member = RoomMember.objects.filter(room=room, user=target).first()
    if member is None:
        raise RoomError('NOT_IN_ROOM', 'They are not in the room.')
    member.was_removed = True
    member.left_at = timezone.now()
    member.save(update_fields=['was_removed', 'left_at'])
    if room.driver_id == target.user_id:
        room.driver = None
        room.save(update_fields=['driver'])
    room.bump()
    return member


def close(room, user):
    if room.host_id != user.user_id:
        raise RoomError('NOT_THE_HOST', 'Only the host can close the room.')
    room.closed_at = timezone.now()
    room.save(update_fields=['closed_at'])
    room.bump()
    return room


def feed(room, user, since=0):
    """Everything since `since`, in one answer.

    `version` comes back so the caller sends it next time. When it is unchanged
    the payload is small on purpose, which is what makes backing off cheap.
    """
    since = max(0, int(since or 0))

    member = RoomMember.objects.filter(room=room, user=user).first()
    if member is not None:
        # Touching it here is what makes "who is actually in the room" true
        # without asking anybody to press anything.
        member.save(update_fields=['last_seen_at'])

    messages = list(RoomMessage.objects.filter(
        room=room, message_id__gt=since).order_by('message_id')[:200])
    annotations = list(RoomAnnotation.objects.filter(
        room=room, annotation_id__gt=since,
        is_removed=False).order_by('annotation_id')[:200])

    # Signals are addressed and consumed: read once, then gone.
    signals = list(RoomSignal.objects.filter(room=room, to_user=user)
                   .order_by('signal_id')[:50])
    signal_ids = [s.signal_id for s in signals]
    if signal_ids:
        RoomSignal.objects.filter(signal_id__in=signal_ids).delete()

    return {
        'version': room.version,
        'page_number': room.page_number,
        'chapter': room.chapter.slug if room.chapter_id else None,
        'driver': (room.driver.username if room.driver_id
                   else room.host.username),
        'closed': not room.is_open,
        'messages': messages,
        'annotations': annotations,
        'signals': signals,
        'members': list(RoomMember.objects.filter(
            room=room, was_removed=False).select_related('user')),
    }


def analytics(room):
    """What the session did, from the rows it already wrote.

    Nothing here is tracked separately: duration is the room's own clock,
    engagement is who was seen recently and how much they sent, and the most
    reacted-to page is a count over the reactions. A second table of statistics
    would be a second answer to the same question.
    """
    now = timezone.now()
    ended = room.closed_at or now
    members = list(RoomMember.objects.filter(room=room).select_related('user'))

    reactions = {}
    for page, in RoomMessage.objects.filter(
            room=room, kind='reaction').values_list('page_number'):
        if page:
            reactions[page] = reactions.get(page, 0) + 1
    best = max(reactions.items(), key=lambda kv: kv[1]) if reactions else None

    return {
        'minutes': int((ended - room.created_at).total_seconds() // 60),
        'people': len(members),
        'still_here': sum(1 for m in members if m.is_active(now)),
        'messages': RoomMessage.objects.filter(room=room, kind='chat').count(),
        'reactions': RoomMessage.objects.filter(room=room,
                                                kind='reaction').count(),
        'annotations': RoomAnnotation.objects.filter(room=room,
                                                     is_removed=False).count(),
        'most_reacted_page': ({'page': best[0], 'reactions': best[1]}
                              if best else None),
        'members': [{
            'username': m.user.username,
            'joined_at': m.joined_at,
            'last_seen_at': m.last_seen_at,
            'active': m.is_active(now),
            'removed': m.was_removed,
            'pages_seen': m.pages_seen,
            'messages_sent': m.messages_sent,
        } for m in members],
    }

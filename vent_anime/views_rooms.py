"""Reading rooms, over HTTP: create, join, turn the page, talk, draw, close.

Everything a participant sees comes back from ONE endpoint, `room_feed`, keyed
on `ReadingRoom.version`. The alternative is five endpoints polled separately,
which is five times the requests and no way to be sure the page and the chat
agree about what moment it is.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request
from vent_auth.models import Users
from vent_auth.views_notifications import create_notification

from . import access, catalogue, rooms
from .models import (Chapter, ReadingRoom, RoomAnnotation, RoomInvite,
                     RoomMember, RoomMessage, RoomSignal, Series)
from .views_series import _err, _ok, _person, _viewer


def _need_user(request):
    return actor_from_request(request)


def _find_room(token):
    return ReadingRoom.objects.select_related(
        'host', 'series', 'chapter', 'driver').filter(token=token).first()


def _room_row(request, room, viewer=None):
    return {
        'token': room.token,
        'name': room.name,
        'host': _person(request, room.host),
        'series': room.series.slug,
        'series_title': room.series.title,
        'chapter': room.chapter.slug if room.chapter_id else None,
        'chapter_number': float(room.chapter.number) if room.chapter_id else None,
        'privacy': room.privacy,
        'has_password': bool(room.password_hash),
        'control': room.control,
        'driver': (room.driver.username if room.driver_id
                   else room.host.username),
        'page_number': room.page_number,
        'version': room.version,
        'open': room.is_open,
        'created_at': room.created_at,
        'people': RoomMember.objects.filter(room=room, was_removed=False,
                                            left_at__isnull=True).count(),
        'mine': bool(viewer and viewer.is_authenticated
                     and room.host_id == viewer.user_id),
        # The share address, built here so a screen never assembles a URL out
        # of an id it happens to have.
        'url': '/anime/room/%s' % room.token,
    }


@api_view(['GET', 'POST'])
def room_list(request):
    """The rooms somebody can see, or a new one."""
    viewer = _viewer(request)

    if request.method == 'POST':
        user, err = _need_user(request)
        if err:
            return err
        series = Series.objects.filter(
            slug=request.data.get('series')).first()
        if series is None:
            return _err('Which comic?', 'SERIES_REQUIRED')
        if not access.may_see_series(user, series):
            return _err('No such comic.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)

        chapter = None
        if request.data.get('chapter'):
            chapter = Chapter.objects.filter(
                slug=request.data['chapter'], series=series).first()
        if chapter is None:
            # A room with nothing open in it draws "this chapter has no pages"
            # and gives everybody in it nothing to do, which is what the walk
            # found. A room made without naming a chapter starts at the first
            # one; the host turns from there.
            chapter = series.chapters.order_by('number').first()

        privacy = request.data.get('privacy') or 'private'
        if privacy not in catalogue.ROOM_PRIVACY:
            return _err('That is not one of the privacy settings.',
                        'BAD_PRIVACY')
        control = request.data.get('control') or 'host'
        if control not in catalogue.ROOM_CONTROL:
            return _err('That is not one of the control settings.',
                        'BAD_CONTROL')

        room = ReadingRoom(host=user, name=str(
            request.data.get('name') or series.title)[:120],
            series=series, chapter=chapter, privacy=privacy, control=control)
        if privacy == 'password':
            password = str(request.data.get('password') or '')
            if not password:
                return _err('A password-protected room needs a password.',
                            'PASSWORD_REQUIRED')
            rooms.set_password(room, password)
        room.save()
        RoomMember.objects.create(room=room, user=user)
        return _ok(_room_row(request, room, user), 'Room open.',
                   status.HTTP_201_CREATED)

    qs = ReadingRoom.objects.select_related('host', 'series', 'chapter',
                                            'driver').filter(closed_at=None)
    if str(request.GET.get('mine') or '').lower() in ('1', 'true'):
        if viewer is None:
            return _err('You need to be signed in to do that.',
                        'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)
        qs = qs.filter(members__user=viewer).distinct()
    else:
        # A private room is not listed. Somebody invited reaches it by its
        # address, which is what the invitation carries.
        qs = qs.filter(privacy__in=('public', 'password'))

    return _ok({'rooms': [_room_row(request, r, viewer) for r in qs[:100]]},
               'Rooms.')


@api_view(['GET'])
def room_detail(request, token):
    viewer = _viewer(request)
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    data = _room_row(request, room, viewer)
    data['may_join'] = (rooms.may_join(room, viewer) is None
                        if viewer else False)
    data['joined'] = bool(viewer and RoomMember.objects.filter(
        room=room, user=viewer, left_at__isnull=True,
        was_removed=False).exists())
    return _ok(data, 'A room.')


@api_view(['POST'])
def room_join(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        rooms.join(room, user, password=request.data.get('password') or '')
    except rooms.RoomError as exc:
        http = (status.HTTP_403_FORBIDDEN
                if exc.code in ('INVITE_ONLY', 'REMOVED_FROM_ROOM')
                else status.HTTP_400_BAD_REQUEST)
        return _err(exc.message, exc.code, http)
    return _ok(_room_row(request, room, user), 'You are in.')


@api_view(['POST'])
def room_leave(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    rooms.leave(room, user)
    return _ok({}, 'You left.')


@api_view(['GET'])
def room_feed(request, token):
    """Everything since `since`. One request, one cursor."""
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not RoomMember.objects.filter(room=room, user=user,
                                     was_removed=False).exists() \
            and room.host_id != user.user_id:
        return _err('You are not in that room.', 'NOT_IN_ROOM',
                    status.HTTP_403_FORBIDDEN)

    raw = rooms.feed(room, user, since=request.GET.get('since') or 0)
    return _ok({
        'version': raw['version'],
        'page_number': raw['page_number'],
        'chapter': raw['chapter'],
        'driver': raw['driver'],
        'closed': raw['closed'],
        'messages': [{
            'id': m.message_id,
            'kind': m.kind,
            'body': m.body,
            'emoji': m.emoji,
            'page': m.page_number,
            'author': m.author.username,
            'created_at': m.created_at,
        } for m in raw['messages']],
        'annotations': [{
            'id': a.annotation_id,
            'kind': a.kind,
            'page': a.page_number,
            'x': a.x, 'y': a.y, 'w': a.w, 'h': a.h,
            'text': a.text,
            'path': a.path,
            'author': a.author.username,
        } for a in raw['annotations']],
        'signals': [{
            'from': s.from_user.username,
            'kind': s.kind,
            'payload': s.payload,
        } for s in raw['signals']],
        'members': [{
            'username': m.user.username,
            'active': m.is_active(),
            'host': m.user_id == room.host_id,
        } for m in raw['members']],
    }, 'Feed.')


@api_view(['POST'])
def room_page(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        page = rooms.turn_page(room, user, request.data.get('page'))
    except rooms.RoomError as exc:
        return _err(exc.message, exc.code, status.HTTP_403_FORBIDDEN)
    return _ok({'page_number': page, 'version': room.version}, 'Turned.')


@api_view(['POST'])
def room_control(request, token):
    """Hand the page control to somebody else."""
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    target = Users.objects.filter(
        username=request.data.get('username')).first()
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        rooms.hand_over(room, user, target)
    except rooms.RoomError as exc:
        return _err(exc.message, exc.code, status.HTTP_403_FORBIDDEN)
    return _ok({'driver': target.username}, 'They are driving now.')


@api_view(['POST'])
def room_chat(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    member = RoomMember.objects.filter(room=room, user=user,
                                       was_removed=False).first()
    if member is None and room.host_id != user.user_id:
        return _err('You are not in that room.', 'NOT_IN_ROOM',
                    status.HTTP_403_FORBIDDEN)

    body = str(request.data.get('body') or '').strip()
    emoji = str(request.data.get('emoji') or '').strip()
    if not body and not emoji:
        return _err('Say something.', 'BODY_REQUIRED')

    row = RoomMessage.objects.create(
        room=room, author=user,
        kind='reaction' if emoji else 'chat',
        body=body[:1000], emoji=emoji[:16],
        page_number=int(request.data.get('page') or room.page_number))
    if member is not None:
        member.messages_sent += 1
        member.save(update_fields=['messages_sent'])
    room.bump()
    return _ok({'id': row.message_id, 'version': room.version}, 'Said.',
               status.HTTP_201_CREATED)


@api_view(['POST', 'DELETE'])
def room_annotations(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not RoomMember.objects.filter(room=room, user=user,
                                     was_removed=False).exists() \
            and room.host_id != user.user_id:
        return _err('You are not in that room.', 'NOT_IN_ROOM',
                    status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        row = RoomAnnotation.objects.filter(
            room=room, annotation_id=request.data.get('id')).first()
        if row is None:
            return _err('No such annotation.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if row.author_id != user.user_id and room.host_id != user.user_id:
            return _err('That is not yours.', 'NOT_YOURS',
                        status.HTTP_403_FORBIDDEN)
        row.is_removed = True
        row.save(update_fields=['is_removed'])
        room.bump()
        return _ok({}, 'Removed.')

    kind = request.data.get('kind') or 'note'
    if kind not in catalogue.ANNOTATION_KINDS:
        return _err('That is not a kind of annotation.', 'BAD_KIND')
    if room.chapter_id is None:
        return _err('There is no chapter open in that room.', 'NO_CHAPTER')

    row = RoomAnnotation.objects.create(
        room=room, author=user, chapter=room.chapter,
        page_number=int(request.data.get('page') or room.page_number),
        kind=kind,
        x=float(request.data.get('x') or 0),
        y=float(request.data.get('y') or 0),
        w=float(request.data.get('w') or 0),
        h=float(request.data.get('h') or 0),
        text=str(request.data.get('text') or '')[:500],
        path=request.data.get('path') or [])
    room.bump()
    return _ok({'id': row.annotation_id, 'version': room.version}, 'Added.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def room_invite(request, token):
    """Invite by link, by name, or by email address."""
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if room.host_id != user.user_id:
        return _err('Only the host can invite.', 'NOT_THE_HOST',
                    status.HTTP_403_FORBIDDEN)

    username = str(request.data.get('username') or '').strip()
    email = str(request.data.get('email') or '').strip()

    target = Users.objects.filter(username=username).first() if username else None
    if username and target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    invite = RoomInvite.objects.create(room=room, invited_by=user,
                                       user=target, email=email[:254])
    if target is not None:
        try:
            create_notification(
                target, category='anime_room',
                title='%s invited you to read' % user.username,
                body=room.name, link='/anime/room/%s' % room.token)
        except Exception:                                   # noqa: BLE001
            pass
    if email:
        try:
            from vent_auth.views_helpers import send_email
            send_email(
                subject='%s invited you to read %s on V-ENT'
                        % (user.username, room.series.title),
                message='Join the room: %s/anime/room/%s'
                        % (_frontend(), room.token),
                recipient=email)
        except Exception:                                   # noqa: BLE001
            # An invitation that did not send is a missing email. Losing the
            # row as well would leave nobody able to tell.
            pass

    return _ok({'id': invite.invite_id, 'url': '/anime/room/%s' % room.token},
               'Invited.')


def _frontend():
    from django.conf import settings
    return getattr(settings, 'FRONTEND_URL', 'https://v-ent.co').rstrip('/')


@api_view(['POST'])
def room_remove(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    target = Users.objects.filter(
        username=request.data.get('username')).first()
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        rooms.remove(room, user, target)
    except rooms.RoomError as exc:
        return _err(exc.message, exc.code, status.HTTP_403_FORBIDDEN)
    return _ok({}, 'Removed from the room.')


@api_view(['POST'])
def room_close(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        rooms.close(room, user)
    except rooms.RoomError as exc:
        return _err(exc.message, exc.code, status.HTTP_403_FORBIDDEN)
    return _ok(rooms.analytics(room), 'Session closed.')


@api_view(['GET'])
def room_analytics(request, token):
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if room.host_id != user.user_id:
        return _err('Only the host sees that.', 'NOT_THE_HOST',
                    status.HTTP_403_FORBIDDEN)
    return _ok(rooms.analytics(room), 'What the session did.')


@api_view(['POST'])
def room_signal(request, token):
    """One WebRTC signalling message, addressed to one other participant.

    The audio never comes here. This is the letterbox two browsers use to find
    each other, and the rows are deleted the moment the other side reads them.
    """
    user, err = _need_user(request)
    if err:
        return err
    room = _find_room(token)
    if room is None:
        return _err('No such room.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not RoomMember.objects.filter(room=room, user=user,
                                     was_removed=False).exists() \
            and room.host_id != user.user_id:
        return _err('You are not in that room.', 'NOT_IN_ROOM',
                    status.HTTP_403_FORBIDDEN)

    target = Users.objects.filter(
        username=request.data.get('to')).first()
    if target is None:
        return _err('No such person.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    kind = str(request.data.get('kind') or '')[:12]
    if kind not in ('offer', 'answer', 'ice', 'bye'):
        return _err('That is not a signalling message.', 'BAD_SIGNAL')

    RoomSignal.objects.create(room=room, from_user=user, to_user=target,
                              kind=kind, payload=request.data.get('payload') or {})
    return _ok({}, 'Sent.')

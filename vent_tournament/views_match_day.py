"""What two players need on the day: say they are here, and find the room.

eFootball's Friend Match room and FC Mobile's quick match code are how two
people on phones actually meet in the game. Until now the platform had no
place for either, so the code went through a group chat and a no-show was the
organiser's word against a player's. Now:

  * **Check in** on the match. When the stage sets check-in minutes, the clock
    starts once both sides are known, and a side that never checks in loses
    0-3 to one that did (EA's FC Pro rule). Neither checked in is left for the
    organiser, because ejecting both is a decision, not a timer.
  * **The room**: the host side (the stage says which) or staff posts the code
    and password. Only the two sides and staff ever see it.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request

from . import match_shape, stage_engine, stage_settings
from .access import may_record_results
from .models import BracketMatch


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _match(match_id):
    return get_object_or_404(
        match_shape.select_related(BracketMatch.objects.select_related('tournament')),
        pk=match_id)


@api_view(['POST'])
def check_in(request, match_id):
    """POST /tournament/match/<id>/check-in/ - the caller's side is here."""
    user, err = actor_from_request(request)
    if err:
        return err
    match = _match(match_id)
    try:
        slot = stage_engine.check_in(match, user)
    except stage_engine.StageEngineError as exc:
        code_status = {'NOT_IN_THIS_MATCH': status.HTTP_403_FORBIDDEN,
                       'MATCH_NOT_OPEN': status.HTTP_409_CONFLICT}
        return _err('You cannot check in to this match.', exc.code,
                    code_status.get(exc.code, status.HTTP_400_BAD_REQUEST))
    match.refresh_from_db()
    return _ok({'slot': slot,
                'match': match_shape.match_row(match, private=True)}, 'Checked in.')


@api_view(['POST'])
def set_room(request, match_id):
    """POST /tournament/match/<id>/room/ - post the room code and password.

    Who may: staff always; of the two players, the side the stage names as
    host ('p1' by default, the first-listed side, as in "home"). Anybody else
    is refused, and a stranger cannot even learn whether there is a room.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    match = _match(match_id)
    staff = may_record_results(user, match.tournament)
    slot = match.participant_owned_by(user)
    if not staff and slot is None:
        return _err('This is not your match.', 'NOT_IN_THIS_MATCH',
                    status.HTTP_403_FORBIDDEN)
    host = stage_settings.of_match(match).get('room_host', 'p1')
    if not staff and host != 'p%s' % slot:
        return _err('The other side hosts the room for this match.', 'NOT_THE_HOST',
                    status.HTTP_403_FORBIDDEN)
    if match.status not in ('scheduled', 'in_progress'):
        return _err('This match is not open.', 'MATCH_NOT_OPEN', status.HTTP_409_CONFLICT)

    code = str(request.data.get('room_code') or '').strip()
    password = str(request.data.get('room_password') or '').strip()
    if not code:
        return _err('Say what the room is.', 'ROOM_CODE_REQUIRED', field='room_code')
    if len(code) > 64 or len(password) > 64:
        return _err('That is too long for a room code.', 'TOO_LONG', field='room_code')

    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        locked.room_code = code
        locked.room_password = password
        locked.save(update_fields=['room_code', 'room_password'])
    match.refresh_from_db()

    # Tell the opponent, who is the one waiting for it. Swallowed: a failed
    # notification must not lose a room code somebody just typed.
    try:
        from vent_auth.views_notifications import create_notification
        other = match.participant_2 if slot == 1 else match.participant_1
        person = other.acting_user if other else None
        if person is not None and person.user_id != user.user_id:
            create_notification(
                person.user_id, 'match',
                'Your match room is ready',
                body=match.tournament.tournament_title,
                link='/tournaments/%s' % (match.tournament.slug or match.tournament_id),
                metadata={'match_id': match.id})
    except Exception:                                           # noqa: BLE001
        pass
    return _ok({'match': match_shape.match_row(match, private=True)}, 'Room posted.')

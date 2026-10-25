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
  * **The place** (P4b): staff name the station and the wave, and neither a
    new time nor a new station may put a person or a station in two matches at
    once unless the organiser says to keep it (stations.py).
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request

from . import match_shape, stage_engine, stage_settings, stations
from .access import may_record_results
from .models import BracketMatch
from vent_auth import inputs


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _clash(found):
    """The refusal for a clash, carrying what it clashes with."""
    return Response({'status': 'error', 'data': {'clashes': found},
                     'message': 'That puts a person or a station in two matches at once.',
                     'code': 'MATCH_CLASH'}, status=status.HTTP_409_CONFLICT)


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
def set_time(request, match_id):
    """POST /tournament/match/<id>/time/ - staff move one match.

    Body: {"scheduled_at": ISO instant, "allow_clash": bool}. The automatic
    time (the break after the players' last matches) only ever fills an empty
    time, so a time set here stays. The check-in window moves with it, and both
    sides are told. A time that puts a person in two matches at once, here or
    in a tournament joined by a feed, is refused with MATCH_CLASH unless
    allow_clash says to keep it.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    match = _match(match_id)
    if not may_record_results(user, match.tournament):
        return _err('Only the people running this tournament can move a match.',
                    'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if match.status not in ('scheduled', 'in_progress'):
        return _err('This match is not open.', 'MATCH_NOT_OPEN', status.HTTP_409_CONFLICT)
    raw = inputs.read_text(request.data, 'scheduled_at', max_length=40)
    when = parse_datetime(raw) if raw else None
    if when is None or when.tzinfo is None:
        return _err('Say when the match starts.', 'MATCH_TIME_INVALID', field='scheduled_at')
    found = stations.clashes(match, when=when)
    if found and not inputs.read_bool(request.data, 'allow_clash'):
        return _clash(found)

    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        locked.scheduled_at = when
        locked.check_in_deadline = None
        locked.save(update_fields=['scheduled_at', 'check_in_deadline'])
        stage_engine.arm_check_in(locked)
    match.refresh_from_db()

    try:
        from vent_auth.views_notifications import create_notification
        for side in (match.participant_1, match.participant_2):
            person = side.acting_user if side else None
            if person is not None and person.user_id != user.user_id:
                create_notification(
                    person.user_id, 'match', 'Your match time has changed',
                    body=match.tournament.tournament_title,
                    link='/tournaments/%s' % (match.tournament.slug or match.tournament_id),
                    metadata={'match_id': match.id,
                              'scheduled_at': match.scheduled_at.isoformat()})
    except Exception:                                           # noqa: BLE001
        pass
    return _ok({'match': match_shape.match_row(match, private=True), 'clashes': found},
               'Match moved.')


@api_view(['POST'])
def set_place(request, match_id):
    """POST /tournament/match/<id>/place/ - staff name the station and the wave.

    Body: {"station": text, "wave": text, "allow_clash": bool}, each label at
    most 40 characters; a key left out is left alone and a blank one clears it.
    A station already holding another open match inside the break is refused
    with MATCH_CLASH unless allow_clash says to keep it; so is a person due
    elsewhere, which only a match with a time can show.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    match = _match(match_id)
    if not may_record_results(user, match.tournament):
        return _err('Only the people running this tournament can place a match.',
                    'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if match.status not in ('scheduled', 'in_progress'):
        return _err('This match is not open.', 'MATCH_NOT_OPEN', status.HTTP_409_CONFLICT)
    # A key sent blank clears the label, so presence decides, not emptiness.
    station = (inputs.read_text(request.data, 'station', max_length=40)
               if 'station' in request.data else match.station)
    wave = (inputs.read_text(request.data, 'wave', max_length=40)
            if 'wave' in request.data else match.wave)
    found = stations.clashes(match, station=station)
    if found and not inputs.read_bool(request.data, 'allow_clash'):
        return _clash(found)

    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        locked.station, locked.wave = station, wave
        locked.save(update_fields=['station', 'wave'])
    match.refresh_from_db()
    return _ok({'match': match_shape.match_row(match, private=True), 'clashes': found},
               'Match placed.')


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

    code = inputs.read_text(request.data, 'room_code', max_length=64)
    password = inputs.read_text(request.data, 'room_password', max_length=64)
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

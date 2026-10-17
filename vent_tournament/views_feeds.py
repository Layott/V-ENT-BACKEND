"""Feeds between tournaments (P2, design section 7).

GET is public: a qualifier says where its places go ("Top 4 go to Nigeria
Finals") and the target says where its places come from, and both are facts
anybody deciding whether to enter wants. Creating and deleting a feed belong
to somebody who manages both tournaments, asked in `feeds.check`; the target's
organiser is asked here first, like every other door on the target.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from vent_auth import inputs
from vent_auth.throttle import limited

from . import feeds, lookup, progressions
from .models import Progression
from .views_stages import _err, _find, _manager, _ok

FEED_STATUS = {
    'FEED_NOT_MANAGER': status.HTTP_403_FORBIDDEN,
    'FEED_TARGET_FULL': status.HTTP_409_CONFLICT,
    'FEED_CYCLE': status.HTTP_409_CONFLICT,
}


def _feed_err(exc):
    return _err('That feed cannot be made.', exc.code,
                FEED_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST), field=exc.field)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def tournament_feeds(request, tournament_id):
    """GET /tournament/<ref>/feeds/ - feeds into and out of it. Public.
    POST - make one into this tournament (manager of both)."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    if request.method == 'GET':
        return _ok(feeds.rows(tournament), 'Feeds')
    return _create(request, tournament)


@limited('feeds', 30)
def _create(request, tournament):
    user, err = _manager(request, tournament)
    if err:
        return err
    body = request.data
    target_stage = tournament.stages.filter(
        pk=inputs.read_int(body, 'target_stage', required=True)).first()
    if target_stage is None:
        return _err('No such stage on this tournament.', 'STAGE_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                    field='target_stage')
    source = lookup.find(inputs.read_text(body, 'source_tournament', max_length=160, required=True))
    if source is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                    field='source_tournament')
    source_stage = source.stages.filter(
        pk=inputs.read_int(body, 'source_stage', required=True)).first()
    if source_stage is None:
        return _err('No such stage on that tournament.', 'STAGE_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                    field='source_stage')
    try:
        with transaction.atomic():
            line = feeds.create(
                target_stage, source_stage, user,
                place_from=inputs.read_int(body, 'place_from', required=True, minimum=1, maximum=512),
                place_to=inputs.read_int(body, 'place_to', required=True, minimum=1, maximum=512),
                group=inputs.read_int(body, 'group', minimum=1, maximum=64),
                side=inputs.read_choice(body, 'side', progressions.SIDES, default='any'),
                fire=inputs.read_choice(body, 'fire', progressions.FIRES, default='after_preview'),
                decline_hours=inputs.read_int(body, 'decline_hours', default=48, minimum=1, maximum=720),
                on_decline=inputs.read_choice(body, 'on_decline', feeds.ON_DECLINE, default='next'))
    except feeds.FeedError as exc:
        return _feed_err(exc)
    row = next(r for r in feeds.rows(tournament)['incoming'] if r['id'] == line.id)
    return _ok({'feed': row}, 'Feed made', status.HTTP_201_CREATED)


@api_view(['DELETE'])
def delete_feed(request, tournament_id, feed_id):
    """DELETE /tournament/<ref>/feeds/<id>/ - only before it has sent anybody."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err
    line = (Progression.objects.filter(pk=feed_id, target__tournament=tournament)
            .exclude(source__tournament=tournament).first())
    if line is None:
        return _err('No such feed on this tournament.', 'FEED_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if line.fired_at is not None:
        return _err('That feed has already sent its places.', 'FEED_ALREADY_SENT',
                    status.HTTP_409_CONFLICT)
    line.delete()
    return _ok({}, 'Feed removed')


QUALIFIER_STATUS = {
    'NOT_YOURS': status.HTTP_403_FORBIDDEN,
    'FEED_NOT_MANAGER': status.HTTP_403_FORBIDDEN,
    'DECLINE_CLOSED': status.HTTP_409_CONFLICT,
    'NOTHING_WAITING': status.HTTP_409_CONFLICT,
    'ALREADY_QUALIFIED': status.HTTP_409_CONFLICT,
}


def _qualifier(tournament, registration_id):
    from .models import TournamentRegistration
    reg = (TournamentRegistration.objects.select_related('qualified_via__source', 'qualified_via__target',
                                                         'user', 'team')
           .filter(pk=registration_id, tournament=tournament).first())
    if reg is None or reg.qualified_via_id is None:
        return None, _err('No such qualifier in this tournament.', 'NOT_A_QUALIFIER',
                          status.HTTP_404_NOT_FOUND)
    return reg, None


def _edit_or_feed_err(exc):
    from . import bracket_edit
    if isinstance(exc, feeds.FeedError):
        return _err('That cannot be done to this place.', exc.code,
                    QUALIFIER_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST), field=exc.field)
    return _err('That cannot be done to this bracket now.', exc.code, status.HTTP_409_CONFLICT,
                field=getattr(exc, 'field', None))


@api_view(['POST'])
@limited('feed-decline', 10)
def decline_place(request, tournament_id, registration_id):
    """POST /tournament/<ref>/qualifiers/<id>/decline/ - the qualifier turns it down."""
    from vent_auth.actors import actor_from_request
    from . import bracket_edit
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = actor_from_request(request)
    if err:
        return err
    reg, err = _qualifier(tournament, registration_id)
    if err:
        return err
    try:
        with transaction.atomic():
            feeds.decline(reg, user)
    except (feeds.FeedError, bracket_edit.EditError) as exc:
        return _edit_or_feed_err(exc)
    return _ok({'registration_id': reg.id, 'status': 'declined'}, 'Declined')


@api_view(['POST'])
@limited('feed-pick', 30)
def pick_place(request, tournament_id, feed_id):
    """POST /tournament/<ref>/feeds/<id>/pick/ - the organiser fills a declined place."""
    from . import bracket_edit
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err
    line = (Progression.objects.select_related('source__tournament', 'target__tournament')
            .filter(pk=feed_id, target__tournament=tournament).exclude(source__tournament=tournament).first())
    if line is None:
        return _err('No such feed on this tournament.', 'FEED_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        with transaction.atomic():
            row = feeds.pick(line, inputs.read_int(request.data, 'source_registration', required=True), user)
    except (feeds.FeedError, bracket_edit.EditError) as exc:
        return _edit_or_feed_err(exc)
    return _ok({'registration_id': row['registration_id']}, 'Picked')


@api_view(['GET', 'POST'])
@limited('feed-recall', 30)
def recall_place(request, tournament_id, registration_id):
    """GET /tournament/<ref>/qualifiers/<id>/recall/ - what a recall touches.
    POST {mode: hand_on|undo, replacement?} - take the place back."""
    from . import bracket_edit
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err
    reg, err = _qualifier(tournament, registration_id)
    if err:
        return err
    try:
        if request.method == 'GET':
            return _ok(feeds.recall_preview(reg), 'Recall preview')
        with transaction.atomic():
            done = feeds.recall(
                reg, user, mode=inputs.read_choice(request.data, 'mode', ('hand_on', 'undo'), default='hand_on'),
                replacement=inputs.read_int(request.data, 'replacement'))
    except (feeds.FeedError, bracket_edit.EditError) as exc:
        return _edit_or_feed_err(exc)
    return _ok(done, 'Recalled')

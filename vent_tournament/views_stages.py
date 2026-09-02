"""Composing a tournament out of stages, and moving it from one to the next.

Reading the plan is public, because the shape of an event is the first thing
somebody deciding whether to enter wants to know and none of it is private.
Composing it and advancing it belong to the organiser.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from . import formats, stages
from .models import Tournament, TournamentStage

from . import lookup


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, **extra):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    body.update({k: v for k, v in extra.items() if v is not None})
    return Response(body, status=http_status)


def _row(stage):
    fmt = formats.get(stage.format)
    return {
        'id': stage.id,
        'order': stage.order,
        'label': stage.label,
        'format': stage.format,
        'format_label': fmt.label if fmt else stage.format,
        'advances': stage.advances,
        'groups': stage.groups,
        'rules': stage.rules,
        'status': stage.status,
        'advanced': stage.advanced,
        'completed_at': stage.completed_at,
    }


def _may_manage(user, tournament):
    return (tournament.tournament_creator_id == user.user_id
            or may_override(user, 'cancel_tournament'))


@api_view(['GET'])
@permission_classes([AllowAny])
def tournament_stages(request, tournament_id):
    """GET /tournament/<id>/stages/ - how this tournament is shaped.

    Public: the shape of an event is the first thing somebody deciding whether
    to enter wants to know, and none of it is private.
    """
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    rows = list(tournament.stages.all())
    cleaned = [
        {'format': s.format, 'label': s.label, 'advances': s.advances,
         'groups': s.groups, 'rules': s.rules}
        for s in rows
    ]
    return _ok({
        'stages': [_row(s) for s in rows],
        # An empty list is the normal case and means the tournament runs as one
        # format from start to finish, which is what almost all of them do.
        'single_format': not rows,
        'summary': stages.summary(cleaned) if cleaned else [],
        'catalogue': formats.catalogue(),
    }, 'Stages')


@api_view(['PUT'])
def set_stages(request, tournament_id):
    """PUT /tournament/<id>/stages/set/ - replace the plan, in order."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _may_manage(user, tournament):
        return _err('This is not your tournament to shape.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    # Once a stage has been played, its shape is history. Re-planning around it
    # would change what a completed stage was, which is not an edit anybody can
    # make honestly.
    if tournament.stages.exclude(status='pending').exists():
        return _err('A stage has already been played, so the plan is fixed now.',
                    'STAGES_LOCKED', status.HTTP_409_CONFLICT)

    try:
        cleaned = stages.plan(
            request.data.get('stages'),
            participants=tournament.registrations.filter(
                status__in=('pending', 'confirmed')).count() or None,
        )
    except stages.StageError as exc:
        return _err(str(exc), 'VALIDATION_FAILED',
                    field=getattr(exc, 'field', None),
                    stage_index=getattr(exc, 'index', None))

    tournament.stages.all().delete()
    for order, stage in enumerate(cleaned):
        TournamentStage.objects.create(
            tournament=tournament, order=order, label=stage['label'],
            format=stage['format'], advances=stage['advances'],
            groups=stage['groups'], rules=stage['rules'],
        )

    rows = list(tournament.stages.all())
    return _ok({'stages': [_row(s) for s in rows],
                'summary': stages.summary(cleaned)}, 'Stages saved.')


@api_view(['POST'])
def advance_stage(request, tournament_id, stage_id):
    """POST /tournament/<id>/stages/<sid>/advance/ - close a stage and carry the
    survivors into the next one.

    A decision the organiser makes, never something that happens on its own. A
    bracket that reseeds the moment the last score lands is a bracket that
    reseeds while a dispute is still open.
    """
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _may_manage(user, tournament):
        return _err('This is not your tournament to advance.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    stage = tournament.stages.filter(pk=stage_id).first()
    if stage is None:
        return _err('No such stage on this tournament.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if stage.status == 'complete':
        return _err('That stage has already been advanced.', 'ALREADY_ADVANCED',
                    status.HTTP_409_CONFLICT)

    nxt = tournament.stages.filter(order=stage.order + 1).first()
    if nxt is None:
        return _err('That is the last stage, so there is nowhere to advance to.',
                    'LAST_STAGE')

    # An open dispute is exactly the thing this endpoint must not run through.
    open_disputes = tournament.disputes.filter(
        status__in=('open', 'under_review')).count()
    if open_disputes and not request.data.get('ignore_disputes'):
        return _err(
            '%s disputes are still open on this tournament. Resolve them first, '
            'or send ignore_disputes to advance anyway.' % open_disputes,
            'DISPUTES_OPEN', status.HTTP_409_CONFLICT)

    rows = request.data.get('standings')
    if not isinstance(rows, list) or not rows:
        return _err(
            'Send the standings this stage finished on, so what was recorded is '
            'what was used.', 'VALIDATION_FAILED', field='standings')

    going_through = stages.advancing(rows, stage.advances, groups=stage.groups)
    if not going_through:
        return _err('Nobody comes out of that stage.', 'NOBODY_ADVANCES')

    stage.advanced = going_through
    stage.status = 'complete'
    stage.completed_at = timezone.now()
    stage.save(update_fields=['advanced', 'status', 'completed_at'])

    nxt.status = 'running'
    nxt.save(update_fields=['status'])

    return _ok({
        'stage': _row(stage),
        'next': _row(nxt),
        'advanced': going_through,
    }, '%s advance into %s.' % (len(going_through), nxt.label))

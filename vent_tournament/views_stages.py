"""Composing a tournament out of stages, drawing each, and moving between them.

Reading the plan, a stage's bracket and its standings is public, because the
shape of an event and how it is going are the first things somebody deciding
whether to enter wants to know, and none of it is private. Composing, drawing
and advancing belong to the organiser (and an admin), asked in `access.py` like
every other tournament door; this file used to keep its own rule, on a
different admin permission, which is how two doors come to disagree.

Refusals carry a code the screen translates, never a sentence built here.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.actors import actor_from_request

from . import formats, stage_engine, stage_settings, stages
from .access import may_manage
from .models import TournamentStage

from . import lookup


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, **extra):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    body.update({k: v for k, v in extra.items() if v is not None})
    return Response(body, status=http_status)


ENGINE_STATUS = {
    'STAGE_ALREADY_DRAWN': status.HTTP_409_CONFLICT,
    'PREVIOUS_STAGE_NOT_FINISHED': status.HTTP_409_CONFLICT,
    'ALREADY_ADVANCED': status.HTTP_409_CONFLICT,
    'STAGE_NOT_FINISHED': status.HTTP_409_CONFLICT,
    'DISPUTES_OPEN': status.HTTP_409_CONFLICT,
    'LAST_STAGE': status.HTTP_400_BAD_REQUEST,
    'NOT_ENOUGH_ENTRANTS': status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _engine_err(exc):
    return _err('That cannot be done to this stage now.', exc.code,
                ENGINE_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
                field=exc.field, detail=exc.extra or None)


def _viewer(request):
    """The signed-in viewer on a public read, or None. Never an error: a
    public page does not refuse a stranger or a stale token."""
    if not (request.headers.get('Authorization') or '').startswith('Bearer '):
        return None
    user, _err_response = actor_from_request(request)
    return user


def _row(stage):
    fmt = formats.get(stage.format)
    starts_at, ends_at, own_when = stage.effective_when()
    place_type, location, virtual_link, own_where = stage.effective_where()
    return {
        'id': stage.id,
        'order': stage.order,
        'label': stage.label,
        'format': stage.format,
        'format_label': fmt.label if fmt else stage.format,
        'advances': stage.advances,
        'groups': stage.groups,
        'rules': stage.rules,
        'settings': stage.settings or stage_settings.clean(stage.format, {}),
        'placement': stage.placement,
        'direct_entrants': stage.direct_entrants or [],
        'entrant_count': len(stage.entrants or []),
        'drawn_at': stage.drawn_at,
        'finished': stage_engine.stage_finished(stage),
        'status': stage.status,
        'advanced': stage.advanced,
        'completed_at': stage.completed_at,
        # What was SET on the stage, which is what an editor has to load back.
        'starts_at': stage.starts_at,
        'ends_at': stage.ends_at,
        'place_type': stage.place_type,
        'location': stage.location,
        'virtual_link': stage.virtual_link,
        # And what actually applies, so a page showing "when is the final" does
        # not have to work out the inheritance for itself and get it wrong on
        # one screen out of five.
        'when': {
            'starts_at': starts_at,
            'ends_at': ends_at,
            'is_its_own': own_when,
        },
        'where': {
            'place_type': place_type,
            'location': location,
            'virtual_link': virtual_link,
            'is_its_own': own_where,
        },
    }


def _find(tournament_id):
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return None, _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
    return tournament, None


def _stage(tournament, stage_id):
    stage = tournament.stages.filter(pk=stage_id).first()
    if stage is None:
        return None, _err('No such stage on this tournament.', 'STAGE_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
    return stage, None


def _manager(request, tournament):
    user, err = actor_from_request(request)
    if err:
        return None, err
    if not may_manage(user, tournament):
        return None, _err('This is not your tournament to run.', 'NOT_YOURS',
                          status.HTTP_403_FORBIDDEN)
    return user, None


@api_view(['GET'])
@permission_classes([AllowAny])
def tournament_stages(request, tournament_id):
    """GET /tournament/<ref>/stages/ - how this tournament is shaped. Public."""
    tournament, err = _find(tournament_id)
    if err:
        return err

    rows = list(tournament.stages.all())
    cleaned = [
        {'format': s.format, 'label': s.label, 'advances': s.advances,
         'groups': s.groups, 'rules': s.rules}
        for s in rows
    ]
    tournament_when = {
        'starts_at': tournament.start_date_and_time,
        'ends_at': tournament.end_date_and_time,
        'place_type': tournament.tournament_type or '',
        'location': tournament.tournament_location or '',
        'virtual_link': tournament.virtual_link or '',
    }
    game = getattr(tournament.tournament_game, 'game_title', '') if tournament.tournament_game_id else ''
    preset_key, preset = stage_settings.preset_for(game)
    viewer = _viewer(request)
    return _ok({
        'stages': [_row(s) for s in rows],
        # An empty list is the normal case and means the tournament runs as one
        # format from start to finish, which is what almost all of them do.
        'single_format': not rows,
        'summary': stages.summary(cleaned) if cleaned else [],
        'catalogue': formats.catalogue(),
        'tournament_when': tournament_when,
        # The game's preset, so the builder can offer "use the FC Mobile
        # settings" and fill each stage with them.
        'preset': ({'key': preset_key, 'modes': preset['modes'],
                    'id_label': preset['id_label'],
                    'group': stage_settings.clean('round_robin', preset['group']),
                    'knockout': stage_settings.clean('single_elimination', preset['knockout'])}
                   if preset else None),
        'can_manage': bool(viewer and may_manage(viewer, tournament)),
        'locked': any(s.drawn_at for s in rows),
    }, 'Stages')


@api_view(['PUT'])
def set_stages(request, tournament_id):
    """PUT /tournament/<ref>/stages/set/ - replace the plan, in order."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err

    # Once a stage has been drawn, its shape is history. Re-planning around it
    # would change what a played stage was, which is not an edit anybody can
    # make honestly.
    if tournament.stages.filter(drawn_at__isnull=False).exists() or \
            tournament.stages.exclude(status='pending').exists():
        return _err('A stage has already been drawn, so the plan is fixed now.',
                    'STAGES_LOCKED', status.HTTP_409_CONFLICT)
    if tournament.bracket_matches.filter(stage__isnull=True).exists():
        return _err('This tournament already has a bracket drawn as one format.',
                    'BRACKET_ALREADY_GENERATED', status.HTTP_409_CONFLICT)

    try:
        cleaned = stages.plan(
            request.data.get('stages'),
            participants=tournament.registrations.filter(
                status__in=('pending', 'confirmed')).count() or None,
        )
    except stages.StageError as exc:
        # A settings refusal arrives as a bare code (KNOCKOUT_NEEDS_A_WINNER);
        # send it AS the code so the screen translates it, rather than as a
        # message somebody would read in capitals.
        text = str(exc)
        is_code = text.replace('_', '').isalpha() and text.isupper()
        return _err('That plan cannot be saved.' if is_code else text,
                    text if is_code else 'VALIDATION_FAILED',
                    field=getattr(exc, 'field', None),
                    stage_index=getattr(exc, 'index', None))

    # An entrant seeded straight into a stage has to be one of this
    # tournament's own, and cannot be seeded into the first stage (everybody
    # starts there anyway).
    own = set(tournament.registrations.values_list('id', flat=True))
    for index, stage in enumerate(cleaned):
        if stage['direct_entrants'] and index == 0:
            return _err('Everybody already starts in the first stage.',
                        'DIRECT_INTO_FIRST_STAGE', field='direct_entrants',
                        stage_index=index)
        if any(i not in own for i in stage['direct_entrants']):
            return _err('That entrant is not in this tournament.',
                        'NOT_AN_ENTRANT', field='direct_entrants', stage_index=index)

    with transaction.atomic():
        tournament.stages.all().delete()
        for order, stage in enumerate(cleaned):
            TournamentStage.objects.create(
                tournament=tournament, order=order, label=stage['label'],
                format=stage['format'], advances=stage['advances'],
                groups=stage['groups'], rules=stage['rules'],
                starts_at=stage['starts_at'], ends_at=stage['ends_at'],
                place_type=stage['place_type'], location=stage['location'],
                virtual_link=stage['virtual_link'], settings=stage['settings'],
                placement=stage['placement'],
                direct_entrants=stage['direct_entrants'],
            )

    rows = list(tournament.stages.all())
    return _ok({'stages': [_row(s) for s in rows],
                'summary': stages.summary(cleaned)}, 'Stages saved.')


@api_view(['POST'])
def draw_stage(request, tournament_id, stage_id):
    """POST /tournament/<ref>/stages/<sid>/draw/ - draw this stage's bracket.

    The first stage draws from the confirmed registrations (seeded by the
    tournament's own seeding method, or `seed_strategy`); a later stage is
    normally drawn by advancing the one before it, and this is the door for
    drawing it again after an advance that chose not to.
    """
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    if tournament.is_draft:
        return _err('Publish the tournament before drawing it.', 'STATE_CONFLICT',
                    status.HTTP_409_CONFLICT)

    from . import options as tournament_options
    strategy = request.data.get('seed_strategy') or \
        tournament_options.clean(tournament.options)['seeding_method']
    if strategy == 'seed_field':
        strategy = 'ranked'
    try:
        with transaction.atomic():
            summary = stage_engine.draw(stage, user, strategy,
                                        request.data.get('manual_order'))
    except stage_engine.StageEngineError as exc:
        return _engine_err(exc)
    stage.refresh_from_db()
    return _ok({'stage': _row(stage), 'draw': summary}, 'Stage drawn.',
               status.HTTP_201_CREATED)


@api_view(['GET'])
def advance_preview(request, tournament_id, stage_id):
    """GET /tournament/<ref>/stages/<sid>/advance/preview/ - who would go
    through, in seed order, before the organiser commits to it."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    nxt = stage_engine.next_stage(stage)
    return _ok({
        'stage': _row(stage),
        'next': _row(nxt) if nxt else None,
        'finished': stage_engine.stage_finished(stage),
        'open_matches': stage.matches.exclude(status__in=stage_engine.TERMINAL).count(),
        'open_disputes': tournament.disputes.filter(
            status__in=('open', 'under_review'), match__stage=stage).count(),
        'advancing': stage_engine.advancing(stage) if stage.drawn_at else [],
        'standings': stage_engine.standings(stage),
    }, 'Preview')


@api_view(['POST'])
def advance_stage(request, tournament_id, stage_id):
    """POST /tournament/<ref>/stages/<sid>/advance/ - close a stage, carry the
    survivors on, and draw the next stage.

    Who goes through is worked out HERE from the stage's own matches. It used
    to be whatever standings the browser sent, so the page that happened to be
    open decided the draw. The organiser may still send `order` to reorder or
    swap, among this stage's own entrants only.

    A decision the organiser makes, never something that happens on its own.
    A bracket that reseeds the moment the last score lands is a bracket that
    reseeds while a dispute is still open.
    """
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament)
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err

    order = request.data.get('order')
    if order is not None and not isinstance(order, list):
        return _err('Send the order as a list.', 'VALIDATION_FAILED', field='order')
    try:
        with transaction.atomic():
            result = stage_engine.advance(
                stage, user, order=order,
                ignore_disputes=bool(request.data.get('ignore_disputes')),
                draw_next=request.data.get('draw_next', True) not in (False, 'false', 0, '0'))
    except stage_engine.StageEngineError as exc:
        return _engine_err(exc)

    stage.refresh_from_db()
    nxt = stage_engine.next_stage(stage)
    return _ok({
        'stage': _row(stage),
        'next': _row(nxt) if nxt else None,
        'advanced': result['advanced'],
        'draw': result['draw'],
    }, 'Advanced.')

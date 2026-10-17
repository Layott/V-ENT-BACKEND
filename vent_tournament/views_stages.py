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

from . import formats, match_shape, ranking, stage_engine, stage_settings, stages
from .access import MANAGE, may, may_manage, permissions_list, permissions_of
from .models import TournamentRegistration

from . import lookup
from vent_auth import inputs


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
        # The invited entrants by name, so the saved plan and the close list
        # can say who joins here (second bracket walk, 28 September 2026: the
        # organiser could not see an invite once the plan was saved).
        'direct_entrants_named': [
            match_shape.entrant(r) for r in TournamentRegistration.objects
            .filter(id__in=stage.direct_entrants or [])
            .select_related('user', 'team', 'squad')
        ],
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


def _lines(tournament, rows):
    """The lines between the stages (P1b). Stages made without the planner
    have none stored until the engine first runs; they are shown as the chain
    their order and advances describe, unsaved (`id` None), so the screen
    always reads the flow the engine will follow."""
    from . import progressions
    stored = list(progressions.lines_of(tournament))
    if stored:
        return [{'id': l.id, 'source': l.source_id, 'target': l.target_id, 'group': l.group,
                 'place_from': l.place_from, 'place_to': l.place_to, 'side': l.side,
                 'carry_points': l.carry_points, 'fire': l.fire, 'order': l.order,
                 'fired_at': l.fired_at, 'sent_count': len(l.sent or []),
                 # Who went through, by name, for the public Path view (P1f).
                 # Names and places only: the same facts the standings show.
                 'sent': [{'registration_id': r.get('registration_id'), 'name': r.get('name'),
                           'group': r.get('group'), 'rank': r.get('rank')}
                          for r in (l.sent or [])]} for l in stored]
    return [{'id': None, 'source': rows[l['source']].id, 'target': rows[l['target']].id,
             'group': l['group'], 'place_from': l['place_from'], 'place_to': l['place_to'],
             'side': l['side'], 'carry_points': l['carry_points'], 'fire': l['fire'],
             'order': l['order'], 'fired_at': None, 'sent_count': 0, 'sent': []}
            for l in progressions.chain(rows)]


def _played_count(tournament, stage):
    from . import bracket_edit
    return bracket_edit.played_count(tournament, stage)


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


def _manager(request, tournament, permission=MANAGE):
    """(user, error) for somebody who holds `permission` here (access.may):
    'structure' to change the plan, 'place' to draw and send people on."""
    user, err = actor_from_request(request)
    if err:
        return None, err
    if not may(user, tournament, permission):
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
        'progressions': _lines(tournament, rows),
        # An empty list is the normal case and means the tournament runs as one
        # format from start to finish, which is what almost all of them do.
        'single_format': not rows,
        'summary': stages.summary(cleaned) if cleaned else [],
        'catalogue': formats.catalogue(),
        # Whole setups a stage can start from (P3 W1): the Counter-Strike
        # Major Swiss today. Settings and scoring cleaned as the server keeps them.
        'format_presets': {
            key: {'format': p['format'],
                  'settings': stage_settings.clean(p['format'], p['settings']),
                  'rules': ranking.clean_rules(p['format'], p['rules'])}
            for key, p in stage_settings.FORMAT_PRESETS.items()},
        'tournament_when': tournament_when,
        # The game's preset, so the builder can offer "use the FC Mobile
        # settings" and fill each stage with them.
        'preset': ({'key': preset_key, 'modes': preset['modes'],
                    'id_label': preset['id_label'],
                    'group': stage_settings.clean('round_robin', preset['group']),
                    'knockout': stage_settings.clean('single_elimination', preset['knockout'])}
                   if preset else None),
        'can_manage': bool(viewer and may_manage(viewer, tournament)),
        # What this viewer may do to the plan (P7): 'structure' changes it,
        # 'place' draws stages and sends people on.
        'permissions': permissions_list(permissions_of(viewer, tournament)),
        # Whether any stage has been drawn. The plan still saves after that;
        # drawn stages keep what was played (bracket_edit.save_plan).
        'locked': any(s.drawn_at for s in rows),
        # A bracket drawn as one format, which becomes stage 1 to be planned
        # around or changed (POST stages/adopt/).
        'one_format_drawn': (not rows and tournament.bracket_matches.filter(
            stage__isnull=True).exists()),
        'finished': tournament.status in ('completed', 'cancelled')
        or tournament.completed_at is not None,
        'played': {s.id: _played_count(tournament, s) for s in rows if s.drawn_at},
    }, 'Stages')


@api_view(['PUT'])
def set_stages(request, tournament_id):
    """PUT /tournament/<ref>/stages/set/ - replace the plan, in order."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament, 'structure')
    if err:
        return err

    # A bracket drawn as one format is made the first stage before a plan is
    # put around it (POST stages/adopt/), so that every match it holds keeps
    # its stage. Saving a plan over it here would have stranded them.
    if tournament.bracket_matches.filter(stage__isnull=True).exists():
        return _err('This tournament already has a bracket drawn as one format.',
                    'BRACKET_ALREADY_GENERATED', status.HTTP_409_CONFLICT)

    # Editable at any point before the tournament ends (CEO, 12 October 2026,
    # inbox 450): stages already drawn keep what was played and change only
    # what nothing played depends on; the rest change freely. bracket_edit.py
    # holds the rules. It used to refuse the whole plan once a stage was drawn.
    from . import bracket_edit
    from .views_bracket_edit import _refused
    try:
        with transaction.atomic():
            rows = bracket_edit.save_plan(
                tournament, request.data.get('stages'), user,
                participants=tournament.registrations.filter(
                    status__in=('pending', 'confirmed')).count() or None,
                # The lines between the stages (P1b); absent means the old
                # planner's chain.
                raw_lines=request.data.get('progressions'))
    except bracket_edit.EditError as exc:
        return _refused(exc)

    cleaned = [{'format': s.format, 'label': s.label, 'advances': s.advances,
                'groups': s.groups, 'rules': s.rules} for s in rows]
    return _ok({'stages': [_row(s) for s in rows],
                'progressions': _lines(tournament, rows),
                'summary': stages.summary(cleaned) if cleaned else []}, 'Stages saved.')


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
    user, err = _manager(request, tournament, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    if tournament.is_draft:
        return _err('Publish the tournament before drawing it.', 'STATE_CONFLICT',
                    status.HTTP_409_CONFLICT)

    from . import options as tournament_options

    # The first stage takes the same check-in guard the one-format draw has:
    # entrants who never checked in are not drawn over while the window says
    # they forfeit. This door skipped it (27 September 2026).
    if stage_engine.previous_stage(stage) is None:
        window = tournament_options.check_in_state(tournament, timezone.now())
        if window and window['closed'] and window['forfeit_without_check_in']:
            missing = tournament.registrations.filter(
                status__in=('pending', 'confirmed'), checked_in_at__isnull=True).count()
            if missing and not inputs.read_bool(request.data, 'ignore_check_in'):
                return _err('Some entrants never checked in. Close check-in first so '
                            'they are forfeited, or send ignore_check_in.',
                            'CHECK_IN_OPEN', status.HTTP_409_CONFLICT,
                            detail={'missing': missing})

    strategy = inputs.read_text(request.data, 'seed_strategy', max_length=30) or \
        tournament_options.clean(tournament.options)['seeding_method']
    if strategy == 'seed_field':
        strategy = 'ranked'
    try:
        with transaction.atomic():
            summary = stage_engine.draw(stage, user, strategy,
                                        request.data.get('manual_order'),
                                        # Before the stages feeding it finish,
                                        # every seat waiting for its place (P1d).
                                        early=inputs.read_bool(request.data, 'early'))
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
    user, err = _manager(request, tournament, 'place')
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
        'open_matches': stage_engine.open_count(stage),
        'open_disputes': tournament.disputes.filter(
            status__in=('open', 'under_review'), match__stage=stage).count(),
        'advancing': stage_engine.advancing(stage) if stage.drawn_at else [],
        'standings': stage_engine.standings(stage),
        # What each line would send (P1b), so the organiser sees who goes
        # where before pressing Advance.
        'lines': _line_preview(stage),
    }, 'Preview')


def _line_preview(stage):
    from . import progressions
    out = []
    for line in progressions.outgoing(stage):
        rows = (line.sent or []) if line.fired_at else (
            progressions.sending(line) if stage.drawn_at else [])
        out.append({'id': line.id, 'target': line.target_id, 'target_label': line.target.label,
                    'group': line.group, 'place_from': line.place_from,
                    'place_to': line.place_to, 'side': line.side, 'fire': line.fire,
                    'fired_at': line.fired_at,
                    'rows': [{'registration_id': r['registration_id'], 'name': r.get('name'),
                              'handle': r.get('handle'), 'group': r.get('group'),
                              'rank': r.get('rank')} for r in rows]})
    return out


@api_view(['POST'])
def pull_line(request, tournament_id, stage_id, line_id):
    """POST /tournament/<ref>/stages/<sid>/lines/<lid>/pull/ - send a line
    that waits for the organiser to press (P1b, design 477)."""
    from . import progressions
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _manager(request, tournament, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    line = stage.outgoing.filter(pk=line_id).select_related('source', 'target').first()
    if line is None:
        return _err('No such line from this stage.', 'LINE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        with transaction.atomic():
            result = progressions.pull(line, user)
    except stage_engine.StageEngineError as exc:
        return _engine_err(exc)
    stage.refresh_from_db()
    return _ok({'stage': _row(stage), 'lines': result['lines'], 'draws': list(result['draws']),
                'waiting': result['waiting']}, 'Sent.')


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
    user, err = _manager(request, tournament, 'place')
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
                ignore_disputes=inputs.read_bool(request.data, 'ignore_disputes'),
                draw_next=inputs.read_bool(request.data, 'draw_next', default=True))
    except stage_engine.StageEngineError as exc:
        return _engine_err(exc)

    stage.refresh_from_db()
    nxt = stage_engine.next_stage(stage)
    return _ok({
        'stage': _row(stage),
        'next': _row(nxt) if nxt else None,
        'advanced': result['advanced'],
        'draw': result['draw'],
        'lines': result['lines'],
        'waiting': result['waiting'],
    }, 'Advanced.')

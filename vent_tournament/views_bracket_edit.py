"""Editing a started tournament's structure and its brackets (inbox 450, 452).

    POST /tournament/<ref>/stages/adopt/                  a one-format bracket becomes stage 1
    POST /tournament/<ref>/stages/<sid>/undraw/           undo a stage's draw
    POST /tournament/<ref>/stages/<sid>/reopen/           open a closed stage again
    POST /tournament/<ref>/stages/<sid>/reformat/         a different format, drawn again
    POST /tournament/<ref>/stages/<sid>/groups/<g>/reopen/        one finished group opens again
    POST /tournament/<ref>/stages/<sid>/rounds/<n>/delete/        the last Swiss round goes
    POST /tournament/<ref>/stages/<sid>/rounds/<n>/regenerate/    paired again without who withdrew
    POST /tournament/<ref>/bracket/swap/                  two entrants trade places
    POST /tournament/<ref>/bracket/replace/               somebody takes an entrant's place
    POST /tournament/<ref>/matches/<mid>/reopen/          take a result back off a match
    POST /tournament/<ref>/matches/<mid>/rules/           how a match is played, after the draw
    GET  /tournament/<ref>/bracket/edits/                 what was changed, by whom, when

Each door asks `access.may` for its own permission (P7): changing what a stage
is (adopt, reformat, a match's rules) is 'structure'; who sits where (undo a
draw, reopen a stage, swap, replace, the record of changes) is 'place'; taking a
result back off a match is 'report'. The organiser and an admin hold all three. The rules themselves live in `bracket_edit.py`; this file reads the
request, asks, and answers with a code the screen translates.

Every door that undoes played results also takes `preview` (body or query):
it answers what would be undone, `matches_undone`, `results_cleared` and
`entrants_moved`, and changes nothing (P4b).
"""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import inputs

from . import bracket_edit
from .models import BracketMatch
from .views_stages import _err, _find, _manager, _ok, _row, _stage

STATUS = {
    'TOURNAMENT_FINISHED': status.HTTP_409_CONFLICT,
    'PRIZES_PAID': status.HTTP_409_CONFLICT,
    'STAGE_ORDER_LOCKED': status.HTTP_409_CONFLICT,
    'DRAWN_STAGE_REMOVED': status.HTTP_409_CONFLICT,
    'STAGE_DRAWN_FIELD': status.HTTP_409_CONFLICT,
    'ALREADY_IN_STAGES': status.HTTP_409_CONFLICT,
    'NO_BRACKET_YET': status.HTTP_409_CONFLICT,
    'STAGE_NOT_DRAWN': status.HTTP_409_CONFLICT,
    'NEXT_STAGE_DRAWN': status.HTTP_409_CONFLICT,
    'DISPUTES_OPEN': status.HTTP_409_CONFLICT,
    'STAGE_HAS_RESULTS': status.HTTP_409_CONFLICT,
    'STAGE_NOT_CLOSED': status.HTTP_409_CONFLICT,
    'STAGE_CLOSED': status.HTTP_409_CONFLICT,
    'ENTRANT_HAS_PLAYED': status.HTTP_409_CONFLICT,
    'NEXT_MATCH_ALREADY_PLAYED': status.HTTP_409_CONFLICT,
    'NEXT_ROUND_ALREADY_DRAWN': status.HTTP_409_CONFLICT,
    'MATCH_ALREADY_PLAYED': status.HTTP_409_CONFLICT,
    'NOTHING_TO_REOPEN': status.HTTP_409_CONFLICT,
    'PREVIOUS_STAGE_NOT_FINISHED': status.HTTP_409_CONFLICT,
    'STAGE_ALREADY_DRAWN': status.HTTP_409_CONFLICT,
    'GROUP_NOT_FINISHED': status.HTTP_409_CONFLICT,
    'ROUND_HAS_RESULTS': status.HTTP_409_CONFLICT,
    'NOT_THE_LAST_ROUND': status.HTTP_409_CONFLICT,
    'NOTHING_TO_REGENERATE': status.HTTP_409_CONFLICT,
    'FIRST_ROUND': status.HTTP_409_CONFLICT,
    'STAGE_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'GROUP_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'ROUND_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'NOT_ENOUGH_ENTRANTS': status.HTTP_422_UNPROCESSABLE_ENTITY,
    'TOO_MANY_ENTRANTS': status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _refused(exc):
    extra = dict(exc.extra or {})
    message = extra.pop('message', None) or 'That cannot be done to this bracket now.'
    stage_index = extra.pop('stage_index', None)
    return _err(message, exc.code, STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
                field=exc.field, stage_index=stage_index, detail=extra or None)


def _run(fn):
    try:
        with transaction.atomic():
            return fn(), None
    except bracket_edit.EditError as exc:
        return None, _refused(exc)


def _preview_asked(request):
    return (inputs.read_bool(request.data, 'preview')
            or inputs.read_bool(request.query_params, 'preview'))


def _match(tournament, match_id):
    match = BracketMatch.objects.filter(pk=match_id, tournament=tournament).first()
    if match is None:
        return None, _err('No such match in this tournament.', 'MATCH_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
    return match, None


def _guarded(request, tournament_id, permission):
    tournament, err = _find(tournament_id)
    if err:
        return None, None, err
    user, err = _manager(request, tournament, permission)
    if err:
        return None, None, err
    return tournament, user, None


@api_view(['POST'])
def adopt(request, tournament_id):
    tournament, user, err = _guarded(request, tournament_id, 'structure')
    if err:
        return err
    stage, err = _run(lambda: bracket_edit.adopt(tournament, user))
    if err:
        return err
    return _ok({'stage': _row(stage)}, 'The bracket is the first stage now.', status.HTTP_201_CREATED)


@api_view(['POST'])
def undraw(request, tournament_id, stage_id):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    discard = inputs.read_bool(request.data, 'discard_results')
    preview = _preview_asked(request)
    summary, err = _run(lambda: bracket_edit.undraw(
        stage, user, discard_results=discard, preview=preview))
    if err:
        return err
    if preview:
        return _ok(summary, 'Preview.')
    stage.refresh_from_db()
    return _ok({'stage': _row(stage), **summary}, 'Draw undone.')


@api_view(['POST'])
def reopen_stage(request, tournament_id, stage_id):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    preview = _preview_asked(request)
    done, err = _run(lambda: bracket_edit.unadvance(stage, user, preview=preview))
    if err:
        return err
    if preview:
        return _ok(done, 'Preview.')
    stage.refresh_from_db()
    return _ok({'stage': _row(stage)}, 'Stage open again.')


@api_view(['POST'])
def reformat(request, tournament_id, stage_id):
    tournament, user, err = _guarded(request, tournament_id, 'structure')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    fmt_key = inputs.read_text(request.data, 'format', max_length=40)
    if not fmt_key:
        return _err('Say which format.', 'VALIDATION_FAILED', field='format')
    settings = request.data.get('settings')
    if settings is not None and not isinstance(settings, dict):
        return _err('Send the settings as a set of named values.', 'VALIDATION_FAILED',
                    field='settings')
    groups = inputs.read_int(request.data, 'groups', minimum=0) if 'groups' in request.data else None
    advances = (inputs.read_int(request.data, 'advances', minimum=0)
                if 'advances' in request.data else None)
    manual = request.data.get('manual_order')
    if manual is not None and not isinstance(manual, list):
        return _err('Send the order as a list.', 'VALIDATION_FAILED', field='manual_order')
    preview = _preview_asked(request)
    strategy = inputs.read_text(request.data, 'seed_strategy', max_length=30) or 'registration'
    if strategy == 'seed_field':
        strategy = 'ranked'
    summary, err = _run(lambda: bracket_edit.reformat(
        stage, user, fmt_key=fmt_key, settings=settings, groups=groups, advances=advances,
        seed_strategy=strategy, manual_order=manual,
        discard_results=inputs.read_bool(request.data, 'discard_results'),
        draw=inputs.read_bool(request.data, 'draw', default=True),
        preview=preview))
    if err:
        return err
    if preview:
        return _ok(summary, 'Preview.')
    stage.refresh_from_db()
    return _ok({'stage': _row(stage), 'draw': summary['draw']}, 'Format changed.')


@api_view(['POST'])
def swap(request, tournament_id):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    a = inputs.read_int(request.data, 'a', minimum=1)
    b = inputs.read_int(request.data, 'b', minimum=1)
    if not a or not b:
        return _err('Say which two entrants.', 'VALIDATION_FAILED', field='a' if not a else 'b')
    stage_id = inputs.read_int(request.data, 'stage_id', minimum=1)
    summary, err = _run(lambda: bracket_edit.swap(tournament, user, a, b, stage_id))
    if err:
        return err
    return _ok(summary, 'Swapped.')


@api_view(['POST'])
def replace(request, tournament_id):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    out = inputs.read_int(request.data, 'out', minimum=1)
    incoming = inputs.read_int(request.data, 'in', minimum=1)
    if not out or not incoming:
        return _err('Say who leaves and who comes in.', 'VALIDATION_FAILED',
                    field='out' if not out else 'in')
    stage_id = inputs.read_int(request.data, 'stage_id', minimum=1)
    summary, err = _run(lambda: bracket_edit.replace(tournament, user, out, incoming, stage_id))
    if err:
        return err
    return _ok(summary, 'Replaced.')


@api_view(['POST'])
def reopen_match(request, tournament_id, match_id):
    tournament, user, err = _guarded(request, tournament_id, 'report')
    if err:
        return err
    match, err = _match(tournament, match_id)
    if err:
        return err

    preview = _preview_asked(request)

    def go():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        return bracket_edit.reopen(locked, user, preview=preview)
    reopened, err = _run(go)
    if err:
        return err
    if preview:
        return _ok(reopened, 'Preview.')
    from .match_shape import match_row
    return _ok({'match': match_row(reopened)}, 'Result taken off.')


@api_view(['POST'])
def match_rules(request, tournament_id, match_id):
    tournament, user, err = _guarded(request, tournament_id, 'structure')
    if err:
        return err
    match, err = _match(tournament, match_id)
    if err:
        return err
    best_of = inputs.read_int(request.data, 'best_of', minimum=1) if 'best_of' in request.data else None
    legs = inputs.read_int(request.data, 'legs', minimum=1) if 'legs' in request.data else None
    draw_allowed = (inputs.read_bool(request.data, 'draw_allowed')
                    if 'draw_allowed' in request.data else None)
    whole_round = inputs.read_bool(request.data, 'whole_round')
    summary, err = _run(lambda: bracket_edit.set_rules(
        match, user, best_of=best_of, legs=legs, draw_allowed=draw_allowed,
        whole_round=whole_round))
    if err:
        return err
    return _ok(summary, 'Match rules changed.')


@api_view(['POST'])
def reopen_group(request, tournament_id, stage_id, group):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    discard = inputs.read_bool(request.data, 'discard_results')
    preview = _preview_asked(request)
    summary, err = _run(lambda: bracket_edit.reopen_group(
        stage, group, user, discard_results=discard, preview=preview))
    if err:
        return err
    if preview:
        return _ok(summary, 'Preview.')
    stage.refresh_from_db()
    return _ok({'stage': _row(stage), **summary}, 'Group open again.')


@api_view(['POST'])
def delete_round(request, tournament_id, stage_id, round_number):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    preview = _preview_asked(request)
    summary, err = _run(lambda: bracket_edit.delete_round(stage, round_number, user, preview=preview))
    if err:
        return err
    return _ok(summary, 'Preview.' if preview else 'Round deleted.')


@api_view(['POST'])
def regenerate_round(request, tournament_id, stage_id, round_number):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    stage, err = _stage(tournament, stage_id)
    if err:
        return err
    preview = _preview_asked(request)
    summary, err = _run(lambda: bracket_edit.regenerate_round(
        stage, round_number, user, preview=preview))
    if err:
        return err
    return _ok(summary, 'Preview.' if preview else 'Round paired again.')


@api_view(['GET'])
def edits(request, tournament_id):
    tournament, user, err = _guarded(request, tournament_id, 'place')
    if err:
        return err
    rows = []
    for edit in tournament.bracket_edits.select_related('user', 'stage')[:100]:
        rows.append({
            'id': edit.id,
            'kind': edit.kind,
            'stage': ({'id': edit.stage_id, 'label': edit.stage.label} if edit.stage_id else None),
            'by': (edit.user.username if edit.user_id else None),
            'at': edit.at,
            'detail': edit.detail,
        })
    return _ok({'edits': rows}, 'Edits')

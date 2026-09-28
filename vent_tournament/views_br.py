"""Battle royale doors: the lobbies page, the settings, results by hand and by
screenshot, and finishing.

Reading a battle royale is public, like every standings page: the lobbies,
each match's results and the table are what somebody deciding whether to
enter wants to see. Room codes are not: they go to the squads seated in that
lobby and to the people running it. Entering results belongs to anybody who
may record results on the tournament (organiser, admin, scorekeeper); the
shape of the stage (settings, moving a squad, adding a match, finishing)
belongs to the organiser and an admin. Both asked in `access.py`, like every
other tournament door.

Refusals carry a code the screen translates, never a sentence built here.
"""
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from vent_auth.actors import actor_from_request, may_override

from . import br_engine, br_ocr, lookup, stage_engine, stage_settings
from .access import may_manage, may_record_results
from .models import BRLobby, BRMap, BROcrJob
from .views_stages import _err, _ok, _row, _viewer


STATUS_FOR = {
    'STAGE_ALREADY_DRAWN': status.HTTP_409_CONFLICT,
    'STAGE_CLOSED': status.HTTP_409_CONFLICT,
    'LOBBY_HAS_RESULTS': status.HTTP_409_CONFLICT,
    'MATCH_HAS_RESULTS': status.HTTP_409_CONFLICT,
    'STAGE_NOT_FINISHED': status.HTTP_409_CONFLICT,
    'ALREADY_FINISHED': status.HTTP_409_CONFLICT,
    'RESULTS_ALREADY_RECORDED': status.HTTP_409_CONFLICT,
    'OCR_NOT_CONFIGURED': status.HTTP_503_SERVICE_UNAVAILABLE,
    'OCR_DAILY_LIMIT': status.HTTP_429_TOO_MANY_REQUESTS,
    'NOT_READY': status.HTTP_409_CONFLICT,
    'ALREADY_COMMITTED': status.HTTP_409_CONFLICT,
}


def _refuse(exc):
    extra = getattr(exc, 'extra', None) or None
    return _err('That cannot be done now.', exc.code,
                STATUS_FOR.get(exc.code, status.HTTP_400_BAD_REQUEST),
                field=getattr(exc, 'field', None), detail=extra)


def _find(ref):
    tournament = lookup.find(ref)
    if tournament is None:
        return None, _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
    return tournament, None


def _signed_in(request):
    user, err = actor_from_request(request)
    return user, err


def _may(request, tournament, *, manage=False):
    user, err = _signed_in(request)
    if err:
        return None, err
    allowed = may_manage(user, tournament) if manage else may_record_results(user, tournament)
    if not allowed:
        return None, _err('This is not your tournament to run.', 'NOT_YOURS',
                          status.HTTP_403_FORBIDDEN)
    return user, None


def _br_stage(tournament, stage_id):
    stage = tournament.stages.filter(pk=stage_id).first()
    if stage is None or not br_engine.is_br(stage):
        return None, _err('No such battle royale stage.', 'STAGE_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
    return stage, None


def _map(tournament, map_id):
    br_map = (BRMap.objects.select_related('lobby__stage__tournament')
              .filter(pk=map_id, lobby__stage__tournament=tournament).first())
    if br_map is None:
        return None, _err('No such match.', 'MATCH_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return br_map, None


def _payload(tournament, stage, viewer):
    may_run = bool(viewer and may_record_results(viewer, tournament))
    data = br_engine.summary(stage, viewer=viewer, may_run=may_run)
    data['stage'] = _row(stage)
    data['can_record'] = may_run
    data['can_manage'] = bool(viewer and may_manage(viewer, tournament))
    # Whether THIS viewer may save the scoring, answered by the same rule the
    # save applies, so the screen never offers a Save the server refuses
    # (walk, 28 September 2026: the organiser was told only an admin could
    # change them, under a live Save button that then answered 409).
    data['may_change_settings'] = bool(
        data['can_manage'] and stage.status != 'complete'
        and (not br_engine.has_results(stage) or may_override(viewer, 'cancel_tournament')))
    data['is_last_stage'] = stage_engine.next_stage(stage) is None
    data['tournament_finished'] = tournament.completed_at is not None
    data['ocr'] = {'available': br_ocr.available(),
                   'max_images': br_ocr.MAX_IMAGES,
                   'max_mb': br_ocr.MAX_BYTES // (1024 * 1024)}
    if may_run:
        data['ocr']['cap'] = br_ocr.daily_cap()
        data['ocr']['used'] = br_ocr.used_today(viewer)
        _attach_open_reads(stage, data)
    return data


def _attach_open_reads(stage, data):
    """Each match's read that has not been saved yet, so reopening the sheet
    picks it up instead of spending another of the day's reads. A reload
    mid-review lost the read and the count still said it was used (walk,
    28 September 2026). A read older than the last save is not offered."""
    since = timezone.now() - timezone.timedelta(hours=24)
    open_reads = {}
    for job in (BROcrJob.objects.filter(map__lobby__stage=stage, created_at__gte=since,
                                        status__in=('queued', 'reading', 'ready'))
                .select_related('map').order_by('created_at')):
        entered = job.map.entered_at
        if entered is not None and entered > job.created_at:
            continue
        open_reads[job.map_id] = job.id
    for lobby in data.get('lobbies') or []:
        for m in lobby.get('maps') or []:
            m['open_read'] = open_reads.get(m.get('id'))


@api_view(['GET'])
@permission_classes([AllowAny])
def battle_royale(request, tournament_id):
    """GET /tournament/<ref>/br/?stage=<id> - every battle royale stage of the
    tournament, and the chosen one (the one being played, by default) in full.
    Public; room codes only to the lobby's own squads and to staff."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    viewer = _viewer(request)
    stages = [s for s in tournament.stages.all().order_by('order') if br_engine.is_br(s)]
    if not stages:
        from .services.bracket import normalize_bracket_type
        is_br = normalize_bracket_type(tournament.bracket_type) == 'battle_royale'
        return _ok({
            'stages': [], 'current': None, 'is_battle_royale': is_br,
            # What a one-format tournament will be scored by, before its stage
            # exists, so the page can say it before the draw.
            'settings': br_engine.settings_from_ruleset(tournament) if is_br else None,
            'can_manage': bool(viewer and may_manage(viewer, tournament)),
        }, 'Battle royale')
    wanted = request.query_params.get('stage')
    chosen = next((s for s in stages if str(s.id) == str(wanted)), None)
    if chosen is None:
        chosen = next((s for s in stages if s.status == 'running'), None) or \
            next((s for s in reversed(stages) if s.drawn_at), None) or stages[0]
    return _ok({
        'stages': [{'id': s.id, 'label': s.label, 'order': s.order, 'status': s.status,
                    'drawn': bool(s.drawn_at)} for s in stages],
        'current': _payload(tournament, chosen, viewer),
        'is_battle_royale': True,
        'can_manage': bool(viewer and may_manage(viewer, tournament)),
    }, 'Battle royale')


@api_view(['POST'])
def ensure_stage(request, tournament_id):
    """POST /tournament/<ref>/br/ensure/ - a one-format battle royale's stage,
    made the first time the organiser opens the console."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament, manage=True)
    if err:
        return err
    stage = br_engine.ensure_stage(tournament)
    if stage is None:
        return _err('This tournament is not a battle royale.', 'NOT_BATTLE_ROYALE')
    return _ok({'stage': _row(stage)}, 'Ready.')


@api_view(['PUT'])
def set_settings(request, tournament_id, stage_id):
    """PUT /tournament/<ref>/br/<sid>/settings/ - how this stage is scored.

    Once a match has results, only an admin may change the scoring, and the
    change rescores everything already entered. Same rule as the rules
    screen: after that point the numbers are the record, not a setting.
    """
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament, manage=True)
    if err:
        return err
    stage, err = _br_stage(tournament, stage_id)
    if err:
        return err
    if stage.status == 'complete':
        return _err('This stage is finished.', 'STAGE_CLOSED', status.HTTP_409_CONFLICT)
    is_admin = may_override(user, 'cancel_tournament')
    if br_engine.has_results(stage) and not is_admin:
        return _err('Matches have been played under these settings. An admin can '
                    'still change them.', 'RESULTS_ALREADY_RECORDED',
                    status.HTTP_409_CONFLICT)
    raw = request.data.get('settings') if isinstance(request.data.get('settings'), dict) \
        else request.data
    try:
        cleaned = stage_settings.clean_battle_royale(raw)
    except stage_settings.SettingsError as exc:
        return _err('Those settings cannot be saved.', exc.code, field=exc.field)
    drawn = stage.br_lobbies.exists()
    if drawn and cleaned['lobby_size'] != br_engine.settings_of(stage)['lobby_size']:
        return _err('The lobbies are drawn; move squads between them instead.',
                    'LOBBIES_ALREADY_DRAWN', status.HTTP_409_CONFLICT, field='lobby_size')
    advances = request.data.get('advances')
    fields = ['settings']
    if advances not in (None, '') and stage_engine.next_stage(stage) is not None:
        try:
            advances = max(1, min(256, int(advances)))
        except (TypeError, ValueError):
            return _err('Say how many go through.', 'NOT_A_NUMBER', field='advances')
        fields.append('advances')
    with transaction.atomic():
        stage.settings = cleaned
        if 'advances' in fields:
            stage.advances = advances
        stage.save(update_fields=fields)
        rescored = br_engine.rescore(stage)
        br_engine.sync_to_ruleset(stage, user)
        if drawn:
            # A new match count on a drawn stage adds the matches it asks for;
            # it never removes one (that is the remove door, one at a time).
            for lobby in stage.br_lobbies.all():
                while lobby.maps.count() < cleaned['maps']:
                    br_engine.add_map(lobby)
    return _ok({'stage': _row(stage), 'rescored': rescored,
                'current': _payload(tournament, stage, user)}, 'Saved.')


@api_view(['POST'])
def move_seat(request, tournament_id, stage_id):
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament, manage=True)
    if err:
        return err
    stage, err = _br_stage(tournament, stage_id)
    if err:
        return err
    # Parsed before the move, and the move's own refusal caught first: a
    # BRError IS a ValueError, and caught the other way round every refusal
    # read "say which squad" (walk, 28 September 2026).
    try:
        registration_id = int(request.data.get('registration_id') or 0)
        lobby_number = int(request.data.get('lobby') or 0)
    except (TypeError, ValueError):
        return _err('Say which squad and which lobby.', 'VALIDATION_FAILED')
    try:
        br_engine.move_seat(stage, registration_id, lobby_number)
    except br_engine.BRError as exc:
        return _refuse(exc)
    return _ok({'current': _payload(tournament, stage, user)}, 'Moved.')


@api_view(['PATCH'])
def rename_lobby(request, tournament_id, stage_id, lobby_id):
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament, manage=True)
    if err:
        return err
    stage, err = _br_stage(tournament, stage_id)
    if err:
        return err
    lobby = BRLobby.objects.filter(pk=lobby_id, stage=stage).first()
    if lobby is None:
        return _err('No such lobby.', 'LOBBY_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    lobby.name = str(request.data.get('name') or '').strip()[:60]
    lobby.save(update_fields=['name'])
    return _ok({'current': _payload(tournament, stage, user)}, 'Saved.')


@api_view(['POST'])
def add_match(request, tournament_id, stage_id, lobby_id):
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament, manage=True)
    if err:
        return err
    stage, err = _br_stage(tournament, stage_id)
    if err:
        return err
    lobby = BRLobby.objects.filter(pk=lobby_id, stage=stage).first()
    if lobby is None:
        return _err('No such lobby.', 'LOBBY_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        br_engine.add_map(lobby)
    except br_engine.BRError as exc:
        return _refuse(exc)
    return _ok({'current': _payload(tournament, stage, user)}, 'Added.',
               status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def match_detail(request, tournament_id, map_id):
    """PATCH: the map name, the time, the room. DELETE: the last match of a
    lobby, before it has results (organiser)."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    br_map, err = _map(tournament, map_id)
    if err:
        return err
    stage = br_map.lobby.stage
    if request.method == 'DELETE':
        user, err = _may(request, tournament, manage=True)
        if err:
            return err
        try:
            br_engine.remove_map(br_map)
        except br_engine.BRError as exc:
            return _refuse(exc)
        return _ok({'current': _payload(tournament, stage, user)}, 'Removed.')

    user, err = _may(request, tournament)
    if err:
        return err
    fields = []
    for key, limit in (('map_name', 60), ('room_code', 64), ('room_password', 64)):
        if key in request.data:
            setattr(br_map, key, str(request.data.get(key) or '').strip()[:limit])
            fields.append(key)
    if 'scheduled_at' in request.data:
        raw = request.data.get('scheduled_at')
        when = parse_datetime(str(raw)) if raw else None
        if raw and when is None:
            return _err('That is not a date and time.', 'NOT_A_DATE', field='scheduled_at')
        if when is not None and timezone.is_naive(when):
            when = timezone.make_aware(when, timezone.utc)
        br_map.scheduled_at = when
        fields.append('scheduled_at')
    if fields:
        br_map.save(update_fields=fields)
    return _ok({'current': _payload(tournament, stage, user)}, 'Saved.')


@api_view(['POST', 'DELETE'])
def match_results(request, tournament_id, map_id):
    """POST: enter (or replace) a match's results by hand. DELETE: clear them."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament)
    if err:
        return err
    br_map, err = _map(tournament, map_id)
    if err:
        return err
    try:
        if request.method == 'DELETE':
            br_engine.clear_results(br_map)
        else:
            br_engine.enter_results(br_map, request.data.get('rows'), user, via='manual')
    except br_engine.BRError as exc:
        return _refuse(exc)
    stage = br_map.lobby.stage
    return _ok({'current': _payload(tournament, stage, user)},
               'Cleared.' if request.method == 'DELETE' else 'Saved.')


@api_view(['POST'])
def finish(request, tournament_id, stage_id):
    """POST /tournament/<ref>/br/<sid>/finish/ - close the last stage and write
    the final places, which is what prizes are paid from."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament, manage=True)
    if err:
        return err
    stage, err = _br_stage(tournament, stage_id)
    if err:
        return err
    try:
        with transaction.atomic():
            stage_engine.finish_last_stage(stage, user)
    except stage_engine.StageEngineError as exc:
        return _refuse(exc)
    tournament.refresh_from_db()
    stage.refresh_from_db()
    return _ok({'current': _payload(tournament, stage, user)}, 'Finished.')


# ---------------------------------------------------------------------------
# Reading results from screenshots
# ---------------------------------------------------------------------------

def _job_row(job, request_ok=True):
    return {
        'id': job.id, 'map_id': job.map_id, 'status': job.status,
        'engine': job.engine, 'error_code': job.error_code or None,
        'rows': job.rows if job.status in ('ready', 'committed') else [],
        'images': job.images.count(),
        'created_at': job.created_at, 'finished_at': job.finished_at,
    }


@api_view(['POST'])
def ocr_upload(request, tournament_id, map_id):
    """POST (multipart, `images`) /tournament/<ref>/br/matches/<mid>/read/"""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament)
    if err:
        return err
    br_map, err = _map(tournament, map_id)
    if err:
        return err
    if br_map.lobby.stage.status == 'complete':
        return _err('This stage is finished.', 'STAGE_CLOSED', status.HTTP_409_CONFLICT)
    if not br_ocr.available():
        return _err('Reading screenshots is not switched on. Type the results in.',
                    'OCR_NOT_CONFIGURED', status.HTTP_503_SERVICE_UNAVAILABLE)
    cap = br_ocr.daily_cap()
    if br_ocr.used_today(user) >= cap:
        return _err('That is today\'s limit of screenshot reads.', 'OCR_DAILY_LIMIT',
                    status.HTTP_429_TOO_MANY_REQUESTS, detail={'cap': cap})
    try:
        uploads = br_ocr.check_uploads(request.FILES.getlist('images'))
    except br_ocr.OcrError as exc:
        return _refuse(exc)
    with transaction.atomic():
        job = br_ocr.create_job(br_map, user, uploads)
    job.refresh_from_db()
    return _ok({'job': _job_row(job)}, 'Reading.', status.HTTP_202_ACCEPTED)


def _job(tournament, job_id):
    job = (BROcrJob.objects.select_related('map__lobby__stage__tournament')
           .filter(pk=job_id, map__lobby__stage__tournament=tournament).first())
    if job is None:
        return None, _err('No such read.', 'READ_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return job, None


@api_view(['GET'])
def ocr_job(request, tournament_id, job_id):
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament)
    if err:
        return err
    job, err = _job(tournament, job_id)
    if err:
        return err
    return _ok({'job': _job_row(job)}, 'Read')


@api_view(['GET'])
def ocr_image(request, tournament_id, job_id, index):
    """The screenshot itself, for the review screen. Staff only; the file lives
    outside the public media tree and is streamed from here."""
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament)
    if err:
        return err
    job, err = _job(tournament, job_id)
    if err:
        return err
    image = job.images.filter(order=index).first()
    if image is None:
        return _err('No such image.', 'IMAGE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    response = FileResponse(image.file.open('rb'), content_type=image.content_type)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@api_view(['POST'])
def ocr_commit(request, tournament_id, job_id):
    """POST /tournament/<ref>/br/reads/<jid>/commit/ {rows} - the corrected
    read, saved through the same path as typing, and every confirmed name
    remembered for next time.

    `rows` is the typing shape plus each player's `screen_name`:
    [{registration_id, placement, played, players: [{screen_name, user_id,
    name, kills, damage, assists}]}].
    """
    tournament, err = _find(tournament_id)
    if err:
        return err
    user, err = _may(request, tournament)
    if err:
        return err
    job, err = _job(tournament, job_id)
    if err:
        return err
    if job.status == 'committed':
        return _err('Already saved.', 'ALREADY_COMMITTED', status.HTTP_409_CONFLICT)
    if job.status != 'ready':
        return _err('The read is not ready.', 'NOT_READY', status.HTTP_409_CONFLICT)
    rows = request.data.get('rows')
    if not isinstance(rows, list):
        return _err('Send the rows.', 'NO_RESULTS', field='rows')
    for row in rows:
        for p in (row.get('players') or []) if isinstance(row, dict) else []:
            if isinstance(p, dict) and not p.get('name'):
                p['name'] = str(p.get('screen_name') or '')[:60]
    try:
        with transaction.atomic():
            br_engine.enter_results(job.map, rows, user, via='ocr')
            learned = br_ocr.learn_aliases(job.map, rows)
            job.status = 'committed'
            job.save(update_fields=['status'])
    except br_engine.BRError as exc:
        return _refuse(exc)
    stage = job.map.lobby.stage
    return _ok({'job': _job_row(job), 'learned': learned,
                'current': _payload(tournament, stage, user)}, 'Saved.')

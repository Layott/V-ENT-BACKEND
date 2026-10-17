"""Blueprints and recurring tournaments, the doors (P7).

    GET    /tournament/blueprints/                     mine
    POST   /tournament/blueprints/                     make one from a tournament I run
    GET    /tournament/blueprints/<ref>/               one, with the editions it made
    PATCH  /tournament/blueprints/<ref>/               change it, pause it, resume it
    DELETE /tournament/blueprints/<ref>/               delete it; its editions stay
    POST   /tournament/blueprints/<ref>/make/          make an edition now

A blueprint is its owner's alone: anybody else, and an address that names
nothing, get the same 404, so a token cannot be checked for existence (R88).
`<ref>` is the opaque token, never the primary key. The rules (when, what an
edition is, which are due) live in blueprints.py; this file reads the request,
asks, and answers with codes the screen translates.
"""
import functools

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import inputs
from vent_auth.actors import actor_from_request
from vent_auth.throttle import limited

from . import blueprints, lookup
from .models import Tournament, TournamentBlueprint

EVERY = [key for key, _label in TournamentBlueprint.EVERY]

#: The answer each refusal from blueprints.py gets.
STATUS = {
    'START_IN_PAST': status.HTTP_400_BAD_REQUEST,
    'BLUEPRINT_SOURCE_GONE': status.HTTP_409_CONFLICT,
    'BLUEPRINT_FINISHED': status.HTTP_409_CONFLICT,
    'EDITION_ALREADY_MADE': status.HTTP_409_CONFLICT,
    'NO_NEXT_EDITION': status.HTTP_409_CONFLICT,
}

MESSAGES = {
    'START_IN_PAST': 'That start has already passed. Pick a time in the future.',
    'BLUEPRINT_SOURCE_GONE': 'The tournament this blueprint copies was deleted or is no longer '
                             'yours. Point it at another tournament you run.',
    'BLUEPRINT_FINISHED': 'This blueprint has made every edition it was asked for.',
    'EDITION_ALREADY_MADE': 'That edition was just made. Refresh to see it.',
    'NO_NEXT_EDITION': 'Nothing is scheduled. Pick a start for this edition.',
}


def _limit_writes(view):
    """20 writes a minute per caller (R59: a write here can mint tournaments);
    reading a list is never refused, because the screen refreshes it."""
    guarded = limited('blueprint-write', 20)(view)

    @functools.wraps(view)
    def choose(request, *args, **kwargs):
        if request.method == 'GET':
            return view(request, *args, **kwargs)
        return guarded(request, *args, **kwargs)
    return choose


def _ok(data, message='OK', http=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _iso(value):
    return value.isoformat() if value else None


def _row(bp):
    """One blueprint, as every screen reads it. Named fields only (R71)."""
    source = bp.tournament
    return {
        'token': bp.token,
        'name': bp.name,
        'tournament': ({'slug': source.slug, 'title': source.tournament_title,
                        'gone': bool(source.deleted_at)} if source else None),
        'every': bp.every,
        'first_start': _iso(bp.first_start),
        'timezone': bp.timezone,
        'lead_days': bp.lead_days,
        'publish': bp.publish,
        'ends_after': bp.ends_after,
        'next_number': bp.next_number,
        'made': bp.made,
        'next_start': _iso(bp.next_start),
        'active': bp.active,
        'paused_reason': bp.paused_reason,
        'created_at': _iso(bp.created_at),
    }


def _editions(bp):
    rows = (Tournament.objects.filter(blueprint=bp)
            .order_by('-blueprint_edition')[:50])
    return [{'slug': t.slug, 'title': t.tournament_title, 'edition': t.blueprint_edition,
             'starts_at': _iso(t.start_date_and_time), 'status': t.status,
             'is_draft': t.is_draft} for t in rows]


def _mine(user, ref):
    """(blueprint, error). 404 alike for somebody else's and for nothing."""
    bp = (TournamentBlueprint.objects.select_related('tournament')
          .filter(token=str(ref or ''), owner=user).first())
    if bp is None:
        return None, _err('No such blueprint.', 'BLUEPRINT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return bp, None


def _own_tournament(user, ref):
    """(tournament, error) for a tournament reference the caller must own."""
    tournament = lookup.find(ref) if ref else None
    if tournament is None or getattr(tournament, 'deleted_at', None):
        return None, _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                          status.HTTP_404_NOT_FOUND, field='tournament')
    if tournament.tournament_creator_id != user.user_id:
        return None, _err('Only the organiser can run their tournament again.',
                          'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN,
                          field='tournament')
    return tournament, None


def _default_name(title):
    """The tournament's name without " (copy)" or a trailing " #12"."""
    import re
    name = re.sub(r'\s*\(copy\)\s*$', '', title or '').strip()
    return re.sub(r'\s*#\d+\s*$', '', name).strip()[:120] or 'Tournament'


def _read_schedule(data, partial):
    """The schedule fields present in `data`, read and bounded. BadInput on a bad one."""
    out = {}
    if not partial or 'name' in data:
        out['name'] = inputs.read_text(data, 'name', max_length=120)
    if not partial or 'every' in data:
        out['every'] = inputs.read_choice(data, 'every', EVERY, default='week')
    if not partial or 'first_start' in data:
        out['first_start'] = inputs.read_datetime(data, 'first_start')
    if not partial or 'timezone' in data:
        out['timezone'] = inputs.read_timezone(data, 'timezone', default='Africa/Lagos')
    if not partial or 'lead_days' in data:
        out['lead_days'] = inputs.read_int(data, 'lead_days', default=7, minimum=0, maximum=60)
    if not partial or 'publish' in data:
        out['publish'] = inputs.read_bool(data, 'publish')
    if not partial or 'ends_after' in data:
        out['ends_after'] = inputs.read_int(data, 'ends_after', minimum=1, maximum=520)
    if not partial or 'next_number' in data:
        out['next_number'] = inputs.read_int(data, 'next_number', default=1, minimum=1,
                                             maximum=100000)
    return out


def _schedule_problem(every, first_start, changed_start):
    """A refusal for a schedule that cannot run, or None."""
    if every != 'none' and first_start is None:
        return _err('Say when the first edition starts.', 'FIRST_START_REQUIRED',
                    field='first_start')
    if changed_start and first_start is not None and first_start <= timezone.now():
        return _err(MESSAGES['START_IN_PAST'], 'START_IN_PAST', field='first_start')
    return None


@api_view(['GET', 'POST'])
@_limit_writes
def blueprint_list(request):
    user, err = actor_from_request(request)
    if err:
        return err
    if request.method == 'GET':
        rows = TournamentBlueprint.objects.filter(owner=user).select_related('tournament')
        return _ok({'blueprints': [_row(bp) for bp in rows]}, 'Blueprints')

    source, err = _own_tournament(user, inputs.read_text(request.data, 'tournament', max_length=160))
    if err:
        return err
    values = _read_schedule(request.data, partial=False)
    values['name'] = values['name'] or _default_name(source.tournament_title)
    problem = _schedule_problem(values['every'], values['first_start'], changed_start=True)
    if problem:
        return problem
    bp = TournamentBlueprint.objects.create(owner=user, tournament=source, **values)
    blueprints.reschedule(bp)
    return _ok({'blueprint': _row(bp)}, 'Saved. Editions are made from it now.',
               status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@_limit_writes
def blueprint_detail(request, blueprint_ref):
    user, err = actor_from_request(request)
    if err:
        return err
    bp, err = _mine(user, blueprint_ref)
    if err:
        return err
    if request.method == 'GET':
        return _ok({'blueprint': _row(bp), 'editions': _editions(bp)}, 'Blueprint')
    if request.method == 'DELETE':
        bp.delete()
        return _ok({'deleted': True}, 'Deleted. The tournaments it made stay as they are.')

    values = _read_schedule(request.data, partial=True)
    if 'name' in values and not values['name']:
        return _err('Give it a name.', 'NAME_REQUIRED', field='name')
    if 'tournament' in request.data:
        source, err = _own_tournament(user, inputs.read_text(request.data, 'tournament',
                                                             max_length=160))
        if err:
            return err
        values['tournament'] = source
    every = values.get('every', bp.every)
    first_start = values['first_start'] if 'first_start' in values else bp.first_start
    problem = _schedule_problem(every, first_start, changed_start='first_start' in values)
    if problem:
        return problem
    active = inputs.read_bool(request.data, 'active', default=bp.active)
    for name, value in values.items():
        setattr(bp, name, value)
    if active and not bp.active:
        # Resuming: only onto a tournament that is still there and still theirs.
        if blueprints._source(bp) is None:
            return _err(MESSAGES['BLUEPRINT_SOURCE_GONE'], 'BLUEPRINT_SOURCE_GONE',
                        status.HTTP_409_CONFLICT)
        bp.paused_reason = ''
    if active and 'tournament' in values:
        bp.paused_reason = ''
    bp.active = active
    bp.save()
    if bp.active:
        blueprints.reschedule(bp)
    else:
        bp.next_start = None
        bp.save(update_fields=['next_start', 'updated_at'])
    return _ok({'blueprint': _row(bp)}, 'Saved.')


@api_view(['POST'])
@_limit_writes
def blueprint_make(request, blueprint_ref):
    """Make an edition now: the next scheduled one when no start is sent (the
    schedule moves on), or a one-off at `starts_at` (the schedule stays)."""
    user, err = actor_from_request(request)
    if err:
        return err
    bp, err = _mine(user, blueprint_ref)
    if err:
        return err
    starts_at = inputs.read_datetime(request.data, 'starts_at')
    if starts_at is None and not bp.active:
        return _err('This blueprint is paused. Resume it, or pick a start for a one-off edition.',
                    'BLUEPRINT_PAUSED', status.HTTP_409_CONFLICT)
    try:
        if starts_at is None:
            edition = blueprints.make_edition(bp, bp.next_start, scheduled=True)
        else:
            edition = blueprints.make_edition(bp, starts_at)
    except blueprints.BlueprintError as exc:
        return _err(MESSAGES.get(exc.code, 'That edition was not made.'), exc.code,
                    STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST))
    bp.refresh_from_db()
    return _ok({'tournament': {'slug': edition.slug, 'title': edition.tournament_title,
                               'edition': edition.blueprint_edition,
                               'is_draft': edition.is_draft},
                'blueprint': _row(bp)},
               'Made %s.' % edition.tournament_title, status.HTTP_201_CREATED)

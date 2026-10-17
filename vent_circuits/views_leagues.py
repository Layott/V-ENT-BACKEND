"""Leagues of division circuits, the doors (P5d). Mounted under /circuits/.

    GET    /circuits/leagues/                              published leagues; ?mine=1 my own, drafts too
    POST   /circuits/leagues/                              make one
    GET    /circuits/leagues/<ref>/                        one: its seasons and each division's ranking
                                                           with who goes up and down; ?season=<n>
    PATCH  /circuits/leagues/<ref>/                        change it, publish it
    DELETE /circuits/leagues/<ref>/                        delete it; its circuits stay, out of any league
    POST   /circuits/leagues/<ref>/divisions/              add a circuit I run as the bottom division
    PATCH  /circuits/leagues/<ref>/divisions/              put the divisions in order, top first
    DELETE /circuits/leagues/<ref>/divisions/<circuit>/    take a division out (newest season only)
    GET    /circuits/leagues/<ref>/next-season/            what ending the season would do
    POST   /circuits/leagues/<ref>/next-season/            end it: the next season, everybody moved
    POST   /circuits/<circuit>/members/                    put somebody in a division, or everybody ranked
    DELETE /circuits/<circuit>/members/<id>/               take somebody out of a division

Reads are public once a league is published; a draft answers the same 404 as
nothing, to everybody but its manager (R88). Writes are its manager's, asked
before anything in the body is read. The moves are leagues.py.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth import inputs, regions
from vent_auth.actors import actor_from_request

from . import access, leagues
from .models import Circuit, CircuitLeague, CircuitMember
from .views import (ENTRANTS, LIST_LIMIT, _circuit_row, _date, _entrant, _err, _find, _game, _limit_writes,
                    _managed, _ok, _org, _person, _standing_rows, _viewer)


# ---------------------------------------------------------------------------
# Finding things
# ---------------------------------------------------------------------------

def _find_league(ref, viewer):
    """(league, moved_to_slug, error), a draft answered like nothing (R88)."""
    from vent_auth.models_slughistory import resolve
    ref = str(ref or '').strip()
    league = CircuitLeague.objects.select_related('game', 'owner', 'organization').filter(slug=ref).first()
    if league is None and ref:
        moved_id = resolve('circuit_league', ref)
        current = CircuitLeague.objects.filter(pk=moved_id).first() if moved_id else None
        if current is not None and access.may_read_league(viewer, current):
            return None, current.slug, None
    if league is None or not access.may_read_league(viewer, league):
        return None, None, _err('No such league.', 'LEAGUE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return league, None, None


def _managed_league(request, ref):
    user, err = actor_from_request(request)
    if err:
        return None, None, err
    league, moved, err = _find_league(ref, user)
    if err or moved:
        return None, None, err or _err('No such league.', 'LEAGUE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not access.may_manage_league(user, league):
        return None, None, _err('Only the person running this league can change it.', 'NOT_YOUR_LEAGUE',
                                status.HTTP_403_FORBIDDEN)
    return user, league, None


def _league_err(exc):
    http = status.HTTP_409_CONFLICT if exc.code in (
        'CIRCUIT_IN_ANOTHER_LEAGUE', 'CIRCUIT_GAME_MISMATCH', 'CIRCUIT_ENTRANT_MISMATCH', 'ALREADY_A_DIVISION',
        'SEASON_OVER', 'ALREADY_IN_DIVISION', 'CIRCUIT_NOT_IN_LEAGUE', 'LEAGUE_NO_DIVISIONS',
        'SEASON_SAME_NAME') else status.HTTP_400_BAD_REQUEST
    return _err('That cannot be done to this league.', exc.code, http, field=exc.field, **exc.extra)


# ---------------------------------------------------------------------------
# Shapes (named fields only, R71)
# ---------------------------------------------------------------------------

def _league_row(league, request, viewer=None):
    return {
        'slug': league.slug, 'name': league.name, 'description': league.description,
        'game': _game(league.game, request), 'entrant': league.entrant, 'region': league.region,
        'up': league.up, 'down': league.down,
        'owner': _person(league.owner), 'organization': _org(league.organization, request),
        'is_published': league.is_published, 'can_manage': access.may_manage_league(viewer, league),
    }


def _division_part(circuit):
    return {'slug': circuit.slug, 'name': circuit.name, 'division': circuit.division,
            'season': circuit.season, 'is_published': circuit.is_published}


def annotate(circuit, rows):
    """Mark a division's ranking rows: where each came from (`came`) and
    whether they go up or down (`zone`). While the season runs the zone is
    where they would go if it ended now; once it has ended, where they went."""
    league = circuit.league
    came = {m.key: m.came for m in circuit.members.all()}
    following = Circuit.objects.filter(previous=circuit).first()
    went = {}
    if following is not None:
        for m in CircuitMember.objects.filter(came_from=circuit, circuit__league_season=following.league_season):
            went[m.key] = m.came if m.came in ('up', 'down') else None
    up, down = leagues.zones(league, circuit, len(rows))
    # On the first day of a season everybody is level on nothing, and the
    # name decides the order: saying "going down" then is saying it about a
    # name (P5 walk, 17 October 2026). Zones wait for a first result.
    started = any(row.get('played') or row.get('adjustments') for row in rows)
    for index, row in enumerate(rows):
        key = (row['entrant']['type'], row['entrant']['id'])
        row['came'] = came.get(key)
        if following is not None:
            row['zone'] = went.get(key)
        else:
            row['zone'] = None if not started else 'up' if index in up else 'down' if index in down else None
    return following


def circuit_league(circuit, viewer):
    """The league a division belongs to, as its circuit page shows it."""
    league = circuit.league
    if league is None or not access.may_read_league(viewer, league):
        return None
    same = leagues.divisions(league, circuit.league_season)
    following = Circuit.objects.filter(previous=circuit).first()
    return {
        'slug': league.slug, 'name': league.name, 'up': league.up, 'down': league.down,
        'division': circuit.division, 'divisions': [_division_part(c) for c in same
                                                    if c.is_published or access.may_manage(viewer, c)],
        'season_number': circuit.league_season, 'ended': following is not None,
        'previous': _division_part(circuit.previous) if circuit.previous_id else None,
        'following': _division_part(following) if following else None,
    }


def _divisions_payload(league, number, request, viewer):
    out = []
    manager = access.may_manage_league(viewer, league)
    for circuit in leagues.divisions(league, number):
        if not (circuit.is_published or manager):
            continue
        rows = _standing_rows(circuit, request)
        annotate(circuit, rows)
        links = circuit.links.filter(tournament__deleted_at__isnull=True)
        out.append({**_division_part(circuit), 'engine': circuit.engine,
                    'starts_on': circuit.starts_on.isoformat() if circuit.starts_on else None,
                    'ends_on': circuit.ends_on.isoformat() if circuit.ends_on else None,
                    'tournaments': links.count(),
                    'played': links.filter(tournament__status='completed').count(),
                    'members': circuit.members.count(),
                    'standings': rows})
    return out


def _detail(league, request, viewer, number=None):
    every = leagues.seasons(league)
    number = number or (every[-1]['number'] if every else None)
    return {
        'league': _league_row(league, request, viewer),
        'seasons': [{**s, 'starts_on': s['starts_on'].isoformat() if s['starts_on'] else None,
                     'ends_on': s['ends_on'].isoformat() if s['ends_on'] else None} for s in every],
        'season': number,
        'divisions': _divisions_payload(league, number, request, viewer) if number else [],
    }


# ---------------------------------------------------------------------------
# Reading what a write sends
# ---------------------------------------------------------------------------

def _read_league(data, user, partial):
    from vent_auth import org_link
    from vent_auth.models import Games
    out = {}
    if not partial or 'name' in data:
        out['name'] = inputs.read_text(data, 'name', max_length=120)
        if not out['name']:
            return None, _err('Give it a name.', 'NAME_REQUIRED', field='name')
    if not partial or 'description' in data:
        out['description'] = inputs.read_text(data, 'description', max_length=2000)
    if not partial:
        game_id = inputs.read_int(data, 'game', minimum=1)
        game = Games.objects.filter(game_id=game_id).first() if game_id else None
        if game_id and game is None:
            return None, _err('No such game.', 'GAME_NOT_FOUND', field='game')
        out['game'] = game
        out['entrant'] = inputs.read_choice(data, 'entrant', ENTRANTS, default='player')
    if not partial or 'region' in data:
        out['region'] = inputs.read_choice(data, 'region', [''] + list(regions.ORDER), default='') or ''
    for key in ('up', 'down'):
        if not partial or key in data:
            out[key] = inputs.read_int(data, key, default=2, minimum=0, maximum=64)
    if not partial or 'organization' in data:
        org, code = org_link.resolve(inputs.read_text(data, 'organization', max_length=160), user)
        if code:
            return None, _err('That organisation cannot run this league.', code, field='organization')
        out['organization'] = org
    if 'is_published' in data:
        out['is_published'] = inputs.read_bool(data, 'is_published')
    return out, None


# ---------------------------------------------------------------------------
# The league
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@_limit_writes
def league_list(request):
    if request.method == 'POST':
        user, err = actor_from_request(request)
        if err:
            return err
        values, err = _read_league(request.data, user, partial=False)
        if err:
            return err
        league = CircuitLeague.objects.create(owner=user, **values)
        return _ok({'league': _league_row(league, request, user)}, 'League made.', status.HTTP_201_CREATED)
    viewer = _viewer(request)
    rows = CircuitLeague.objects.select_related('game', 'owner', 'organization')
    if inputs.read_bool(request.query_params, 'mine'):
        user, err = actor_from_request(request)
        if err:
            return err
        rows, viewer = rows.filter(owner=user), user
    else:
        rows = rows.filter(is_published=True)
    query = inputs.read_text(request.query_params, 'q', max_length=80)
    if query:
        from vent_auth import fuzzy
        rows = fuzzy.filter(rows, query, ['name'])
    out = []
    for league in rows[:LIST_LIMIT]:
        row = _league_row(league, request, viewer)
        current = leagues.divisions(league)
        row['divisions'] = len(current)
        row['season'] = current[0].season if current else None
        out.append(row)
    return _ok({'leagues': out}, 'Leagues')


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([AllowAny])
@_limit_writes
def league_detail(request, league_ref):
    if request.method == 'GET':
        viewer = _viewer(request)
        league, moved, err = _find_league(league_ref, viewer)
        if moved:
            return Response({'status': 'moved', 'data': {'slug': moved, 'url': '/circuits/leagues/%s' % moved},
                             'message': 'This league has a new address.'})
        if err:
            return err
        number = inputs.read_int(request.query_params, 'season', minimum=1, maximum=1000)
        if number and not any(s['number'] == number for s in leagues.seasons(league)):
            return _err('No such season in this league.', 'SEASON_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                        field='season')
        return _ok(_detail(league, request, viewer, number), league.name)

    user, league, err = _managed_league(request, league_ref)
    if err:
        return err
    if request.method == 'DELETE':
        with transaction.atomic():
            Circuit.objects.filter(league=league).update(league=None, division=None, league_season=None)
            league.delete()
        return _ok({'deleted': True}, 'Deleted. Its circuits stay as they are.')
    values, err = _read_league(request.data, user, partial=True)
    if err:
        return err
    for name, value in values.items():
        setattr(league, name, value)
    league.save()
    return _ok(_detail(league, request, user), 'Saved.')


@api_view(['POST', 'PATCH'])
@_limit_writes
def division_list(request, league_ref):
    user, league, err = _managed_league(request, league_ref)
    if err:
        return err
    try:
        if request.method == 'PATCH':
            order = request.data.get('order') if isinstance(request.data, dict) else None
            if not isinstance(order, list) or not all(isinstance(s, str) for s in order) or len(order) > 32:
                return _err('Give the divisions in order, top first.', 'DIVISIONS_ORDER', field='order')
            with transaction.atomic():
                leagues.reorder(league, order)
            return _ok(_detail(league, request, user), 'Saved.')
        circuit, moved, err = _find(inputs.read_text(request.data, 'circuit', max_length=160, required=True), user)
        if err or moved or not access.may_manage(user, circuit):
            return _err('No such circuit of yours.', 'CIRCUIT_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                        field='circuit')
        with transaction.atomic():
            leagues.add_division(league, circuit)
    except leagues.LeagueError as exc:
        return _league_err(exc)
    return _ok(_detail(league, request, user), 'Division added.', status.HTTP_201_CREATED)


@api_view(['DELETE'])
@_limit_writes
def division_detail(request, league_ref, circuit_ref):
    user, league, err = _managed_league(request, league_ref)
    if err:
        return err
    circuit = Circuit.objects.filter(slug=str(circuit_ref or '').strip(), league=league).first()
    if circuit is None:
        return _err('No such division in this league.', 'NOT_A_DIVISION', status.HTTP_404_NOT_FOUND)
    try:
        with transaction.atomic():
            leagues.remove_division(league, circuit)
    except leagues.LeagueError as exc:
        return _league_err(exc)
    return _ok(_detail(league, request, user), 'Division taken out. The circuit stays as it is.')


def _plan_payload(league, request):
    out = []
    for m in leagues.plan(league):
        def people(rows):
            return [{'entrant': {k: r['entrant'].get(k) for k in ('type', 'id', 'name', 'handle', 'slug')},
                     'points': r['points'], 'rank': r.get('rank')} for r in rows]
        out.append({'circuit': _division_part(m['circuit']),
                    'above': _division_part(m['above']) if m['above'] else None,
                    'below': _division_part(m['below']) if m['below'] else None,
                    'up': people(m['up']), 'stay': people(m['stay']), 'down': people(m['down']),
                    'level': m['level']})
    return out


@api_view(['GET', 'POST'])
@_limit_writes
def next_season(request, league_ref):
    user, league, err = _managed_league(request, league_ref)
    if err:
        return err
    try:
        if request.method == 'GET':
            return _ok({'plan': _plan_payload(league, request)}, 'What ending the season does')
        # A date that cannot be read is answered by inputs.exception_handler.
        starts_on, ends_on = _date(request.data, 'starts_on'), _date(request.data, 'ends_on')
        made, moved = leagues.start_next(league, inputs.read_text(request.data, 'season', max_length=60),
                                         starts_on, ends_on)
    except leagues.LeagueError as exc:
        return _league_err(exc)
    return _ok({**_detail(league, request, user), 'moved': len(moved),
                'started': [_division_part(c) for c in made]}, 'Season started.', status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Who is in a division
# ---------------------------------------------------------------------------

def _member_rows(circuit):
    out = []
    for m in circuit.members.select_related('user', 'team'):
        if m.team_id:
            entrant = {'type': 'team', 'name': m.team.team_name, 'handle': None, 'slug': m.team.slug}
        else:
            entrant = {'type': 'user', 'name': m.user.full_name or m.user.username,
                       'handle': m.user.username, 'slug': m.user.username}
        out.append({'id': m.id, 'entrant': entrant, 'came': m.came})
    return out


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@_limit_writes
def member_list(request, circuit_ref):
    if request.method == 'GET':
        viewer = _viewer(request)
        circuit, moved, err = _find(circuit_ref, viewer)
        if err or moved:
            return err or _err('No such circuit.', 'CIRCUIT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
        return _ok({'members': _member_rows(circuit)}, 'Members')
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    try:
        with transaction.atomic():
            if inputs.read_bool(request.data, 'everyone'):
                added = leagues.add_everyone_ranked(circuit)
            else:
                who = _entrant(circuit, inputs.read_text(request.data, 'entrant', max_length=160, required=True))
                if who is None:
                    return _err('Nobody by that name.', 'ENTRANT_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                                field='entrant')
                key = ('team', who.team_id) if circuit.entrant == 'team' else ('user', who.user_id)
                leagues.add_member(circuit, key)
                added = 1
    except leagues.LeagueError as exc:
        return _league_err(exc)
    return _ok({'members': _member_rows(circuit), 'added': added}, 'Added.', status.HTTP_201_CREATED)


@api_view(['DELETE'])
@_limit_writes
def member_detail(request, circuit_ref, circuit_member_id):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    member = circuit.members.filter(pk=circuit_member_id).first()
    if member is None:
        return _err('Nobody like that in this division.', 'MEMBER_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    member.delete()
    return _ok({'members': _member_rows(circuit)}, 'Taken out.')

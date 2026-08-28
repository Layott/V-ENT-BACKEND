"""Admins keep the game catalogue, and the editions under each game.

The catalogue was only editable through the Django admin, so adding next year's
EA FC meant somebody with database access doing it by hand, and it landed as a
brand new unrelated game. This is the console's own way in: add a game, add its
editions, retire what nobody runs any more.

Nothing here deletes a game outright. Several models cascade from Games, so
deleting one would take the tournaments played on it with it. Retiring takes it
out of every picker and leaves the history standing.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import admin_role_required
from .models import GameMode, GameSeries, Games

# Adding a game shapes what every organiser can run, so it sits with the other
# structural powers rather than with day-to-day moderation.
GAME_ADMIN_ROLES = ['super_admin', 'mod_admin']


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message, 'code': code},
                    status=http_status)


def _game_row(game, request):
    return {
        'id': game.game_id,
        'name': game.game_title,
        'description': game.description,
        'is_active': game.is_active,
        'sort_order': game.sort_order,
        'logo': request.build_absolute_uri(game.logo.url) if game.logo else None,
        'series': [
            {
                'id': s.series_id,
                'name': s.name,
                'slug': s.slug,
                'release_year': s.release_year,
                'is_active': s.is_active,
                'sort_order': s.sort_order,
                'tournaments': s.tournaments.count(),
            }
            for s in game.series.all().order_by('sort_order', '-release_year', 'name')
        ],
        'modes': [
            {
                'id': m.mode_id,
                'name': m.name,
                'description': m.description,
                'series': m.series_id,
                'series_name': m.series.name if m.series_id else None,
                'team_size': m.team_size,
                'default_format': m.default_format,
                'default_placement_table': m.default_placement_table,
                'is_active': m.is_active,
                'sort_order': m.sort_order,
            }
            for m in game.modes.all().select_related('series')
                                     .order_by('sort_order', 'name')
        ],
        'tournaments': game.tournament_set.count() if hasattr(game, 'tournament_set') else 0,
    }


@api_view(['GET', 'POST'])
@admin_role_required(GAME_ADMIN_ROLES)
def admin_games(request):
    """GET  /auth/admin/games/  - every game, retired ones included.
       POST /auth/admin/games/  - add one.
    """
    if request.method == 'GET':
        games = Games.objects.prefetch_related('series').order_by('sort_order', 'game_title')
        return _ok({'results': [_game_row(g, request) for g in games],
                    'count': games.count()})

    name = (request.data.get('name') or '').strip()
    if not name:
        return _err('A game needs a name.', 'VALIDATION_FAILED')
    if Games.objects.filter(game_title__iexact=name).exists():
        return _err('There is already a game with that name.', 'GAME_EXISTS',
                    status.HTTP_409_CONFLICT)

    game = Games.objects.create(
        game_title=name,
        description=(request.data.get('description') or '').strip() or None,
        sort_order=int(request.data.get('sort_order') or 0),
    )
    if request.FILES.get('logo'):
        game.logo = request.FILES['logo']
        game.save(update_fields=['logo'])

    return _ok(_game_row(game, request), 'Game added.', status.HTTP_201_CREATED)


@api_view(['PATCH'])
@admin_role_required(GAME_ADMIN_ROLES)
def admin_game_detail(request, game_id):
    """PATCH /auth/admin/games/{id}/ - rename, describe, reorder, retire, restore.

    No DELETE. Games cascades into several models, so removing a row would take
    the tournaments played on it too. `is_active: false` is the retire.
    """
    game = get_object_or_404(Games, game_id=game_id)
    updated = []

    name = request.data.get('name')
    if name is not None:
        name = name.strip()
        if not name:
            return _err('A game needs a name.', 'VALIDATION_FAILED')
        clash = Games.objects.filter(game_title__iexact=name).exclude(pk=game.pk)
        if clash.exists():
            return _err('There is already a game with that name.', 'GAME_EXISTS',
                        status.HTTP_409_CONFLICT)
        game.game_title = name
        updated.append('game_title')

    if 'description' in request.data:
        game.description = (request.data.get('description') or '').strip() or None
        updated.append('description')

    if 'is_active' in request.data:
        game.is_active = str(request.data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if 'sort_order' in request.data:
        try:
            game.sort_order = int(request.data.get('sort_order') or 0)
        except (TypeError, ValueError):
            return _err('sort_order must be a number.', 'VALIDATION_FAILED')
        updated.append('sort_order')

    if request.FILES.get('logo'):
        game.logo = request.FILES['logo']
        updated.append('logo')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    game.save(update_fields=updated)
    return _ok(_game_row(game, request), 'Game updated.')


@api_view(['POST'])
@admin_role_required(GAME_ADMIN_ROLES)
def admin_game_series(request, game_id):
    """POST /auth/admin/games/{id}/series/ - add an edition to a game."""
    game = get_object_or_404(Games, game_id=game_id)

    name = (request.data.get('name') or '').strip()
    if not name:
        return _err('An edition needs a name.', 'VALIDATION_FAILED')
    if GameSeries.objects.filter(game=game, name__iexact=name).exists():
        return _err('That game already has an edition with that name.',
                    'SERIES_EXISTS', status.HTTP_409_CONFLICT)

    year = request.data.get('release_year')
    if year in ('', None):
        year = None
    else:
        try:
            year = int(year)
        except (TypeError, ValueError):
            return _err('The release year must be a number.', 'VALIDATION_FAILED')

    series = GameSeries(game=game, name=name, release_year=year,
                        sort_order=int(request.data.get('sort_order') or 0))
    series.save()
    return _ok(_game_row(game, request), 'Edition added.', status.HTTP_201_CREATED)


@api_view(['PATCH'])
@admin_role_required(GAME_ADMIN_ROLES)
def admin_series_detail(request, series_id):
    """PATCH /auth/admin/series/{id}/ - rename, reorder, retire, restore.

    No DELETE, for the same reason as a game: tournaments point at it.
    """
    series = get_object_or_404(GameSeries, series_id=series_id)
    updated = []

    name = request.data.get('name')
    if name is not None:
        name = name.strip()
        if not name:
            return _err('An edition needs a name.', 'VALIDATION_FAILED')
        clash = (GameSeries.objects
                 .filter(game=series.game, name__iexact=name)
                 .exclude(pk=series.pk))
        if clash.exists():
            return _err('That game already has an edition with that name.',
                        'SERIES_EXISTS', status.HTTP_409_CONFLICT)
        series.name = name
        updated.append('name')

    if 'release_year' in request.data:
        year = request.data.get('release_year')
        if year in ('', None):
            series.release_year = None
        else:
            try:
                series.release_year = int(year)
            except (TypeError, ValueError):
                return _err('The release year must be a number.', 'VALIDATION_FAILED')
        updated.append('release_year')

    if 'is_active' in request.data:
        series.is_active = str(request.data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if 'sort_order' in request.data:
        try:
            series.sort_order = int(request.data.get('sort_order') or 0)
        except (TypeError, ValueError):
            return _err('sort_order must be a number.', 'VALIDATION_FAILED')
        updated.append('sort_order')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    # save() recomputes the slug from the game and the edition name, and adds
    # `slug` to update_fields itself when it changed.
    series.save(update_fields=updated)
    return _ok(_game_row(series.game, request), 'Edition updated.')


@api_view(['POST'])
@admin_role_required(GAME_ADMIN_ROLES)
def admin_game_modes(request, game_id):
    """POST /auth/admin/games/{id}/modes/ - add a way this game is played.

    The modes were seeded by a migration and nothing could touch them
    afterwards, so a game added through the console arrived with no modes and
    no way to give it any - and the wizard then offered its organiser nothing
    to pick.

    `series` is optional and usually left empty: a mode with no edition applies
    to every edition of the game, which is the ordinary case. Naming one is for
    the mode an edition introduced or dropped.
    """
    game = get_object_or_404(Games, game_id=game_id)

    name = (request.data.get('name') or '').strip()
    if not name:
        return _err('A mode needs a name.', 'VALIDATION_FAILED')

    series = None
    series_id = request.data.get('series')
    if series_id not in ('', None):
        series = GameSeries.objects.filter(pk=series_id, game=game).first()
        if series is None:
            return _err('That edition does not belong to this game.',
                        'VALIDATION_FAILED')

    if GameMode.objects.filter(game=game, series=series, name__iexact=name).exists():
        return _err('That game already has a mode with that name.',
                    'MODE_EXISTS', status.HTTP_409_CONFLICT)

    try:
        team_size = int(request.data.get('team_size') or 0)
    except (TypeError, ValueError):
        return _err('The team size must be a number.', 'VALIDATION_FAILED')
    if team_size < 0 or team_size > 100:
        return _err('A team size between 0 and 100. Zero means the organiser decides.',
                    'VALIDATION_FAILED')

    GameMode.objects.create(
        game=game,
        series=series,
        name=name,
        description=(request.data.get('description') or '')[:200],
        team_size=team_size,
        default_format=(request.data.get('default_format') or '')[:40],
        default_placement_table=(request.data.get('default_placement_table') or '')[:40],
        sort_order=int(request.data.get('sort_order') or 0),
    )
    return _ok(_game_row(game, request), 'Mode added.', status.HTTP_201_CREATED)


@api_view(['PATCH'])
@admin_role_required(GAME_ADMIN_ROLES)
def admin_mode_detail(request, mode_id):
    """PATCH /auth/admin/modes/{id}/ - rename, reorder, retire, restore.

    No DELETE, for the same reason as a game and an edition: a tournament
    records the mode it was played in by name, and deleting one rewrites the
    history rather than ending it. Retiring takes it out of the wizard and
    leaves everything already run intact.
    """
    mode = get_object_or_404(GameMode, mode_id=mode_id)
    updated = []

    name = request.data.get('name')
    if name is not None:
        name = name.strip()
        if not name:
            return _err('A mode needs a name.', 'VALIDATION_FAILED')
        clash = (GameMode.objects
                 .filter(game=mode.game, series=mode.series, name__iexact=name)
                 .exclude(pk=mode.pk))
        if clash.exists():
            return _err('That game already has a mode with that name.',
                        'MODE_EXISTS', status.HTTP_409_CONFLICT)
        mode.name = name
        updated.append('name')

    if 'description' in request.data:
        mode.description = (request.data.get('description') or '')[:200]
        updated.append('description')

    if 'team_size' in request.data:
        try:
            size = int(request.data.get('team_size') or 0)
        except (TypeError, ValueError):
            return _err('The team size must be a number.', 'VALIDATION_FAILED')
        if size < 0 or size > 100:
            return _err('A team size between 0 and 100. Zero means the organiser decides.',
                        'VALIDATION_FAILED')
        mode.team_size = size
        updated.append('team_size')

    for field in ('default_format', 'default_placement_table'):
        if field in request.data:
            setattr(mode, field, (request.data.get(field) or '')[:40])
            updated.append(field)

    if 'is_active' in request.data:
        mode.is_active = str(request.data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if 'sort_order' in request.data:
        try:
            mode.sort_order = int(request.data.get('sort_order') or 0)
        except (TypeError, ValueError):
            return _err('sort_order must be a number.', 'VALIDATION_FAILED')
        updated.append('sort_order')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    mode.save(update_fields=updated)
    return _ok(_game_row(mode.game, request), 'Mode updated.')

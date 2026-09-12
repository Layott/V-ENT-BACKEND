"""What the wizard needs to ask the right questions.

Two catalogues, both public, because somebody deciding whether to run a
tournament here should be able to see what the platform supports before they
have an account.

The point of serving these rather than hardcoding them in the frontend is the
one the participant-count bug made: the rules and the copy have to come from the
same place as the logic that enforces them, or the form says "must be an even
number for single elimination" while refusing an odd number for round robin.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import GameMode, Games

from . import formats as fmt
from . import scoring
from . import structure as struct


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message})


def _as_int(raw, default=0):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _with_structure(entries, participants, seats):
    """Add what each format will actually BUILD to what it is called.

    The catalogue answered "round robin, minimum 3" and left the organiser to
    work out that twelve teams is sixty-six fixtures. `?participants=` is what
    turns the catalogue into an answer to the question somebody is actually
    asking on the wizard's format step.

    Every existing key is left alone: the edit screen reads this endpoint and
    must keep working unchanged.
    """
    out = []
    for entry in entries:
        described = struct.describe(entry['key'], participants, seats)
        merged = dict(entry)
        if described:
            merged.update({
                'advancement': described['advancement'],
                'seeding': described['seeding'],
                'plays_all_at_once': described['plays_all_at_once'],
                'seats_per_side': described['seats_per_side'],
                'drawn_as': described['drawn_as'],
                'drawn_as_differs': described['drawn_as_differs'],
                'shape': described['shape'],
            })
        out.append(merged)
    return out


@api_view(['GET'])
@permission_classes([AllowAny])
def format_catalogue(request):
    """GET /tournament/formats/ - every format, its rules and its tie-breaks.

    `?participants=16&seats=2` adds the shape each format takes for that field:
    rounds, matches, byes, and how many matches end up on the floor once a
    fixture is a tie of several. Numbers and codes only, so the screen showing
    them can be in any of the three languages.
    """
    participants = _as_int(request.GET.get('participants'))
    seats = max(1, _as_int(request.GET.get('seats'), 1))
    return _ok({
        'formats': _with_structure(fmt.catalogue(), participants, seats),
        'tiebreakers': [
            {'key': k, 'label': v} for k, v in fmt.TIEBREAKERS.items()
        ],
        'placement_tables': {
            key: {'name': key, 'points': table}
            for key, table in scoring.PLACEMENT_TABLES.items()
        },
    }, 'Tournament formats')


@api_view(['GET'])
@permission_classes([AllowAny])
def game_modes(request, game_id=None):
    """GET /tournament/games/<id>/modes/ - how this game is played.

    The wizard's Game Mode select was a fixed list, so it offered Free Fire's
    modes to somebody running EA FC. A mode belongs to a game.

    `series` narrows it further where an edition changed what is on offer.
    """
    game = Games.objects.filter(pk=game_id).first()
    if game is None:
        return Response(
            {'status': 'error', 'code': 'GAME_NOT_FOUND',
             'message': 'No such game.', 'data': None},
            status=status.HTTP_404_NOT_FOUND,
        )

    qs = GameMode.objects.filter(game=game, is_active=True)
    series_id = request.GET.get('series')
    if series_id:
        # A mode with no series applies to every edition, which is the usual
        # case; one that names a series is only offered for that edition.
        qs = qs.filter(series__isnull=True) | qs.filter(series_id=series_id)

    return _ok({
        'game': {'id': game.game_id, 'title': game.game_title},
        'modes': [
            {
                'id': m.mode_id,
                'name': m.name,
                'description': m.description,
                'team_size': m.team_size or None,
                # What this mode is normally run as, so choosing Battle Royale
                # pre-selects points scoring with the right placement table.
                'default_format': m.default_format or None,
                'default_placement_table': m.default_placement_table or None,
                'series': m.series_id,
            }
            for m in qs.distinct()
        ],
    }, 'Game modes')

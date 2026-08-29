"""The organiser's metrics, the stat lines, and the award.

PRD section 3: performance metrics specific to the game, and an MVP recorded
together with the metrics it was based on.

Who may do what:

- **Read the table**: anybody. A tournament's result is public, and an MVP that
  only its organiser can see is a trophy in a drawer.
- **Choose the metrics, record the stats, make the award**: the organiser.
  Recording a stat line decides a prize, so it is the same permission as
  entering a score, not a lighter one.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth.models import Users

from . import metrics as catalogue
from .models import (BracketMatch, MatchPlayerStat, Tournament,
                     TournamentMetric, TournamentMVP)
from .services import mvp as service


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _tournament(ref):
    if str(ref).isdigit():
        return Tournament.objects.filter(pk=int(ref)).first()
    return Tournament.objects.filter(slug=ref).first()


def _organiser(request, tournament):
    user, err = actor_from_request(request)
    if err:
        return None, err
    if tournament.tournament_creator_id == user.user_id:
        return user, None
    if may_override(user, 'cancel_tournament'):
        return user, None
    return None, _err('Only the tournament organizer can do this.',
                      'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def _serialize_metric(key, weight, position):
    definition = catalogue.get(key)
    return {
        'key': key,
        'label': definition.label if definition else key,
        'weight': weight,
        'higher_is_better': definition.higher_is_better if definition else True,
        'decimals': definition.decimals if definition else 0,
        'notes': definition.notes if definition else '',
        'position': position,
    }


@api_view(['GET', 'PUT'])
def tournament_metrics(request, tournament_id):
    """GET what is counted here. PUT to change it.

    The whole list is written at once rather than one row at a time, because
    the ORDER is a setting: it is the tiebreak, and a per-row edit cannot
    express a reordering without a second call that might not arrive.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        counted = service.counted_metrics(tournament)
        chosen = TournamentMetric.objects.filter(tournament=tournament).exists()
        return Response({'status': 'success', 'data': {
            'metrics': [_serialize_metric(key, weight, index)
                        for index, (key, weight) in enumerate(counted)],
            # Whether these are the organiser's choices or the game's defaults.
            # The screen says "we suggested these" rather than implying somebody
            # sat down and picked them.
            'is_default': not chosen,
            'catalogue': catalogue.catalogue(),
        }, 'message': ''})

    _user, err = _organiser(request, tournament)
    if err:
        return err

    rows = request.data.get('metrics')
    if not isinstance(rows, list):
        return _err('Send the metrics as a list.', 'VALIDATION_ERROR')
    if len(rows) > 12:
        return _err('Twelve metrics is already more than anybody fills in '
                    'after a match.', 'VALIDATION_ERROR')

    cleaned = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return _err('Each metric needs a key and a weight.',
                        'VALIDATION_ERROR')
        definition = catalogue.get(row.get('key'))
        if definition is None:
            return _err('There is no metric called %r.' % row.get('key'),
                        'UNKNOWN_METRIC')
        if definition.key in seen:
            return _err('%s is listed twice.' % definition.label,
                        'VALIDATION_ERROR')
        seen.add(definition.key)
        try:
            weight = float(row.get('weight', definition.default_weight))
        except (TypeError, ValueError):
            return _err('The weight for %s has to be a number.'
                        % definition.label, 'VALIDATION_ERROR')
        cleaned.append((definition.key, weight, index))

    with transaction.atomic():
        # Replaced wholesale. A metric the organiser removed should stop
        # counting, and reconciling additions against removals row by row is
        # how one of the two gets forgotten.
        TournamentMetric.objects.filter(tournament=tournament).delete()
        TournamentMetric.objects.bulk_create([
            TournamentMetric(tournament=tournament, key=key, weight=weight,
                             position=position)
            for key, weight, position in cleaned
        ])

    return Response({'status': 'success', 'data': {
        'metrics': [_serialize_metric(k, w, p) for k, w, p in cleaned],
        'is_default': False,
    }, 'message': ''})


@api_view(['GET', 'POST'])
def match_stats(request, tournament_id, match_id):
    """GET the stat lines for one match. POST to record them."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    match = BracketMatch.objects.filter(tournament=tournament,
                                        pk=match_id).first()
    if match is None:
        return _err('No such match in this tournament.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        rows = (MatchPlayerStat.objects.filter(match=match)
                .select_related('player'))
        by_player = {}
        for stat in rows:
            entry = by_player.setdefault(stat.player_id, {
                'player_id': stat.player_id,
                'username': stat.player.username,
                'stats': {},
            })
            entry['stats'][stat.key] = stat.value
        return Response({'status': 'success', 'data': {
            'players': list(by_player.values()),
        }, 'message': ''})

    _user, err = _organiser(request, tournament)
    if err:
        return err

    lines = request.data.get('players')
    if not isinstance(lines, list) or not lines:
        return _err('Send a stat line for at least one player.',
                    'VALIDATION_ERROR')

    counted = {key for key, _weight in service.counted_metrics(tournament)}
    written = 0

    with transaction.atomic():
        for line in lines:
            if not isinstance(line, dict):
                return _err('Each stat line needs a player and their numbers.',
                            'VALIDATION_ERROR')
            player = _resolve_player(line.get('player'))
            if player is None:
                return _err('There is no player called %r.'
                            % line.get('player'), 'PLAYER_NOT_FOUND',
                            status.HTTP_404_NOT_FOUND)

            registration = None
            for side in (match.participant_1, match.participant_2):
                if side is None:
                    continue
                if side.user_id == player.user_id:
                    registration = side
                    break
                if side.team_id and _in_team(side.team_id, player):
                    registration = side
                    break

            stats = line.get('stats') or {}
            if not isinstance(stats, dict):
                return _err('A stat line is a set of numbers keyed by metric.',
                            'VALIDATION_ERROR')

            for key, raw in stats.items():
                definition = catalogue.get(key)
                if definition is None:
                    return _err('There is no metric called %r.' % key,
                                'UNKNOWN_METRIC')
                if definition.key not in counted:
                    # Refused rather than stored and ignored. Somebody typing a
                    # number that will never be counted should be told at the
                    # time, not discover it when the MVP table omits it.
                    return _err('This tournament does not count %s. Add it to '
                                'the metrics first.' % definition.label,
                                'METRIC_NOT_COUNTED')
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    return _err('%s has to be a number.' % definition.label,
                                'VALIDATION_ERROR')
                MatchPlayerStat.objects.update_or_create(
                    match=match, player=player, key=definition.key,
                    defaults={'value': value, 'registration': registration})
                written += 1

    return Response({'status': 'success', 'data': {
        'recorded': written,
    }, 'message': ''}, status=status.HTTP_201_CREATED)


def _resolve_player(ref):
    """A user by id or by username. Organisers type names, screens send ids."""
    if ref in (None, ''):
        return None
    if isinstance(ref, int) or str(ref).isdigit():
        return Users.objects.filter(pk=int(ref)).first()
    return Users.objects.filter(username=str(ref)).first()


def _in_team(team_id, player):
    from vent_auth.models import TeamMembers
    return TeamMembers.objects.filter(team_id=team_id, user=player).exists()


@api_view(['GET'])
def mvp_table(request, tournament_id):
    """The ranking, and the award if one has been made. Public."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    counted = service.counted_metrics(tournament)
    award = TournamentMVP.objects.filter(tournament=tournament).select_related(
        'player').first()

    return Response({'status': 'success', 'data': {
        'metrics': [_serialize_metric(k, w, i)
                    for i, (k, w) in enumerate(counted)],
        'table': service.table(tournament),
        'award': {
            'player_id': award.player_id,
            'username': award.player.username,
            'score': award.score,
            'overridden': award.overridden,
            'reason': award.reason,
            'decided_at': award.decided_at,
        } if award else None,
    }, 'message': ''})


@api_view(['POST'])
def award_mvp(request, tournament_id):
    """Make the award. Defaults to whoever the arithmetic says.

    An override is allowed and is recorded AS an override, with the reason.
    "The numbers said X and the organiser chose Y" is a fact somebody will ask
    about, and losing it makes a defensible decision look arbitrary.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    user, err = _organiser(request, tournament)
    if err:
        return err

    top = service.leader(tournament)
    chosen = request.data.get('player')

    if chosen in (None, ''):
        if top is None:
            return _err('No stats have been recorded, so there is nothing to '
                        'work from yet.', 'NO_STATS')
        player = Users.objects.filter(pk=top['player_id']).first()
        score = top['score']
        overridden = False
    else:
        player = _resolve_player(chosen)
        if player is None:
            return _err('There is no player called %r.' % chosen,
                        'PLAYER_NOT_FOUND', status.HTTP_404_NOT_FOUND)
        row = next((r for r in service.table(tournament)
                    if r['player_id'] == player.user_id), None)
        score = row['score'] if row else 0
        overridden = top is None or player.user_id != top['player_id']

    reason = str(request.data.get('reason') or '').strip()[:300]
    if overridden and not reason:
        return _err('Say why, when the award does not go to the top of the '
                    'table. The reason is what makes it a decision rather '
                    'than a surprise.', 'REASON_REQUIRED')

    award, _made = TournamentMVP.objects.update_or_create(
        tournament=tournament,
        defaults={'player': player, 'score': score, 'overridden': overridden,
                  'reason': reason, 'decided_by': user})

    return Response({'status': 'success', 'data': {
        'award': {
            'player_id': award.player_id,
            'username': award.player.username,
            'score': award.score,
            'overridden': award.overridden,
            'reason': award.reason,
        },
    }, 'message': ''}, status=status.HTTP_201_CREATED)

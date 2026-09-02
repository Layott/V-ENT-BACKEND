"""An organiser editing their own rules.

Read the rules, change any of them, put the order of the tie-breakers where they
want it, and reset back to the preset if they have made a mess.

Only the organiser, or an admin with the permission that overrules them. And
only while it still means anything: rewriting the points table after results
have been recorded silently restates every standing, which is the sort of thing
that ends a league, so it is refused once a result exists unless an admin is
doing it deliberately.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from . import formats as fmt
from . import rules as rules_mod
from .models import BracketMatch, Tournament, TournamentRuleset


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _ruleset_for(tournament):
    """The tournament's rules, built from its format the first time they are
    asked for. A tournament created before any of this existed still answers."""
    existing = TournamentRuleset.objects.filter(tournament=tournament).first()
    if existing is not None and existing.data:
        return existing
    data = rules_mod.preset_for(tournament.bracket_type)
    if existing is None:
        existing = TournamentRuleset.objects.create(tournament=tournament, data=data)
    else:
        existing.data = data
        existing.save(update_fields=['data'])
    return existing


def _payload(ruleset, tournament):
    definition = fmt.get(ruleset.format_key)
    return {
        'tournament': tournament.tournament_id,
        'rules': ruleset.data,
        'format': {
            'key': definition.key,
            'label': definition.label,
            'summary': definition.summary,
            'notes': definition.notes,
        } if definition else None,
        # What the editor should offer THIS tournament, so the frontend never
        # carries its own copy of the list and never drifts from what is
        # accepted.
        #
        # It used to send all thirteen to everybody, so an EA FC organiser was
        # asked to choose between "Goals scored" and "Total kills" in the same
        # row. Each format already declared the ones it uses and nothing read
        # them; the game narrows it further, because a football game has no
        # idea what a kill is.
        'available_tiebreakers': [
            {'key': k, 'label': fmt.TIEBREAKERS[k]}
            for k in fmt.tiebreakers_for(
                tournament.bracket_type,
                tournament.tournament_game.game_title
                if tournament.tournament_game else None)
        ],
        # The whole catalogue as well, for an organiser who wants something
        # unusual. Offered separately rather than mixed in, so the ordinary
        # list stays short and right.
        'all_tiebreakers': [
            {'key': k, 'label': v} for k, v in fmt.TIEBREAKERS.items()
        ],
        'placement_presets': rules_mod.PLACEMENT_PRESETS,
        'scoring_methods': sorted(rules_mod.scoring.METHODS),
        'locked': _has_results(tournament),
    }


def _has_results(tournament):
    """Whether anything has been played. Changing the points table after that
    restates every standing without touching a single result."""
    return BracketMatch.objects.filter(
        tournament=tournament, status='completed').exists()


@api_view(['GET'])
@permission_classes([AllowAny])
def tournament_rules(request, tournament_id):
    """GET /tournament/<id>/rules/ - the rules this tournament is played by.

    Public, because a player deciding whether to enter should be able to read
    what a win is worth before they pay an entry fee.
    """
    tournament = Tournament.objects.filter(pk=tournament_id).first()
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    return _ok(_payload(_ruleset_for(tournament), tournament), 'Tournament rules')


@api_view(['PUT'])
def set_tournament_rules(request, tournament_id):
    """PUT /tournament/<id>/rules/ - change them."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = Tournament.objects.filter(pk=tournament_id).first()
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    is_owner = tournament.tournament_creator_id == user.user_id
    is_admin = may_override(user, 'cancel_tournament')
    if not (is_owner or is_admin):
        return _err('These are not your tournament\'s rules to change.',
                    'NOT_YOURS', status.HTTP_403_FORBIDDEN)

    # Once something has been played, the numbers behind the table stop being a
    # setting and start being the record of what happened.
    if _has_results(tournament) and not is_admin:
        return _err(
            'Matches have already been played under these rules. An admin can '
            'still change them, and the change is recorded.',
            'RESULTS_ALREADY_RECORDED', status.HTTP_409_CONFLICT)

    try:
        cleaned = rules_mod.clean(request.data.get('rules') or request.data)
    except rules_mod.RulesetError as exc:
        return _err(str(exc), 'VALIDATION_FAILED', field=getattr(exc, 'field', None))

    ruleset = _ruleset_for(tournament)
    ruleset.data = cleaned
    ruleset.updated_by = user
    ruleset.save(update_fields=['data', 'updated_by', 'updated_at'])

    # The format on the tournament follows the rules, so the two cannot disagree
    # about what is being played.
    if tournament.bracket_type != cleaned['format']:
        tournament.bracket_type = cleaned['format']
        tournament.save(update_fields=['bracket_type'])

    return _ok(_payload(ruleset, tournament), 'Rules saved.')


@api_view(['POST'])
def reset_tournament_rules(request, tournament_id):
    """POST /tournament/<id>/rules/reset/ - back to the preset for the format."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = Tournament.objects.filter(pk=tournament_id).first()
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if not (tournament.tournament_creator_id == user.user_id
            or may_override(user, 'cancel_tournament')):
        return _err('These are not your tournament\'s rules to change.',
                    'NOT_YOURS', status.HTTP_403_FORBIDDEN)

    wanted = request.data.get('format') or tournament.bracket_type
    ruleset = _ruleset_for(tournament)
    ruleset.data = rules_mod.preset_for(wanted)
    ruleset.updated_by = user
    ruleset.save(update_fields=['data', 'updated_by', 'updated_at'])
    return _ok(_payload(ruleset, tournament), 'Rules reset to the preset.')


@api_view(['GET'])
@permission_classes([AllowAny])
def rule_presets(request):
    """GET /tournament/rule-presets/ - what an organiser can start from."""
    return _ok({
        'presets': {key: rules_mod.preset_for(key) for key in fmt.FORMATS},
        'placement_presets': rules_mod.PLACEMENT_PRESETS,
        'tiebreakers': [{'key': k, 'label': v} for k, v in fmt.TIEBREAKERS.items()],
    }, 'Rule presets')

"""Both tables for a tournament, and recording a tie's individual games.

"for every tournament, there should always be two types of results if it's a team
tournament, the team result/table, and then individual player results/table also."

So one endpoint answers with both rather than making the page ask twice and
reconcile them - two round trips is how a team table and a player table end up
disagreeing on screen about the same fixture.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.decorators import resolve_admin

from .models import BracketMatch, LeagueRules, TieFixture, Tournament
from .services import league


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': None},
                    status=http)


@api_view(['GET'])
def standings(request, tournament_id):
    """The team table and the player table, from the same fixtures.

    Public: a league table is the most shareable thing a tournament produces,
    and putting it behind a sign-in is how a competition stays invisible.
    """
    try:
        tournament = Tournament.objects.get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    rules = league.rules_for(tournament)

    return _ok({
        'tournament_id': tournament.pk,
        'title': tournament.tournament_title,
        'format': tournament.bracket_type,
        'rules': {
            'points_win': rules.points_win,
            'points_draw': rules.points_draw,
            'points_loss': rules.points_loss,
            'tiebreakers': rules.ordered_tiebreakers(),
            'players_per_team': rules.players_per_team,
        },
        # Both, always. A team competition is also an individual one.
        'team_table': league.team_table(tournament),
        'player_table': league.player_table(tournament),
    }, 'Standings')


@api_view(['GET'])
def tie_detail(request, tie_id):
    """One tie and the individual games inside it."""
    try:
        tie = BracketMatch.objects.select_related(
            'participant_1', 'participant_2').get(pk=tie_id)
    except BracketMatch.DoesNotExist:
        return _err('Match not found', 'MATCH_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    running_1, running_2 = league.aggregate(tie)

    return _ok({
        'tie_id': tie.pk,
        'round': tie.round_number,
        'status': tie.status,
        'aggregate': {'participant_1': running_1, 'participant_2': running_2},
        'settled': {'participant_1': tie.score_p1, 'participant_2': tie.score_p2},
        'winner_registration_id': tie.winner_id,
        'fixtures': [
            {
                'slot': f.slot,
                'player_1': _person(f.player_1),
                'player_2': _person(f.player_2),
                'goals_1': f.goals_1,
                'goals_2': f.goals_2,
                'status': f.status,
            }
            for f in tie.fixtures.select_related('player_1', 'player_2').all()
        ],
    }, 'Tie')


def _person(user):
    if user is None:
        return None
    return {
        'user_id': user.pk,
        'username': user.username,
        'name': getattr(user, 'full_name', '') or user.username,
    }


@api_view(['POST'])
def record_fixture(request, tie_id):
    """Record one player-versus-player game, then settle the tie if it is complete.

    Admin only for now, deliberately. The player-submits-and-opponent-confirms
    flow already exists for ordinary matches, and wiring a second, subtly
    different one for tie fixtures before the format has been used in anger is
    how two score paths drift apart. An organiser recording results is enough to
    run the CEO's first league.
    """
    admin, err = resolve_admin(request)
    if err:
        return err

    try:
        tie = BracketMatch.objects.get(pk=tie_id)
    except BracketMatch.DoesNotExist:
        return _err('Match not found', 'MATCH_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    slot = request.data.get('slot')
    goals_1 = request.data.get('goals_1')
    goals_2 = request.data.get('goals_2')

    try:
        slot = int(slot)
        goals_1 = int(goals_1)
        goals_2 = int(goals_2)
    except (TypeError, ValueError):
        return _err('slot, goals_1 and goals_2 must be whole numbers',
                    'VALIDATION_FAILED')

    if goals_1 < 0 or goals_2 < 0:
        return _err('A score cannot be negative', 'VALIDATION_FAILED')

    fixture = TieFixture.objects.filter(tie=tie, slot=slot).first()
    if fixture is None:
        return _err(f'This tie has no slot {slot}', 'FIXTURE_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    fixture.goals_1 = goals_1
    fixture.goals_2 = goals_2
    fixture.status = 'completed'
    fixture.save(update_fields=['goals_1', 'goals_2', 'status'])

    # Settles only when every slot is in, so a half-recorded tie stays open.
    winner = league.settle(tie)
    tie.refresh_from_db()

    return _ok({
        'tie_id': tie.pk,
        'slot': slot,
        'aggregate': {'participant_1': tie.score_p1, 'participant_2': tie.score_p2},
        'tie_status': tie.status,
        'winner_registration_id': winner.id if winner else None,
        'drawn': tie.status == 'completed' and winner is None,
    }, 'Result recorded')


@api_view(['POST'])
def set_league_rules(request, tournament_id):
    """The organiser's points and tiebreakers."""
    admin, err = resolve_admin(request)
    if err:
        return err

    try:
        tournament = Tournament.objects.get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    rules, _ = LeagueRules.objects.get_or_create(tournament=tournament)

    for field in ('points_win', 'points_draw', 'points_loss', 'players_per_team'):
        if field in request.data:
            try:
                setattr(rules, field, int(request.data[field]))
            except (TypeError, ValueError):
                return _err(f'{field} must be a whole number', 'VALIDATION_FAILED')

    if 'tiebreakers' in request.data:
        asked = request.data['tiebreakers']
        if not isinstance(asked, list):
            return _err('tiebreakers must be a list, in the order you want them applied',
                        'VALIDATION_FAILED')
        rules.tiebreakers = asked

    if rules.players_per_team < 1:
        return _err('A team needs at least one player', 'VALIDATION_FAILED')

    rules.save()

    return _ok({
        'points_win': rules.points_win,
        'points_draw': rules.points_draw,
        'points_loss': rules.points_loss,
        # Echo what will actually be applied, which is not always what was sent:
        # an unrecognised name is dropped rather than silently accepted.
        'tiebreakers': rules.ordered_tiebreakers(),
        'players_per_team': rules.players_per_team,
    }, 'Rules saved')

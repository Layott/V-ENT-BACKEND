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

from vent_auth.actors import actor_from_request, may_override
from vent_auth.decorators import resolve_admin

from .models import BracketMatch, LeagueRules, TieFixture, Tournament
from .services import league


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': None},
                    status=http)


def _organiser_or_admin(request, tournament):
    """(user, error). The tournament's own creator, or somebody overriding.

    These endpoints were admin-only, which meant an organiser could not set the
    points for their own league or record a score in it. Everything they run
    would have needed somebody with a console login standing behind them, and
    the format was therefore unusable by the people it was built for.

    `may_override` keeps the admin path for support work, and the audit trail
    that goes with it.
    """
    user, err = actor_from_request(request)
    if err:
        return None, err
    if tournament.tournament_creator_id == user.user_id:
        return user, None
    # The same roles that may cancel a tournament outright. There is no
    # 'manage_tournaments' permission; naming one that does not exist means
    # may_override always says no, and the admin path quietly stops working.
    if may_override(user, 'cancel_tournament'):
        return user, None
    return None, _err(
        'Only the tournament organizer can do that.',
        'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


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
        #
        # The full tables: the same rows as before with the rest of what a
        # league keeps merged on - clean sheets, averages, win rate, biggest
        # win and loss, walkovers both ways, points per game and form. Merged
        # rather than served from a second endpoint, because two tables about
        # one fixture list is how they start disagreeing on screen.
        'team_table': league.team_table_full(tournament),
        'player_table': league.player_table_full(tournament),
        # How the metrics with more than one defensible definition are being
        # worked out, and what each choice means, so the page never keeps its
        # own copy of the list.
        'stat_settings': league.stat_settings(tournament),
        'stat_choices': _stat_choices(),
        'adjustments': (tournament.options or {}).get('league_adjustments') or [],
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

    The organiser records results, or an admin overriding them. Not the players:
    the submit-and-confirm flow already exists for ordinary matches, and wiring
    a second, subtly different one for tie fixtures before the format has been
    used in anger is how two score paths drift apart.

    It used to be admin-only, which meant the person actually running the
    league could not enter a score in it.
    """
    try:
        tie = BracketMatch.objects.get(pk=tie_id)
    except BracketMatch.DoesNotExist:
        return _err('Match not found', 'MATCH_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    _user, err = _organiser_or_admin(request, tie.tournament)
    if err:
        return err

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
        # The RUNNING aggregate, not the settled one. `settle` only writes
        # score_p1/score_p2 once every seat is in, so echoing those told an
        # organiser who had just recorded the first match of a fixture that the
        # score was 0-0 - which reads as though the entry had not saved.
        'aggregate': dict(zip(('participant_1', 'participant_2'),
                              league.aggregate(tie))),
        'tie_status': tie.status,
        'winner_registration_id': winner.id if winner else None,
        'drawn': tie.status == 'completed' and winner is None,
    }, 'Result recorded')


@api_view(['POST'])
def set_league_rules(request, tournament_id):
    """The organiser's points and tiebreakers.

    Theirs, so they set them. This was admin-only, which made the one setting
    the format depends on unreachable by the person running it.
    """
    try:
        tournament = Tournament.objects.get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    _user, err = _organiser_or_admin(request, tournament)
    if err:
        return err

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


# ---------------------------------------------------------------------------
# How the league is worked out, and corrections to it
# ---------------------------------------------------------------------------

from . import league_stats as _stats


def _stat_choices():
    """The settings with more than one defensible answer, and what each does.

    Only these. A metric with one correct definition is not a choice, and
    offering one is noise. Noise in a settings screen is how the real settings
    stop being read.
    """
    return [
        {'key': 'walkover_goals_count', 'type': 'boolean',
         'label': 'Walkover goals count towards goal difference',
         'detail': 'A walkover records a scoreline nobody played. Counting it '
                   'lets a no-show move a goal difference that may decide the '
                   'title.'},
        {'key': 'walkover_counts_as_played', 'type': 'boolean',
         'label': 'A walkover counts as a match played',
         'detail': 'Changes every average, and points per game.'},
        {'key': 'clean_sheet_includes_walkover', 'type': 'boolean',
         'label': 'A walkover can be a clean sheet',
         'detail': 'Off if a clean sheet is something you earn on the pitch.'},
        {'key': 'win_rate_method', 'type': 'choice',
         'label': 'How win rate is worked out',
         'detail': 'Wins over games played, or a draw counting half. The '
                   'second changes who leads a tight table.',
         'options': [
             {'value': _stats.WIN_RATE_WINS, 'label': 'Wins only'},
             {'value': _stats.WIN_RATE_WITH_DRAWS, 'label': 'A draw counts half'},
         ]},
        {'key': 'biggest_win_method', 'type': 'choice',
         'label': 'What counts as a bigger win',
         'detail': 'By margin, or by how many were scored. A 9-2 and an 8-0 '
                   'are a different answer depending which you mean.',
         'options': [
             {'value': _stats.BIGGEST_BY_MARGIN, 'label': 'The margin'},
             {'value': _stats.BIGGEST_BY_GOALS, 'label': 'Goals scored'},
         ]},
        {'key': 'form_window', 'type': 'number',
         'label': 'How many recent matches make up form',
         'detail': 'Form is scored out of what a perfect run over that many '
                   'is worth, rather than a fixed 15.'},
        {'key': 'walkover_points_winner', 'type': 'number',
         'label': 'Points to whoever was left waiting'},
        {'key': 'walkover_points_loser', 'type': 'number',
         'label': 'Points to whoever did not turn up'},
        {'key': 'walkover_goals_winner', 'type': 'number',
         'label': 'Goals recorded for the winner of a walkover'},
        {'key': 'walkover_goals_loser', 'type': 'number',
         'label': 'Goals recorded against them'},
    ]


@api_view(['GET', 'POST'])
def stat_settings(request, tournament_id):
    """GET/POST /tournament/<id>/stat-settings/"""
    try:
        tournament = Tournament.objects.get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return _ok({'settings': league.stat_settings(tournament),
                    'defaults': dict(_stats.DEFAULTS),
                    'choices': _stat_choices()}, 'Scoring')

    _user, err = _organiser_or_admin(request, tournament)
    if err:
        return err

    stored = dict((tournament.options or {}).get('league_stats') or {})
    stored.update(request.data or {})
    settings, errors = _stats.clean_settings(stored)
    if errors:
        # Named, not defaulted. Substituting a rule the organiser did not
        # choose is how a league runs differently from how it was announced.
        return _err(errors[0], 'INVALID_LEAGUE_SETTING')

    options = dict(tournament.options or {})
    options['league_stats'] = settings
    tournament.options = options
    tournament.save(update_fields=['options'])
    return _ok({'settings': league.stat_settings(tournament)}, 'Scoring saved.')


@api_view(['POST'])
def league_adjustment(request, tournament_id):
    """POST /tournament/<id>/league-adjustment/

    A deduction or award against one entrant's one metric, with the reason.

    The reason is required. A points deduction is a decision somebody has to
    defend weeks later, and the spreadsheet this came from records one on its
    single adjustment row: "Stood up mid match and quit, decided to leave."
    """
    try:
        tournament = Tournament.objects.get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    user, err = _organiser_or_admin(request, tournament)
    if err:
        return err

    player = str(request.data.get('player') or '').strip()
    metric = str(request.data.get('metric') or '').strip().upper()
    reason = str(request.data.get('reason') or '').strip()

    if not player:
        return _err('Say who this is about.', 'VALIDATION_FAILED')
    if metric not in _stats.ADJUSTABLE:
        return _err('That is not something that can be adjusted.',
                    'INVALID_METRIC')
    if not reason:
        return _err('An adjustment needs a reason.', 'REASON_REQUIRED')
    try:
        value = int(request.data.get('value'))
    except (TypeError, ValueError):
        return _err('The amount has to be a whole number.', 'VALIDATION_FAILED')
    if value == 0:
        return _err('An adjustment of nothing is not an adjustment.',
                    'VALIDATION_FAILED')

    options = dict(tournament.options or {})
    rows = list(options.get('league_adjustments') or [])
    rows.append({'player': player, 'metric': metric, 'value': value,
                 'reason': reason[:200], 'by': user.username})
    options['league_adjustments'] = rows
    tournament.options = options
    tournament.save(update_fields=['options'])
    return _ok({'adjustments': rows}, 'Adjustment recorded.')


@api_view(['GET'])
def head_to_head(request, tournament_id):
    """GET /tournament/<id>/head-to-head/?a=&b="""
    try:
        tournament = Tournament.objects.get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    first = str(request.GET.get('a') or '').strip()
    second = str(request.GET.get('b') or '').strip()
    if not first or not second:
        return _err('Name both.', 'VALIDATION_FAILED')
    if first == second:
        return _err('That is the same person twice.', 'VALIDATION_FAILED')

    return _ok(league.head_to_head(tournament, first, second), 'Head to head')

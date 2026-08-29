"""Taking the data out.

PRD: "extract data from the platform in different forms... Excel or CSV Formats:
Exportable spreadsheets providing detailed results and statistics."

CSV, because it opens in Excel, Sheets, Numbers and a text editor, and because
the organiser's actual next step is almost always a pivot table or a mail merge.
XLSX would need a library to write a format that Excel already reads from CSV.

Three sheets, and they are the three questions an organiser is asked after an
event: who entered, what happened, and where does that leave everybody.

Organiser only. A participant list carries contact details somebody handed over
to enter a competition, not to be published.
"""
import csv
import io

from rest_framework import status
from rest_framework.decorators import api_view
from django.http import HttpResponse
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import BracketMatch, TieFixture, Tournament, TournamentRegistration
from .services import league


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _tournament(ref):
    from vent_auth.slugs import resolve_or_redirect
    try:
        found, _moved = resolve_or_redirect(
            ref, entity_type='tournament', id_field='tournament_id',
            model=Tournament)
        if found is not None:
            return found
    except Exception:
        pass
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
    return None, _err('Only the tournament organizer can export this.',
                      'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def _entrant_name(reg):
    if reg is None:
        return ''
    if reg.team_id:
        return reg.team.team_name
    if reg.user_id:
        return reg.user.full_name or reg.user.username
    return ''


def _csv(rows, header, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    # A plain HttpResponse, not a DRF Response: DRF would render the CSV
    # string through its JSON renderer, so the downloaded file would contain a
    # quoted string with escaped newlines rather than a spreadsheet. The tests
    # missed it because `res.data` is the string before rendering.
    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response


@api_view(['GET'])
def export_tournament(request, tournament_id):
    """One of three sheets. `?sheet=participants|results|standings`.

    Not `?format=`: DRF reserves that name for content negotiation and answers
    404 for a renderer it does not have.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    _user, err = _organiser(request, tournament)
    if err:
        return err

    sheet = str(request.GET.get('sheet') or 'participants').lower()
    stem = tournament.slug or str(tournament.pk)

    if sheet == 'participants':
        rows = (TournamentRegistration.objects.filter(tournament=tournament)
                .select_related('team', 'user').order_by('registered_at'))
        return _csv(
            [[
                r.pk,
                'team' if r.team_id else 'player',
                _entrant_name(r),
                r.user.email if r.user_id else '',
                r.status,
                'yes' if r.entry_fee_paid else 'no',
                r.seed if r.seed is not None else '',
                r.registered_at.isoformat() if r.registered_at else '',
            ] for r in rows],
            ['registration_id', 'type', 'name', 'email', 'status',
             'entry_fee_paid', 'seed', 'registered_at'],
            '%s-participants.csv' % stem)

    if sheet == 'results':
        # One row per MATCH, not per fixture. On an aggregate league a fixture
        # is several matches, and a sheet that collapsed them would throw away
        # exactly the detail somebody exports results to look at.
        rows = []
        for tie in (BracketMatch.objects.filter(tournament=tournament)
                    .select_related('participant_1__team', 'participant_1__user',
                                    'participant_2__team', 'participant_2__user')
                    .order_by('day', 'running_order', 'round_number', 'match_number')):
            seats = list(TieFixture.objects.filter(tie=tie)
                         .select_related('player_1', 'player_2').order_by('slot'))
            if seats:
                for seat in seats:
                    rows.append([
                        tie.pk, tie.round_number, tie.match_number,
                        tie.day.isoformat() if tie.day else '',
                        tie.running_order,
                        seat.slot,
                        _entrant_name(tie.participant_1),
                        seat.player_1.username if seat.player_1_id else '',
                        seat.goals_1,
                        seat.goals_2,
                        seat.player_2.username if seat.player_2_id else '',
                        _entrant_name(tie.participant_2),
                        seat.status,
                    ])
            else:
                rows.append([
                    tie.pk, tie.round_number, tie.match_number,
                    tie.day.isoformat() if tie.day else '',
                    tie.running_order,
                    '',
                    _entrant_name(tie.participant_1), '',
                    tie.score_p1, tie.score_p2,
                    '', _entrant_name(tie.participant_2),
                    tie.status,
                ])
        return _csv(rows, [
            'fixture_id', 'round', 'match_number', 'day', 'running_order',
            'seat', 'side_1', 'player_1', 'goals_1', 'goals_2', 'player_2',
            'side_2', 'status',
        ], '%s-results.csv' % stem)

    if sheet == 'standings':
        teams = league.team_table(tournament)
        players = league.player_table(tournament)
        rows = []
        for table_name, table in (('team', teams), ('player', players)):
            for row in table:
                rows.append([
                    table_name, row.get('position'), row.get('name'),
                    row.get('played'), row.get('won'), row.get('drawn'),
                    row.get('lost'), row.get('goals_for'),
                    row.get('goals_against'), row.get('goal_difference'),
                    row.get('points'),
                ])
        return _csv(rows, [
            'table', 'position', 'name', 'played', 'won', 'drawn', 'lost',
            'goals_for', 'goals_against', 'goal_difference', 'points',
        ], '%s-standings.csv' % stem)

    return _err('Ask for participants, results or standings.', 'VALIDATION_ERROR')

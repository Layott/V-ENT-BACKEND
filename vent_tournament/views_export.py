"""Taking the data out.

PRD: "extract data from the platform in different forms... Excel or CSV Formats:
Exportable spreadsheets providing detailed results and statistics."

CSV first, because it opens in Excel, Sheets, Numbers and a text editor, and
because the organiser's next step is almost always a pivot table or a mail
merge.

The spec also asks for "Documents (PDF, DOCX)", and that is a different job
from a spreadsheet: a sheet is for working on, a document is for SENDING - to a
sponsor, to a venue, to a federation. So the same three sheets come out as
xlsx, docx and pdf through `documents.py`, which is the one place in this repo
that knows how to write any of those. CSV stays free; the formatted document is
one of the things the spec marks premium.

Three sheets, and they are the three questions an organiser is asked after an
event: who entered, what happened, and where does that leave everybody.

Organiser only. A participant list carries contact details somebody handed over
to enter a competition, not to be published.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth import premium

from . import documents
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
    # `entrant_name` covers a club, a lone player and a squad. Branching
    # here by hand is how a squad came back blank.
    if reg is None:
        return ''
    return getattr(reg, 'entrant_name', '') or ''


#: What each sheet is called on the front of a document. A file somebody is
#: sent has to say what it is; a CSV never needed a title and a PDF always does.
SHEET_TITLES = {
    'participants': 'Participants',
    'results': 'Results',
    'standings': 'Standings',
}


def _deliver(tournament, sheet, header, rows, wanted):
    """The same rows, in whichever format was asked for.

    Written once rather than per sheet: three sheets times five formats is
    fifteen places to get a filename wrong, and the filename is the only thing
    the person who receives it sees before they open it.
    """
    stem = tournament.slug or str(tournament.pk)
    name = '%s-%s' % (stem, sheet)
    title = '%s: %s' % (tournament.tournament_title,
                        SHEET_TITLES.get(sheet, sheet))

    if wanted == 'csv':
        return documents.as_csv(header, rows, '%s.csv' % name)
    if wanted == 'xlsx':
        return documents.as_xlsx(header, rows, '%s.xlsx' % name,
                                 sheet_title=SHEET_TITLES.get(sheet, sheet))
    if wanted == 'docx':
        return documents.as_docx(title, header, rows, '%s.docx' % name)
    if wanted == 'pdf':
        return documents.as_pdf(title, header, rows, '%s.pdf' % name)
    # Unreachable: the caller has already checked the name against FORMATS.
    return documents.as_csv(header, rows, '%s.csv' % name)


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

    # `?as=`, not `?format=`: DRF reserves that name for content negotiation
    # and answers 404 for a renderer it does not have.
    wanted = documents.FORMATS.get(
        str(request.GET.get('as') or 'csv').strip().lower())
    if wanted is None:
        return _err('That is not a format this exports as. Ask for csv, xlsx, '
                    'docx or pdf.', 'UNKNOWN_FORMAT')
    if wanted == 'txt':
        return _err('A sheet of results is not a list of lines. Ask for csv, '
                    'xlsx, docx or pdf.', 'UNKNOWN_FORMAT')

    # Checked before the rows are read, so a refusal costs nobody a query.
    if wanted in ('xlsx', 'docx', 'pdf') and not premium.has_premium(tournament):
        return Response(premium.refuse('export_documents'),
                        status=status.HTTP_402_PAYMENT_REQUIRED)

    if sheet == 'participants':
        rows = (TournamentRegistration.objects.filter(tournament=tournament)
                .select_related('team', 'user').order_by('registered_at'))
        return _deliver(
            tournament, sheet,
            ['registration_id', 'type', 'name', 'email', 'status',
             'entry_fee_paid', 'seed', 'registered_at'],
            [[
                r.pk,
                # `entrant_kind` knows a squad; this read 'player' for one.
                'team' if r.entrant_kind == 'team' else r.entrant_kind,
                _entrant_name(r),
                r.user.email if r.user_id else '',
                r.status,
                'yes' if r.entry_fee_paid else 'no',
                r.seed if r.seed is not None else '',
                r.registered_at.isoformat() if r.registered_at else '',
            ] for r in rows],
            wanted)

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
        return _deliver(tournament, sheet, [
            'fixture_id', 'round', 'match_number', 'day', 'running_order',
            'seat', 'side_1', 'player_1', 'goals_1', 'goals_2', 'player_2',
            'side_2', 'status',
        ], rows, wanted)

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
        return _deliver(tournament, sheet, [
            'table', 'position', 'name', 'played', 'won', 'drawn', 'lost',
            'goals_for', 'goals_against', 'goal_difference', 'points',
        ], rows, wanted)

    return _err('Ask for participants, results or standings.', 'VALIDATION_ERROR')

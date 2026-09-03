"""Every match in a tournament, so an admin can pick one instead of typing an id.

The override form asked for a match id, two scores and a winner registration id,
all typed by hand, with a note saying "find it on the bracket". Which means:
open the bracket in another tab, read a number off it, come back, and hope. The
CEO's words were that it "shouldn't look like this at all", and the reason it
matters is in the same sentence: brackets, multiple structures, teams, players,
different match-ups, structures combined together. Nobody can hold that in their
head well enough to type the right number.

So this hands back every match with both sides NAMED, the round and match number
it sits at, the current score, and what state it is in - which is what a picker
needs to be a picker.

Two kinds of thing are returned, because two kinds exist:

  * bracket matches, which is most formats
  * tie fixtures, the per-player games inside an aggregate tie, where overriding
    one fixture changes an aggregate rather than a result

They are told apart by `kind`, so a caller can never override one thinking it is
the other.
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import admin_role_required

OVERRIDE_ROLES = ['super_admin', 'mod_admin']


def _side(reg):
    """A participant, named. The whole point of this endpoint."""
    # Through the shared accessors: a hand-built branch here knew teams and
    # lone players only, so a squad came back as 'unknown' with no name.
    if reg is None:
        return None
    kind = reg.entrant_kind
    return {'registration_id': reg.id,
            'type': 'player' if kind == 'user' else kind,
            'name': reg.entrant_name or None}


@api_view(['GET'])
@admin_role_required(OVERRIDE_ROLES)
def admin_tournament_matches(request, tournament_id):
    """GET /auth/admin/tournaments/<id>/matches/ - everything that can be overridden."""
    from vent_tournament.models import (
        BracketMatch, TieFixture, Tournament, TournamentRegistration,
    )

    tournament = Tournament.objects.filter(pk=tournament_id).first()
    if tournament is None:
        return Response(
            {'status': 'error', 'code': 'TOURNAMENT_NOT_FOUND',
             'message': 'No such tournament.', 'data': None},
            status=status.HTTP_404_NOT_FOUND)

    matches = (
        BracketMatch.objects
        .filter(tournament=tournament)
        .select_related('participant_1__team', 'participant_1__user',
                        'participant_2__team', 'participant_2__user', 'winner')
        .order_by('round_number', 'match_number')
    )

    rows = [
        {
            'kind': 'match',
            'id': m.id,
            'round': m.round_number,
            'match_number': m.match_number,
            # Said the way a bracket says it, so an admin can find the same row
            # on the bracket they are looking at.
            'label': 'Round %s, match %s' % (m.round_number, m.match_number),
            'side_1': _side(m.participant_1),
            'side_2': _side(m.participant_2),
            'score_1': m.score_p1,
            'score_2': m.score_p2,
            'status': m.status,
            'winner_registration_id': m.winner_id,
            'scheduled_at': m.scheduled_at,
            'completed_at': m.completed_at,
        }
        for m in matches
    ]

    # The aggregate format: overriding one of these changes a TOTAL, not a
    # result, and the caller has to know that before it touches one.
    fixtures = (
        TieFixture.objects
        .filter(tie__tournament=tournament)
        .select_related('tie')
        .order_by('tie_id', 'slot')
    )
    fixture_rows = [
        {
            'kind': 'fixture',
            'id': f.id,
            'tie': f.tie_id,
            'slot': f.slot,
            'label': 'Tie %s, player %s' % (f.tie_id, f.slot),
            'score_1': f.goals_1,
            'score_2': f.goals_2,
            'status': f.status,
            'note': 'Part of an aggregate tie: changing this changes the total, '
                    'not a single result.',
        }
        for f in fixtures
    ]

    # Who is registered, so disqualifying somebody is picking a name off a list
    # rather than typing one and hoping it matches. The old form asked for a
    # team name, free text, with "e.g. Crimson Wolves" as the hint.
    registrations = (
        TournamentRegistration.objects
        .filter(tournament=tournament)
        .select_related('team', 'user')
        .order_by('id')
    )
    participants = []
    for reg in registrations:
        side = _side(reg)
        if side is None:
            continue
        side['status'] = reg.status
        # What disqualifying this one will actually do, worked out before the
        # click rather than discovered after it.
        side['live_matches'] = BracketMatch.objects.filter(
            tournament=tournament, status__in=('scheduled', 'in_progress'),
        ).filter(Q(participant_1=reg) | Q(participant_2=reg)).count()
        participants.append(side)

    return Response({
        'status': 'success',
        'message': 'Matches',
        'data': {
            'participants': participants,
            'tournament': {
                'id': tournament.tournament_id,
                'title': tournament.tournament_title,
                'format': tournament.bracket_type,
            },
            'matches': rows,
            'fixtures': fixture_rows,
            'counts': {
                'matches': len(rows),
                'fixtures': len(fixture_rows),
                'completed': sum(1 for r in rows if r['status'] == 'completed'),
                'playable': sum(1 for r in rows
                                if r['side_1'] and r['side_2']),
            },
        },
    }, status=status.HTTP_200_OK)

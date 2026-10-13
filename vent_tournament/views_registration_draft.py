"""A tournament registration saved as it goes (inbox 436).

    GET    /tournament/<ref>/registration-draft/   where this person is up to, and
                                                   whether the event ticket is the entry
    PUT    /tournament/<ref>/registration-draft/   save a step
    DELETE /tournament/<ref>/registration-draft/   start again
    GET    /tournament/registration-drafts/mine/   every registration this person
                                                   has left part done

CEO, 12 October 2026: register for the event "while they are registering for
the tournament under that event, just incase its a long process and they dont
have to start their registration from begining again, maybe it can be savinbg
progress." Approved as the "Tournament + ticket" screen of
docs/mockups/party-games.html.

Only the person's own draft is ever read or written: the row is keyed to the
signed-in account, so there is no id in any of these addresses to change.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import inputs
from vent_auth.actors import actor_from_request as _actor_from_request
from vent_auth.models import TeamMembers, Teams

from . import lookup
from .models import TournamentRegistration, TournamentRegistrationDraft

KEEP = timedelta(days=TournamentRegistrationDraft.KEEP_DAYS)
STEPS = [code for code, _ in TournamentRegistrationDraft.STEP_CHOICES]
#: A registration that still holds a place; a withdrawn one does not.
HOLDING = ('pending', 'confirmed')


def _error(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _tournament(ref):
    tournament = lookup.find(ref)
    if tournament is None or getattr(tournament, 'deleted_at', None) or tournament.is_draft:
        return None
    return tournament


def _registered(user, tournament):
    """Already in, alone or with a team they belong to: nothing left to resume."""
    rows = TournamentRegistration.objects.filter(tournament=tournament, status__in=HOLDING)
    if rows.filter(user=user).exists():
        return True
    teams = TeamMembers.objects.filter(user=user).values_list('team_id', flat=True)
    return rows.filter(team_id__in=list(teams)).exists()


def _live(draft, now=None):
    return draft.updated_at >= (now or timezone.now()) - KEEP


def _entry(user, tournament):
    """Whether the event ticket is the entry, and whether this person has it."""
    from vent_event.views_linking import entry_requires_ticket, holds_entry_ticket
    needs, link = entry_requires_ticket(tournament)
    if not needs:
        return {'needs_ticket': False, 'holds_ticket': False, 'event': None, 'tier': None}
    return {
        'needs_ticket': True,
        'holds_ticket': holds_entry_ticket(user, link),
        'event': {'slug': link.event.slug, 'name': link.event.name},
        'tier': ({'id': link.entry_tier_id, 'name': link.entry_tier.name}
                 if link.entry_tier_id else None),
    }


def serialize_draft(draft):
    team = draft.team
    return {
        'tournament': {
            'slug': draft.tournament.slug,
            'title': draft.tournament.tournament_title,
        },
        'mode': draft.mode or None,
        # The same fields the registration's team step hands on, so a resumed
        # review draws the team exactly as a fresh one does.
        'team': ({'id': team.team_id, 'slug': getattr(team, 'slug', None), 'name': team.team_name,
                  'logo': team.team_logo.url if team.team_logo else None,
                  'game': team.game.game_title if team.game_id else None}
                 if team is not None else None),
        'step': draft.step,
        'saved_at': draft.updated_at,
        'expires_at': draft.updated_at + KEEP,
    }


def _may_use_team(user, team):
    return (team.team_owner_id == user.user_id
            or TeamMembers.objects.filter(team=team, user=user).exists())


@api_view(['GET', 'PUT', 'DELETE'])
def registration_draft(request, tournament_id):
    user, auth_error = _actor_from_request(request)
    if auth_error is not None:
        return auth_error
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    draft = (TournamentRegistrationDraft.objects
             .filter(user=user, tournament=tournament).select_related('team', 'team__game').first())

    if request.method == 'DELETE':
        if draft is not None:
            draft.delete()
        return Response({'status': 'success', 'code': 'DRAFT_DISCARDED', 'message': '',
                         'data': {'draft': None}})

    if request.method == 'GET':
        # A draft past its keep, or one whose registration went through some
        # other way, is not something to offer: it is removed as it is read.
        if draft is not None and (not _live(draft) or _registered(user, tournament)):
            draft.delete()
            draft = None
        return Response({'status': 'success', 'message': '', 'data': {
            'draft': serialize_draft(draft) if draft is not None else None,
            'entry': _entry(user, tournament),
            'registered': _registered(user, tournament),
        }})

    # PUT: save a step.
    if _registered(user, tournament):
        return _error('You are already registered for this tournament.',
                      'ALREADY_REGISTERED', status.HTTP_409_CONFLICT)
    # Only what this save names changes; everything else stays as the row has
    # it. A screen saves "solo" and "the ticket step" as two requests that can
    # arrive in either order (or at once, from a phone), and each must add to
    # the other rather than put back what it read before the other landed.
    mode = inputs.read_choice(request.data, 'mode', list(TournamentRegistrationDraft.MODES))
    step = inputs.read_choice(request.data, 'step', STEPS)
    naming_team = 'team_id' in request.data
    team = None
    if naming_team:
        team_id = inputs.read_int(request.data, 'team_id', minimum=1)
        if team_id:
            team = Teams.objects.filter(pk=team_id).first()
            # Said the same way for a team that does not exist and one that is
            # not theirs: a draft is no way to learn which team numbers exist.
            if team is None or not _may_use_team(user, team):
                return _error('That is not one of your teams.', 'TEAM_NOT_YOURS',
                              status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        # One row per person and tournament, made by whichever save gets there
        # first. Two first saves at once used to both INSERT, and the second
        # answered 500 and lost its step (phone walk, 13 October 2026).
        draft, _ = (TournamentRegistrationDraft.objects.select_for_update()
                    .get_or_create(user=user, tournament=tournament))
        if mode is not None:
            draft.mode = mode
        if step is not None:
            draft.step = step
        if naming_team:
            draft.team = team
        if draft.mode != 'team':
            draft.team = None
        draft.save()
    return Response({'status': 'success', 'code': 'DRAFT_SAVED', 'message': '', 'data': {
        'draft': serialize_draft(draft),
        'entry': _entry(user, tournament),
    }})


@api_view(['GET'])
def my_registration_drafts(request):
    """Every registration this person has left part done, newest first."""
    user, auth_error = _actor_from_request(request)
    if auth_error is not None:
        return auth_error
    now = timezone.now()
    rows = []
    for draft in (TournamentRegistrationDraft.objects.filter(user=user)
                  .select_related('tournament', 'team').order_by('-updated_at')):
        tournament = draft.tournament
        if (not _live(draft, now) or getattr(tournament, 'deleted_at', None)
                or tournament.is_draft or _registered(user, tournament)):
            draft.delete()
            continue
        rows.append(serialize_draft(draft))
    return Response({'status': 'success', 'message': '', 'data': {'drafts': rows}})


def clear_after_registering(user, tournament):
    """A registration went through: the draft has nothing left to keep."""
    TournamentRegistrationDraft.objects.filter(user=user, tournament=tournament).delete()

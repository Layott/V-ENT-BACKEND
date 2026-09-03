# -*- coding: utf-8 -*-
"""Submitting a squad, and the organiser accepting or rejecting it.

CEO, 3 September 2026: "there should be a place where the players can like
select the cards they want to submit and submit it, then a place for admins to
accept or reject etc. also a place for admins to set rules for the squads that
the players are submitting to use if not they wont be able to submit."

Three endpoints, and the last clause of that sentence is enforced rather than
documented: with no rules set, a player cannot submit and is told why.
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from vent_auth.models import Users

from . import squad_rules as rules_engine
from . import windows
from .models import GameCard, Lineup, SquadRules
from .views import _err, _ok, _tournament, _viewer, serialize_lineup
from .views_lineups import _my_lineup


def _may_manage(user, tournament):
    from vent_tournament.access import may_manage
    return may_manage(user, tournament)


@api_view(['POST'])
@permission_classes([AllowAny])
def submit_lineup(request, tournament_id):
    """A player says this squad is their answer.

    Separate from saving on purpose. Saving is a draft; submitting is the act
    that puts it in front of the organiser, and it is the only one that checks
    the squad against the rules.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)

    window = windows.window_for(tournament)
    if window.state == 'off':
        return _err('This tournament is not using lineups.', 'LINEUPS_OFF')
    if not window.can_edit:
        return _err('Lineups are closed.', 'LINEUPS_CLOSED',
                    status.HTTP_409_CONFLICT, closes_at=window.closes_at,
                    state=window.state)

    lineup = _my_lineup(tournament, user)
    if lineup is None:
        return _err('Build a squad first.', 'NO_LINEUP', status.HTTP_404_NOT_FOUND)

    rules = SquadRules.objects.filter(tournament=tournament).first()
    body = serialize_lineup(lineup)
    allowed, found = rules_engine.may_submit(body['slots'], rules)
    if not allowed:
        # Every refusal carries its numbers, so the screen can say "you are
        # 240,000 over" rather than "that is not allowed".
        return _err('That squad cannot be submitted yet.', found[0]['code'],
                    status.HTTP_409_CONFLICT, violations=found)

    lineup.status = Lineup.SUBMITTED
    lineup.submitted_at = timezone.now()
    lineup.reviewed_by = None
    lineup.reviewed_at = None
    lineup.review_note = ''
    lineup.save(update_fields=['status', 'submitted_at', 'reviewed_by',
                               'reviewed_at', 'review_note', 'updated_at'])

    return _ok({'lineup': serialize_lineup(lineup)}, 'Squad submitted.')


@api_view(['POST'])
@permission_classes([AllowAny])
def review_lineup(request, tournament_id, username):
    """The organiser accepts or rejects a submitted squad.

    A rejection must carry a reason. "No" with nothing after it is a message
    the player cannot act on, and they will ask anyway.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(user, tournament):
        return _err('Only the organiser can check squads.',
                    'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    player = Users.objects.filter(username__iexact=str(username)).first()
    if player is None:
        return _err('No player by that name.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    lineup = _my_lineup(tournament, player)
    if lineup is None:
        return _err('They have not built a squad.', 'NO_LINEUP',
                    status.HTTP_404_NOT_FOUND)
    if lineup.status == Lineup.DRAFT:
        return _err('They have not submitted it yet.', 'NOT_SUBMITTED',
                    status.HTTP_409_CONFLICT)

    decision = str(request.data.get('decision') or '').strip().lower()
    if decision not in ('accept', 'reject'):
        return _err('Say accept or reject.', 'VALIDATION_ERROR', field='decision')

    note = str(request.data.get('note') or '').strip()[:280]
    if decision == 'reject' and not note:
        return _err('Say why, so they can fix it.', 'REASON_REQUIRED',
                    field='note')

    lineup.status = Lineup.ACCEPTED if decision == 'accept' else Lineup.REJECTED
    lineup.reviewed_by = user
    lineup.reviewed_at = timezone.now()
    lineup.review_note = note
    lineup.save(update_fields=['status', 'reviewed_by', 'reviewed_at',
                               'review_note', 'updated_at'])

    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            user=player, category='tournament',
            title=('Your squad was accepted' if decision == 'accept'
                   else 'Your squad needs changing'),
            body=note or 'The organiser has checked your squad.',
            link='/tournaments/%s/manage?tab=lineup'
                 % (tournament.slug or tournament.tournament_id),
            metadata={'lineup_status': lineup.status})
    except Exception:                                       # noqa: BLE001
        # A decision made and not announced is recoverable; one that failed
        # because the announcement failed is not.
        pass

    return _ok({'lineup': serialize_lineup(lineup)},
               'Accepted.' if decision == 'accept' else 'Sent back.')


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def squad_rules_view(request, tournament_id):
    """The rules a squad must satisfy. Public to read, the organiser's to set.

    Public on GET because a player has to build to them, and a rule nobody can
    read until they are refused is not a rule, it is a trap.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        rules = SquadRules.objects.filter(tournament=tournament).first()
        return _ok({'squad_rules': rules_engine.payload(rules)})

    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(user, tournament):
        return _err('Only the organiser can set the squad rules.',
                    'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    rules, _ = SquadRules.objects.get_or_create(tournament=tournament)

    for field in ('max_budget_coins', 'min_from_nation', 'max_card_rating'):
        if field not in request.data:
            continue
        raw = request.data.get(field)
        if raw in (None, ''):
            setattr(rules, field, None if field == 'max_card_rating' else 0)
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return _err('That is a number.', 'VALIDATION_ERROR', field=field)
        if value < 0:
            return _err('That cannot be negative.', 'VALIDATION_ERROR', field=field)
        setattr(rules, field, value)

    if 'required_nation' in request.data:
        rules.required_nation = str(request.data.get('required_nation') or '')[:120]
    if 'notes' in request.data:
        rules.notes = str(request.data.get('notes') or '')[:280]

    if 'banned_item_types' in request.data:
        raw = request.data.get('banned_item_types') or []
        if not isinstance(raw, list):
            return _err('Send a list of item types.', 'VALIDATION_ERROR',
                        field='banned_item_types')
        known = {k for k, _ in GameCard.ITEM_TYPES}
        cleaned = [str(k).strip().lower() for k in raw]
        unknown = [k for k in cleaned if k not in known]
        if unknown:
            return _err('There is no item type called %s.' % unknown[0],
                        'UNKNOWN_ITEM_TYPE', field='banned_item_types')
        rules.banned_item_types = cleaned

    rules.save()
    return _ok({'squad_rules': rules_engine.payload(rules)}, 'Saved.')

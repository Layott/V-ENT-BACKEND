# -*- coding: utf-8 -*-
"""A player's lineup, and the deadline the organiser sets for it."""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import Users

from . import formations as formation_catalogue
from . import windows
from . import squad_rules as rules_engine
from .models import GameCard, Lineup, LineupRules, LineupSlot, SquadRules
from .views import _err, _ok, _tournament, _viewer, serialize_lineup


def _rules_payload(rules, window):
    body = window.payload()
    body.update({
        'enabled': bool(rules and rules.enabled),
        'weekly_day': rules.weekly_day if rules else None,
        'weekly_time': rules.weekly_time if rules else None,
        'changes_open_at': rules.changes_open_at if rules else None,
        'changes_close_at': rules.changes_close_at if rules else None,
        'locked_by_hand': bool(rules and rules.locked_by_hand),
        'reopened_by_hand': bool(rules and rules.reopened_by_hand),
    })
    return body


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def lineup_rules(request, tournament_id):
    """When lineups close. Anybody may read it; the organiser sets it.

    Public on GET deliberately: a deadline nobody can see until they try to
    save is not a deadline, it is a surprise.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return _ok({'rules': _rules_payload(windows.rules_for(tournament),
                                            windows.window_for(tournament))})

    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)

    from vent_tournament.access import may_manage
    if not may_manage(user, tournament):
        return _err('Only the organiser can set the deadline.',
                    'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    rules, _ = LineupRules.objects.get_or_create(tournament=tournament)

    simple = ['enabled', 'locked_by_hand', 'reopened_by_hand']
    times = ['opens_at', 'closes_at', 'changes_open_at', 'changes_close_at']

    for field in simple:
        if field in request.data:
            setattr(rules, field, bool(request.data.get(field)))
    for field in times:
        if field in request.data:
            setattr(rules, field, request.data.get(field) or None)

    if 'weekly_day' in request.data:
        raw = request.data.get('weekly_day')
        if raw in (None, ''):
            rules.weekly_day = None
        else:
            try:
                day = int(raw)
            except (TypeError, ValueError):
                return _err('A day is 0 for Monday through 6 for Sunday.',
                            'VALIDATION_ERROR', field='weekly_day')
            if not 0 <= day <= 6:
                return _err('A day is 0 for Monday through 6 for Sunday.',
                            'VALIDATION_ERROR', field='weekly_day')
            rules.weekly_day = day
    if 'weekly_time' in request.data:
        rules.weekly_time = request.data.get('weekly_time') or None

    if 'changes_allowed' in request.data:
        try:
            rules.changes_allowed = max(0, int(request.data.get('changes_allowed') or 0))
        except (TypeError, ValueError):
            return _err('That is a number of swaps.', 'VALIDATION_ERROR',
                        field='changes_allowed')

    # Locked and reopened are opposites; holding both is a state nobody can
    # reason about, so the one just set wins.
    if rules.locked_by_hand and rules.reopened_by_hand:
        if 'locked_by_hand' in request.data:
            rules.reopened_by_hand = False
        else:
            rules.locked_by_hand = False

    try:
        rules.full_clean(exclude=['tournament'])
    except Exception as caught:                              # noqa: BLE001
        return _err('That did not look right: %s' % caught, 'VALIDATION_ERROR')

    rules.save()
    return _ok({'rules': _rules_payload(rules, windows.window_for(tournament))},
               'Saved.')


def _my_lineup(tournament, user):
    return (Lineup.objects
            .filter(tournament=tournament, user=user)
            .prefetch_related('slots__card').first())


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def my_lineup(request, tournament_id):
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _err('Sign in to build a lineup.', 'AUTH_REQUIRED',
                    status.HTTP_401_UNAUTHORIZED)

    window = windows.window_for(tournament)

    if request.method == 'GET':
        mine = _my_lineup(tournament, user)
        rules = SquadRules.objects.filter(tournament=tournament).first()
        body = serialize_lineup(mine)
        slots = (body or {}).get('slots') or []
        return _ok({
            'lineup': body,
            'window': window.payload(),
            'formations': formation_catalogue.catalogue(),
            # The rules, and exactly where this squad stands against them, so a
            # player builds to them rather than discovering them on a refusal.
            'squad_rules': rules_engine.payload(rules),
            'violations': rules_engine.violations(slots, rules),
            'spend': rules_engine.spend(slots),
        })

    if window.state == 'off':
        return _err('This tournament is not using lineups.', 'LINEUPS_OFF')
    if not window.can_edit:
        # The refusal carries the time, so somebody who missed it by a minute
        # can see that rather than guess.
        return _err('Lineups are closed.', 'LINEUPS_CLOSED',
                    status.HTTP_409_CONFLICT,
                    closes_at=window.closes_at, opens_at=window.opens_at,
                    state=window.state)

    formation = str(request.data.get('formation') or '').strip()
    if not formation_catalogue.is_known(formation):
        return _err('Pick one of the formations offered.', 'UNKNOWN_FORMATION',
                    field='formation')

    slots_in = request.data.get('slots')
    if not isinstance(slots_in, list):
        return _err('Send the slots.', 'VALIDATION_ERROR', field='slots')

    seen_slots = set()
    seen_cards = set()
    seen_people = {}
    prepared = []

    for entry in slots_in:
        if not isinstance(entry, dict):
            return _err('Each slot is an object.', 'VALIDATION_ERROR', field='slots')
        try:
            index = int(entry.get('slot_index'))
            card_id = int(entry.get('card_id'))
        except (TypeError, ValueError):
            return _err('A slot needs a slot_index and a card_id.',
                        'VALIDATION_ERROR', field='slots')

        if not 0 <= index < formation_catalogue.TOTAL_SLOTS:
            return _err('There is no slot %d.' % index, 'BAD_SLOT',
                        field='slot_index')
        if index in seen_slots:
            return _err('Two cards were put in slot %d.' % index,
                        'DUPLICATE_SLOT', field='slot_index')
        seen_slots.add(index)

        if card_id in seen_cards:
            return _err('The same card is in the side twice.',
                        'DUPLICATE_CARD', field='card_id')
        seen_cards.add(card_id)

        card = GameCard.objects.filter(pk=card_id).first()
        if card is None:
            return _err('That card is not in the catalogue.', 'CARD_NOT_FOUND',
                        status.HTTP_404_NOT_FOUND, field='card_id')

        # Two cards of the same PERSON cannot both play. This is what the slug
        # is for: gold Mbappé and TOTY Mbappé share it.
        if card.slug in seen_people:
            return _err('%s is already in the side as %s.'
                        % (card.name, seen_people[card.slug]),
                        'DUPLICATE_PLAYER', field='card_id')
        seen_people[card.slug] = card.name

        prepared.append((index, card))

    with transaction.atomic():
        lineup, _ = Lineup.objects.get_or_create(
            tournament=tournament, user=user,
            defaults={'formation': formation})
        lineup.formation = formation
        lineup.slots.all().delete()
        LineupSlot.objects.bulk_create([
            LineupSlot(lineup=lineup, card=card, slot_index=index,
                       position=formation_catalogue.slot_position(formation, index))
            for index, card in prepared
        ])
        # Saving is NOT submitting. A player fiddles with a squad for an hour;
        # the moment they submit is the moment it is theirs to be judged on.
        # Editing an already-reviewed squad puts it back in the queue, because
        # an organiser who accepted eleven cards did not accept these.
        if lineup.status in (Lineup.SUBMITTED, Lineup.ACCEPTED, Lineup.REJECTED):
            lineup.status = Lineup.DRAFT
            lineup.reviewed_by = None
            lineup.reviewed_at = None
            lineup.review_note = ''
        lineup.save()

    lineup = _my_lineup(tournament, user)
    body = serialize_lineup(lineup)
    rules = SquadRules.objects.filter(tournament=tournament).first()
    # The same three things the GET carries, so a screen that has just saved
    # knows where the squad now stands without asking again, and without
    # working it out itself. A second implementation of the rules in the
    # browser would be a second implementation of the rules.
    return _ok({'lineup': body,
                'window': windows.window_for(tournament).payload(),
                'squad_rules': rules_engine.payload(rules),
                'violations': rules_engine.violations(body['slots'], rules),
                'spend': rules_engine.spend(body['slots'])},
               'Lineup saved.')


@api_view(['GET'])
@permission_classes([AllowAny])
def player_lineup(request, tournament_id, username):
    """One player's lineup, by name.

    Public, and deliberately so: this is what a broadcast reads, and a browser
    source in OBS carries no session. It is a team sheet, which is the most
    public thing at any tournament.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    player = Users.objects.filter(username__iexact=str(username)).first()
    if player is None:
        return _err('No player by that name.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    return _ok({'lineup': serialize_lineup(_my_lineup(tournament, player)),
                'window': windows.window_for(tournament).payload()})


@api_view(['GET'])
@permission_classes([AllowAny])
def tournament_lineups(request, tournament_id):
    """Every lineup in the tournament. The organiser's list, and a broadcast's."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    rows = (Lineup.objects.filter(tournament=tournament)
            .select_related('user').prefetch_related('slots__card'))
    return _ok({
        'lineups': [serialize_lineup(l) for l in rows],
        'window': windows.window_for(tournament).payload(),
        'submitted': sum(1 for l in rows if l.is_complete),
        'count': len(rows),
    })

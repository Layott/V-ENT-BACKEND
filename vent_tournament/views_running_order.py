"""The organiser's running order: which fixtures happen on which day, in what order.

CEO, of the Rivalry Series schedule: "Given, not generated. Layo set it. Do not
reorder it to optimise something without asking."

So nothing here generates an order. It reads the one the organiser set and it
writes the one they send. The only opinion it holds is that a fixture without a
day is unscheduled rather than "on day zero", because the list of what still
needs a slot is the thing an organiser is actually working from.

Reading is public. A schedule is the single most shared thing a tournament
produces - it goes in the group chat, on the poster and on the stream overlay -
and putting it behind a sign-in is how an event stays invisible.
"""
from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import BracketMatch, Tournament


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _tournament(tournament_id):
    """By id or by slug, so a shared link and an internal call both work."""
    from vent_auth.slugs import resolve_or_redirect

    try:
        tournament, _moved = resolve_or_redirect(
            tournament_id, entity_type='tournament',
            id_field='tournament_id', model=Tournament,
        )
        if tournament is not None:
            return tournament
    except Exception:
        pass
    if str(tournament_id).isdigit():
        return Tournament.objects.filter(pk=int(tournament_id)).first()
    return Tournament.objects.filter(slug=tournament_id).first()


def _organiser_or_admin(request, tournament):
    user, err = actor_from_request(request)
    if err:
        return None, err
    if tournament.tournament_creator_id == user.user_id:
        return user, None
    if may_override(user, 'cancel_tournament'):
        return user, None
    return None, _err('Only the tournament organizer can set the running order.',
                      'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def _side(registration):
    # Through the shared accessors: a hand-built branch here knew teams and
    # lone players only, so a squad came back as 'unknown' with no name.
    if registration is None:
        return None
    return {'type': registration.entrant_kind, 'id': registration.entrant_id,
            'name': registration.entrant_name}


def _row(match):
    return {
        'match_id': match.pk,
        'round': match.round_number,
        'match_number': match.match_number,
        'day': match.day.isoformat() if match.day else None,
        'running_order': match.running_order,
        'status': match.status,
        'participant_1': _side(match.participant_1),
        'participant_2': _side(match.participant_2),
    }


def _serialize_order(tournament):
    """The schedule as stored, grouped by day, with the leftovers named."""
    matches = list(BracketMatch.objects.filter(tournament=tournament)
                   .select_related('participant_1__team', 'participant_1__user',
                                   'participant_2__team', 'participant_2__user')
                   .order_by('day', 'running_order', 'round_number', 'match_number'))

    days = {}
    unscheduled = []
    for match in matches:
        if match.day is None:
            unscheduled.append(_row(match))
        else:
            days.setdefault(match.day.isoformat(), []).append(_row(match))

    return _ok({
        'tournament_id': tournament.pk,
        'days': [{'day': day, 'fixtures': rows} for day, rows in sorted(days.items())],
        'unscheduled': unscheduled,
        'total': len(matches),
    }, 'Running order')


@api_view(['GET'])
def running_order(request, tournament_id):
    """The schedule as the organiser set it, grouped by day.

    Unscheduled fixtures come back under their own key rather than being
    dropped: an organiser part way through building a schedule needs to see
    what is left far more than they need to see what is done.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    return _serialize_order(tournament)


@api_view(['PUT'])
def set_running_order(request, tournament_id):
    """Write the order the organiser sent. Nothing is inferred or optimised.

    The whole list arrives at once rather than one fixture at a time, because
    moving a fixture up a day changes the position of everything after it and
    a per-fixture endpoint would leave the order inconsistent between calls.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    _user, err = _organiser_or_admin(request, tournament)
    if err:
        return err

    rows = request.data.get('fixtures')
    if not isinstance(rows, list):
        return _err('Send the fixtures as a list.', 'VALIDATION_ERROR')

    # Every id checked against this tournament before anything is written. A
    # request naming somebody else's fixture must change nothing at all, not
    # change the ones it was allowed to touch and then refuse.
    wanted = {}
    for row in rows:
        if not isinstance(row, dict):
            return _err('Each fixture is a set of named values.', 'VALIDATION_ERROR')
        match_id = row.get('match_id')
        if match_id is None:
            return _err('Each fixture needs its match_id.', 'VALIDATION_ERROR')

        raw_day = row.get('day')
        day = None
        if raw_day not in (None, ''):
            day = parse_date(str(raw_day))
            if day is None:
                return _err('A day has to be a date, like 2026-09-04.',
                            'INVALID_DATE')

        try:
            order = int(row.get('running_order') or 0)
        except (TypeError, ValueError):
            return _err('The position has to be a whole number.', 'INVALID_NUMBER')

        wanted[int(match_id)] = (day, max(0, order))

    mine = {m.pk: m for m in BracketMatch.objects.filter(
        tournament=tournament, pk__in=list(wanted))}
    missing = [pk for pk in wanted if pk not in mine]
    if missing:
        return _err('Those fixtures are not in this tournament.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    with transaction.atomic():
        for pk, (day, order) in wanted.items():
            match = mine[pk]
            match.day = day
            match.running_order = order
            match.save(update_fields=['day', 'running_order'])

    # Answer with the whole order, so the screen redraws from what was stored
    # rather than from what it hoped it stored.
    return _serialize_order(tournament)

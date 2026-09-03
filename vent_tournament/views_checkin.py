"""Check-in: the fifteen minutes that decide who actually turns up.

Every platform an organiser has used before does this, and for one reason: a
bracket built from the registration list is a bracket full of people who signed
up three weeks ago and forgot. A check-in window turns "registered" into "here
right now", and the no-shows are removed before the draw rather than discovered
in round one when somebody is sitting waiting for an opponent who never existed.

Three endpoints, matching the three people involved:

    GET  /tournament/<id>/check-in/          what the entrant sees
    POST /tournament/<id>/check-in/          the entrant saying they are here
    POST /tournament/<id>/close-check-in/    the organiser drawing the line

The organiser's close is deliberately explicit rather than a timer. A window
that expires on its own disqualifies people while nobody is watching, and the
first anybody hears of it is a complaint. This way the forfeits happen when a
human presses the button, and the response says exactly who was removed.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.views_profile import _user_from_bearer
from . import options as tournament_options
from .models import Tournament, TournamentRegistration

from . import lookup


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message}, status=status.HTTP_200_OK)


def _err(message, code='ERROR', http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': data},
                    status=http_status)


def _registration_for(tournament, user):
    """The entry this person controls: their own, or their team's."""
    own = tournament.registrations.filter(user=user).first()
    if own is not None:
        return own
    return tournament.registrations.filter(team__team_owner=user).first()


def _label(registration):
    return registration.entrant_name or 'Unknown entrant'


def _window(tournament):
    return tournament_options.check_in_state(tournament, timezone.now())


@api_view(['GET'])
def check_in_status(request, tournament_id):
    """What this person needs to know: is it open, am I in, how long left."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    window = _window(tournament)
    registration = _registration_for(tournament, user)

    if window is None:
        return _ok({
            'required': False,
            'registered': registration is not None,
            'checked_in': True,          # nothing to do, so nothing is outstanding
        }, 'This tournament does not use check-in.')

    now = timezone.now()
    seconds_left = int((window['closes_at'] - now).total_seconds()) if window['open_now'] else 0

    return _ok({
        'required': True,
        'registered': registration is not None,
        'checked_in': bool(registration and registration.checked_in_at),
        'checked_in_at': registration.checked_in_at if registration else None,
        'opens_at': window['opens_at'],
        'closes_at': window['closes_at'],
        'open_now': window['open_now'],
        'closed': window['closed'],
        'closed_by_organiser': window['closed_by_organiser'],
        'seconds_remaining': max(0, seconds_left),
        'forfeit_without_check_in': window['forfeit_without_check_in'],
        'checked_in_count': tournament.registrations.filter(
            status__in=('pending', 'confirmed'), checked_in_at__isnull=False,
        ).count(),
        'registered_count': tournament.registrations.filter(
            status__in=('pending', 'confirmed'),
        ).count(),
    }, 'Check-in status')


@api_view(['POST'])
def check_in(request, tournament_id):
    """I am here. Idempotent, because people press buttons twice."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    window = _window(tournament)
    if window is None:
        return _err('This tournament does not use check-in.', 'NOT_REQUIRED')

    registration = _registration_for(tournament, user)
    if registration is None:
        return _err('You are not registered for this tournament.', 'NOT_REGISTERED',
                    status.HTTP_403_FORBIDDEN)

    if registration.status in ('disqualified', 'withdrawn'):
        return _err('This entry is no longer in the tournament.', 'NOT_ACTIVE',
                    status.HTTP_409_CONFLICT)

    if registration.checked_in_at:
        return _ok({'checked_in': True, 'checked_in_at': registration.checked_in_at},
                   'You are already checked in.')

    now = timezone.now()
    if now < window['opens_at']:
        minutes = int((window['opens_at'] - now).total_seconds() // 60) + 1
        return _err(
            f'Check-in has not opened yet. It opens in {minutes} minutes.',
            'TOO_EARLY', status.HTTP_409_CONFLICT,
            {'opens_at': window['opens_at']},
        )
    if window['closed']:
        return _err('Check-in has closed for this tournament.', 'TOO_LATE',
                    status.HTTP_409_CONFLICT, {'closed_at': window['closes_at']})

    registration.checked_in_at = now
    registration.save(update_fields=['checked_in_at'])
    return _ok({'checked_in': True, 'checked_in_at': now}, 'You are checked in.')


@api_view(['POST'])
def close_check_in(request, tournament_id):
    """Organiser only: draw the line and forfeit whoever did not show.

    Answers with the names on both sides. An organiser is about to remove real
    people from a bracket, so the response tells them who, rather than a count.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if tournament.tournament_creator_id != user.user_id and not user.is_staff:
        return _err('Only the organiser can close check-in.', 'FORBIDDEN',
                    status.HTTP_403_FORBIDDEN)

    window = _window(tournament)
    if window is None:
        return _err('This tournament does not use check-in.', 'NOT_REQUIRED')

    if tournament.bracket_matches.exists():
        return _err('The bracket has already been generated for this tournament.',
                    'BRACKET_EXISTS', status.HTTP_409_CONFLICT)

    if tournament.check_in_closed_at:
        return _err('Check-in has already been closed for this tournament.',
                    'ALREADY_CLOSED', status.HTTP_409_CONFLICT,
                    {'closed_at': tournament.check_in_closed_at})

    now = timezone.now()
    if now < window['opens_at']:
        return _err('Check-in has not opened yet.', 'TOO_EARLY', status.HTTP_409_CONFLICT,
                    {'opens_at': window['opens_at']})

    active = list(
        tournament.registrations
        .filter(status__in=('pending', 'confirmed'))
        .select_related('user', 'team')
    )
    present = [r for r in active if r.checked_in_at]
    absent = [r for r in active if not r.checked_in_at]

    forfeit = window['forfeit_without_check_in']
    removed = []

    if forfeit and absent:
        # Refuse to empty the tournament. If nobody checked in, the likeliest
        # explanation is that the window was wrong or nobody was told, and
        # disqualifying the entire field helps no one.
        if len(present) < 2:
            return _err(
                f'Only {len(present)} of {len(active)} entrants checked in. Closing now would '
                'leave no tournament to run, so nothing was changed. Extend the window or '
                'switch the forfeit rule off.',
                'TOO_FEW_CHECKED_IN', status.HTTP_409_CONFLICT,
                {'checked_in': len(present), 'registered': len(active)},
            )

        with transaction.atomic():
            for registration in absent:
                registration.status = 'disqualified'
                registration.forfeited_reason = 'Did not check in'
                registration.save(update_fields=['status', 'forfeited_reason'])
                removed.append({'registration_id': registration.id, 'name': _label(registration)})

    with transaction.atomic():
        tournament.status = 'registration_closed'
        tournament.check_in_closed_at = now
        tournament.save(update_fields=['status', 'check_in_closed_at'])

    return _ok({
        'checked_in': [{'registration_id': r.id, 'name': _label(r)} for r in present],
        'forfeited': removed,
        'not_checked_in': [{'registration_id': r.id, 'name': _label(r)} for r in absent],
        'forfeit_applied': bool(forfeit and removed),
        'remaining': len(present) if forfeit else len(active),
    }, (
        f'Check-in closed. {len(present)} in, {len(removed)} forfeited.' if removed
        else f'Check-in closed. {len(present)} of {len(active)} checked in.'
    ))


@api_view(['POST'])
def extend_check_in(request, tournament_id):
    """Push the start back so late arrivals are not punished for a bad window.

    Real tournaments run late. Moving the start moves the window with it,
    because the window is defined relative to the start rather than stored.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if tournament.tournament_creator_id != user.user_id and not user.is_staff:
        return _err('Only the organiser can extend check-in.', 'FORBIDDEN',
                    status.HTTP_403_FORBIDDEN)

    try:
        minutes = int(request.data.get('minutes') or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes < 1 or minutes > 240:
        return _err('Extend by between 1 and 240 minutes.', 'VALIDATION_FAILED')

    if tournament.check_in_closed_at:
        return _err(
            'Check-in was already closed for this tournament, so moving the start would '
            'not reopen it.',
            'ALREADY_CLOSED', status.HTTP_409_CONFLICT,
        )

    tournament.start_date_and_time = tournament.start_date_and_time + timedelta(minutes=minutes)
    fields = ['start_date_and_time']
    if tournament.end_date_and_time and tournament.end_date_and_time < tournament.start_date_and_time:
        tournament.end_date_and_time = tournament.start_date_and_time + timedelta(hours=1)
        fields.append('end_date_and_time')
    tournament.save(update_fields=fields)

    window = _window(tournament)
    return _ok({
        'start_date_and_time': tournament.start_date_and_time,
        'closes_at': window['closes_at'] if window else None,
    }, f'Start moved back {minutes} minutes. Check-in closes with it.')

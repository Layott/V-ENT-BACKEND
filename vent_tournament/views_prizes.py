"""Paying the winners: seeing it first, and setting it to happen on its own.

Two things the spec asks for that the platform did not have.

**A warning before coins leave.** `distribute-prizes` paid immediately, on one
press, with nothing shown first. So the money moved and the organiser found out
what had moved by reading the transactions afterwards. The plan below is the
same resolution the payout uses - same winners, same amounts, same order - and
the payout now refuses without `confirm`. A confirmation that says "are you
sure" warns nobody. One that names four people, four amounts and a total is
something somebody can check.

**Automated distribution.** An organiser sets a time. A cron job pays at that
time, and the organiser is told before it happens, not after. That is the whole
reason the schedule is a row: something has to remember that they were warned.

Premium, because the spec marks it so. The manual path stays free: charging for
the ability to pay your own winners would be indefensible.
"""
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import premium
from vent_auth.actors import actor_from_request, may_override

from . import lookup
from .models import PrizeSchedule
from .services import prizes as prize_service


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'data': data or {}, 'message': message,
                     'code': code}, status=http_status)


def _organiser(request, tournament):
    user, err = actor_from_request(request)
    if err:
        return None, err
    if tournament.tournament_creator_id == user.user_id:
        return user, None
    if may_override(user, 'cancel_tournament'):
        return user, None
    return None, _err('Only the organiser can do that with the prize money.',
                      'NOT_YOURS', status.HTTP_403_FORBIDDEN)


def _schedule_row(schedule):
    if schedule is None:
        return None
    return {
        'run_at': schedule.run_at,
        'warn_hours': schedule.warn_hours,
        'warn_at': schedule.warn_at(),
        'state': schedule.state,
        'warned_at': schedule.warned_at,
        'ran_at': schedule.ran_at,
        'problem': schedule.problem,
    }


@api_view(['GET'])
def prize_plan(request, tournament_id):
    """GET /tournament/<ref>/prizes/plan/ - who gets paid what, before anybody is.

    Organiser only: it names entrants and amounts, which is not a public list.
    """
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    _user, err = _organiser(request, tournament)
    if err:
        return err

    plan = prize_service.plan(tournament)
    schedule = getattr(tournament, 'prize_schedule', None)
    return _ok({
        **plan,
        'schedule': _schedule_row(schedule),
        # So the screen can show the automatic option as premium rather than
        # offering it and refusing on save.
        'has_premium': premium.has_premium(tournament),
    }, 'Prize plan')


@api_view(['POST', 'DELETE'])
def prize_schedule(request, tournament_id):
    """POST /tournament/<ref>/prizes/schedule/ - pay at a time, on its own.

    DELETE calls it off. A schedule that has already run cannot be changed:
    what happened, happened.
    """
    tournament = lookup.find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    user, err = _organiser(request, tournament)
    if err:
        return err

    existing = getattr(tournament, 'prize_schedule', None)

    if request.method == 'DELETE':
        if existing is None:
            return _err('There is no automatic payout to call off.', 'NOT_SCHEDULED',
                        status.HTTP_404_NOT_FOUND)
        if existing.state == 'paid':
            return _err('That payout has already run.', 'ALREADY_PAID',
                        status.HTTP_409_CONFLICT)
        existing.state = 'cancelled'
        existing.save(update_fields=['state'])
        return _ok({'schedule': _schedule_row(existing)},
                   'The automatic payout is called off.')

    if not premium.has_premium(tournament):
        return Response(premium.refuse('automated_prizes'),
                        status=status.HTTP_402_PAYMENT_REQUIRED)

    when = parse_datetime(str(request.data.get('run_at') or ''))
    if when is None:
        return _err('Say when the prizes should be paid.', 'VALIDATION_FAILED',
                    data={'field': 'run_at'})
    if timezone.is_naive(when):
        when = timezone.make_aware(when, timezone.utc)
    if when <= timezone.now():
        return _err('That time has already passed.', 'IN_THE_PAST',
                    data={'field': 'run_at'})

    try:
        warn_hours = int(request.data.get('warn_hours', 24))
    except (TypeError, ValueError):
        return _err('How long before is a number of hours.', 'VALIDATION_FAILED',
                    data={'field': 'warn_hours'})
    if not 0 <= warn_hours <= 168:
        return _err('Between 0 and 168 hours of notice.', 'VALIDATION_FAILED',
                    data={'field': 'warn_hours'})

    # A prize table has to exist, or the schedule is a promise to pay nobody.
    # Said now rather than at 3am when the job runs and does nothing.
    plan = prize_service.plan(tournament)
    if 'prize_distribution_missing' in plan['problems']:
        return _err('Set the prize positions first, so there is something to pay.',
                    'PRIZE_DISTRIBUTION_MISSING', status.HTTP_409_CONFLICT)
    if 'no_prize_configured' in plan['problems']:
        return _err('This tournament awards no prizes.', 'NO_PRIZE_CONFIGURED',
                    status.HTTP_409_CONFLICT)

    if existing is not None and existing.state == 'paid':
        return _err('The prizes for this tournament have already been paid.',
                    'ALREADY_PAID', status.HTTP_409_CONFLICT)

    schedule = existing or PrizeSchedule(tournament=tournament)
    schedule.run_at = when
    schedule.warn_hours = warn_hours
    schedule.state = 'scheduled'
    # Rescheduling clears the warning: somebody told about Friday has not been
    # told about Saturday.
    schedule.warned_at = None
    schedule.problem = ''
    schedule.created_by = user
    schedule.save()

    return _ok({'schedule': _schedule_row(schedule), 'plan': plan},
               'The prizes will be paid automatically.')

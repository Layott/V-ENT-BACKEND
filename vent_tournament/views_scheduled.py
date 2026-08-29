"""Reminders an organiser sets now for the platform to send later.

CEO, 29 August 2026: "organizers should be able to schedule reminders."

The sending itself already exists - `views_reminders.deliver()` is the one
definition, shared by the button and by the cron command. This is only the
diary: what to send, and when to send it.

**The time is an anchor plus an offset, not a timestamp.** "An hour before
check-in opens" is what an organiser means, and it is the version that survives
them moving the tournament. A timestamp computed at save time quietly points at
the wrong moment the first time a start date changes, which is the most common
edit there is. A fixed time is still available for the cases that genuinely are
one, and is stored as such.
"""
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import ScheduledReminder, Tournament
from .views_reminders import KINDS

# More than this and an organiser is writing a newsletter, not a reminder.
MAX_PER_TOURNAMENT = 10


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _tournament(ref):
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
    return None, _err('Only the tournament organizer can schedule reminders.',
                      'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def serialize(row):
    due = row.due_at()
    return {
        'id': row.id,
        'kind': row.kind,
        'subject': row.subject,
        'body': row.body,
        'anchor': row.anchor,
        'offset_minutes': row.offset_minutes,
        'fixed_at': row.fixed_at,
        # Computed fresh, so a moved tournament shows its moved reminder rather
        # than the time it would have gone out under the old date.
        'due_at': due,
        # Null when the tournament has no start time, or uses no check-in and
        # the anchor needs one. The screen says so rather than showing a blank
        # date, because "never" and "not set" are different problems.
        'schedulable': due is not None,
        'sent_at': row.sent_at,
        'cancelled_at': row.cancelled_at,
        'skipped_reason': row.skipped_reason,
        'people_reached': row.people_reached,
        'created_at': row.created_at,
    }


@api_view(['GET', 'POST'])
def scheduled_reminders(request, tournament_id):
    """GET what is in the diary. POST to add to it."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    _user, err = _organiser(request, tournament)
    if err:
        return err

    if request.method == 'GET':
        rows = ScheduledReminder.objects.filter(tournament=tournament)
        return Response({'status': 'success', 'data': {
            'scheduled': [serialize(r) for r in rows],
            'anchors': [{'value': v, 'label': label}
                        for v, label in ScheduledReminder.ANCHOR_CHOICES],
            'kinds': [{'value': v, 'label': label}
                      for v, label in ScheduledReminder.KIND_CHOICES],
        }, 'message': ''})

    pending = ScheduledReminder.objects.filter(
        tournament=tournament, sent_at__isnull=True,
        cancelled_at__isnull=True).count()
    if pending >= MAX_PER_TOURNAMENT:
        return _err('There are already %d reminders waiting to go out. Send or '
                    'cancel some before adding more.' % pending,
                    'TOO_MANY_SCHEDULED')

    kind = str(request.data.get('kind') or 'check_in').strip()
    if kind not in KINDS:
        return _err('Schedule a check-in reminder, a match reminder, or your '
                    'own message.', 'VALIDATION_ERROR')

    anchor = str(request.data.get('anchor') or 'check_in_opens').strip()
    if anchor not in dict(ScheduledReminder.ANCHOR_CHOICES):
        return _err('Measure it from the start, from check-in opening or '
                    'closing, or pick a time.', 'VALIDATION_ERROR')

    subject = str(request.data.get('subject') or '').strip()
    body = str(request.data.get('body') or '').strip()
    if kind == 'custom':
        if not subject or not body:
            return _err('Give the message a subject and something to say.',
                        'VALIDATION_ERROR')
        if len(subject) > 140 or len(body) > 2000:
            return _err('Keep the subject under 140 characters and the message '
                        'under 2000.', 'VALIDATION_ERROR')

    fixed_at = None
    offset_minutes = 0
    if anchor == 'fixed':
        raw = request.data.get('fixed_at')
        fixed_at = parse_datetime(str(raw)) if raw else None
        if fixed_at is None:
            return _err('That is not a date and time.', 'VALIDATION_ERROR')
        if timezone.is_naive(fixed_at):
            fixed_at = timezone.make_aware(fixed_at)
        if fixed_at <= timezone.now():
            return _err('That time has already passed, so it would never go '
                        'out.', 'VALIDATION_ERROR')
    else:
        try:
            offset_minutes = int(request.data.get('offset_minutes', 60))
        except (TypeError, ValueError):
            return _err('The offset has to be a number of minutes.',
                        'VALIDATION_ERROR')
        # A week either side. Negative means after the anchor, which is the
        # honest way to say "fifteen minutes into check-in".
        if abs(offset_minutes) > 60 * 24 * 7:
            return _err('Keep it within a week of whatever it is measured '
                        'from.', 'VALIDATION_ERROR')

    row = ScheduledReminder.objects.create(
        tournament=tournament, kind=kind, subject=subject, body=body,
        anchor=anchor, offset_minutes=offset_minutes, fixed_at=fixed_at,
        created_by=_user)

    # Answered rather than refused when it cannot be placed on the clock. A
    # tournament with no start time yet is a normal thing to be scheduling
    # around; the reminder simply waits until there is one.
    return Response({'status': 'success', 'data': {
        'scheduled': serialize(row),
    }, 'message': ''}, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
def cancel_scheduled_reminder(request, tournament_id, reminder_id):
    """Call it off. Cancelled rather than deleted, so the diary keeps its
    history and an organiser can see they changed their mind."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    _user, err = _organiser(request, tournament)
    if err:
        return err

    row = ScheduledReminder.objects.filter(tournament=tournament,
                                           pk=reminder_id).first()
    if row is None:
        return _err('No such scheduled reminder.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if row.sent_at:
        return _err('That one has already gone out.', 'ALREADY_SENT',
                    status.HTTP_409_CONFLICT)

    row.cancelled_at = timezone.now()
    row.save(update_fields=['cancelled_at'])
    return Response({'status': 'success', 'data': {'scheduled': serialize(row)},
                     'message': ''})

"""What a tournament came to, and telling the people in it something.

CEO, 7 September 2026, from the admin dashboard spec:

    View Tournament Analytics: access analytics related to tournament
    participation, performance metrics, and revenue generation.
    Send Notifications: send reminders and announcements to tournament
    participants.

The console could cancel a tournament, correct a score and disqualify a team,
and could not tell anybody it had done any of it. An event has had
announcements since 4 September (`vent_event/views_announce.py`); a tournament
had nothing, on either side of the fence.

## Why the announcement is not a new table

An event announcement is a row because a ticket holder is usually a guest with
no account, so the only record that the message existed is the one we write.
Everybody in a tournament HAS an account, by definition: you cannot register
for one without signing in. So the message lands in their notification inbox,
which is a real row they can read back, and the send itself is recorded as an
`AdminAction` carrying the subject, the body and how many people it reached.

That is the same record an EventAnnouncement gives, in the table this platform
already uses for "an admin did something and here is what". A second
announcement model would be a second place to look for the same question.

## The daily limit is the same number

Five a day, as for events. A different number on each would mean the answer to
"why did that not send" depends on which thing you are running.
"""
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import ROLE_PERMISSIONS, admin_role_required
from .models import AdminAction, Transaction
from .views_notifications import create_notification

READ_ROLES = ROLE_PERMISSIONS['manage_tournaments']
ANNOUNCE_ROLES = ROLE_PERMISSIONS['send_notifications']

DAILY_LIMIT = 5

# Who a message goes to. Named here so the screen and the endpoint cannot
# disagree about what "everybody" means.
AUDIENCES = {
    'all': 'Everybody registered',
    'confirmed': 'Confirmed entries only',
    'paid': 'People who paid an entry fee',
}


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _tournament(ref):
    """By slug first, by id for anything older. Slugs everywhere, ids nowhere
    that a person can see, but an API path may still carry one."""
    from vent_tournament.models import Tournament

    row = Tournament.objects.filter(slug=str(ref)).first()
    if row is None and str(ref).isdigit():
        row = Tournament.objects.filter(tournament_id=int(ref)).first()
    return row


@api_view(['GET'])
@admin_role_required(READ_ROLES)
def admin_tournament_analytics(request, tournament_ref):
    """Participation, revenue and how far through it is.

    Every number is counted from rows rather than read from a stored counter.
    A counter drifts the first time a refund or a disqualification happens, and
    then nobody can say which of the two numbers is true.
    """
    from vent_tournament.models import BracketMatch, TournamentRegistration

    tournament = _tournament(tournament_ref)
    if tournament is None:
        return _err('No tournament at that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    registrations = TournamentRegistration.objects.filter(
        tournament=tournament)
    counts = registrations.aggregate(
        total=Count('id'),
        confirmed=Count('id', filter=Q(status='confirmed')),
        pending=Count('id', filter=Q(status='pending')),
        disqualified=Count('id', filter=Q(status='disqualified')),
        withdrawn=Count('id', filter=Q(status='withdrawn')),
        paid=Count('id', filter=Q(entry_fee_paid=True)),
        checked_in=Count('id', filter=Q(checked_in_at__isnull=False)),
    )

    # Money is read off the ledger, filtered to this tournament, because that
    # is where it actually happened. Multiplying an entry fee by a headcount
    # gives a number that is wrong the moment one person is refunded.
    money = Transaction.objects.filter(tournament=tournament).aggregate(
        taken=Sum('amount', filter=Q(amount__lt=0, status='completed')),
        given=Sum('amount', filter=Q(amount__gt=0, status='completed')),
    )

    matches = BracketMatch.objects.filter(tournament=tournament).aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status='completed')),
        in_progress=Count('id', filter=Q(status='in_progress')),
    )

    disputes = tournament.disputes.aggregate(
        total=Count('id'),
        open=Count('id', filter=Q(status__in=['open', 'under_review'])),
    )

    # Thirty days of sign-ups, oldest first, so a screen can draw the shape of
    # the run-up rather than one final number.
    since = timezone.now() - timedelta(days=30)
    by_day = {}
    for row in registrations.filter(registered_at__gte=since):
        key = row.registered_at.date().isoformat()
        by_day[key] = by_day.get(key, 0) + 1

    capacity = tournament.max_number_of_teams or 0
    entered = counts['total'] or 0

    return _ok({
        'tournament': {
            'id': tournament.tournament_id,
            'slug': tournament.slug,
            'name': tournament.tournament_title,
            'status': tournament.status,
            'game': (tournament.tournament_game.game_title
                     if tournament.tournament_game_id else ''),
            'organizer': (tournament.tournament_creator.username
                          if tournament.tournament_creator_id else ''),
            'starts': (tournament.start_date_and_time.isoformat()
                       if tournament.start_date_and_time else None),
        },
        'participation': {
            'entered': entered,
            'confirmed': counts['confirmed'] or 0,
            'pending': counts['pending'] or 0,
            'disqualified': counts['disqualified'] or 0,
            'withdrawn': counts['withdrawn'] or 0,
            'checked_in': counts['checked_in'] or 0,
            'capacity': capacity,
            # Null rather than zero when there is no cap. A fill rate of 0%
            # on an uncapped tournament is a number that means nothing and
            # reads as a failure.
            'fill_percent': (round(entered * 100.0 / capacity, 1)
                             if capacity else None),
            'by_day': [{'day': day, 'entries': by_day[day]}
                       for day in sorted(by_day)],
        },
        'revenue': {
            'entry_fee_vc': int(tournament.entry_fee_price or 0),
            'taken_vc': abs(money['taken'] or 0),
            'returned_vc': money['given'] or 0,
            'net_vc': abs(money['taken'] or 0) - (money['given'] or 0),
            'paid_entries': counts['paid'] or 0,
        },
        'matches': {
            'total': matches['total'] or 0,
            'completed': matches['completed'] or 0,
            'in_progress': matches['in_progress'] or 0,
            'disputes': disputes['total'] or 0,
            'open_disputes': disputes['open'] or 0,
        },
    })


def _sent_today(tournament):
    since = timezone.now() - timedelta(days=1)
    return AdminAction.objects.filter(
        action_type='announce_tournament', target_model='Tournament',
        target_id=str(tournament.tournament_id),
        performed_at__gte=since).count()


def _announcement_row(action):
    meta = action.metadata or {}
    return {
        'id': action.id,
        'subject': meta.get('subject', ''),
        'body': meta.get('body', ''),
        'audience': meta.get('audience', 'all'),
        'recipients': meta.get('recipients', 0),
        'sent_at': action.performed_at.isoformat() if action.performed_at else None,
        'sent_by': action.admin.username if action.admin_id else '',
    }


@api_view(['GET', 'POST'])
@admin_role_required(ANNOUNCE_ROLES)
def admin_tournament_announce(request, tournament_ref):
    """GET what has been sent. POST sends one.

    Every recipient gets a notification in their inbox and an email, and the
    send is written down before either goes out, so a message that half
    delivers is a record with a count on it rather than an event nobody can
    prove happened.
    """
    from vent_tournament.models import TournamentRegistration

    tournament = _tournament(tournament_ref)
    if tournament is None:
        return _err('No tournament at that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        rows = AdminAction.objects.filter(
            action_type='announce_tournament', target_model='Tournament',
            target_id=str(tournament.tournament_id)
        ).select_related('admin').order_by('-performed_at')[:50]
        return _ok({
            'announcements': [_announcement_row(a) for a in rows],
            'audiences': [{'value': v, 'label': label}
                          for v, label in AUDIENCES.items()],
            'sent_today': _sent_today(tournament),
            'daily_limit': DAILY_LIMIT,
        })

    admin = request.admin_user
    subject = str(request.data.get('subject') or '').strip()
    body = str(request.data.get('body') or '').strip()
    audience = str(request.data.get('audience') or 'all').strip().lower()

    if not subject:
        return _err('Give the message a subject.', 'VALIDATION_ERROR')
    if not body:
        return _err('The message is empty.', 'VALIDATION_ERROR')
    if len(subject) > 140:
        return _err('Keep the subject under 140 characters.',
                    'VALIDATION_ERROR')
    if len(body) > 2000:
        return _err('Keep the message under 2000 characters.',
                    'VALIDATION_ERROR')
    if audience not in AUDIENCES:
        return _err('Send to everybody, to confirmed entries, or to people '
                    'who paid.', 'VALIDATION_ERROR')

    already = _sent_today(tournament)
    if already >= DAILY_LIMIT:
        return _err('That is %d messages about this tournament today. People '
                    'stop reading, so the rest have to wait until tomorrow.'
                    % already, 'RATE_LIMITED',
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    extra={'limit': DAILY_LIMIT, 'sent_today': already})

    rows = TournamentRegistration.objects.filter(
        tournament=tournament).exclude(
            status__in=['withdrawn', 'disqualified']).select_related(
                'user', 'team')
    if audience == 'confirmed':
        rows = rows.filter(status='confirmed')
    elif audience == 'paid':
        rows = rows.filter(entry_fee_paid=True)

    # One message per person, not per registration. Somebody entered as a
    # player and as a team captain is one person and is told once.
    people = {}
    for row in rows:
        if row.user_id and row.user_id not in people:
            people[row.user_id] = row.user
        if row.team_id:
            from .models import TeamMembers
            for member in TeamMembers.objects.filter(
                    team_id=row.team_id).select_related('user'):
                if member.user_id and member.user_id not in people:
                    people[member.user_id] = member.user

    link = '/tournaments/%s' % (tournament.slug or tournament.tournament_id)

    record = AdminAction.objects.create(
        admin=admin, action_type='announce_tournament',
        target_model='Tournament', target_id=str(tournament.tournament_id),
        reason=subject,
        metadata={'subject': subject, 'body': body, 'audience': audience,
                  'recipients': len(people),
                  'tournament': tournament.tournament_title})

    for user in people.values():
        create_notification(user, 'tournament', subject, body=body[:500],
                            link=link,
                            metadata={'tournament_id': tournament.tournament_id,
                                      'announcement': record.id})

    failures = 0
    from . import emails
    for user in people.values():
        address = (user.email or '').strip().lower()
        if not address:
            continue
        try:
            if not emails.send_tournament_announcement(
                    address, tournament=tournament, subject=subject, body=body):
                failures += 1
        except Exception:                                        # noqa: BLE001
            failures += 1

    if failures:
        meta = record.metadata or {}
        meta['email_error'] = '%d of %d emails did not send.' % (
            failures, len(people))
        record.metadata = meta
        record.save(update_fields=['metadata'])

    return _ok({'announcement': _announcement_row(record)},
               'Sent to %d people.' % len(people))

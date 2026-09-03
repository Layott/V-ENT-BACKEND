"""Telling entrants what they have to do, before they miss it.

PRD section 3: check-in and match reminders.

Two things cost an entrant their place, and both are silent. Check-in opens
thirty minutes before the first match and closes at the start; miss it and
`forfeit_without_check_in` removes you. And a fixture becomes yours the moment
somebody else finishes theirs, which is not a moment anybody is watching for.

There is no scheduler on this deployment - Celery is installed and no task has
ever been defined - so this is not a cron that fires by itself. It is the button
an organiser presses, and it is deliberately built as one:

- **check_in** goes to everybody who has NOT checked in yet. Sending it to
  people who already did is how a reminder becomes something entrants filter.
- **match** goes to both sides of every fixture still to be played, and names
  the opponent, the round and the time. A reminder that does not say who you are
  playing sends the entrant looking for the bracket.
- **custom** is the organiser's own words to every confirmed entrant.

A team registration reaches every member. The captain is not reliably the person
who turns up, and one member reading it is what actually prevents the forfeit.
"""
import logging
import secrets

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth.models import TeamMembers
from vent_auth.views_notifications import create_notification

from .models import BracketMatch, Tournament, TournamentRegistration
from .options import check_in_state

logger = logging.getLogger(__name__)

DAILY_LIMIT = 5
KINDS = ('check_in', 'match', 'custom')


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
    return None, _err('Only the tournament organizer can send this.',
                      'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def people_behind(registration):
    """Every user a registration reaches.

    A team registration reaches every member, not the captain. The captain is
    not reliably the person who turns up, and one member reading it is what
    prevents the forfeit.
    """
    # `people` on the registration knows all three kinds of side. This branch
    # knew two, so nobody in a squad was ever reminded of anything.
    return list(registration.people)


def _entrant_name(registration):
    # `entrant_name` covers a club, a lone player and a squad. Branching
    # here by hand is how a squad came back blank.
    if registration is None:
        return 'an opponent yet to be decided'
    return (getattr(registration, 'entrant_name', '')
            or 'an opponent yet to be decided')


def _link(tournament):
    return '/tournaments/%s' % (tournament.slug or tournament.tournament_id)


def _check_in_targets(tournament):
    """Confirmed entrants who have not checked in. Nobody else."""
    return list(TournamentRegistration.objects
                .filter(tournament=tournament, status='confirmed',
                        checked_in_at__isnull=True)
                .select_related('team', 'user'))


def _match_messages(tournament):
    """(registration, title, body) for every side of every unplayed fixture."""
    out = []
    fixtures = (BracketMatch.objects.filter(tournament=tournament)
                .exclude(status__in=('completed', 'bye'))
                .select_related('participant_1__team', 'participant_1__user',
                                'participant_2__team', 'participant_2__user')
                .order_by('round_number', 'match_number'))
    for tie in fixtures:
        when = ''
        if tie.scheduled_at:
            when = ' at %s' % timezone.localtime(
                tie.scheduled_at).strftime('%d %b, %H:%M')
        elif tie.day:
            when = ' on %s' % tie.day.strftime('%d %b')
        for side, other in ((tie.participant_1, tie.participant_2),
                            (tie.participant_2, tie.participant_1)):
            if side is None:
                continue
            out.append((
                side,
                'Round %d: you play %s' % (tie.round_number, _entrant_name(other)),
                'Match %d of round %d%s. Be ready.'
                % (tie.match_number, tie.round_number, when),
            ))
    return out


@api_view(['GET'])
def reminder_audience(request, tournament_id):
    """How many each reminder would reach, before anybody writes one.

    Sending "check in" to nobody, because everybody already did, is how an
    organiser sends it twice.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    _user, err = _organiser(request, tournament)
    if err:
        return err

    window = check_in_state(tournament, timezone.now())
    confirmed = TournamentRegistration.objects.filter(
        tournament=tournament, status='confirmed')

    return Response({'status': 'success', 'data': {
        'check_in': {
            'used': window is not None,
            'entrants': len(_check_in_targets(tournament)),
            'window': window,
        },
        'match': {'sides': len(_match_messages(tournament))},
        'custom': {'entrants': confirmed.count()},
        'sent_today': _sent_today(tournament),
        'daily_limit': DAILY_LIMIT,
    }, 'message': ''})


def _sent_today(tournament):
    """Reminders this tournament has sent in the last day.

    Counted from the notification rows themselves rather than a separate table.
    There is one row per person, so the count is of distinct sends, keyed by the
    metadata this module writes.
    """
    from vent_auth.models import Notification
    since = timezone.now() - timezone.timedelta(days=1)
    rows = Notification.objects.filter(
        category='tournament', created_at__gte=since,
        metadata__reminder_for=tournament.tournament_id)
    return len({r.metadata.get('batch') for r in rows if r.metadata.get('batch')})


class ReminderRefused(Exception):
    """Why a reminder cannot be sent, in a form both callers can use.

    The button turns it into a 400 and the scheduler writes it onto the row.
    """

    def __init__(self, message, code, extra=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra or {}


def deliver(tournament, kind, *, subject='', body='', enforce_limit=True):
    """Send one reminder. The single definition, used by both callers.

    The button and the scheduler must not each carry their own copy of this.
    The poll read path and vote path drifted apart exactly that way earlier
    tonight, and the half nobody exercised was the half that was wrong.

    Raises `ReminderRefused` rather than returning an error response, because
    one caller answers over HTTP and the other writes to a database row.
    """
    if kind not in KINDS:
        raise ReminderRefused(
            'Send a check-in reminder, a match reminder, or your own message.',
            'VALIDATION_ERROR')

    if enforce_limit:
        already = _sent_today(tournament)
        if already >= DAILY_LIMIT:
            raise ReminderRefused(
                'That is %d reminders today. Entrants stop reading, so the '
                'rest have to wait until tomorrow.' % already, 'RATE_LIMITED',
                {'limit': DAILY_LIMIT, 'sent_today': already})

    # One id shared by every row in this send, so a batch can be counted as one
    # reminder rather than as however many people are in the tournament. The
    # random tail is what makes it one PER SEND: keyed on the clock alone, two
    # reminders in the same second shared an id, were counted as one, and the
    # daily limit could be walked straight past.
    batch = '%s-%s-%d-%s' % (kind, timezone.now().strftime('%Y%m%d%H%M%S'),
                             tournament.tournament_id, secrets.token_hex(4))
    link = _link(tournament)
    messages = []

    if kind == 'check_in':
        window = check_in_state(tournament, timezone.now())
        if window is None:
            raise ReminderRefused('This tournament does not use check-in.',
                                  'NOT_REQUIRED')
        closes = timezone.localtime(window['closes_at']).strftime('%H:%M') \
            if window.get('closes_at') else 'the start'
        title = 'Check in for %s' % tournament.tournament_title
        line = ('Check-in closes at %s. %s' % (
            closes,
            'Miss it and your slot goes to a substitute.'
            if window.get('forfeit_without_check_in')
            else 'Check in so the organiser knows you are coming.'))
        for registration in _check_in_targets(tournament):
            messages.append((registration, title, line))

    elif kind == 'match':
        messages = _match_messages(tournament)

    else:
        subject = str(subject or '').strip()
        body = str(body or '').strip()
        if not subject or not body:
            raise ReminderRefused(
                'Give the message a subject and something to say.',
                'VALIDATION_ERROR')
        if len(subject) > 140 or len(body) > 2000:
            raise ReminderRefused(
                'Keep the subject under 140 characters and the message under '
                '2000.', 'VALIDATION_ERROR')
        for registration in (TournamentRegistration.objects
                             .filter(tournament=tournament, status='confirmed')
                             .select_related('team', 'user')):
            messages.append((registration, subject, body))

    reached = set()
    for registration, title, line in messages:
        for person in people_behind(registration):
            create_notification(
                person, 'tournament', title, body=line, link=link,
                metadata={'reminder_for': tournament.tournament_id,
                          'batch': batch, 'kind': kind})
            reached.add(person.user_id)

    return {'kind': kind, 'entrants': len(messages), 'people': len(reached),
            'batch': batch}


@api_view(['POST'])
def send_reminder(request, tournament_id):
    """`kind=check_in|match|custom`, sent now. Answers with who it reached."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    _user, err = _organiser(request, tournament)
    if err:
        return err

    try:
        result = deliver(
            tournament, str(request.data.get('kind') or 'check_in').strip(),
            subject=request.data.get('subject'),
            body=request.data.get('body'))
    except ReminderRefused as refusal:
        http = (status.HTTP_429_TOO_MANY_REQUESTS
                if refusal.code == 'RATE_LIMITED'
                else status.HTTP_400_BAD_REQUEST)
        return _err(refusal.message, refusal.code, http, extra=refusal.extra)

    return Response({'status': 'success', 'data': result, 'message': ''})

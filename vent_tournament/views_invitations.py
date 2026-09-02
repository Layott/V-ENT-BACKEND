"""Asking a named player or a named team to enter a tournament.

CEO, 29 August 2026: "tournament organizers, should be able to invite people or
teams to their events."

There were already invite codes, which are a different thing: an organiser makes
sixty-four of them and hands them out, and whoever holds one can spend it. That
works for "the Lagos lot" and not at all for "I want these four teams in this
bracket", where the organiser ends up keeping a spreadsheet of which code went
to whom and chasing the ones that never got used.

An invitation is addressed. It names who it is for, it tells them, and they
accept or decline. The organiser's list is then the answer to "who have I asked
and what did they say", which is the question they actually have.

    GET    /tournament/<t>/invitations/        the organiser's list
    POST   /tournament/<t>/invitations/        ask somebody
    DELETE /tournament/<t>/invitations/<id>/   withdraw it
    POST   /tournament/<t>/invitations/<id>/respond/   accept or decline

Accepting does not register anybody. It says yes, and the organiser or the
player completes registration through the ordinary path, which is the one that
checks entry requirements and takes the entry fee. An invitation that quietly
registered somebody would be an invitation that quietly charged them.
"""

from django.db import models
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.models import Teams, Users

from .models import Tournament, TournamentInvitation


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _tournament(key):
    if str(key).isdigit():
        found = Tournament.objects.filter(tournament_id=int(key)).first()
        if found:
            return found
    return Tournament.objects.filter(slug=str(key)).first()


def _may_manage(user, tournament):
    return user is not None and tournament.tournament_creator_id == user.user_id


def _may_answer(user, invitation):
    """Who gets to say yes.

    A player answers their own invitation. A team's is answered by whoever owns
    the team, because entering a tournament commits the roster.
    """
    if user is None:
        return False
    if invitation.user_id:
        return invitation.user_id == user.user_id
    if invitation.team_id:
        return invitation.team.team_owner_id == user.user_id
    return False


def serialize(invitation):
    return {
        'id': invitation.id,
        'status': invitation.status,
        'message': invitation.message,
        'created_at': invitation.created_at,
        'answered_at': invitation.answered_at,
        'player': ({'username': invitation.user.username,
                    'full_name': invitation.user.full_name}
                   if invitation.user_id else None),
        'team': ({'name': invitation.team.team_name,
                  'slug': getattr(invitation.team, 'slug', None)}
                 if invitation.team_id else None),
    }


def _tell_them(invitation, tournament):
    """Say it, once, to whoever can answer it."""
    if invitation.user_id:
        recipient = invitation.user
    elif invitation.team_id:
        recipient = invitation.team.team_owner
    else:
        return
    if recipient is None:
        return
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            user=recipient, category='tournament',
            title='You have been invited to %s' % tournament.tournament_title,
            body=invitation.message or 'The organiser asked you to enter.',
            link='/tournaments/%s' % (tournament.slug or tournament.tournament_id),
            metadata={'invitation_id': invitation.id},
        )
    except Exception:                                       # noqa: BLE001
        # An invitation that exists and was not announced is recoverable: the
        # organiser can see it pending and chase. One that failed to be created
        # because the notification failed is not.
        pass


@api_view(['GET', 'POST'])
def invitations(request, tournament_id):
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _error('Sign in first.', 'AUTH_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(user, tournament):
        return _error('Only the organiser can see or send invitations.',
                      'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        rows = (TournamentInvitation.objects
                .filter(tournament=tournament)
                .select_related('user', 'team'))
        return _ok({'invitations': [serialize(i) for i in rows],
                    'count': rows.count()})

    username = str(request.data.get('username') or '').strip()
    team_ref = str(request.data.get('team') or '').strip()
    if bool(username) == bool(team_ref):
        return _error('Name either a player or a team.', 'VALIDATION_ERROR')

    invited_user = None
    invited_team = None
    if username:
        invited_user = Users.objects.filter(username__iexact=username).first()
        if invited_user is None:
            return _error('No player by that name.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        if invited_user.user_id == user.user_id:
            return _error('You do not need to invite yourself.',
                          'VALIDATION_ERROR')
    else:
        invited_team = Teams.objects.filter(team_name__iexact=team_ref).first()
        if invited_team is None and str(team_ref).isdigit():
            invited_team = Teams.objects.filter(team_id=int(team_ref)).first()
        if invited_team is None:
            return _error('No team by that name.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)

    existing = TournamentInvitation.objects.filter(
        tournament=tournament, user=invited_user, team=invited_team).first()
    if existing is not None:
        if existing.status == TournamentInvitation.PENDING:
            # Asking again is a reminder, not a second invitation.
            _tell_them(existing, tournament)
            return _ok({'invitation': serialize(existing), 'reminded': True},
                       'They have been reminded.')
        if existing.status == TournamentInvitation.ACCEPTED:
            return _error('They have already accepted.', 'ALREADY_ACCEPTED',
                          status.HTTP_409_CONFLICT)
        # Declined or withdrawn: asking again reopens the same row, so the
        # recipient's list does not fill up with the same tournament.
        existing.status = TournamentInvitation.PENDING
        existing.answered_at = None
        existing.message = str(request.data.get('message') or '').strip()[:280]
        existing.save(update_fields=['status', 'answered_at', 'message'])
        _tell_them(existing, tournament)
        return _ok({'invitation': serialize(existing)}, 'Asked again.')

    invitation = TournamentInvitation.objects.create(
        tournament=tournament, user=invited_user, team=invited_team,
        message=str(request.data.get('message') or '').strip()[:280],
        invited_by=user)
    _tell_them(invitation, tournament)
    return Response({'status': 'success',
                     'data': {'invitation': serialize(invitation)},
                     'message': 'Invitation sent.'},
                    status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
def invitation_detail(request, tournament_id, invitation_id):
    """The organiser taking it back."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if not _may_manage(user, tournament):
        return _error('Only the organiser can withdraw an invitation.',
                      'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    invitation = TournamentInvitation.objects.filter(
        tournament=tournament, pk=invitation_id).first()
    if invitation is None:
        return _error('Invitation not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    # Withdrawn rather than deleted: the recipient was told about it, and a row
    # that vanishes leaves them with a notification pointing at nothing.
    invitation.status = TournamentInvitation.WITHDRAWN
    invitation.answered_at = timezone.now()
    invitation.save(update_fields=['status', 'answered_at'])
    return _ok({'invitation': serialize(invitation)}, 'Invitation withdrawn.')


@api_view(['POST'])
def respond(request, tournament_id, invitation_id):
    """The player, or the team's owner, saying yes or no."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _error('Sign in first.', 'AUTH_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)

    invitation = (TournamentInvitation.objects
                  .select_related('user', 'team')
                  .filter(tournament=tournament, pk=invitation_id).first())
    if invitation is None:
        return _error('Invitation not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    if not _may_answer(user, invitation):
        return _error('This invitation is not yours to answer.',
                      'NOT_YOURS', status.HTTP_403_FORBIDDEN)
    if invitation.status == TournamentInvitation.WITHDRAWN:
        return _error('The organiser withdrew this invitation.',
                      'WITHDRAWN', status.HTTP_409_CONFLICT)

    answer = str(request.data.get('answer') or '').strip().lower()
    if answer not in ('accept', 'decline'):
        return _error('Answer with accept or decline.', 'VALIDATION_ERROR')

    invitation.status = (TournamentInvitation.ACCEPTED if answer == 'accept'
                         else TournamentInvitation.DECLINED)
    invitation.answered_at = timezone.now()
    invitation.save(update_fields=['status', 'answered_at'])

    try:
        from vent_auth.views_notifications import create_notification
        who = (invitation.team.team_name if invitation.team_id
               else invitation.user.username)
        create_notification(
            user=tournament.tournament_creator, category='tournament',
            title='%s %sed your invitation' % (who, answer),
            body=tournament.tournament_title,
            link='/tournaments/%s/manage' % (tournament.slug
                                             or tournament.tournament_id),
            metadata={'invitation_id': invitation.id},
        )
    except Exception:                                       # noqa: BLE001
        pass

    return _ok({
        'invitation': serialize(invitation),
        # Accepting is not registering. Registration is the path that checks the
        # entry requirements and takes the entry fee, and an invitation that
        # quietly did that would be one that quietly charged somebody.
        'next': ('/tournaments/%s/register' % (tournament.slug
                                               or tournament.tournament_id)
                 if answer == 'accept' else None),
    }, 'Answer recorded.')


@api_view(['GET'])
def my_invitation(request, tournament_id):
    """The invitation this viewer can answer, if there is one.

    The organiser's list is theirs alone, so without this the recipient has no
    way to find the thing they were told about: the notification links to the
    tournament, and the tournament page had nothing to show them. An endpoint
    they cannot reach makes the accept and decline endpoint unreachable too,
    which is what `tools/endpoint-callers.py` caught.

    Answers `{invitation: null}` rather than 404 when there is none, because
    "you have not been invited" is a normal state of this page, not an error.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _ok({'invitation': None})

    # Theirs, or one addressed to a team they own.
    from vent_auth.models import Teams

    owned = list(Teams.objects.filter(team_owner=user).values_list('team_id', flat=True))
    invitation = (TournamentInvitation.objects
                  .select_related('user', 'team')
                  .filter(tournament=tournament, status=TournamentInvitation.PENDING)
                  .filter(models.Q(user=user) | models.Q(team_id__in=owned))
                  .first())
    return _ok({'invitation': serialize(invitation) if invitation else None})

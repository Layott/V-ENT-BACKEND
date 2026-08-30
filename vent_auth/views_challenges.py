"""The rest of a challenge's life: editing it, calling it off, agreeing what
happened, and remembering it afterwards.

CEO, 29-30 August 2026: "after you create challenge the user should be able to
edit it also. also when another user logs in and they see a challenge, how does
it look and if they choose to join or accept, does it work and what is the flow,
when it does work, they should then be able to talk with themselves to send
details and then record results also, the results should also show on their
profiles as history and challenges should also show past matches and games and
the data also."

Posting and accepting already existed. Everything after the handshake did not:
an accepted challenge simply sat there, with no way to change it, call it off,
talk about it, say who won, or find it again a week later.

The one decision worth stating: a result is reported by one side and confirmed
by the other. A scrim has no referee - no bracket, no organiser, nobody
watching - so whatever one player types is the only account of what happened.
If reporting were enough, the record would be whatever the faster typist
claimed, and a history built on that is worse than none, because people would
rely on it.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Conversation, Scrim, ScrimResult, TeamMembers, Teams, Users


def _ok(data, message='OK', http=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _authenticate(request):
    from .views_community import _authenticate as shared
    return shared(request)


def _scrim_by_ref(ref):
    """A challenge by its opaque token, or by its id for an older link."""
    ref = str(ref).strip()
    row = Scrim.objects.select_related(
        'team', 'player', 'opponent', 'opponent_player',
        'challenged', 'challenged_player', 'game', 'created_by',
    ).filter(slug=ref).first()
    if row is None and ref.isdigit():
        row = Scrim.objects.select_related(
            'team', 'player', 'opponent', 'opponent_player',
            'challenged', 'challenged_player', 'game', 'created_by',
        ).filter(id=int(ref)).first()
    return row


def side_of(scrim, user):
    """Which side of this challenge somebody is on: 'a', 'b' or None.

    'a' is whoever posted it and 'b' is whoever accepted, and that never
    changes, so a score always means the same thing however it is read.
    """
    if user is None:
        return None
    if scrim.is_solo:
        if scrim.player_id == user.user_id:
            return 'a'
        if scrim.opponent_player_id == user.user_id:
            return 'b'
        return None

    def in_team(team_id):
        if not team_id:
            return False
        team = Teams.objects.filter(team_id=team_id).first()
        if team is None:
            return False
        if team.team_owner_id == user.user_id:
            return True
        return TeamMembers.objects.filter(team=team, user=user).exists()

    if in_team(scrim.team_id):
        return 'a'
    if in_team(scrim.opponent_id):
        return 'b'
    return None


def serialize_result(request, result):
    from .views_community import _person
    if result is None:
        return None
    return {
        'score_a': result.score_a,
        'score_b': result.score_b,
        'status': result.status,
        'winner': result.winner if result.status == 'confirmed' else None,
        'note': result.note,
        'reported_by': _person(request, result.reported_by),
        'reported_at': result.reported_at,
        'confirmed_by': _person(request, result.confirmed_by) if result.confirmed_by_id else None,
        'confirmed_at': result.confirmed_at,
        # Both accounts of a disagreement, side by side. A dispute carrying
        # only one set of numbers is not a dispute, it is a rewrite.
        'disputed': None if result.status != 'disputed' else {
            'score_a': result.disputed_score_a,
            'score_b': result.disputed_score_b,
            'by': _person(request, result.disputed_by) if result.disputed_by_id else None,
            'at': result.disputed_at,
        },
    }


# ---------------------------------------------------------------------------
# editing and calling off
# ---------------------------------------------------------------------------

@api_view(['GET', 'PATCH', 'DELETE'])
def challenge_detail(request, scrim_id):
    """Read one challenge, change it, or call it off."""
    from .views_community import serialize_scrim

    scrim = _scrim_by_ref(scrim_id)
    if scrim is None:
        return _err('Challenge not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        from .views_community import _optional_user
        viewer = _optional_user(request)
        row = serialize_scrim(request, scrim, viewer)
        row['result'] = serialize_result(request, getattr(scrim, 'result', None))
        row['my_side'] = side_of(scrim, viewer)
        return _ok({'scrim': row}, 'Challenge retrieved.')

    user, err = _authenticate(request)
    if err:
        return err
    if scrim.created_by_id != user.user_id:
        return _err('Only whoever posted this can change it.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        if scrim.status == 'played':
            return _err('That challenge has already been played.', 'STATE_CONFLICT',
                        status.HTTP_409_CONFLICT)
        scrim.status = 'cancelled'
        scrim.save(update_fields=['status'])
        # Whoever accepted it planned their evening around this.
        _tell_other_side(scrim, user, 'called off the challenge')
        return _ok({'scrim': serialize_scrim(request, scrim, user)}, 'Called off.')

    # PATCH. Only while it is still open: once somebody has accepted, the terms
    # are what they agreed to, and quietly changing the format or the time
    # under them is worse than refusing the edit.
    if scrim.status != 'open':
        return _err('Somebody has already accepted this, so the terms are '
                    'settled. Call it off if it has to change.',
                    'ALREADY_ACCEPTED', status.HTTP_409_CONFLICT)

    from .game_modes import mode_for, modes_for

    data = request.data
    game_title = scrim.game.game_title if scrim.game else ''

    if 'mode' in data:
        mode = mode_for(game_title, (data.get('mode') or '').strip())
        if mode is None:
            return _err('That is not a way this game is played.', 'UNKNOWN_MODE')
        scrim.mode = mode['id']
        # A format from the old mode cannot survive a mode change.
        if scrim.match_format not in mode['formats']:
            scrim.match_format = mode['formats'][0]

    current_mode = mode_for(game_title, scrim.mode) or modes_for(game_title)[0]

    if 'format' in data:
        fmt = (data.get('format') or '').strip()
        if fmt and fmt not in current_mode['formats']:
            return _err(f"{current_mode['label']} is not played as \"{fmt}\".",
                        'UNKNOWN_FORMAT')
        scrim.match_format = fmt or scrim.match_format

    if 'open_to' in data:
        open_to = (data.get('open_to') or '').strip()
        if open_to not in dict(Scrim.OPEN_TO_CHOICES):
            return _err('That is not a way to choose who may answer.', 'BAD_OPEN_TO')
        scrim.open_to = open_to

    if 'countries' in data:
        countries = data.get('countries') or []
        if not isinstance(countries, list):
            return _err('Countries must be a list.', 'VALIDATION_ERROR')
        scrim.countries = [str(c)[:60] for c in countries][:40]

    for field, cap in (('country', 60), ('map_code', 40), ('notes', 280)):
        if field in data:
            setattr(scrim, field, (data.get(field) or '').strip()[:cap])

    if 'scheduled_for' in data or 'scheduled_at' in data:
        scrim.scheduled_for = data.get('scheduled_for') or data.get('scheduled_at') or None

    if scrim.open_to == 'countries' and not scrim.countries:
        return _err('Choose at least one country, or open it to everybody.',
                    'NO_COUNTRIES')

    scrim.save()
    return _ok({'scrim': serialize_scrim(request, scrim, user)}, 'Challenge updated.')


def _tell_other_side(scrim, actor, what):
    """Notify whoever is on the other side of this challenge."""
    from .views_notifications import create_notification

    targets = []
    if scrim.is_solo:
        for uid in (scrim.player_id, scrim.opponent_player_id):
            if uid and uid != actor.user_id:
                targets.append(Users.objects.filter(user_id=uid).first())
    else:
        for team_id in (scrim.team_id, scrim.opponent_id):
            team = Teams.objects.filter(team_id=team_id).first() if team_id else None
            if team and team.team_owner_id != actor.user_id:
                targets.append(team.team_owner)

    for target in targets:
        if target is None:
            continue
        try:
            create_notification(
                user=target, category='team',
                title=f'{actor.username} {what}',
                body=scrim.notes or '',
                link=f'/community/challenge/{scrim.slug or scrim.id}',
                metadata={'scrim_id': scrim.id},
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# talking about it
# ---------------------------------------------------------------------------

@api_view(['POST'])
def challenge_conversation(request, scrim_id):
    """Open the direct message with the other side.

    CEO: "they should then be able to talk with themselves to send details".

    Answers with the conversation's address rather than sending anything, so
    the page can put somebody straight into the thread. A solo challenge has an
    obvious other person; a team challenge uses the two owners, because a
    conversation is between two people and a team is not a person.
    """
    user, err = _authenticate(request)
    if err:
        return err

    scrim = _scrim_by_ref(scrim_id)
    if scrim is None:
        return _err('Challenge not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if side_of(scrim, user) is None:
        return _err('You are not part of this challenge.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    if scrim.status not in ('accepted', 'played'):
        return _err('Nobody has accepted this yet, so there is nobody to talk to.',
                    'NOT_ACCEPTED', status.HTTP_409_CONFLICT)

    if scrim.is_solo:
        other_id = (scrim.opponent_player_id if scrim.player_id == user.user_id
                    else scrim.player_id)
        other = Users.objects.filter(user_id=other_id).first()
    else:
        mine = side_of(scrim, user)
        other_team_id = scrim.opponent_id if mine == 'a' else scrim.team_id
        team = Teams.objects.filter(team_id=other_team_id).first()
        other = team.team_owner if team else None

    if other is None or other.user_id == user.user_id:
        return _err('There is nobody on the other side yet.', 'NO_OPPONENT',
                    status.HTTP_409_CONFLICT)

    convo = (Conversation.objects.filter(user_a=user, user_b=other).first()
             or Conversation.objects.filter(user_a=other, user_b=user).first())
    if convo is None:
        convo = Conversation.objects.create(user_a=user, user_b=other)

    from .views_community import _person
    return _ok({'conversation': {'id': convo.id, 'slug': convo.slug},
                'with': _person(request, other),
                'url': f'/community/dm?id={convo.slug or convo.id}'},
               'Conversation ready.')


# ---------------------------------------------------------------------------
# the result
# ---------------------------------------------------------------------------

@api_view(['POST'])
def report_result(request, scrim_id):
    """Say what the score was. The other side has to agree before it counts."""
    user, err = _authenticate(request)
    if err:
        return err

    scrim = _scrim_by_ref(scrim_id)
    if scrim is None:
        return _err('Challenge not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    mine = side_of(scrim, user)
    if mine is None:
        return _err('You are not part of this challenge.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    if scrim.status not in ('accepted', 'played'):
        return _err('That challenge has not been played yet.', 'NOT_ACCEPTED',
                    status.HTTP_409_CONFLICT)

    try:
        score_a = int(request.data.get('score_a'))
        score_b = int(request.data.get('score_b'))
    except (TypeError, ValueError):
        return _err('Both scores have to be numbers.', 'VALIDATION_ERROR')
    if score_a < 0 or score_b < 0:
        return _err('A score cannot be negative.', 'VALIDATION_ERROR')

    existing = ScrimResult.objects.filter(scrim=scrim).first()
    if existing is not None and existing.status == 'confirmed':
        return _err('That result is already agreed.', 'ALREADY_CONFIRMED',
                    status.HTTP_409_CONFLICT)
    if existing is not None:
        if existing.reported_by_id != user.user_id:
            return _err('The other side has already reported a score. Confirm '
                        'it or dispute it.', 'ALREADY_REPORTED',
                        status.HTTP_409_CONFLICT)
        # The same person correcting their own report before anybody answered.
        existing.score_a = score_a
        existing.score_b = score_b
        existing.note = (request.data.get('note') or '').strip()[:280]
        existing.save(update_fields=['score_a', 'score_b', 'note'])
        return _ok({'result': serialize_result(request, existing)}, 'Score updated.')

    result = ScrimResult.objects.create(
        scrim=scrim, score_a=score_a, score_b=score_b, reported_by=user,
        note=(request.data.get('note') or '').strip()[:280],
    )
    _tell_other_side(scrim, user, 'reported a score. Confirm it or say it is wrong')
    return _ok({'result': serialize_result(request, result)},
               'Reported. The other side has to agree before it counts.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def confirm_result(request, scrim_id):
    """Agree with the reported score, or say what it really was.

    Confirming is what makes a result count. Disagreeing records BOTH sets of
    numbers rather than replacing one with the other.
    """
    user, err = _authenticate(request)
    if err:
        return err

    scrim = _scrim_by_ref(scrim_id)
    if scrim is None:
        return _err('Challenge not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    mine = side_of(scrim, user)
    if mine is None:
        return _err('You are not part of this challenge.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    result = ScrimResult.objects.filter(scrim=scrim).first()
    if result is None:
        return _err('Nobody has reported a score yet.', 'NO_RESULT',
                    status.HTTP_404_NOT_FOUND)
    if result.status == 'confirmed':
        return _err('That result is already agreed.', 'ALREADY_CONFIRMED',
                    status.HTTP_409_CONFLICT)
    if result.reported_by_id == user.user_id:
        # The point of two sides is that one of them is not you.
        return _err('The other side has to confirm it, not the side that '
                    'reported it.', 'SAME_SIDE', status.HTTP_403_FORBIDDEN)

    agree = bool(request.data.get('agree', True))

    if not agree:
        try:
            their_a = int(request.data.get('score_a'))
            their_b = int(request.data.get('score_b'))
        except (TypeError, ValueError):
            return _err('Say what the score actually was.', 'VALIDATION_ERROR')
        result.status = 'disputed'
        result.disputed_score_a = their_a
        result.disputed_score_b = their_b
        result.disputed_by = user
        result.disputed_at = timezone.now()
        result.save(update_fields=['status', 'disputed_score_a', 'disputed_score_b',
                                   'disputed_by', 'disputed_at'])
        _tell_other_side(scrim, user, 'disagrees with the score you reported')
        return _ok({'result': serialize_result(request, result)},
                   'Recorded as a disagreement. Neither score counts until you agree.')

    with transaction.atomic():
        result.status = 'confirmed'
        result.confirmed_by = user
        result.confirmed_at = timezone.now()
        result.save(update_fields=['status', 'confirmed_by', 'confirmed_at'])
        # Only now does it become part of anybody's history.
        scrim.status = 'played'
        scrim.save(update_fields=['status'])

    _tell_other_side(scrim, user, 'agreed the score')
    return _ok({'result': serialize_result(request, result)}, 'Agreed.')


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def _history_queryset(user):
    """Every challenge this person was actually part of.

    Both sides of both kinds: the solo challenges they posted or accepted, and
    the team ones their teams played. Somebody who was in the team is part of
    that history whether or not they personally pressed anything.
    """
    from django.db.models import Q

    team_ids = list(TeamMembers.objects.filter(user=user)
                    .values_list('team_id', flat=True))
    team_ids += list(Teams.objects.filter(team_owner=user)
                     .values_list('team_id', flat=True))

    return (Scrim.objects
            .filter(Q(player=user) | Q(opponent_player=user)
                    | Q(team_id__in=team_ids) | Q(opponent_id__in=team_ids))
            .select_related('team', 'player', 'opponent', 'opponent_player',
                            'game', 'created_by')
            .prefetch_related('result')
            .distinct())


@api_view(['GET'])
@permission_classes([AllowAny])
def challenge_history(request, username):
    """Somebody's challenge history, for their profile.

    Public, because a profile is public: the whole point of a record is that
    other people can see it before agreeing to play you.
    """
    from .views_community import serialize_scrim

    person = Users.objects.filter(username__iexact=username).first()
    if person is None:
        return _err('No player by that name.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    rows = _history_queryset(person).filter(status='played').order_by('-created_at')[:50]

    played = won = lost = drawn = 0
    out = []
    for scrim in rows:
        result = getattr(scrim, 'result', None)
        if result is None or result.status != 'confirmed':
            continue
        their_side = side_of(scrim, person)
        outcome = 'unknown'
        if their_side:
            if result.winner == 'draw':
                outcome = 'draw'
                drawn += 1
            elif result.winner == their_side:
                outcome = 'won'
                won += 1
            else:
                outcome = 'lost'
                lost += 1
        played += 1

        row = serialize_scrim(request, scrim, None)
        row['result'] = serialize_result(request, result)
        row['outcome'] = outcome
        out.append(row)

    return _ok({
        'challenges': out,
        'record': {'played': played, 'won': won, 'lost': lost, 'drawn': drawn},
    }, 'History retrieved.')

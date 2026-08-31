"""Reading a club, saying something in it, and running it.

Split from `views_clubs.py` only so neither file becomes unreadable; the
permission rules live in one place either way, in `ClubMember.outranks` and
`_capabilities`.

The rule that matters throughout: **the API decides**. Hiding a control is a
courtesy to somebody using the screen, not a permission. Every endpoint here
re-asks who the caller is and what their role allows, because anybody can call
an endpoint directly.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from .models import Club, ClubMember, ClubMessage, ClubTopic, Users
from .views_community import _authenticate, _created, _error, _ok, _optional_user, _person
from .views_clubs import (
    MAX_BODY, _capabilities, _club_or_error, _may_read, _membership, _moved,
    _serialize_member, _serialize_message, _serialize_topic,
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def club_overview(request, club_ref):
    """The club, its topics, and what the caller may do in it."""
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    viewer = _optional_user(request)
    membership = _membership(club, viewer)
    if not _may_read(club, membership):
        return _error('This club is private.', 'CLUB_PRIVATE', status.HTTP_403_FORBIDDEN)

    topics = (ClubTopic.objects.filter(club=club)
              .annotate(message_count=Count(
                  'messages', filter=Q(messages__deleted_at__isnull=True))))

    return _ok({
        'club': {
            'id': club.id,
            'slug': club.slug,
            'name': club.name,
            'description': club.description,
            'is_private': club.is_private,
            'game': club.game.game_title if club.game else None,
            'logo': request.build_absolute_uri(club.logo.url) if club.logo else None,
            'banner': request.build_absolute_uri(club.banner.url) if club.banner else None,
            'owner': _person(request, club.owner) if club.owner else None,
            'member_count': ClubMember.objects.filter(club=club).count(),
            'created_at': club.created_at,
        },
        'topics': [_serialize_topic(t) for t in topics],
        'me': _capabilities(membership),
    }, 'Club retrieved.')


@api_view(['GET'])
@permission_classes([AllowAny])
def club_messages(request, club_ref, topic_id):
    """A topic's messages, oldest last.

    `after` returns only what is newer than an id the caller already holds,
    which is what lets the screen ask for new messages cheaply instead of
    fetching the whole thread again every few seconds.
    """
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    viewer = _optional_user(request)
    membership = _membership(club, viewer)
    if not _may_read(club, membership):
        return _error('This club is private.', 'CLUB_PRIVATE', status.HTTP_403_FORBIDDEN)

    topic = ClubTopic.objects.filter(club=club, id=topic_id).first()
    if topic is None:
        return _error('Topic not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    qs = ClubMessage.objects.filter(topic=topic).select_related('author')

    after = request.query_params.get('after')
    if after:
        try:
            qs = qs.filter(id__gt=int(after))
        except (TypeError, ValueError):
            return _error('after must be a message id.', 'VALIDATION',
                          status.HTTP_400_BAD_REQUEST)
        rows = list(qs.order_by('id')[:200])
    else:
        # The newest sixty, then reversed, so opening a topic lands at the end
        # of the conversation the way every chat does.
        rows = list(qs.order_by('-id')[:60])[::-1]

    return _ok({
        'topic': _serialize_topic(topic),
        'messages': [_serialize_message(request, m) for m in rows],
        'me': _capabilities(membership),
    }, 'Messages retrieved.')


@api_view(['GET'])
@permission_classes([AllowAny])
def club_members(request, club_ref):
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    viewer = _optional_user(request)
    membership = _membership(club, viewer)
    if not _may_read(club, membership):
        return _error('This club is private.', 'CLUB_PRIVATE', status.HTTP_403_FORBIDDEN)

    rows = (ClubMember.objects.filter(club=club)
            .select_related('user')
            .order_by('-role', 'joined_at'))
    return _ok({
        'members': [_serialize_member(request, m) for m in rows],
        'me': _capabilities(membership),
    }, 'Members retrieved.')


# ---------------------------------------------------------------------------
# Leaving
# ---------------------------------------------------------------------------

@api_view(['POST'])
def club_leave(request, club_ref):
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err

    membership = _membership(club, user)
    if membership is None:
        return _error('You are not in this club.', 'NOT_A_MEMBER',
                      status.HTTP_400_BAD_REQUEST)
    if membership.role == ClubMember.ROLE_OWNER:
        # Otherwise the club is left with nobody who can appoint anybody.
        return _error('An owner cannot leave their own club. Hand it to somebody else first.',
                      'OWNER_CANNOT_LEAVE', status.HTTP_400_BAD_REQUEST)
    membership.delete()
    return _ok({'member_count': ClubMember.objects.filter(club=club).count()},
               'You left the club.')


# ---------------------------------------------------------------------------
# Saying something
# ---------------------------------------------------------------------------

@api_view(['POST'])
def club_post_message(request, club_ref, topic_id):
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err

    membership = _membership(club, user)
    if membership is None:
        # Joining is what earns the right to post. Reading needed nothing.
        return _error('Join this club to post in it.', 'JOIN_REQUIRED',
                      status.HTTP_403_FORBIDDEN)
    if membership.is_muted:
        return _error('You are muted in this club.', 'MUTED', status.HTTP_403_FORBIDDEN,
                      {'muted_until': membership.muted_until})

    topic = ClubTopic.objects.filter(club=club, id=topic_id).first()
    if topic is None:
        return _error('Topic not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if topic.is_locked and membership.rank < ClubMember.RANK[ClubMember.ROLE_MODERATOR]:
        return _error('This topic is locked.', 'TOPIC_LOCKED', status.HTTP_403_FORBIDDEN)

    body = (request.data.get('body') or '').strip()
    if not body:
        return _error('A message needs some words in it.', 'VALIDATION',
                      status.HTTP_400_BAD_REQUEST)
    if len(body) > MAX_BODY:
        return _error('A message can be at most %d characters.' % MAX_BODY,
                      'VALIDATION', status.HTTP_400_BAD_REQUEST)

    msg = ClubMessage.objects.create(topic=topic, author=user, body=body)
    return _created({'message': _serialize_message(request, msg)}, 'Message sent.')


@api_view(['POST'])
def club_delete_message(request, club_ref, message_id):
    """Take a message down. An author may take down their own; a moderator may
    take down anybody's below them."""
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err

    msg = (ClubMessage.objects
           .filter(id=message_id, topic__club=club)
           .select_related('author', 'topic').first())
    if msg is None:
        return _error('Message not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if msg.is_deleted:
        return _ok({'id': msg.id}, 'Already removed.')

    membership = _membership(club, user)
    mine = msg.author_id == user.user_id
    theirs = _membership(club, msg.author) if msg.author_id else None
    may_moderate = (
        membership is not None
        and membership.rank >= ClubMember.RANK[ClubMember.ROLE_MODERATOR]
        and (theirs is None or membership.outranks(theirs))
    )
    if not (mine or may_moderate):
        return _error('You cannot remove that message.', 'FORBIDDEN',
                      status.HTTP_403_FORBIDDEN)

    msg.deleted_at = timezone.now()
    msg.deleted_by = user
    msg.save(update_fields=['deleted_at', 'deleted_by'])
    return _ok({'id': msg.id}, 'Message removed.')


# ---------------------------------------------------------------------------
# Running the club
# ---------------------------------------------------------------------------

def _require_rank(club, user, minimum):
    """The caller's membership if it reaches `minimum`, else an error response."""
    membership = _membership(club, user)
    if membership is None:
        return None, _error('You are not in this club.', 'NOT_A_MEMBER',
                            status.HTTP_403_FORBIDDEN)
    if membership.rank < ClubMember.RANK[minimum]:
        return None, _error('Your role in this club does not allow that.',
                            'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    return membership, None


@api_view(['POST'])
def club_create_topic(request, club_ref):
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err
    _, err = _require_rank(club, user, ClubMember.ROLE_ADMIN)
    if err:
        return err

    name = (request.data.get('name') or '').strip()
    if not name:
        return _error('A topic needs a name.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)
    if len(name) > 80:
        return _error('A topic name can be at most 80 characters.', 'VALIDATION',
                      status.HTTP_400_BAD_REQUEST)
    if ClubTopic.objects.filter(club=club, name__iexact=name).exists():
        return _error('This club already has a topic with that name.', 'DUPLICATE',
                      status.HTTP_400_BAD_REQUEST)

    last = ClubTopic.objects.filter(club=club).order_by('-position').first()
    topic = ClubTopic.objects.create(
        club=club,
        name=name,
        description=(request.data.get('description') or '').strip()[:200],
        position=(last.position + 1) if last else 0,
        created_by=user,
    )
    return _created({'topic': _serialize_topic(topic)}, 'Topic created.')


@api_view(['POST'])
def club_update_topic(request, club_ref, topic_id):
    """Rename a topic, change its description, or lock and unlock it."""
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err
    _, err = _require_rank(club, user, ClubMember.ROLE_ADMIN)
    if err:
        return err

    topic = ClubTopic.objects.filter(club=club, id=topic_id).first()
    if topic is None:
        return _error('Topic not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    fields = []
    if 'name' in request.data:
        name = (request.data.get('name') or '').strip()
        if not name:
            return _error('A topic needs a name.', 'VALIDATION',
                          status.HTTP_400_BAD_REQUEST)
        if ClubTopic.objects.filter(club=club, name__iexact=name).exclude(id=topic.id).exists():
            return _error('This club already has a topic with that name.', 'DUPLICATE',
                          status.HTTP_400_BAD_REQUEST)
        topic.name = name[:80]
        fields.append('name')
    if 'description' in request.data:
        topic.description = (request.data.get('description') or '').strip()[:200]
        fields.append('description')
    if 'is_locked' in request.data:
        topic.is_locked = bool(request.data.get('is_locked'))
        fields.append('is_locked')

    if fields:
        topic.save(update_fields=fields)
    return _ok({'topic': _serialize_topic(topic)}, 'Topic updated.')


@api_view(['POST'])
def club_delete_topic(request, club_ref, topic_id):
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err
    _, err = _require_rank(club, user, ClubMember.ROLE_ADMIN)
    if err:
        return err

    topic = ClubTopic.objects.filter(club=club, id=topic_id).first()
    if topic is None:
        return _error('Topic not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if ClubTopic.objects.filter(club=club).count() <= 1:
        # A club with no topic has nowhere to say anything.
        return _error('A club needs at least one topic.', 'LAST_TOPIC',
                      status.HTTP_400_BAD_REQUEST)

    topic.delete()
    return _ok({'id': topic_id}, 'Topic removed.')


@api_view(['POST'])
def club_set_role(request, club_ref):
    """Appoint or demote somebody.

    An admin may appoint moderators and members. Only the owner may appoint an
    admin, because an admin who can make admins can hand the club away.
    """
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err
    actor, err = _require_rank(club, user, ClubMember.ROLE_ADMIN)
    if err:
        return err

    username = (request.data.get('username') or '').strip()
    role = (request.data.get('role') or '').strip().lower()
    if role not in dict(ClubMember.ROLE_CHOICES):
        return _error('That is not a role.', 'VALIDATION', status.HTTP_400_BAD_REQUEST)
    if role == ClubMember.ROLE_OWNER:
        return _error('Ownership is handed over, not assigned.', 'VALIDATION',
                      status.HTTP_400_BAD_REQUEST)

    target_user = Users.objects.filter(username__iexact=username).first()
    if target_user is None:
        return _error('No V-ENT account with that username.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    target = _membership(club, target_user)
    if target is None:
        return _error('That person is not in this club.', 'NOT_A_MEMBER',
                      status.HTTP_400_BAD_REQUEST)
    if target.id == actor.id:
        return _error('You cannot change your own role.', 'FORBIDDEN',
                      status.HTTP_403_FORBIDDEN)
    if not actor.outranks(target):
        # Equal rank is not enough: two admins could otherwise demote each
        # other and the club would be left with no management.
        return _error('You cannot change the role of somebody at your own level or above.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    if role == ClubMember.ROLE_ADMIN and actor.role != ClubMember.ROLE_OWNER:
        return _error('Only the owner can make somebody an admin.', 'FORBIDDEN',
                      status.HTTP_403_FORBIDDEN)

    target.role = role
    target.save(update_fields=['role'])
    return _ok({'member': _serialize_member(request, target)}, 'Role updated.')


@api_view(['POST'])
def club_remove_member(request, club_ref):
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err
    actor, err = _require_rank(club, user, ClubMember.ROLE_MODERATOR)
    if err:
        return err

    username = (request.data.get('username') or '').strip()
    target_user = Users.objects.filter(username__iexact=username).first()
    target = _membership(club, target_user) if target_user else None
    if target is None:
        return _error('That person is not in this club.', 'NOT_A_MEMBER',
                      status.HTTP_400_BAD_REQUEST)
    if not actor.outranks(target):
        return _error('You cannot remove somebody at your own level or above.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    target.delete()
    return _ok({'member_count': ClubMember.objects.filter(club=club).count()},
               'Member removed.')


@api_view(['POST'])
def club_mute_member(request, club_ref):
    """Mute for a number of minutes, or lift a mute with `minutes: 0`.

    A time rather than a flag, so it expires by itself instead of relying on
    somebody remembering to undo it.
    """
    club, moved_to, err = _club_or_error(club_ref)
    if moved_to:
        return _moved(moved_to)
    if err:
        return err

    user, err = _authenticate(request)
    if err:
        return err
    actor, err = _require_rank(club, user, ClubMember.ROLE_MODERATOR)
    if err:
        return err

    username = (request.data.get('username') or '').strip()
    target_user = Users.objects.filter(username__iexact=username).first()
    target = _membership(club, target_user) if target_user else None
    if target is None:
        return _error('That person is not in this club.', 'NOT_A_MEMBER',
                      status.HTTP_400_BAD_REQUEST)
    if not actor.outranks(target):
        return _error('You cannot mute somebody at your own level or above.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    try:
        minutes = int(request.data.get('minutes', 60))
    except (TypeError, ValueError):
        return _error('minutes must be a number.', 'VALIDATION',
                      status.HTTP_400_BAD_REQUEST)
    if minutes < 0 or minutes > 60 * 24 * 30:
        return _error('A mute can last from 0 minutes to 30 days.', 'VALIDATION',
                      status.HTTP_400_BAD_REQUEST)

    target.muted_until = None if minutes == 0 else timezone.now() + timedelta(minutes=minutes)
    target.save(update_fields=['muted_until'])
    return _ok({'member': _serialize_member(request, target)},
               'Mute lifted.' if minutes == 0 else 'Member muted.')

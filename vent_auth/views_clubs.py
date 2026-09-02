"""Clubs, as group chats that somebody runs.

CEO, 31 August 2026: "Clubs are meant to be like group chats, that people can
join and stay an read and send messages around particular set topics, then you
have people who manage the group chat and manage it, they also can add also
admins too with varying levels of control to their clubs."

The shape that follows from that:

- a club holds **topics**, and every message belongs to one;
- **joining** is what earns the right to post, and reading a public club needs
  no account at all;
- four roles - owner, admin, moderator, member - and each one's limits are
  enforced **here**, in the API. Hiding a button is a courtesy; it is not a
  permission. Anybody can call an endpoint directly.

Every "may this person act on that person" question goes through
`ClubMember.outranks`, so the rule exists once. Two endpoints that each decide
it for themselves is how a moderator ends up able to remove an owner.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Club, ClubMember, ClubMessage, ClubTopic, Users
from .views_community import (
    _authenticate, _created, _error, _ok, _optional_user, _person,
)

# A message body long enough for a real explanation, short enough that one
# person cannot push a topic out of readability.
MAX_BODY = 4000


# ---------------------------------------------------------------------------
# Who is this, and what may they do here
# ---------------------------------------------------------------------------

def _membership(club, user):
    if user is None:
        return None
    return ClubMember.objects.filter(club=club, user=user).select_related('user').first()


def _club_or_error(club_ref):
    """A club by slug or id, with the rename history honoured.

    Returns (club, moved_to, error). Exactly one of the three is meaningful.
    """
    from vent_auth.slugs import resolve_or_redirect

    club, moved_to = resolve_or_redirect(
        club_ref, entity_type='club', id_field='id', model=Club,
        queryset=Club.objects.select_related('owner', 'game'),
    )
    if moved_to:
        return None, moved_to, None
    if not club:
        return None, None, _error('Club not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return club, None, None


def _moved(moved_to):
    return Response({
        'status': 'moved',
        'code': 'SLUG_CHANGED',
        'message': 'This club has been renamed.',
        'data': {'slug': moved_to, 'url': f'/community/club/{moved_to}'},
    }, status=status.HTTP_200_OK)


def _may_read(club, membership):
    """A private club is readable only from the inside. A public one by anybody,
    signed in or not - see the standing rule that content is public and it is
    the action that is gated."""
    return (not club.is_private) or membership is not None


def _capabilities(membership):
    """What the caller may do, as plain booleans.

    Sent to the client so it can draw the right controls, and used here so it
    cannot matter if it draws the wrong ones.
    """
    if membership is None:
        return {
            'is_member': False, 'role': None, 'can_post': False,
            'can_moderate': False, 'can_manage_topics': False,
            'can_manage_roles': False, 'can_remove_members': False,
            'can_edit_club': False, 'can_delete_club': False,
            'is_muted': False, 'muted_until': None,
        }
    rank = membership.rank
    muted = membership.is_muted
    return {
        'is_member': True,
        'role': membership.role,
        # A muted member is still a member: they read, they do not write.
        'can_post': not muted,
        'can_moderate': rank >= ClubMember.RANK[ClubMember.ROLE_MODERATOR],
        'can_manage_topics': rank >= ClubMember.RANK[ClubMember.ROLE_ADMIN],
        'can_manage_roles': rank >= ClubMember.RANK[ClubMember.ROLE_ADMIN],
        'can_remove_members': rank >= ClubMember.RANK[ClubMember.ROLE_MODERATOR],
        # Renaming a club changes its address, so it sits with the other
        # admin powers. Deleting it is the owner's alone: an admin was
        # appointed to help run the club, not to end it.
        'can_edit_club': rank >= ClubMember.RANK[ClubMember.ROLE_ADMIN],
        'can_delete_club': membership.role == ClubMember.ROLE_OWNER,
        'is_muted': muted,
        'muted_until': membership.muted_until,
    }


def _serialize_topic(topic, unread_for=None):
    return {
        'id': topic.id,
        'name': topic.name,
        'description': topic.description,
        'is_locked': topic.is_locked,
        'position': topic.position,
        'message_count': getattr(topic, 'message_count', None),
    }


def _serialize_message(request, msg):
    # A deleted message keeps its place in the thread and loses its words. The
    # gap is the point: a conversation with messages silently missing cannot be
    # read back to settle what happened.
    if msg.is_deleted:
        return {
            'id': msg.id,
            'deleted': True,
            'body': '',
            'author': _person(request, msg.author) if msg.author else None,
            'created_at': msg.created_at,
        }
    return {
        'id': msg.id,
        'deleted': False,
        'body': msg.body,
        'author': _person(request, msg.author) if msg.author else None,
        'created_at': msg.created_at,
        'edited_at': msg.edited_at,
    }


def _serialize_member(request, m):
    return {
        'id': m.id,
        'role': m.role,
        'joined_at': m.joined_at,
        'muted_until': m.muted_until,
        'is_muted': m.is_muted,
        'user': _person(request, m.user),
    }

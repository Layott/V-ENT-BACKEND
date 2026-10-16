"""One page for any invitation, whether or not the person has an account yet.

    GET  /auth/invitations/<token>/          what it is, who sent it, to which address (hinted)
    POST /auth/invitations/<token>/respond/  {"accept": true|false}, as the person it was sent to
    POST /auth/invitations/<token>/cancel/   the sender, or whoever runs the thing, withdraws it
    GET  /auth/invitations/mine/             every invitation waiting for the signed-in person

CEO, 13 October 2026 (inbox 455): somebody invited to help run an event, a
tournament or an organisation "should be able to get the mail in their email
and then, they can create an account using the link in their mail and still
accept the invite." The email links here, to /invitations/<token>: the page
says what it is, offers to create an account or sign in, and then to accept.

Reading an invitation is public to whoever holds its link, which is the person
it was emailed to; it names the thing and the sender and only hints at the
address. Answering is the addressee's alone (invites.answer).
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import inputs, invites
from .actors import actor_from_request


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _viewer(request):
    if not (request.headers.get('Authorization') or '').startswith('Bearer '):
        return None
    user, _err_response = actor_from_request(request)
    return user


@api_view(['GET'])
@permission_classes([AllowAny])
def invitation(request, token):
    invite = invites.find(token)
    if invite is None:
        return _err('No such invitation.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({'invitation': invites.describe(invite, _viewer(request))}, 'Invitation')


@api_view(['POST'])
def respond(request, token):
    user, auth_error = actor_from_request(request)
    if auth_error is not None:
        return auth_error
    invite = invites.find(token)
    if invite is None:
        return _err('No such invitation.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    accept = inputs.read_bool(request.data, 'accept')
    try:
        invite = invites.answer(invite, user, accept)
    except invites.AnswerError as exc:
        http = (status.HTTP_403_FORBIDDEN if exc.code == 'INVITE_FOR_ANOTHER_ACCOUNT'
                else status.HTTP_409_CONFLICT)
        return _err('That invitation cannot be answered from this account.', exc.code, http)
    return _ok({'invitation': invites.describe(invite, user), 'accepted': accept},
               'Accepted.' if accept else 'Declined.')


def _may_withdraw(user, invite):
    from .models import OrgInvite, OrgMember
    if invite.invited_by_id == user.user_id:
        return True
    if isinstance(invite, OrgInvite):
        return OrgMember.objects.filter(
            org=invite.org, user=user,
            role__in=(OrgMember.ROLE_OWNER, OrgMember.ROLE_ADMIN)).exists()
    if invite.kind == invite.KIND_EVENT:
        from .actors import may_override
        return invite.event.creator_id == user.user_id or may_override(user, 'manage_events')
    from vent_tournament.access import may_manage
    return may_manage(user, invite.tournament)


@api_view(['POST'])
def cancel(request, token):
    user, auth_error = actor_from_request(request)
    if auth_error is not None:
        return auth_error
    invite = invites.find(token)
    if invite is None or not _may_withdraw(user, invite):
        # The same answer for one that does not exist and one that is not
        # theirs: a token is no way to learn who invited whom.
        return _err('No such invitation.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if invite.status != 'pending':
        return _err('That invitation has already been answered.', 'ALREADY_ANSWERED',
                    status.HTTP_409_CONFLICT)
    from django.utils import timezone
    invite.status = 'cancelled'
    invite.responded_at = timezone.now()
    invite.save(update_fields=['status', 'responded_at'])
    return _ok({'invitation': invites.describe(invite, user)}, 'Withdrawn.')


@api_view(['GET'])
def mine(request):
    user, auth_error = actor_from_request(request)
    if auth_error is not None:
        return auth_error
    from .models import OrgInvite, RoleInvite
    rows = list(OrgInvite.objects.filter(user=user, status='pending')
                .select_related('org', 'invited_by', 'user'))
    rows += list(RoleInvite.objects.filter(user=user, status='pending')
                 .select_related('event', 'tournament', 'invited_by', 'user'))
    rows.sort(key=lambda r: r.created_at, reverse=True)
    return _ok({'invitations': [invites.describe(r, user) for r in rows],
                'count': len(rows)}, 'Invitations')

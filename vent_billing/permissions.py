"""Who may read a plan, and who may run one.

Two questions, and they have different answers, which is the whole shape of the
public-by-default rule:

- **Reading** a public plan is public. A price behind a sign-in wall cannot be
  ranked, cannot be shared, and cannot be read by the one person who most needs
  to see it: somebody deciding whether to join.
- **Doing** anything - subscribing, cancelling, creating a plan, reading the
  members area - needs an account, and every one of those endpoints refuses an
  anonymous caller here rather than trusting a hidden button.

A draft plan is the exception on the reading side, and deliberately: a price
somebody has seen is a price they expect to be charged, so a plan that is still
being written is readable only by the people who may edit it.
"""
from rest_framework import status
from rest_framework.response import Response

from vent_auth.models import OrgMember, Users


def error(message, code, http=status.HTTP_400_BAD_REQUEST, **extra):
    """The platform envelope. `code` is what the interface translates."""
    body = {'status': 'error', 'code': code, 'message': message, 'data': {}}
    if extra:
        body['data'] = extra
    return Response(body, status=http)


def ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def viewer(request):
    """The caller, or None. The bearer pattern every V-ENT view uses."""
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    if not token:
        return None
    return Users.objects.filter(login_session_token=token).first()


def require_viewer(request):
    """(user, None) or (None, 401).

    Every gated endpoint calls this. A hidden control is a courtesy; this is
    the permission, and `tests_signed_out` proves each one refuses.
    """
    who = viewer(request)
    if who is None:
        return None, error('Sign in to do that.', 'UNAUTHORIZED',
                           status.HTTP_401_UNAUTHORIZED)
    return who, None


def may_manage_plan(user, plan):
    """Whether this person may edit a plan and see who is on it.

    An organisation's plan is run by anybody who runs its EVENTS, rather than by
    a new scope of its own. A membership is the organisation selling something,
    which is the same job as running an event, and inventing a fifth scope
    would mean every existing manager silently losing a capability they would
    expect to have.
    """
    if user is None or plan is None:
        return False
    if plan.owner_id == user.user_id:
        return True
    if not plan.org_id:
        return False
    member = OrgMember.objects.filter(org_id=plan.org_id, user=user).first()
    if member is None:
        return plan.org.org_owner_id == user.user_id
    return member.may_run(OrgMember.SCOPE_EVENTS)


def may_manage_org(user, org):
    """Whether this person may create a plan for this organisation."""
    if user is None or org is None:
        return False
    if org.org_owner_id == user.user_id:
        return True
    member = OrgMember.objects.filter(org=org, user=user).first()
    return member is not None and member.may_run(OrgMember.SCOPE_EVENTS)

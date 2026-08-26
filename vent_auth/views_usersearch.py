"""Finding a person by name, so they can be picked rather than typed.

The direct-message composer asked for a username in a plain text box, with the
placeholder "Username, e.g. playr". You had to know exactly how somebody spells
their handle, get the capitalisation right, and find out you were wrong only
after pressing Send. There was no user search endpoint at all - the search page
faked one by filtering the rankings list, which only ever contained ranked
players.

This is that endpoint. It answers with enough to draw a row somebody can
recognise - the handle, the real name, the picture - and with `can_message`,
so the composer can say who is reachable before a message is written rather
than after.

Two rules it keeps:

  * **A private profile is not listed.** Somebody who set their profile to
    private has said they do not want to be found, and a search box that
    returns them anyway is the same leak by another route.
  * **`can_message` is advice, not enforcement.** `dm_send` checks the same
    setting itself. A client that ignores this field gets a 403, not a message.
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, UserProfile
from .views_profile import _user_from_bearer, can_view_profile, privacy_of

MAX_RESULTS = 12
MIN_QUERY = 2


def _absolute(request, file_field):
    """A URL the browser can load, or None."""
    if not file_field:
        return None
    try:
        return request.build_absolute_uri(file_field.url)
    except (ValueError, AttributeError):
        return None


def may_message(viewer, owner):
    """Whether `viewer` may open a conversation with `owner`.

    `allow_direct_messages` was being written by the Privacy panel and read by
    nothing, so switching it off changed nothing at all. It is read here and in
    dm_send.
    """
    if owner is None:
        return False
    if viewer is not None and viewer.pk == owner.pk:
        return False                       # nobody messages themselves
    setting = privacy_of(owner).get('allow_direct_messages', 'anyone')
    if setting in (False, 'nobody', 'none'):
        return False
    if setting in (True, 'anyone', 'everyone'):
        return True
    if setting in ('followers', 'following'):
        if viewer is None:
            return False
        return owner.community.filter(pk=viewer.pk).exists() if hasattr(owner, 'community') else False
    return True


@api_view(['GET'])
def user_search(request):
    """GET /user/search/?q=  - people whose handle or name starts with `q`.

    Open to signed-out visitors too, because the same rows are what a public
    search page needs; `can_message` is simply false for all of them.
    """
    query = (request.GET.get('q') or '').strip()
    if len(query) < MIN_QUERY:
        return Response({
            'status': 'success',
            'data': {'users': []},
            'message': 'Type at least %d characters.' % MIN_QUERY,
        }, status=status.HTTP_200_OK)

    viewer, _ignored = _user_from_bearer(request)
    viewer = None if _ignored else viewer

    # Starts-with first, then contains, so typing "te" puts "temi" above
    # "monster". Ordering by username keeps it stable between keystrokes.
    matches = (
        Users.objects
        .filter(Q(username__istartswith=query)
                | Q(full_name__istartswith=query)
                | Q(username__icontains=query)
                | Q(full_name__icontains=query))
        .filter(is_active=True)
        .order_by('username')[:MAX_RESULTS * 3]
    )

    rows = []
    for user in matches:
        if getattr(user, 'is_deactivated', False):
            continue
        if viewer is not None and user.pk == viewer.pk:
            continue
        # Somebody who is not findable should not be found here either.
        if not can_view_profile(viewer, user):
            continue

        profile = UserProfile.objects.filter(user=user).order_by('profile_id').first()
        rows.append({
            'user_id': user.user_id,
            'username': user.username,
            'full_name': user.full_name or user.username,
            'avatar': _absolute(request, getattr(profile, 'profile_picture', None)),
            'can_message': may_message(viewer, user),
            'founder_badge': bool(getattr(user, 'is_founder', False) and user.show_founder_badge),
        })
        if len(rows) >= MAX_RESULTS:
            break

    starts = [r for r in rows if r['username'].lower().startswith(query.lower())]
    rest = [r for r in rows if r not in starts]

    return Response({
        'status': 'success',
        'data': {'users': starts + rest},
        'message': '%d found' % len(rows),
    }, status=status.HTTP_200_OK)

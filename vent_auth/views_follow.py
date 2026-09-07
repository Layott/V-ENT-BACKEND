"""Following a team or a person, and seeing who follows you.

CEO, 7 September 2026: "org owners should also be able to see their followers,
same for teams and users and info on like how many."

Organisations already had all of this. Teams and people had none of it: no
table, no endpoint, no count, no list. So a team page could not say how many
people cared about it and a player could not see anybody following them.

One set of endpoints for both, addressed by `kind`, because following is one
concept. Two near-identical view files is how the org version and the team
version end up disagreeing about what a follower is.

    POST   /follow/<kind>/<ref>/          follow
    DELETE /follow/<kind>/<ref>/          unfollow
    GET    /follow/<kind>/<ref>/followers/ who follows it, and how many
    GET    /follow/mine/                  what I follow

`ref` is a slug or a username, never a primary key, per the slug rule.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Follow, Teams, Users, follower_count, is_following

PAGE_SIZE = 40


def _error(message, code, http_status):
    return Response({'status': 'error', 'data': {}, 'message': message,
                     'code': code}, status=http_status)


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _viewer(request):
    """The signed-in account, or None. Never an error: most of this is public."""
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    return Users.objects.filter(
        login_session_token=header.split(' ', 1)[1].strip()).first()


def _target(kind, ref):
    """The thing being followed, by slug or username. (object, id) or (None, None).

    Addressed by name rather than by key so the URL obeys the slug rule, and
    because `/follow/user/41/` invites anybody to walk the user table by
    counting.
    """
    if kind == 'team':
        team = Teams.objects.filter(slug=str(ref)).first()
        if team is None and str(ref).isdigit():
            team = Teams.objects.filter(team_id=int(ref)).first()
        return (team, team.team_id) if team else (None, None)
    if kind == 'user':
        person = Users.objects.filter(username__iexact=str(ref)).first()
        return (person, person.user_id) if person else (None, None)
    return None, None


@api_view(['POST', 'DELETE'])
def follow(request, kind, ref):
    """Follow or unfollow a team or a person."""
    viewer = _viewer(request)
    if viewer is None:
        return _error('Sign in to follow.', 'AUTHENTICATION_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)

    if kind not in ('team', 'user'):
        return _error('That is not something you can follow.',
                      'FOLLOW_KIND_UNKNOWN', status.HTTP_400_BAD_REQUEST)

    obj, target_id = _target(kind, ref)
    if obj is None:
        return _error('Not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if kind == 'user' and target_id == viewer.user_id:
        # Not an error worth a red toast, but not a row either.
        return _error('You cannot follow yourself.', 'FOLLOW_SELF',
                      status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        Follow.objects.filter(follower=viewer, kind=kind,
                              target_id=target_id).delete()
        following = False
    else:
        # get_or_create, so a double tap on a slow connection is one row and
        # one follower rather than two of each.
        Follow.objects.get_or_create(follower=viewer, kind=kind,
                                     target_id=target_id)
        following = True

    return _ok({
        'is_following': following,
        'follower_count': follower_count(kind, target_id),
    }, 'Following.' if following else 'No longer following.')


@api_view(['GET'])
@permission_classes([AllowAny])
def followers(request, kind, ref):
    """Who follows this, and how many.

    Public: a follower count is a fact about a team the same way its member
    count is, and hiding it behind a session is what makes a page look empty to
    everybody deciding whether to join.
    """
    if kind not in ('team', 'user', 'org'):
        return _error('That is not something you can follow.',
                      'FOLLOW_KIND_UNKNOWN', status.HTTP_400_BAD_REQUEST)

    viewer = _viewer(request)

    if kind == 'org':
        from .models import Organization, OrgFollower
        org = Organization.objects.filter(slug=str(ref)).first()
        if org is None:
            return _error('Not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        rows = OrgFollower.objects.filter(org=org).select_related('user')
        people = [r.user for r in rows[:PAGE_SIZE]]
        target_id = org.org_id
    else:
        obj, target_id = _target(kind, ref)
        if obj is None:
            return _error('Not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        rows = Follow.objects.filter(kind=kind, target_id=target_id) \
            .select_related('follower')
        people = [r.follower for r in rows[:PAGE_SIZE]]

    from .views_community import _person
    return _ok({
        'count': follower_count(kind, target_id),
        'followers': [_person(request, p) for p in people],
        'is_following': is_following(viewer, kind, target_id),
    }, 'Followers.')


@api_view(['GET'])
def following(request):
    """Everything the signed-in person follows, of every kind.

    One list rather than three endpoints, because "what do I follow" is one
    question and answering it in three calls is three chances for a screen to
    show two thirds of the answer.
    """
    viewer = _viewer(request)
    if viewer is None:
        return _error('Sign in to see this.', 'AUTHENTICATION_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)

    from .models import OrgFollower
    from .views_community import _person

    teams, people = [], []
    rows = Follow.objects.filter(follower=viewer)
    team_ids = [r.target_id for r in rows if r.kind == 'team']
    user_ids = [r.target_id for r in rows if r.kind == 'user']

    for t in Teams.objects.filter(team_id__in=team_ids):
        logo = None
        if t.team_logo:
            try:
                logo = request.build_absolute_uri(t.team_logo.url)
            except ValueError:
                logo = None
        teams.append({'id': t.team_id, 'slug': t.slug, 'name': t.team_name,
                      'logo': logo})

    for u in Users.objects.filter(user_id__in=user_ids):
        people.append(_person(request, u))

    orgs = [{'id': r.org.org_id, 'slug': r.org.slug, 'name': r.org.org_name}
            for r in OrgFollower.objects.filter(user=viewer).select_related('org')]

    return _ok({
        'teams': teams,
        'users': people,
        'organizations': orgs,
        'count': len(teams) + len(people) + len(orgs),
    }, 'What you follow.')

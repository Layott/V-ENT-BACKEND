"""What the organisations you follow are doing.

CEO, 2 September 2026: "Users shuould be able to follow an organization, in which
that particular orgs events, tournaments and anything about that org should show
constantly."

Following already existed and did nothing: `OrgFollower` rows were written and
never read back for anything a follower could see. A follow that changes nothing
about what you are shown is a counter, not a subscription, and the person who
pressed it has no way to tell the difference.

So the follow now has a consequence: two endpoints, one for who you follow and
one for what they are doing.

## Two decisions

**One feed, both kinds.** Events and tournaments come back in a single list
ordered by when they start, because a follower is asking "what is coming up from
these people", not "show me the events table". Two endpoints would make every
screen merge and re-sort them, and they would drift.

**Past is included, deliberately, at the end.** An organisation with nothing
upcoming is not an organisation with nothing to show, and an empty feed is what
makes somebody unfollow. What ran last month is still the answer to "who are
these people".
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import OrgFollower, Users


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _error(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message,
                     'code': code}, status=http)


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _media(request, field):
    try:
        return request.build_absolute_uri(field.url) if field else ''
    except ValueError:
        return ''


@api_view(['GET'])
def following(request):
    """GET /organization/following/ - the organisations this person follows."""
    user = _viewer(request)
    if user is None:
        return _error('You need an account to see who you follow.',
                      'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)

    from .views_orgs import serialize_org

    rows = (OrgFollower.objects
            .filter(user=user)
            .select_related('org')
            .order_by('-id'))
    return _ok({
        'organizations': [serialize_org(request, row.org, viewer=user)
                          for row in rows],
        'count': rows.count(),
    }, 'Organizations you follow')


@api_view(['GET'])
def following_feed(request):
    """GET /organization/following/feed/ - what those organisations are running.

    Events and tournaments in one list, soonest first, with anything already
    past after them. `?limit=` caps it; the default is enough to fill a screen
    without making a follower of forty organisations wait.
    """
    user = _viewer(request)
    if user is None:
        return _error('You need an account to see this feed.',
                      'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)

    from django.utils import timezone

    from vent_event.models import Event
    from vent_tournament.models import Tournament

    org_ids = list(OrgFollower.objects.filter(user=user)
                   .values_list('org_id', flat=True))
    if not org_ids:
        return _ok({'items': [], 'organizations': 0},
                   'You are not following any organizations yet.')

    try:
        limit = max(1, min(int(request.GET.get('limit') or 40), 100))
    except (TypeError, ValueError):
        limit = 40

    now = timezone.now()
    items = []

    for event in (Event.objects
                  .filter(organization_id__in=org_ids, is_active=True)
                  .select_related('organization')
                  .order_by('-start_date')[:limit]):
        items.append({
            'kind': 'event',
            'id': event.event_id,
            'slug': event.slug,
            'title': event.name,
            'starts_at': event.start_date,
            'image': _media(request, event.banner) or _media(request, event.logo),
            'location': event.location or '',
            'organization': {
                'id': event.organization_id,
                'name': getattr(event.organization, 'org_name', ''),
                'slug': getattr(event.organization, 'slug', None),
            },
            'url': '/events/%s' % (event.slug or event.event_id),
        })

    for t in (Tournament.objects
              .filter(tournament_organization_id__in=org_ids, is_draft=False)
              .select_related('tournament_organization')
              .order_by('-start_date_and_time')[:limit]):
        items.append({
            'kind': 'tournament',
            'id': t.tournament_id,
            'slug': t.slug,
            'title': t.tournament_title,
            'starts_at': t.start_date_and_time,
            'image': _media(request, t.tournament_banner) or _media(request, t.tournament_logo),
            'location': t.tournament_location or '',
            'organization': {
                'id': t.tournament_organization_id,
                'name': getattr(t.tournament_organization, 'org_name', ''),
                'slug': getattr(t.tournament_organization, 'slug', None),
            },
            'url': '/tournaments/%s' % (t.slug or t.tournament_id),
        })

    # Upcoming first and soonest at the top; everything past after it, most
    # recent first. An organisation with nothing coming up is not an
    # organisation with nothing to show, and an empty feed is what makes
    # somebody unfollow.
    def order(item):
        when = item['starts_at']
        if when is None:
            return (2, 0)
        if when >= now:
            return (0, when.timestamp())
        return (1, -when.timestamp())

    items.sort(key=order)

    return _ok({
        'items': items[:limit],
        'organizations': len(org_ids),
        'upcoming': sum(1 for i in items
                        if i['starts_at'] and i['starts_at'] >= now),
    }, 'From the organizations you follow')


# ---------------------------------------------------------------------------
# The organisations somebody may run something in the name of
# ---------------------------------------------------------------------------
#
# What fills the picker in the tournament and event wizards. Separate from
# `/organization/list/`, which is every organisation on the platform: this is
# the short list of the ones this person may speak for, and most people have
# none, in which case the wizards do not show the field at all.

@api_view(['GET'])
def my_organizations(request):
    """GET /organization/mine/ - the organisations I can run things under."""
    user = _viewer(request)
    if user is None:
        return _error('You need an account to see this.',
                      'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)

    from vent_auth import org_link

    rows = [
        {
            'id': org.org_id,
            'slug': org.slug or '',
            'name': org.org_name,
            'tag': org.tag or '',
            'role': org_link.role_of(org, user),
            'logo': _media(request, getattr(org, 'logo', None)),
        }
        for org in org_link.mine(user)
    ]
    return _ok({'organizations': rows, 'count': len(rows)},
               'Organizations you can run things under.')

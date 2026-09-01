"""Short addresses for ticket links.

CEO, 1 September: "add an option for people to be able to shorten their ticket
links, so you create very short versions of the ticket links."

A ticket link is long by the time it is worth sharing. The event carries a
readable slug, the tickets sit behind a tab, and an influencer's link adds a
code, so what an organiser is asked to read out on a livestream or print on a
flyer runs to seventy characters of which most are punctuation.

    https://v-ent.co/events/lagos-anime-con-2026?tab=tickets&ref=TEMI
    https://v-ent.co/s/k7m2q

## The three things this file is careful about

**The target is a path on this site, never a URL.** Letting a caller store
somewhere to redirect to is an open redirect: anybody could hand out a v-ent.co
address that lands on a page they control, with the platform's name lending it
credibility. `target` must start with a single `/`, and it is resolved against
our own origin at redirect time. `//evil.example` is refused precisely because
a browser reads it as a host.

**The token is opaque and short, not a counter.** Sequential codes can be walked
by counting, which publishes every unlisted event anybody shortened - including
the ones left off the public listing, which stay reachable by their link. Five
characters from the platform's own alphabet, which already excludes the
characters misread when a link is read aloud, and being read aloud is what these
are for. See `TOKEN_LENGTH` for why five is the floor.

**A short link is not a tracker.** It counts arrivals and nothing else. No
address, no user agent, no row per visitor. The organiser's question is "did the
flyer work", and a count answers it without keeping a log of who read what.
"""
import re
import secrets

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth.slugs import TOKEN_ALPHABET

from .models import ShortLink

# Five characters from a 32 character alphabet: 33.5 million codes.
#
# CEO, 1 September: "is it possible to make the links even shorter?"
#
# Five is the floor, and the reason is enumeration rather than arithmetic. At
# four the space is a million, which anybody can walk through in an afternoon,
# and walking it lists every short link on the platform - including the ones
# pointing at events the organiser deliberately left off the public listing.
# Those events stay reachable by their link, so publishing the links publishes
# the events.
#
# What is left to cut after this is the domain and the `/s/`, and both cost more
# than the character they save: serving tokens at the root would mean a code
# could shadow a real page, or a new page could break codes already printed.
TOKEN_LENGTH = 5

# If the short space ever gets crowded, tokens get longer rather than the
# generator spinning. Old codes keep working whatever their length, because a
# lookup is an exact string match and nothing anywhere assumes a size.
MAX_TOKEN_LENGTH = 12

# A path on this site: one leading slash, then anything that is not another
# slash or a backslash. Both of those are how a relative-looking string turns
# into a different host in a browser.
SAFE_TARGET = re.compile(r'^/(?![/\\]).*$')


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _frontend_origin():
    """Where a short link actually points, for building the address to show.

    Read from settings rather than from the request, because the request that
    creates a link arrives at the API host and the link has to be on the site
    people visit. Getting this from the request is what put `test.app.v-ent.co`
    into every emailed link once already.
    """
    from django.conf import settings
    return str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')


def new_token():
    """A token nothing else is using.

    Draws rather than counts, and checks, because a silent collision hands two
    organisers the same address and neither would ever find out.

    After a few failures at one length it tries a longer one. A generator that
    loops forever on a full space is a request that hangs, and the character
    saved is not worth that.
    """
    length = TOKEN_LENGTH
    attempts = 0
    while True:
        token = ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))
        if not ShortLink.objects.filter(token=token).exists():
            return token
        attempts += 1
        if attempts >= 8 and length < MAX_TOKEN_LENGTH:
            length += 1
            attempts = 0


def _serialize(link):
    origin = _frontend_origin()
    return {
        'id': link.id,
        'token': link.token,
        'url': '%s/s/%s' % (origin, link.token) if origin else '/s/%s' % link.token,
        'target': link.target,
        'label': link.label,
        'hits': link.hits,
        'is_active': link.is_active,
        'created_at': link.created_at.isoformat(),
    }


def _event_and_permission(request, event_id):
    user, err = actor_from_request(request)
    if err:
        return None, None, err

    from .views import _event_by_ref
    event = _event_by_ref(event_id)
    if event is None:
        return None, None, _err('Event not found.', 'NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return None, None, _err(
            'Only the event organizer can shorten its links.',
            'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)
    return event, user, None


@api_view(['GET', 'POST'])
def short_links(request, event_id):
    """GET/POST /event/<id>/short-links/

    POST takes `{target, label}` and answers with the short address. Asking for
    the same target twice returns the link that already exists rather than
    minting a second one: an organiser pressing the button again wants the code
    they printed on the flyer, not a new one that makes the first look wrong.
    """
    event, user, err = _event_and_permission(request, event_id)
    if err:
        return err

    if request.method == 'GET':
        return _ok({'links': [_serialize(l) for l in event.short_links.all()],
                    'origin': _frontend_origin()}, 'Short links')

    target = str(request.data.get('target') or '').strip()
    if not target:
        # The ordinary case, and the one the button on the share card sends:
        # shorten this event's ticket link.
        target = '/events/%s?tab=tickets' % (event.slug or event.pk)

    if not SAFE_TARGET.match(target):
        return _err(
            'A short link can only point at a page on this site.',
            'INVALID_TARGET', field='target')
    if len(target) > 500:
        return _err('That address is too long to shorten.', 'INVALID_TARGET',
                    field='target')

    label = str(request.data.get('label') or '').strip()[:80]

    existing = event.short_links.filter(target=target, is_active=True).first()
    if existing is not None:
        # Pressing it twice is not a request for a second code. The label is
        # still worth taking, because that is often why somebody came back.
        if label and label != existing.label:
            existing.label = label
            existing.save(update_fields=['label'])
        return _ok({'link': _serialize(existing)}, 'Short link ready.')

    link = ShortLink.objects.create(
        token=new_token(), event=event, target=target, label=label,
        created_by=user,
    )
    return _ok({'link': _serialize(link)}, 'Short link created.',
               status.HTTP_201_CREATED)


@api_view(['DELETE'])
def delete_short_link(request, event_id, link_id):
    """DELETE /event/<id>/short-links/<id>/ - stop a short link working.

    Switched off rather than deleted. The address is printed on things that
    already exist, and a code that comes back to life pointing somewhere else
    because it was reissued is worse than one that stops.
    """
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    link = event.short_links.filter(pk=link_id).first()
    if link is None:
        return _err('No such short link on this event.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    link.is_active = False
    link.save(update_fields=['is_active'])
    return _ok({'links': [_serialize(l) for l in event.short_links.all()]},
               'Short link switched off.')


@api_view(['GET'])
@permission_classes([AllowAny])
def resolve_short_link(request, token):
    """GET /s/<token>/ - where this short address goes.

    Public and unauthenticated, because a short link is handed to strangers;
    that is the whole point of one.

    It answers with the path rather than redirecting. The page doing the
    redirecting is on the frontend and it is the only side that knows what a
    frontend URL is, which is the same reason a moved slug answers 200 with
    `{status: 'moved'}` rather than a 301.
    """
    link = ShortLink.objects.filter(token=str(token or '').strip(),
                                    is_active=True).first()
    if link is None:
        return _err('That short link does not exist.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    # Counted with an atomic update rather than read-modify-write, because two
    # people opening the same flyer link at once is the ordinary case for a
    # link that works.
    from django.db.models import F
    ShortLink.objects.filter(pk=link.pk).update(hits=F('hits') + 1)

    return _ok({'target': link.target, 'event': link.event.slug or link.event.pk},
               'Short link')

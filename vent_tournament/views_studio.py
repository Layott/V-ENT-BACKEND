"""The production studio: elements V-ENT ships, bound to the bracket.

CEO, 1 September 2026: "the site's tournament bracket systems will handle the
calculations and seeding and feed it into the production studio based off what
is being requested for each element, and each element can be copied and pasted
into your streaming software as browser sources and it updates in realtime...
it'll be like a production studio for any organizer who can pay for it."

## How it is put together

Three surfaces, and keeping them apart is most of the design:

| Surface | Who opens it | Auth |
|---|---|---|
| Operator console | the organiser, signed in | Bearer token |
| Element page | OBS, vMix, anything with a browser source | session token in the URL |
| Element feed | the element page, a few times a second | session token in the URL |

**The element page holds no state.** It asks the feed what it should be showing.
That is what makes a broadcast survivable: OBS can be restarted mid-show, the
machine can be swapped, a second operator can open the same URL on another
laptop, and every graphic comes back exactly as it was. A design that kept state
in the page would lose the broadcast with the tab, at the moment nobody has time
to rebuild it.

**The feed is one request.** An overlay running six hours on a venue hotspot
should ask one question, not one per element, and should be able to answer "has
anything changed" without diffing a payload. Hence `version`.

## Why this exists next to TournamentOverlay

`TournamentOverlay` serves a designer's uploaded HTML. This serves elements the
platform ships. Both stay: an organiser with a designer uses the first, an
organiser who wants a scoreboard in ten minutes uses this. They share the same
idea about tokens, for the same reason.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import Users

from .models import BroadcastElement, BroadcastSession, Tournament

KINDS = [k for k, _ in BroadcastElement.KINDS]


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


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


def may_use_studio(user, tournament):
    """Whether this person may run a broadcast for this tournament.

    Ownership today. The studio is a paid capability and this is where the
    subscription check belongs, but billing does not exist yet, and gating a
    feature on a subscription nobody can buy would ship a control that refuses
    everybody. When plans land, the line to add is here and only here.
    """
    if user is None:
        return False
    if tournament.tournament_creator_id == user.user_id:
        return True
    from vent_auth.actors import may_override
    return bool(may_override(user, 'manage_tournaments'))


def _element_state(session):
    rows = {e.kind: e for e in session.elements.all()}
    out = {}
    for kind in KINDS:
        row = rows.get(kind)
        out[kind] = {
            'kind': kind,
            'active': bool(row and row.is_active),
            'payload': (row.payload if row else {}) or {},
            'updated_at': row.updated_at.isoformat() if row else None,
        }
    return out


def _version(session, elements):
    """Something an element can compare without reading the whole payload.

    Counts and timestamps rather than a hash of everything, because the point is
    to be cheap on a bad connection.
    """
    stamp = max(
        [e['updated_at'] or '' for e in elements.values()] or [''])
    return '%s-%s-%s' % (
        session.id,
        sum(1 for e in elements.values() if e['active']),
        stamp)


def _session_payload(session, request):
    # The element pages are FRONTEND routes and the feed is an API route, so
    # they do not share a host and cannot share a base.
    #
    # `request.build_absolute_uri` builds against the host that made the
    # request, which is always the API, because it is the frontend calling it.
    # So every URL an organiser copied read
    #
    #     https://api.v-ent.co/studio/<token>/scorebar/
    #
    # which 404s. There is no such Django route; `/studio/<token>/feed/` is the
    # only thing under that prefix on the API. Pasted into OBS it gives a blank
    # browser source, and the one thing this whole feature exists to produce
    # was unusable. Nothing reported it: the endpoint answered 200 with a
    # perfectly well-formed URL to a page that does not exist.
    from django.conf import settings

    frontend = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    page_base = '%s/studio/%s' % (frontend, session.token)
    feed_base = request.build_absolute_uri('/studio/%s' % session.token)
    elements = _element_state(session)
    return {
        'id': session.id,
        'name': session.name,
        'status': session.status,
        'is_live': session.is_live,
        'started_at': session.started_at.isoformat(),
        'ended_at': session.ended_at.isoformat() if session.ended_at else None,
        'tournament': {
            'title': session.tournament.tournament_title,
            'slug': session.tournament.slug,
        },
        # The whole reason the feature exists: URLs somebody can paste.
        'urls': {kind: '%s/%s' % (page_base, kind) for kind in KINDS},
        'feed': '%s/feed/' % feed_base,
        'elements': elements,
        'version': _version(session, elements),
    }


# ---------------------------------------------------------------------------
# The operator's side
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def sessions(request, tournament_id):
    """GET/POST /tournament/<id>/studio/sessions/

    POST starts a broadcast. A tournament may have several over its run - a
    three day event is three - and only one is live at a time, because two live
    sessions means two sets of URLs and no way for an operator to know which
    screen they are looking at.
    """
    user = _viewer(request)
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not may_use_studio(user, tournament):
        return _err('Only the organiser can run a broadcast for this tournament.',
                    'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        return _ok({
            'sessions': [_session_payload(s, request)
                         for s in tournament.broadcast_sessions.all()[:20]],
            'kinds': [{'kind': k, 'label': label}
                      for k, label in BroadcastElement.KINDS],
        }, 'Broadcast sessions')

    # Ending the previous one rather than refusing. An operator starting a new
    # broadcast has already decided the old one is over, and making them go and
    # end it first is a step that exists only to be annoying.
    tournament.broadcast_sessions.filter(status='live').update(
        status='ended', ended_at=timezone.now())

    session = BroadcastSession.objects.create(
        tournament=tournament,
        name=str(request.data.get('name') or '').strip()[:120],
        started_by=user,
    )
    return _ok({'session': _session_payload(session, request)},
               'Broadcast started.', )


@api_view(['GET', 'POST'])
def session_detail(request, tournament_id, session_id):
    """GET the operator state. POST `{"end": true}` to finish the broadcast.

    Ending clears every element, because the alternative is a graphic left on
    screen after the show with nobody watching the console to notice.
    """
    user = _viewer(request)
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not may_use_studio(user, tournament):
        return _err('Only the organiser can run a broadcast for this tournament.',
                    'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    session = tournament.broadcast_sessions.filter(pk=session_id).first()
    if session is None:
        return _err('No such broadcast.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'POST' and request.data.get('end'):
        session.elements.update(is_active=False)
        session.status = 'ended'
        session.ended_at = timezone.now()
        session.save(update_fields=['status', 'ended_at'])

    return _ok({'session': _session_payload(session, request)}, 'Broadcast')


@api_view(['POST'])
def element(request, tournament_id, session_id, kind):
    """POST /tournament/<id>/studio/sessions/<sid>/element/<kind>/

    `{"active": true, "payload": {...}}` puts a graphic on screen or corrects
    what it says. `{"active": false}` takes it off. The payload is merged rather
    than replaced, so an operator can nudge one field mid-show without resending
    everything and without a race against their own last request.
    """
    user = _viewer(request)
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not may_use_studio(user, tournament):
        return _err('Only the organiser can run a broadcast for this tournament.',
                    'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    if kind not in KINDS:
        return _err('There is no element of that kind.', 'UNKNOWN_ELEMENT',
                    status.HTTP_404_NOT_FOUND, field='kind')

    session = tournament.broadcast_sessions.filter(pk=session_id).first()
    if session is None:
        return _err('No such broadcast.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not session.is_live:
        return _err('This broadcast has ended. Start a new one.',
                    'BROADCAST_ENDED', status.HTTP_409_CONFLICT)

    row, _made = BroadcastElement.objects.get_or_create(
        session=session, kind=kind, defaults={'payload': {}})

    payload = request.data.get('payload')
    if isinstance(payload, dict):
        merged = dict(row.payload or {})
        merged.update(payload)
        row.payload = merged

    if 'active' in request.data:
        row.is_active = bool(request.data.get('active'))

    row.save()
    return _ok({'session': _session_payload(session, request)},
               'Element updated.')


# ---------------------------------------------------------------------------
# What the browser source reads
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def feed(request, token):
    """GET /studio/<token>/feed/ - everything on screen, plus the live data.

    Public by token, because a browser source cannot sign in. Nothing here is
    secret: it is the same standings the tournament page shows, pointed at a
    camera. The token exists so element URLs cannot be enumerated.

    One request for every element, deliberately. Six elements polling
    separately on a venue connection is six times the chance of one of them
    being the request that fails while it is on screen.
    """
    session = (BroadcastSession.objects
               .select_related('tournament')
               .filter(token=str(token)).first())
    if session is None:
        return _err('This broadcast link is not valid any more.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    # A retired link answers, once, with nothing on it.
    #
    # The console promises that the URLs "stop working when you end it", and
    # this used to keep serving the whole payload for ever, which made the
    # sentence a shade stronger than the code.
    #
    # It is deliberately not a 404. The runtime keeps its last good frame on
    # anything that is not a success, precisely so a dropped connection does
    # not blank a graphic mid-match - so refusing here would freeze whatever
    # was on screen when the operator pressed End, at the exact moment they
    # wanted it gone. Answering with `retired` clears the screen and tells the
    # page to stop asking, which is what "stops working" has to mean for a
    # browser source.
    if session.status != 'live':
        return _ok({
            'session': {
                'id': session.id,
                'name': session.name,
                'is_live': False,
                'retired': True,
            },
            'retired': True,
            'elements': {kind: {'kind': kind, 'active': False, 'payload': {},
                                'updated_at': None}
                         for kind, _label in BroadcastElement.KINDS},
            'tournament': {},
            'teams': [],
            'live': [],
            'version': 'retired-%s' % session.id,
        }, 'This broadcast has ended.')

    elements = _element_state(session)

    # The bracket's own numbers, computed where they are already computed. The
    # studio does not do arithmetic: seeding, standings and scores are the
    # tournament's answers, and a second implementation here would eventually
    # disagree with the page the players are reading.
    from .views_overlay_feed import overlay_feed
    inner = overlay_feed(request._request if hasattr(request, '_request') else request,
                         session.tournament.slug or session.tournament.tournament_id)
    data = getattr(inner, 'data', {}) or {}
    tournament_data = (data.get('data') or {}) if isinstance(data, dict) else {}

    return _ok({
        'session': {
            'id': session.id,
            'name': session.name,
            'is_live': session.is_live,
        },
        'elements': elements,
        'tournament': tournament_data.get('tournament', {}),
        'teams': tournament_data.get('teams', []),
        'live': tournament_data.get('live', []),
        'version': '%s|%s' % (_version(session, elements),
                              tournament_data.get('version', '')),
    }, 'Studio feed')

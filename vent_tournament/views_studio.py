"""The production studio: elements V-ENT ships, bound to the bracket or the programme.

CEO, 1 September 2026: "the site's tournament bracket systems will handle the
calculations and seeding and feed it into the production studio based off what
is being requested for each element, and each element can be copied and pasted
into your streaming software as browser sources and it updates in realtime...
it'll be like a production studio for any organizer who can pay for it."

And on 2 September, after the audit found the studio was tournament-only:
"i want the production studio built with a very strong background."

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
laptop, and every graphic comes back exactly as it was.

**The feed is one request.** An overlay running six hours on a venue hotspot
should ask one question, not one per element, and should be able to answer "has
anything changed" without diffing a payload. Hence `version`.

**One studio for both things V-ENT runs.** A broadcast is of a tournament or of
an event; the console, the routes, the feed and the tokens are the same code,
and only the kinds of graphic and the data behind them differ. Which kinds a
broadcast may use is `BroadcastElement.kinds_for()`, and who may run it is
`production_access.may_run_production()`, shared with the overlays.

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

from . import presentation
from .models import BroadcastElement, BroadcastSession
from .production_access import (
    REFUSAL_CODE, find_owner, kind_of, may_run_production, viewer as _viewer)
from .views_assets import library_for, resolve_asset, serialize as serialize_asset

# Kept under its old name: the tests and the handovers call it this, and it
# is the one line the plan check will sit on when plans exist.
may_use_studio = may_run_production


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _refuse(kind):
    noun = 'event' if kind == 'event' else 'tournament'
    return _err('Only the organiser can run a broadcast for this %s.' % noun,
                REFUSAL_CODE[kind], status.HTTP_403_FORBIDDEN)


def _kinds(session):
    return [k for k, _ in BroadcastElement.kinds_for(session.kind)]


def _element_state(session):
    rows = {e.kind: e for e in session.elements.all()}
    out = {}
    for kind in _kinds(session):
        row = rows.get(kind)
        payload = (row.payload if row else {}) or {}
        out[kind] = {
            'kind': kind,
            'active': bool(row and row.is_active),
            'payload': payload,
            # How this graphic arrives and leaves, already merged with the
            # broadcast's house style, so the page never has to know which
            # level a value came from. See presentation.py.
            'presentation': presentation.resolve(session.defaults,
                                                 payload.get('options')),
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


def _owner_summary(session):
    owner = session.owner
    if session.kind == 'event':
        return {'name': owner.name, 'slug': owner.slug}
    return {'title': owner.tournament_title, 'slug': owner.slug}


def _session_payload(session, request):
    # The element pages are FRONTEND routes and the feed is an API route, so
    # they do not share a host and cannot share a base.
    #
    # `request.build_absolute_uri` builds against the host that made the
    # request, which is always the API, because it is the frontend calling it.
    # So every URL an organiser copied once read
    #
    #     https://api.v-ent.co/studio/<token>/scorebar/
    #
    # which 404s. There is no such Django route; `/studio/<token>/feed/` is the
    # only thing under that prefix on the API. Pasted into OBS it gives a blank
    # browser source. Nothing reported it: the endpoint answered 200 with a
    # perfectly well-formed URL to a page that does not exist.
    from django.conf import settings

    frontend = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    page_base = '%s/studio/%s' % (frontend, session.token)
    owner = session.owner
    owner_slug = getattr(owner, 'slug', None) or (
        session.event_id or session.tournament_id)
    named_base = '%s/studio/%s' % (frontend, owner_slug)
    feed_base = request.build_absolute_uri('/studio/%s' % session.token)
    elements = _element_state(session)
    kinds = _kinds(session)
    payload = {
        'id': session.id,
        'name': session.name,
        'status': session.status,
        'is_live': session.is_live,
        'kind': session.kind,
        # Published rather than left to be parsed out of a URL. Every reader
        # was pulling it from `urls.scorebar` by position, so moving the token
        # to the end of a named address broke all of them at once. It goes
        # only to somebody who may run production, and it is already inside
        # every URL in this payload.
        'token': session.token,
        'started_at': session.started_at.isoformat(),
        'ended_at': session.ended_at.isoformat() if session.ended_at else None,
        # The whole reason the feature exists: URLs somebody can paste.
        # The address carries the name of the thing being broadcast and the
        # name of the graphic, because an operator pastes eight of these into
        # OBS and then reads them back in a list of browser sources.
        # `/studio/<token>/scorebar` told them nothing about which broadcast it
        # belonged to. The token stays in it: it is still the credential, and
        # the slug is a label. CEO, 3 September 2026.
        'urls': {kind: '%s/%s/%s' % (named_base, kind, session.token)
                 for kind in kinds},
        # What the URLs were before this, so a source already pasted into a
        # machine at a venue keeps working for ever.
        'legacy_urls': {kind: '%s/%s' % (page_base, kind) for kind in kinds},
        'feed': '%s/feed/' % feed_base,
        'elements': elements,
        # The house style, and what a console may offer for it.
        'defaults': presentation.resolve(session.defaults, None),
        'presentation_options': presentation.catalogue(),
        'version': _version(session, elements),
    }
    # Named by what it is of, and `tournament` kept as the key the console
    # has always read.
    payload[session.kind] = _owner_summary(session)
    return payload


# ---------------------------------------------------------------------------
# The operator's side, once, for both kinds
# ---------------------------------------------------------------------------

def _sessions(request, owner, kind):
    if owner is None:
        return _err('%s not found.' % ('Event' if kind == 'event' else 'Tournament'),
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if not may_run_production(user, owner):
        return _refuse(kind)

    if request.method == 'GET':
        return _ok({
            'sessions': [_session_payload(s, request)
                         for s in owner.broadcast_sessions.all()[:20]],
            'kinds': [{'kind': k, 'label': label}
                      for k, label in BroadcastElement.kinds_for(kind)],
        }, 'Broadcast sessions')

    # Ending the previous one rather than refusing. An operator starting a new
    # broadcast has already decided the old one is over, and making them go and
    # end it first is a step that exists only to be annoying.
    owner.broadcast_sessions.filter(status='live').update(
        status='ended', ended_at=timezone.now())

    session = BroadcastSession.objects.create(
        tournament=owner if kind == 'tournament' else None,
        event=owner if kind == 'event' else None,
        name=str(request.data.get('name') or '').strip()[:120],
        started_by=user,
    )
    return _ok({'session': _session_payload(session, request)},
               'Broadcast started.')


def _session_detail(request, owner, kind, session_id):
    if owner is None:
        return _err('%s not found.' % ('Event' if kind == 'event' else 'Tournament'),
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if not may_run_production(user, owner):
        return _refuse(kind)

    session = owner.broadcast_sessions.filter(pk=session_id).first()
    if session is None:
        return _err('No such broadcast.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'POST' and 'defaults' in request.data:
        # The house style for the whole broadcast. Set once, and any single
        # graphic may still differ.
        try:
            session.defaults = presentation.clean(request.data.get('defaults'))
        except presentation.PresentationError as err:
            return _err(str(err), 'INVALID_PRESENTATION', field=err.field)
        session.save(update_fields=['defaults'])

    if request.method == 'POST' and request.data.get('end'):
        # Ending clears every element, because the alternative is a graphic
        # left on screen after the show with nobody watching the console.
        session.elements.update(is_active=False)
        session.status = 'ended'
        session.ended_at = timezone.now()
        session.save(update_fields=['status', 'ended_at'])

    return _ok({'session': _session_payload(session, request)}, 'Broadcast')


def _element(request, owner, kind, session_id, element_kind):
    if owner is None:
        return _err('%s not found.' % ('Event' if kind == 'event' else 'Tournament'),
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if not may_run_production(user, owner):
        return _refuse(kind)

    session = owner.broadcast_sessions.filter(pk=session_id).first()
    if session is None:
        return _err('No such broadcast.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if element_kind not in _kinds(session):
        # A bracket on an event, a programme on a tournament: named so the
        # console can say which, rather than a generic 404.
        return _err('There is no %s graphic for %s.' % (
            element_kind.replace('_', ' '), 'an event' if kind == 'event' else 'a tournament'),
            'UNKNOWN_ELEMENT', status.HTTP_404_NOT_FOUND, field='kind')
    if not session.is_live:
        return _err('This broadcast has ended. Start a new one.',
                    'BROADCAST_ENDED', status.HTTP_409_CONFLICT)

    row, _made = BroadcastElement.objects.get_or_create(
        session=session, kind=element_kind, defaults={'payload': {}})

    # The payload is merged rather than replaced, so an operator can nudge one
    # field mid-show without resending everything and without a race against
    # their own last request.
    payload = request.data.get('payload')
    if isinstance(payload, dict):
        merged = dict(row.payload or {})
        merged.update(payload)
        if 'options' in payload:
            # Validated here rather than at read time, so a typo is refused at
            # the press that made it and not discovered on air.
            try:
                merged['options'] = presentation.clean(payload.get('options'))
            except presentation.PresentationError as err:
                return _err(str(err), 'INVALID_PRESENTATION', field=err.field)
        row.payload = merged

    if 'active' in request.data:
        row.is_active = bool(request.data.get('active'))

    row.save()
    return _ok({'session': _session_payload(session, request)},
               'Element updated.')


# The routes. Tournament-scoped and event-scoped, the same three each.

@api_view(['GET', 'POST'])
def sessions(request, tournament_id):
    """GET/POST /tournament/<ref>/studio/sessions/"""
    return _sessions(request, find_owner('tournament', tournament_id), 'tournament')


@api_view(['GET', 'POST'])
def session_detail(request, tournament_id, session_id):
    """GET the operator state. POST `{"end": true}` to finish the broadcast."""
    return _session_detail(request, find_owner('tournament', tournament_id),
                           'tournament', session_id)


@api_view(['POST'])
def element(request, tournament_id, session_id, kind):
    """POST /tournament/<ref>/studio/sessions/<sid>/element/<kind>/"""
    return _element(request, find_owner('tournament', tournament_id),
                    'tournament', session_id, kind)


@api_view(['GET', 'POST'])
def event_sessions(request, event_id):
    """GET/POST /event/<ref>/studio/sessions/"""
    return _sessions(request, find_owner('event', event_id), 'event')


@api_view(['GET', 'POST'])
def event_session_detail(request, event_id, session_id):
    return _session_detail(request, find_owner('event', event_id), 'event', session_id)


@api_view(['POST'])
def event_element(request, event_id, session_id, kind):
    return _element(request, find_owner('event', event_id), 'event', session_id, kind)


# ---------------------------------------------------------------------------
# What the browser source reads
# ---------------------------------------------------------------------------

def _retired(session):
    """A retired link answers, once, with nothing on it.

    The console promises that the URLs "stop working when you end it". This is
    deliberately not a 404: the runtime keeps its last good frame on anything
    that is not a success, precisely so a dropped connection does not blank a
    graphic mid-match. Refusing here would freeze whatever was on screen when
    the operator pressed End, at the exact moment they wanted it gone.
    Answering with `retired` clears the screen and tells the page to stop
    asking, which is what "stops working" has to mean for a browser source.
    """
    return _ok({
        'session': {
            'id': session.id,
            'name': session.name,
            'is_live': False,
            'retired': True,
        },
        'kind': session.kind,
        'retired': True,
        'elements': {kind: {'kind': kind, 'active': False, 'payload': {},
                            'presentation': presentation.resolve(None, None),
                            'asset': None, 'updated_at': None}
                     for kind in _kinds(session)},
        'assets': [],
        'tournament': {},
        'event': {},
        'teams': [],
        'live': [],
        'sponsors': [],
        'programme': [],
        'version': 'retired-%s' % session.id,
    }, 'This broadcast has ended.')


@api_view(['GET'])
@permission_classes([AllowAny])
def feed(request, token):
    """GET /studio/<token>/feed/ - everything on screen, plus the live data.

    Public by token, because a browser source cannot sign in. Nothing here is
    secret: it is the same standings or programme the public page shows,
    pointed at a camera. The token exists so element URLs cannot be enumerated.

    One request for every element, deliberately. Six elements polling
    separately on a venue connection is six times the chance of one of them
    being the request that fails while it is on screen.
    """
    session = (BroadcastSession.objects
               .select_related('tournament', 'event')
               .filter(token=str(token)).first())
    if session is None:
        return _err('This broadcast link is not valid any more.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if session.status != 'live':
        return _retired(session)

    elements = _element_state(session)
    raw = request._request if hasattr(request, '_request') else request

    # The media library, and the one asset a `media` graphic is pointed at,
    # already resolved. An element page must not have to look anything up: it
    # is a browser source with one request, and a second round trip to turn a
    # tag into a URL is a second chance to fail with a clip half on screen.
    owner, owner_kind = session.owner, session.kind
    assets = [serialize_asset(a, request) for a in library_for(owner, owner_kind)]
    by_id = {a['id']: a for a in assets}
    for kind in BroadcastElement.MEDIA_KINDS:
        state = elements.get(kind)
        if not state:
            continue
        payload = state['payload'] or {}
        chosen = None
        if payload.get('asset_id'):
            try:
                chosen = by_id.get(int(payload['asset_id']))
            except (TypeError, ValueError):
                chosen = None
        if chosen is None and payload.get('tag'):
            found = resolve_asset(owner, owner_kind, word=payload['tag'])
            chosen = by_id.get(found.id) if found else None
        state['asset'] = chosen

    # The squad depth graphic: one player's EAFC lineup, already resolved.
    #
    # CEO, 3 September 2026: "what they picked and formation they selected was
    # shown inside the player squad depth overlay design, updated automatically
    # for each player." Automatic is the whole ask, so the feed carries the
    # lineup rather than the page fetching it: the page already polls this, and
    # a lineup saved at 8pm is on screen within one poll with nobody pressing
    # anything.
    squad = elements.get('squad_depth')
    if squad is not None:
        squad['lineup'] = None
        squad['formation_slots'] = []
        wanted = str((squad['payload'] or {}).get('player') or '').strip()
        if wanted and session.kind == 'tournament':
            try:
                from vent_cards import formations as _formations
                from vent_cards.models import Lineup
                from vent_cards.views import serialize_lineup
                row = (Lineup.objects
                       .filter(tournament=session.tournament,
                               user__username__iexact=wanted)
                       .prefetch_related('slots__card').first())
                if row is not None:
                    squad['lineup'] = serialize_lineup(row)
                    squad['formation_slots'] = _formations.get(row.formation) or []
            except Exception:                               # noqa: BLE001
                # A graphic must never be taken down by a lookup. An empty
                # lineup draws the empty state, which is a designed thing.
                pass

    # The numbers come from where they are already computed. The studio does
    # no arithmetic: standings, scores, what is on now, how many are through
    # the door are the tournament's or the event's answers, and a second
    # implementation here would eventually disagree with the page the players
    # or the attendees are reading.
    if session.kind == 'event':
        from .views_overlay_feed import event_overlay_feed
        inner = event_overlay_feed(raw, session.event.slug or session.event.event_id)
        data = (getattr(inner, 'data', {}) or {}).get('data') or {}
        return _ok({
            'session': {'id': session.id, 'name': session.name, 'is_live': True},
            'kind': 'event',
            'elements': elements,
            'event': data.get('event', {}),
            'programme': data.get('programme', []),
            'sponsors': data.get('sponsors', []),
            'assets': assets,
            'version': '%s|%s|%s' % (_version(session, elements),
                                     data.get('version', ''), len(assets)),
        }, 'Studio feed')

    from .views_overlay_feed import overlay_feed
    inner = overlay_feed(raw, session.tournament.slug or session.tournament.tournament_id)
    data = (getattr(inner, 'data', {}) or {}).get('data') or {}
    return _ok({
        'session': {'id': session.id, 'name': session.name, 'is_live': True},
        'kind': 'tournament',
        'elements': elements,
        'tournament': data.get('tournament', {}),
        'teams': data.get('teams', []),
        'live': data.get('live', []),
        'sponsors': data.get('sponsors', []),
        'assets': assets,
        'version': '%s|%s|%s' % (_version(session, elements),
                                 data.get('version', ''), len(assets)),
    }, 'Studio feed')

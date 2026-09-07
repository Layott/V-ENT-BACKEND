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
from . import text_layers
from .models import (
    BroadcastElement, BroadcastSession, BroadcastSlot, TournamentOverlay)
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
    # The layers come with the elements in one query. An element page is a
    # browser source with one request, and a second round trip per graphic is a
    # second chance to fail with a caption half on screen.
    rows = {e.kind: e for e in session.elements.prefetch_related('text_layers')}
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
            # The words an operator put on top of this graphic. Active only,
            # and in paint order, so the page draws the list it is given rather
            # than deciding anything. Filtered in Python off the prefetch,
            # because a filtered query per element is a query per element.
            'layers': text_layers.serialize_many(
                text_layers.active_of(list(row.text_layers.all()))
            ) if row else [],
            'updated_at': row.updated_at.isoformat() if row else None,
        }
    return out


def _slot_state(session):
    """The four layers, always all four, whether or not a row exists yet.

    Always all four because the console draws four controls and OBS holds four
    browser sources: a role with no row is an EMPTY layer, not a missing one,
    and making the reader distinguish those two would put a hole in the panel
    on the first broadcast of every session.
    """
    rows = {row.role: row for row in session.slots.select_related('overlay')}
    out = {}
    for role, label in BroadcastSlot.ROLES:
        row = rows.get(role)
        out[role] = {
            'role': role,
            'label': label,
            'holds': row.holds if row else '',
            'item_kind': row.item_kind if row else '',
            'overlay_id': row.overlay_id if row else None,
            'overlay_name': (row.overlay.name if row and row.overlay_id else ''),
            # The token the slot page loads the uploaded file by. The page
            # builds `<API>/overlay/<token>/` from it, the same address the
            # organiser would paste into OBS directly, so a file in a slot and
            # a file on its own URL are byte for byte the same thing.
            'overlay_token': (row.overlay.token if row and row.overlay_id else ''),
            'active': bool(row.active) if row else False,
        }
    return out


def _version(session, elements):
    """Something an element can compare without reading the whole payload.

    Counts and timestamps rather than a hash of everything, because the point is
    to be cheap on a bad connection.
    """
    stamp = max(
        [e['updated_at'] or '' for e in elements.values()] or [''])
    # The look is in here on purpose. An element page skips its redraw when the
    # version has not moved, so a broadcast switched from the house look to the
    # Rivalry pack would keep drawing the old one until something else changed.
    #
    # The text layers are in here for exactly the same reason, and they need
    # their own stamp rather than riding on `updated_at`: `updated_at` belongs
    # to the ELEMENT, and adding, editing, reordering or removing a layer does
    # not touch it. A layer edited under a stale version is a change nobody on
    # air ever sees, which has already happened twice here.
    # The slots are in here for the same reason the look and the text layers
    # are: a slot page skips its redraw when the version has not moved, so an
    # operator cueing a different graphic into `full` would change nothing on
    # air until something else happened to move the stamp.
    slots = session.slots.all()
    slot_stamp = '.'.join(
        '%s:%s:%s:%s' % (row.role, row.item_kind or row.overlay_id or '',
                         int(row.active),
                         row.updated_at.isoformat() if row.updated_at else '')
        for row in sorted(slots, key=lambda r: r.role))

    return '%s-%s-%s-%s-%s-%s' % (
        session.id,
        session.theme,
        sum(1 for e in elements.values() if e['active']),
        stamp,
        text_layers.stamp(elements),
        slot_stamp)


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
        # THE FOUR LAYERS, and the four addresses an operator pastes once.
        #
        # This is what makes the studio usable in a gallery. Before it, going
        # on air with twenty graphics meant twenty browser sources added and
        # removed by hand during a show, which nobody does. Now: four sources,
        # stacked once bottom to top, and everything after that is a press in
        # the console. Modelled on the RIVALRY control room the CEO sent.
        'slots': _slot_state(session),
        # The files THIS organiser uploaded, so the layer picker can offer
        # them beside V-ENT's own graphics. To an operator they are the same
        # decision - what goes in this layer - so they belong in one control.
        'overlays': [
            {'id': o.id, 'name': o.name, 'token': o.token}
            for o in (session.tournament.overlays.all() if session.tournament_id
                      else session.event.overlays.all())
        ],
        'slot_urls': {role: '%s/slot-%s/%s' % (named_base, role, session.token)
                      for role, _label in BroadcastSlot.ROLES},
        'legacy_slot_urls': {role: '%s/slot-%s' % (page_base, role)
                             for role, _label in BroadcastSlot.ROLES},
        # The house style, and what a console may offer for it.
        'defaults': presentation.resolve(session.defaults, None),
        'presentation_options': presentation.catalogue(),
        # Which look the graphics are drawn in, and the looks that exist. A
        # list rather than a hardcoded pair in the console, so a look added
        # here appears there without a second change.
        'theme': session.theme,
        'themes': [{'value': v, 'label': label}
                   for v, label in BroadcastSession.THEMES],
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

    if request.method == 'POST' and 'theme' in request.data:
        # Which look this broadcast is drawn in. Refused rather than ignored
        # when it is not one that exists: an operator who set a look and saw
        # nothing change would set it again rather than read a name back.
        wanted = str(request.data.get('theme') or '').strip()
        if wanted not in dict(BroadcastSession.THEMES):
            return _err('There is no broadcast look called %s.' % wanted,
                        'INVALID_THEME', field='theme')
        session.theme = wanted
        session.save(update_fields=['theme'])

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


def _slot(request, owner, kind, session_id, role):
    """Putting a graphic, or an uploaded file, into one of the four layers.

    CEO, 7 September 2026, on the online control room: "BUILD IT PROPERLY."

    An operator pastes four browser sources into OBS once - bg, full, lower,
    bug - and from then on this is the only thing they touch. What occupies a
    slot and whether that slot is on air are separate presses, because that is
    how a gallery actually works: load the next graphic while the layer is
    dark, take it up on the cue.
    """
    if owner is None:
        return _err('%s not found.' % ('Event' if kind == 'event' else 'Tournament'),
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if not may_run_production(user, owner):
        return _refuse(kind)

    session = owner.broadcast_sessions.filter(pk=session_id).first()
    if session is None:
        return _err('No such broadcast.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    roles = [r for r, _label in BroadcastSlot.ROLES]
    if role not in roles:
        return _err('There is no %s layer.' % role, 'UNKNOWN_SLOT',
                    status.HTTP_404_NOT_FOUND, field='role')
    if not session.is_live:
        return _err('This broadcast has ended. Start a new one.',
                    'BROADCAST_ENDED', status.HTTP_409_CONFLICT)

    row, _made = BroadcastSlot.objects.get_or_create(session=session, role=role)

    # What goes in it. Exactly one of the two, and sending either clears the
    # other, because a slot showing two things is not a thing anybody wants and
    # letting it happen is how a stale graphic ends up under a new one.
    if 'item_kind' in request.data:
        wanted = str(request.data.get('item_kind') or '').strip()
        if wanted and wanted not in _kinds(session):
            return _err('There is no %s graphic for %s.' % (
                wanted.replace('_', ' '),
                'an event' if kind == 'event' else 'a tournament'),
                'UNKNOWN_ELEMENT', status.HTTP_404_NOT_FOUND, field='item_kind')
        row.item_kind = wanted
        if wanted:
            row.overlay = None

    if 'overlay_id' in request.data:
        raw = request.data.get('overlay_id')
        if raw in (None, '', 0, '0'):
            row.overlay = None
        else:
            # Only an overlay belonging to THIS broadcast's own tournament or
            # event. Without this check a token for one broadcast could put
            # somebody else's uploaded file on air.
            overlay = TournamentOverlay.objects.filter(
                pk=raw, tournament=session.tournament_id and session.tournament,
            ).first() if session.tournament_id else TournamentOverlay.objects.filter(
                pk=raw, event=session.event).first()
            if overlay is None:
                return _err('That overlay does not belong to this broadcast.',
                            'UNKNOWN_OVERLAY', status.HTTP_404_NOT_FOUND,
                            field='overlay_id')
            row.overlay = overlay
            row.item_kind = ''

    if 'active' in request.data:
        row.active = bool(request.data.get('active'))

    row.save()
    return _ok({'session': _session_payload(session, request)}, 'Slot updated.')


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
def slot(request, tournament_id, session_id, role):
    """POST /tournament/<ref>/studio/sessions/<sid>/slot/<role>/"""
    return _slot(request, find_owner('tournament', tournament_id),
                 'tournament', session_id, role)


@api_view(['POST'])
def event_slot(request, event_id, session_id, role):
    """POST /event/<ref>/studio/sessions/<sid>/slot/<role>/"""
    return _slot(request, find_owner('event', event_id),
                 'event', session_id, role)


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
    from .views_overlay_feed import BLANK_RIVALRY, BLANK_RUN_OF_SHOW

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
                            # Present and empty for the same reason as the
                            # blocks below: a page reading a name that is not
                            # there throws on the way to drawing nothing.
                            'layers': [],
                            'asset': None, 'updated_at': None}
                     for kind in _kinds(session)},
        'assets': [],
        'tournament': {},
        'event': {},
        'teams': [],
        'live': [],
        'sponsors': [],
        'programme': [],
        # Present and empty rather than absent, exactly as above: a retired
        # link clears the screen, and an element reading a name that is not
        # there would throw on the way to drawing nothing.
        'rivalry': dict(BLANK_RIVALRY),
        'run_of_show': dict(BLANK_RUN_OF_SHOW),
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
            'session': {'id': session.id, 'name': session.name,
                        'is_live': True, 'theme': session.theme},
            'kind': 'event',
            'elements': elements,
            # What each of the four layers is holding right now. A slot page
            # reads its own role out of this and renders whatever it names.
            'slots': _slot_state(session),
            'event': data.get('event', {}),
            'programme': data.get('programme', []),
            'sponsors': data.get('sponsors', []),
            'assets': assets,
            'version': '%s|%s|%s' % (_version(session, elements),
                                     data.get('version', ''), len(assets)),
        }, 'Studio feed')

    from .views_overlay_feed import (BLANK_RIVALRY, overlay_feed,
                                     run_of_show_for)
    inner = overlay_feed(raw, session.tournament.slug or session.tournament.tournament_id)
    data = (getattr(inner, 'data', {}) or {}).get('data') or {}

    # The run of show, asked for again with the organiser's own access. The
    # public feed above withholds a sheet that is not published, and a private
    # sheet is exactly the one an organiser runs their show from: this surface
    # is reached only through a session token they hold, so it sees the whole
    # thing. Its stamp joins the version, because the cue on screen changes
    # when the clock passes 14:00 and no row in any table moves when it does.
    run_of_show, run_stamp = run_of_show_for(session.tournament,
                                             include_private=True)
    return _ok({
        'session': {'id': session.id, 'name': session.name,
                    'is_live': True, 'theme': session.theme},
        'kind': 'tournament',
        'elements': elements,
        # What each of the four layers is holding right now.
        'slots': _slot_state(session),
        'tournament': data.get('tournament', {}),
        'teams': data.get('teams', []),
        'live': data.get('live', []),
        'sponsors': data.get('sponsors', []),
        # The aggregate league, forwarded whole. The fixture card, the result
        # cards, the head to head and both standings tables draw from this one
        # block, and it is empty with `enabled` false for a tournament that is
        # not an aggregate one.
        'rivalry': data.get('rivalry') or dict(BLANK_RIVALRY),
        'run_of_show': run_of_show,
        'assets': assets,
        'version': '%s|%s|%s|%s' % (_version(session, elements),
                                    data.get('version', ''), len(assets),
                                    run_stamp),
    }, 'Studio feed')

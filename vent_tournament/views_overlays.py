"""Uploading an overlay, and the URL that goes into OBS.

    GET    /tournament/<t>/overlays/            the organiser's list
    POST   /tournament/<t>/overlays/            upload one
    DELETE /tournament/<t>/overlays/<id>/       remove it
    POST   /tournament/<t>/overlays/<id>/rotate/   change its URL
    GET    /overlay/<token>/                    what OBS opens

The last one is the point. A browser source in OBS or vMix is a URL and nothing
else: no session, no cookie, no header, no way to sign in. So the token in the
URL is the credential, the page is public, and it renders without anybody
touching it.

What the served page is: the uploader's own file, with one script injected ahead
of it. That script fetches the tournament, fills anything marked `data-vent`,
calls `window.build()` if the file defines one, and polls for changes. The
uploader's markup is otherwise untouched, because the file they debug against
has to be the file that is served.
"""

import re

from django.http import HttpResponse
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from vent_auth.models import Users

from . import overlay_binding
from .models import Tournament, TournamentOverlay

#: An overlay is markup. A 5MB one is already unusual; the KON10DR pack reaches
#: 3.3MB only because it inlines every image as base64.
MAX_BYTES = 8 * 1024 * 1024


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


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


def _may_manage(user, tournament):
    return user is not None and tournament.tournament_creator_id == user.user_id


def serialize(overlay, request):
    # The whole reason the feature exists: a URL somebody can paste.
    url = request.build_absolute_uri('/overlay/%s/' % overlay.token)
    return {
        'id': overlay.id,
        'name': overlay.name,
        'url': url,
        'binding': overlay.binding,
        'bound_fields': overlay.bound_fields,
        'created_at': overlay.created_at,
        'updated_at': overlay.updated_at,
    }


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser])
def overlays(request, tournament_id):
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _error('Sign in first.', 'AUTH_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(user, tournament):
        return _error('Only the organiser can manage overlays.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        rows = TournamentOverlay.objects.filter(tournament=tournament)
        return _ok({'overlays': [serialize(o, request) for o in rows],
                    'count': rows.count(),
                    # What the runtime can fill, the prompt a designer is
                    # given, and what they can start from instead of drawing.
                    # Sent with the list so the page never keeps its own copy
                    # of names the server is the authority on.
                    'fields': BINDINGS_FOR_TOURNAMENT,
                    'prompt': DESIGNER_PROMPT_TOURNAMENT,
                    'templates': TEMPLATES_FOR_TOURNAMENT})

    # One implementation for both owners. The validation, the inspection of
    # what the file binds to and the warning about names the runtime cannot
    # fill are the same job whichever kind of thing it is for.
    return _create_overlay(request, tournament=tournament, user=user)


@api_view(['DELETE'])
def overlay_detail(request, tournament_id, overlay_id):
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    if not _may_manage(_viewer(request), tournament):
        return _error('Only the organiser can remove an overlay.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    overlay = TournamentOverlay.objects.filter(
        tournament=tournament, pk=overlay_id).first()
    if overlay is None:
        return _error('Overlay not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    overlay.delete()
    return _ok({'removed': overlay_id}, 'Overlay removed.')


@api_view(['POST'])
def rotate(request, tournament_id, overlay_id):
    """A new URL for the same file.

    The old one stops working immediately, which is the point: a URL pasted into
    a machine at a venue eighteen months ago is a URL somebody else may have.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    if not _may_manage(_viewer(request), tournament):
        return _error('Only the organiser can rotate a URL.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    overlay = TournamentOverlay.objects.filter(
        tournament=tournament, pk=overlay_id).first()
    if overlay is None:
        return _error('Overlay not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    import secrets
    overlay.token = secrets.token_urlsafe(24)[:48]
    overlay.save(update_fields=['token'])
    return _ok({'overlay': serialize(overlay, request)}, 'The URL has changed.')


# ---------------------------------------------------------------------------
# What OBS actually opens.
# ---------------------------------------------------------------------------

_HEAD = re.compile(r'<head[^>]*>', re.I)
_HTML = re.compile(r'<html[^>]*>', re.I)


def _inject(markup, runtime_tag):
    """Put the runtime in front of the uploader's own scripts.

    Ahead of them, because a file like the KON10DR pack reads `window.VENT` the
    moment it runs, and a runtime that arrives afterwards is a runtime that
    arrives too late.
    """
    match = _HEAD.search(markup)
    if match:
        at = match.end()
        return markup[:at] + runtime_tag + markup[at:]
    match = _HTML.search(markup)
    if match:
        at = match.end()
        return markup[:at] + runtime_tag + markup[at:]
    return runtime_tag + markup


@xframe_options_exempt
def serve_overlay(request, token):
    """The URL pasted into OBS, vMix, or anything else with a browser source.

    Public by token, because a browser source cannot sign in. Deliberately not
    a DRF view: it answers HTML, and a DRF `Response` would content-negotiate
    its way into JSON.
    """
    overlay = (TournamentOverlay.objects
               .select_related('tournament')
               .filter(token=str(token)).first())
    if overlay is None:
        return HttpResponse(
            '<!doctype html><meta charset="utf-8"><title>Not found</title>'
            '<body style="margin:0;background:#131316;color:#8a8a8f;'
            'font:14px system-ui;display:flex;align-items:center;'
            'justify-content:center;height:100vh">'
            'This overlay link is not valid any more.</body>',
            content_type='text/html; charset=utf-8', status=404)

    try:
        overlay.file.open('rb')
        markup = overlay.file.read().decode('utf-8', 'replace')
    finally:
        try:
            overlay.file.close()
        except Exception:                                   # noqa: BLE001
            pass

    # Whichever kind of thing this overlay belongs to. Reading `.tournament`
    # unconditionally is what happens the moment a field that was always set
    # can be null, and it threw a NoneType on every event overlay.
    if overlay.event_id:
        owner = overlay.event
        feed = request.build_absolute_uri(
            '/event/%s/overlay-feed/' % (owner.slug or owner.event_id))
    else:
        owner = overlay.tournament
        feed = request.build_absolute_uri(
            '/tournament/%s/overlay-feed/' % (owner.slug or owner.tournament_id))
    runtime = request.build_absolute_uri('/static/overlay-runtime.js')

    # The runtime is configured through a data attribute rather than a query
    # string, so an overlay's own `?t=AX` reaches the overlay untouched.
    tag = (
        '<script id="vent-overlay-runtime" '
        'data-feed="%s" data-every="4000" src="%s"></script>'
        % (feed, runtime))

    response = HttpResponse(_inject(markup, tag),
                            content_type='text/html; charset=utf-8')
    # A browser source that caches is a scoreboard that is wrong.
    response['Cache-Control'] = 'no-store, must-revalidate'
    return response


# ---------------------------------------------------------------------------
# What a designer needs to know, and what they can start from
# ---------------------------------------------------------------------------
#
# CEO: "they upload designs as html using the prompt they copy from the site
# and their own designs to convert their designs to usable html files for the
# website ... pick from existing stream element templates for tournaments and
# events."
#
# So three things had to exist beside the upload: the PROMPT an organiser
# copies into whatever tool drew their design, the LIST of names the runtime
# can fill, and a set of TEMPLATES to start from instead of uploading.
#
# The prompt names the fields explicitly. A designer given "make me an
# overlay" produces something beautiful that binds to nothing, and the fault
# only appears on air.

def _prompt_for(kind, fields):
    # The example is built from THIS list rather than written once and shared.
    # A designer asked for an event overlay and shown a `home_score` example
    # binds to a field that does not exist for them, and the mistake only
    # turns up on air.
    example = ''.join('  <div data-vent="%s">%s</div>\n' % (key, why.capitalize())
                      for key, why in fields[:2])
    return (
        'Make a single self-contained HTML file for a broadcast overlay.\n'
        '\n'
        'Rules it has to follow:\n'
        '  - One file. Inline the CSS in a <style> tag. No external '
        'stylesheets, fonts or scripts: it is loaded by OBS with no network '
        'guarantees.\n'
        '  - Transparent background on <body>, so the video shows through. '
        'Do not paint a colour behind everything.\n'
        '  - Design it at 1920x1080. It is scaled by the streaming software, '
        'not by the browser.\n'
        '  - Put each live value in an element carrying a data-vent attribute. '
        'The runtime finds those and fills them, and anything else stays '
        'exactly as you drew it.\n'
        '\n'
        'The values available for %s are:\n%s\n'
        '\n'
        'For example:\n'
        '%s'
        '\n'
        'Whatever is between the tags is placeholder text. It is replaced when '
        'the overlay is on air, so make it realistic and the right length: an '
        'overlay drawn around "0" breaks at "12".\n'
    ) % (kind, '\n'.join('  %s - %s' % (key, why) for key, why in fields),
         example)


# What the runtime can fill on a tournament overlay.
TOURNAMENT_FIELDS = [
    ('tournament_name', 'the tournament title'),
    ('home_name', 'the first participant'),
    ('away_name', 'the second participant'),
    ('home_score', 'the first score'),
    ('away_score', 'the second score'),
    ('round_name', 'which round this match is in'),
    ('standings', 'the table, repeated per row'),
    ('position', 'a place in the table, inside standings'),
    ('player_name', 'a name, inside standings'),
    ('points', 'points, inside standings'),
    ('played', 'games played, inside standings'),
    ('goal_difference', 'goal difference, inside standings'),
]

# And on an event. An event has no bracket; it has a programme, a door count
# and the people who paid for the banners.
EVENT_FIELDS = [
    ('event_name', 'the event title'),
    ('venue', 'where it is'),
    ('starts_at', 'when it starts'),
    ('now_on', 'what is happening now, from the programme'),
    ('next_on', 'what is on next'),
    ('room', 'which room the current session is in'),
    ('attending', 'how many are in'),
    ('tickets_sold', 'how many tickets have gone'),
    ('sponsor_name', 'a sponsor, repeated per row'),
]

DESIGNER_PROMPT_TOURNAMENT = _prompt_for('a tournament', TOURNAMENT_FIELDS)
DESIGNER_PROMPT_EVENT = _prompt_for('an event', EVENT_FIELDS)

BINDINGS_FOR_TOURNAMENT = [k for k, _why in TOURNAMENT_FIELDS]
BINDINGS_FOR_EVENT = [k for k, _why in EVENT_FIELDS]

# Something to start from. Named for the moment they are used rather than for
# their shape, because an organiser is choosing a job and not a rectangle.
TEMPLATES_FOR_TOURNAMENT = [
    {'key': 'scorebar', 'name': 'Score bar',
     'detail': 'Two names and the score, along the top. The one that is on '
               'screen for most of a broadcast.'},
    {'key': 'standings', 'name': 'Standings',
     'detail': 'The table, for the break between matches.'},
    {'key': 'lower_third', 'name': 'Lower third',
     'detail': 'A name and a line under it, for introducing somebody.'},
    {'key': 'player_card', 'name': 'Player card',
     'detail': 'One competitor and their record.'},
    {'key': 'bracket', 'name': 'Bracket',
     'detail': 'Where everybody is in the draw.'},
    {'key': 'ticker', 'name': 'Ticker',
     'detail': 'A line along the bottom for results and announcements.'},
    {'key': 'intro', 'name': 'Starting soon',
     'detail': 'What is on screen before anybody speaks.'},
    {'key': 'outro', 'name': 'Thanks for watching',
     'detail': 'The card at the end, with the sponsors.'},
]

TEMPLATES_FOR_EVENT = [
    {'key': 'now_next', 'name': 'Now and next',
     'detail': 'What is happening in this room, and what follows it.'},
    {'key': 'lower_third', 'name': 'Lower third',
     'detail': 'A name and a line under it, for whoever is speaking.'},
    {'key': 'sponsors', 'name': 'Sponsor wall',
     'detail': 'The people who paid for the banners, in rotation.'},
    {'key': 'ticker', 'name': 'Ticker',
     'detail': 'Announcements along the bottom.'},
    {'key': 'intro', 'name': 'Doors open',
     'detail': 'The holding card before the room fills.'},
]


def _create_overlay(request, tournament=None, event=None, user=None):
    """Upload one HTML file and turn it into an address OBS can open.

    Shared by both owners deliberately. The validation, the inspection of what
    the file binds to, and the warning about names the runtime cannot fill are
    the same job whichever kind of thing the overlay is for, and a second copy
    would drift.
    """
    upload = request.FILES.get('file')
    if upload is None:
        return _error('Choose an HTML file.', 'VALIDATION_ERROR')
    if not str(upload.name).lower().endswith(('.html', '.htm')):
        return _error('An overlay is an HTML file.', 'NOT_HTML')
    if upload.size > MAX_BYTES:
        return _error('That file is larger than %dMB.'
                      % (MAX_BYTES // 1024 // 1024), 'TOO_LARGE')

    markup = upload.read().decode('utf-8', 'replace')
    upload.seek(0)

    binding, fields, warnings = overlay_binding.inspect(markup)

    # Told at upload rather than discovered on air, which is the only moment
    # it is cheap to fix.
    known = set(BINDINGS_FOR_EVENT if event is not None else BINDINGS_FOR_TOURNAMENT)
    unknown = [f for f in fields if f not in known]
    if unknown:
        warnings.append(
            'These names are not ones the overlay runtime knows how to fill, '
            'so they will stay empty: %s' % ', '.join(unknown))
    if not fields:
        warnings.append(
            'Nothing in this file carries a data-vent attribute, so it will '
            'show exactly what you drew and never update.')

    overlay = TournamentOverlay.objects.create(
        tournament=tournament, event=event,
        name=str(request.data.get('name') or upload.name)[:120],
        file=upload, binding=binding, bound_fields=fields,
        created_by=user or _viewer(request))

    return Response({'status': 'success', 'data': {
        'overlay': serialize(overlay, request),
        'warnings': warnings,
    }, 'message': 'Overlay uploaded.'}, status=status.HTTP_201_CREATED)


def new_overlay_token():
    import secrets
    return secrets.token_urlsafe(24)[:48]


# ---------------------------------------------------------------------------
# The same thing, owned by an event
# ---------------------------------------------------------------------------
#
# An event has a programme, a door count, ticket sales and sponsors, all of
# which somebody wants on a screen behind a stage. It was tournament-only, so
# an organiser running an event had nowhere to upload a design and no URL to
# paste into OBS - the same shape of gap as short links, and the reason
# `tools/check-parity.py` has a row for this pair.

def _event(key):
    from vent_event.models import Event
    if str(key).isdigit():
        found = Event.objects.filter(event_id=int(key)).first()
        if found:
            return found
    return Event.objects.filter(slug=str(key)).first()


def _may_manage_event(user, event):
    return user is not None and event.creator_id == user.user_id


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser])
def event_overlays(request, event_id):
    """GET/POST /event/<id>/overlays/"""
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    user = _viewer(request)
    if user is None:
        return _error('Sign in first.', 'AUTH_REQUIRED',
                      status.HTTP_401_UNAUTHORIZED)
    if not _may_manage_event(user, event):
        return _error('Only the organiser can manage overlays.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        rows = TournamentOverlay.objects.filter(event=event)
        return _ok({'overlays': [serialize(o, request) for o in rows],
                    'count': rows.count(),
                    'fields': BINDINGS_FOR_EVENT,
                    'prompt': DESIGNER_PROMPT_EVENT,
                    'templates': TEMPLATES_FOR_EVENT}, 'Overlays')

    return _create_overlay(request, event=event, user=user)


@api_view(['DELETE'])
def event_overlay_detail(request, event_id, overlay_id):
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not _may_manage_event(_viewer(request), event):
        return _error('Only the organiser can remove an overlay.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    overlay = TournamentOverlay.objects.filter(event=event, pk=overlay_id).first()
    if overlay is None:
        return _error('Overlay not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    overlay.delete()
    return _ok({'removed': overlay_id}, 'Overlay removed.')


@api_view(['POST'])
def event_overlay_rotate(request, event_id, overlay_id):
    """A new address for the same file.

    The URL is the credential: OBS opens a browser source with no session and
    no header, so if the address leaks the only remedy is a new one.
    """
    event = _event(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not _may_manage_event(_viewer(request), event):
        return _error('Only the organiser can rotate an overlay.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    overlay = TournamentOverlay.objects.filter(event=event, pk=overlay_id).first()
    if overlay is None:
        return _error('Overlay not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    overlay.token = new_overlay_token()
    overlay.save(update_fields=['token'])
    return _ok({'overlay': serialize(overlay, request)}, 'New address issued.')

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
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from django.core.files.base import ContentFile

from vent_auth.models import Users

from . import overlay_binding, overlay_templates
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
    # One answer for the studio and the overlays; see production_access.
    from .production_access import may_run_production
    return may_run_production(user, tournament)


def overlay_label(overlay):
    """The overlay's own name, as one word for a URL.

    `Score bar.html` becomes `score-bar`. The extension goes, because it is an
    artefact of how the file arrived and means nothing to the person reading
    the address.
    """
    from django.utils.text import slugify
    import os
    stem = os.path.splitext(str(overlay.name or ''))[0]
    return slugify(stem)[:60] or 'overlay'


def overlay_path(overlay):
    """The address an organiser reads, or the bare one when it has no owner.

    CEO, 3 September 2026: "can the urls for the overlays posses the names of
    the overlays, depending on the project or event or tournament the studio is
    working with, so slugs for the urls also."

    The studio's own graphics were given named addresses and the files people
    upload were not, which is the wrong half to miss: an organiser has one
    folder of HTML and eight tabs of identical-looking token URLs, and the
    whole problem is telling them apart.

    The token is still the entire credential. The two names in front of it are
    a label, so a stale one still opens the right overlay, exactly as a renamed
    tournament's old address does.
    """
    owner = overlay.owner
    owner_slug = getattr(owner, 'slug', '') or ''
    if not owner_slug:
        return '/overlay/%s/' % overlay.token
    return '/overlay/%s/%s/%s/' % (owner_slug, overlay_label(overlay), overlay.token)


def serialize(overlay, request):
    # The whole reason the feature exists: a URL somebody can paste.
    url = request.build_absolute_uri(overlay_path(overlay))
    # What it is bound to, said in words rather than left to be inferred from
    # which screen it happens to be listed on. An organiser running four
    # tournaments and two events has one folder of HTML files and no way to
    # tell from a filename which URL is pointed at which thing.
    owner = overlay.owner
    return {
        'id': overlay.id,
        'name': overlay.name,
        'url': url,
        'binding': overlay.binding,
        'bound_fields': overlay.bound_fields,
        'bound_to_kind': 'event' if overlay.event_id else 'tournament',
        'bound_to': (
            getattr(owner, 'name', None)
            or getattr(owner, 'tournament_title', '')) if owner else '',
        'bound_to_slug': getattr(owner, 'slug', '') or '',
        'created_at': overlay.created_at,
        'updated_at': overlay.updated_at,
    }


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
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
                      'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        rows = TournamentOverlay.objects.filter(tournament=tournament)
        return _ok({'overlays': [serialize(o, request) for o in rows],
                    'count': rows.count(),
                    # What the runtime can fill, the prompt a designer is
                    # given, and what they can start from instead of drawing.
                    # Sent with the list so the page never keeps its own copy
                    # of names the server is the authority on.
                    'fields': BINDINGS_FOR_TOURNAMENT,
                    'field_help': FIELD_HELP_TOURNAMENT,
                    'repeat_help': REPEAT_HELP_TOURNAMENT,
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
                      'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

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
                      'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)

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


_RUNTIME_VERSION = []


def _runtime_version():
    """A short fingerprint of the runtime, put on its own URL.

    Found on 3 September 2026, on production, by watching an overlay draw the
    right number of empty images. The page is `no-store`, so the markup was
    fresh every time, and the runtime it pulled in was a copy the browser had
    cached weeks earlier. Three fixes had shipped into a file nobody was
    loading.

    A browser source at a venue is the worst place for this: it is opened once
    and left running for a day, on a machine whose cache nobody will clear, and
    the failure is silent because a stale runtime still fills most of the page.

    Content-addressed rather than a version number, so it changes when the file
    changes and never when it does not, which is what makes the cache useful
    the rest of the time.
    """
    if not _RUNTIME_VERSION:
        import hashlib
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'static', 'overlay-runtime.js')
        try:
            with open(path, 'rb') as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()[:12]
        except OSError:
            # Never fail an on-air page over a cache hint. Falling back to a
            # constant means the overlay still loads, just without the bust.
            digest = 'x'
        _RUNTIME_VERSION.append(digest)
    return _RUNTIME_VERSION[0]


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
def serve_overlay(request, token, owner=None, label=None):
    """The URL pasted into OBS, vMix, or anything else with a browser source.

    Public by token, because a browser source cannot sign in. Deliberately not
    a DRF view: it answers HTML, and a DRF `Response` would content-negotiate
    its way into JSON.

    `owner` and `label` are the readable half of the address and are ignored on
    purpose. They exist so an organiser can tell eight tabs apart; the token is
    the credential. A renamed tournament must not break a URL already sitting
    in somebody's scene collection, which is the same rule SlugHistory keeps
    for an ordinary page.
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
    runtime = request.build_absolute_uri(
        '/static/overlay-runtime.js?v=%s' % _runtime_version())

    # The runtime is configured through a data attribute rather than a query
    # string on the OVERLAY, so an overlay's own `?t=AX` reaches it untouched.
    # The `?v=` above is on the RUNTIME's own URL and is a different thing.
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

# The vocabulary is the feed's, not a second one
# ---------------------------------------------------------------------------
#
# The first version of this listed names like `tournament_name` and
# `home_score`. Nothing sends those. The feed sends `tournament.title` and a
# `teams` array, and `static/overlay-runtime.js` resolves a dotted path against
# exactly those roots, so every name in that first list would have resolved to
# an empty string. A designer would have followed the prompt precisely and got
# an overlay that filled with nothing, on air, with no error anywhere.
#
# That is the one-model rule with the serial numbers filed off: the prompt, the
# runtime and the feed are three views of ONE vocabulary, and the moment they
# are written out separately one of them is wrong and silent. So the lists
# below are the feed's own keys, `tests_overlay_vocabulary.py` asserts they
# stay that way, and the frontend reads them from here instead of keeping a
# fourth copy.

#: Dotted names a tournament overlay can carry, and what each one is.
TOURNAMENT_NAMES = [
    ('tournament.title', 'the tournament title'),
    ('tournament.game', 'which game it is'),
    ('tournament.logo', 'the tournament logo, on an img'),
    ('tournament.starts_at', 'when it starts'),
    ('team.tag', 'the short tag of the team this overlay is pointed at'),
    ('team.name', 'that team'),
    ('team.logo', 'that team logo, on an img'),
    ('team.place', 'where they are in the table'),
    ('team.played', 'matches they have played'),
    ('team.won', 'matches won'),
    ('team.lost', 'matches lost'),
    ('team.points_for', 'points scored'),
    ('team.points_against', 'points conceded'),
    ('player.ign', 'the first player in that team'),
    ('player.id', 'their in-game id'),
    ('player.img', 'their picture, on an img'),
    ('player.represents',
     'the club that player actually plays for, when the side is a squad '
     'assembled for this tournament rather than a club'),
    ('asset.<name>',
     'a picture or clip uploaded to the studio, on an img or a video. '
     'The organiser types the name when they upload it, so write '
     'asset.hero and whatever they call hero appears there'),
]

#: What `data-vent-repeat` may be on a tournament overlay, and the bare field
#: names addressable inside one.
TOURNAMENT_REPEATS = [
    ('standings', 'the table, best record first',
     ['place', 'tag', 'name', 'logo', 'played', 'won', 'lost',
      'points_for', 'points_against']),
    ('teams', 'everybody in the tournament, same fields as standings',
     ['place', 'tag', 'name', 'logo', 'played', 'won', 'lost',
      'points_for', 'points_against']),
    ('players', 'the roster of the team this overlay is pointed at',
     ['ign', 'id', 'img', 'pictures', 'represents', 'represents_logo',
      'is_captain', 'record']),
    ('live', 'matches in progress right now',
     ['round', 'match', 'status', 'home', 'away', 'score']),
    ('sponsors', 'the people who paid for the banners',
     ['name', 'logo', 'website']),
    ('assets', 'everything uploaded to this studio, newest first',
     ['id', 'name', 'kind', 'url', 'slot', 'team_tag', 'player']),
]

#: And on an event. An event has no bracket; it has a programme, a door count,
#: ticket sales and the people who paid for the banners.
EVENT_NAMES = [
    ('event.name', 'the event title'),
    ('event.venue', 'where it is'),
    ('event.starts_at', 'when it starts'),
    ('event.now_on', 'what is happening now, read from the programme'),
    ('event.room', 'which room that is in'),
    ('event.next_on', 'what is on next'),
    ('event.next_room', 'which room that is in'),
    ('event.attending', 'how many people are through the door'),
    ('event.tickets_sold', 'how many tickets have gone'),
    ('event.capacity', 'how many the room holds'),
    ('asset.<name>',
     'a picture or clip uploaded to the studio, on an img or a video. '
     'The organiser types the name when they upload it, so write '
     'asset.hero and whatever they call hero appears there'),
]

EVENT_REPEATS = [
    ('programme', 'the running order for the day',
     ['title', 'room', 'starts_at', 'ends_at', 'speaker']),
    ('sponsors', 'the people who paid for the banners',
     ['name', 'logo']),
    ('assets', 'everything uploaded to this studio, newest first',
     ['id', 'name', 'kind', 'url', 'slot', 'team_tag', 'player']),
]


def _prompt_for(kind, names, repeats, pointing):
    """The text an organiser copies into whatever tool drew their design.

    Written out in full rather than summarised. Somebody pasting this along
    with their file should get back something that works here and looks exactly
    as it did, without coming back to read anything else. It is built from the
    lists above so it cannot describe a name the feed does not send.
    """
    first = names[0][0]
    second = names[1][0]
    an_image = next((k for k, why in names if 'img' in why or 'logo' in k), None)
    a_repeat = repeats[0]
    row_example = ''.join(
        '<td data-vent="%s">%s</td>' % (f, f) for f in a_repeat[2][:3])

    lines = ['  %s  %s' % (key.ljust(24), why) for key, why in names]
    repeat_lines = [
        '  %s  %s\n      inside it: %s'
        % (key.ljust(12), why, ', '.join(fields))
        for key, why, fields in repeats]

    return """I have an HTML file for a livestream overlay. I want to upload it to V-ENT so
it fills itself from a live %(kind)s and keeps updating while the stream runs.

Please edit my file so the parts that should follow the %(kind)s are marked,
and change NOTHING else. Keep every style, animation, keyframe, font, gradient,
image and piece of layout exactly as it is. Do not reformat, do not tidy, do not
rename a class, and do not remove anything you think is unused. I need to open
the file afterwards and recognise it.

HOW TO MARK IT

- A single value: add data-vent="..." to the element and LEAVE ITS CURRENT TEXT
  in place as the placeholder, so the file still looks right opened on its own.
    <div class="headline" data-vent="%(first)s">Whatever it says now</div>

- An image: add data-vent-src="..." and leave the existing src alone.
    <img class="crest" src="logos/ax.png" data-vent-src="%(image)s" alt="">

- A repeating list: put data-vent-repeat="..." on the container and keep
  EXACTLY ONE child inside it as the template. Delete the other repeated
  children. Inside the template, address the row's own fields with no prefix.
    <tbody data-vent-repeat="%(repeat)s">
      <tr>%(row)s</tr>
    </tbody>

- Something that should disappear when there is no value:
    <div data-vent-show="%(second)s">Only drawn when there is one</div>

THE ONLY NAMES THAT EXIST

%(names)s

  data-vent-repeat may be one of:

%(repeats)s

Use only those names. If part of my design has no matching name, LEAVE IT
EXACTLY AS IT IS and list at the end which parts you left alone and why.

WHAT NOT TO DO

- Do not add a <script>. V-ENT injects its own runtime ahead of the file.
- Do not fetch, XMLHttpRequest or WebSocket anything. The runtime does that.
- Do not add an <iframe>.
- Do not touch document.cookie or localStorage.
- Do not add a background colour to <body> unless my design already had one:
  an overlay is composited over video and its background must stay transparent.
- Do not change the pixel dimensions of the stage. It is designed for a
  1920x1080 browser source.

%(pointing)s

Give me back the complete file.""" % {
        'kind': kind,
        'first': first,
        'second': second,
        'image': an_image or first,
        'repeat': a_repeat[0],
        'row': row_example,
        'names': '\n'.join(lines),
        'repeats': '\n'.join(repeat_lines),
        'pointing': pointing,
    }


DESIGNER_PROMPT_TOURNAMENT = _prompt_for(
    'tournament', TOURNAMENT_NAMES, TOURNAMENT_REPEATS,
    """WHICH TEAM IT SHOWS

The overlay is pointed at a team with ?t=TAG on its URL. Do not add any
selection logic for that: the runtime reads it and picks the team.""")

DESIGNER_PROMPT_EVENT = _prompt_for(
    'event', EVENT_NAMES, EVENT_REPEATS,
    """WHAT IT SHOWS

An event overlay always shows the whole event: what is on now, what is next,
the door count and the sponsors. There is nothing to point it at, so do not
add any selection logic.""")


def _accepted(names, repeats):
    """Every name the runtime can resolve on this kind of overlay.

    Both the dotted paths and the bare row fields, because a file legitimately
    writes `data-vent="name"` inside a repeat, and a warning that fires on
    correct markup is a warning people learn to ignore.
    """
    out = [key for key, _why in names]
    for key, _why, fields in repeats:
        out.append(key)
        out.extend(fields)
    return sorted(set(out))


BINDINGS_FOR_TOURNAMENT = _accepted(TOURNAMENT_NAMES, TOURNAMENT_REPEATS)
BINDINGS_FOR_EVENT = _accepted(EVENT_NAMES, EVENT_REPEATS)

#: What the frontend shows as a picker. The dotted names alone, with their
#: descriptions, because a bare row field is meaningless out of its repeat.
FIELD_HELP_TOURNAMENT = [{'name': k, 'detail': w} for k, w in TOURNAMENT_NAMES]
FIELD_HELP_EVENT = [{'name': k, 'detail': w} for k, w in EVENT_NAMES]
REPEAT_HELP_TOURNAMENT = [
    {'name': k, 'detail': w, 'fields': f} for k, w, f in TOURNAMENT_REPEATS]
REPEAT_HELP_EVENT = [
    {'name': k, 'detail': w, 'fields': f} for k, w, f in EVENT_REPEATS]

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
    {'key': 'sponsors', 'name': 'Sponsor wall',
     'detail': 'The people who paid for the banners, along the bottom.'},
    {'key': 'ticker', 'name': 'Ticker',
     'detail': 'A line along the bottom for results and announcements.'},
    {'key': 'intro', 'name': 'Starting soon',
     'detail': 'What is on screen before anybody speaks.'},
    {'key': 'outro', 'name': 'Thanks for watching',
     'detail': 'The card at the end, with the sponsors.'},
]

TEMPLATES_FOR_EVENT = [
    {'key': 'programme', 'name': 'Programme',
     'detail': 'The whole running order, for the wall or a break.'},
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
    # Start from one of ours instead of uploading. The organiser owns the file
    # from that moment: it lands in exactly the same place an upload does, and
    # is editable, rotatable and removable the same way. A template that could
    # only be used as-is would be a fourth thing to maintain rather than a
    # starting point.
    template_key = str(request.data.get('template') or '').strip()
    if template_key:
        kind = 'event' if event is not None else 'tournament'
        markup = overlay_templates.render(kind, template_key)
        if markup is None:
            return _error('There is no template by that name.',
                          'UNKNOWN_TEMPLATE')
        upload = ContentFile(markup.encode('utf-8'),
                             name='%s-%s.html' % (kind, template_key))
        default_name = template_key.replace('_', ' ').capitalize()
    else:
        upload = request.FILES.get('file')
        default_name = None
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
    unknown = overlay_binding.unknown_fields(fields, known)
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
        name=str(request.data.get('name') or default_name or upload.name)[:120],
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
    from .production_access import may_run_production
    return may_run_production(user, event)


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
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
                    'field_help': FIELD_HELP_EVENT,
                    'repeat_help': REPEAT_HELP_EVENT,
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

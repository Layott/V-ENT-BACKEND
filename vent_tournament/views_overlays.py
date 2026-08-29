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
                    'count': rows.count()})

    upload = request.FILES.get('file')
    if upload is None:
        return _error('Choose an HTML file.', 'VALIDATION_ERROR')
    if not str(upload.name).lower().endswith(('.html', '.htm')):
        return _error('An overlay is an HTML file.', 'NOT_HTML')
    if upload.size > MAX_BYTES:
        return _error('That file is larger than %dMB.' % (MAX_BYTES // 1024 // 1024),
                      'TOO_LARGE')

    markup = upload.read().decode('utf-8', 'replace')
    upload.seek(0)

    binding, fields, warnings = overlay_binding.inspect(markup)
    unknown = overlay_binding.unknown_fields(fields)
    if unknown:
        warnings.append(
            'These names are not ones the overlay runtime knows how to fill, so '
            'they will stay empty: %s' % ', '.join(unknown))

    overlay = TournamentOverlay.objects.create(
        tournament=tournament,
        name=str(request.data.get('name') or upload.name)[:120],
        file=upload, binding=binding, bound_fields=fields,
        created_by=user)

    return Response({'status': 'success', 'data': {
        'overlay': serialize(overlay, request),
        # Said at upload rather than discovered on air.
        'warnings': warnings,
    }, 'message': 'Overlay uploaded.'}, status=status.HTTP_201_CREATED)


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

    tournament = overlay.tournament
    feed = request.build_absolute_uri(
        '/tournament/%s/overlay-feed/' % (tournament.slug or tournament.tournament_id))
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

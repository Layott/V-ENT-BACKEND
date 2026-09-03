"""The studio's media library: clips and pictures, uploaded once, called on later.

CEO, 3 September 2026: "i want to be able use player brolls on the site if
possible maybe the videos are uploaded to a place in the studio and then can be
called on whenever."

Uploaded to a tournament's or an event's studio by whoever may run production
there. Listed to the console with everything it needs to show a library: a
name, what it is about, how big it is, how long it runs. Played by putting the
`media` graphic on air with the asset's id, or with a word: a tag, a team tag,
or a player's username, which resolves to the newest asset that answers to it.

Deliberately not in the overlay uploader: an overlay is a page a designer
wrote, and this is footage. They are stored apart, they are listed apart, and
the only thing they share is the studio they belong to.
"""
import os

from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from vent_auth.models import Users

from .models import StudioAsset
from .production_access import find_owner, may_run_production, viewer as _viewer

# What a browser source can actually play, and what a phone can actually
# upload over a Nigerian connection. Both halves matter.
VIDEO_TYPES = {'.mp4': 'video', '.webm': 'video', '.mov': 'video', '.m4v': 'video'}
IMAGE_TYPES = {'.png': 'image', '.jpg': 'image', '.jpeg': 'image',
               '.webp': 'image', '.gif': 'image'}
ACCEPTED = dict(VIDEO_TYPES, **IMAGE_TYPES)

MAX_FILE_BYTES = 200 * 1024 * 1024      # one clip
MAX_LIBRARY_BYTES = 2 * 1024 * 1024 * 1024   # everything one studio holds


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def serialize(asset, request):
    url = asset.file.url if asset.file else None
    if url and request is not None:
        url = request.build_absolute_uri(url)
    return {
        'id': asset.id,
        'kind': asset.kind,
        'name': asset.name,
        'url': url,
        'size_bytes': asset.size_bytes,
        'duration_ms': asset.duration_ms,
        'tags': asset.tags or [],
        'team_tag': asset.team_tag or '',
        'player': asset.player.username if asset.player_id else '',
        'created_at': asset.created_at.isoformat(),
    }


def library_for(owner, kind):
    field = 'event' if kind == 'event' else 'tournament'
    return StudioAsset.objects.filter(**{field: owner}).select_related('player')


def resolve_asset(owner, kind, *, asset_id=None, word=None):
    """The asset a `media` graphic means: by id, else the newest by name."""
    rows = library_for(owner, kind)
    if asset_id:
        try:
            return rows.filter(pk=int(asset_id)).first()
        except (TypeError, ValueError):
            return None
    if word:
        for asset in rows:
            if asset.matches(word):
                return asset
    return None


def _assets(request, owner, kind):
    if owner is None:
        return _err('%s not found.' % ('Event' if kind == 'event' else 'Tournament'),
                    'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not may_run_production(user, owner):
        noun = 'event' if kind == 'event' else 'tournament'
        return _err('Only the organiser can manage the media for this %s.' % noun,
                    'NOT_ORGANIZER' if kind == 'event' else 'NOT_TOURNAMENT_ORGANIZER',
                    status.HTTP_403_FORBIDDEN)

    rows = library_for(owner, kind)

    if request.method == 'GET':
        used = sum(a.size_bytes for a in rows)
        return _ok({
            'assets': [serialize(a, request) for a in rows],
            'used_bytes': used,
            'limit_bytes': MAX_LIBRARY_BYTES,
            'max_file_bytes': MAX_FILE_BYTES,
            'accepts': sorted(ACCEPTED),
        }, 'Media')

    upload = request.FILES.get('file')
    if upload is None:
        return _err('Choose a file first.', 'FILE_REQUIRED', field='file')

    extension = os.path.splitext(upload.name or '')[1].lower()
    if extension not in ACCEPTED:
        return _err(
            'That kind of file cannot go on a stream. Videos: mp4, webm, mov. '
            'Pictures: png, jpg, webp, gif.', 'UNSUPPORTED_FILE', field='file')

    if upload.size > MAX_FILE_BYTES:
        return _err('One file can be up to %d MB.' % (MAX_FILE_BYTES // (1024 * 1024)),
                    'FILE_TOO_LARGE', field='file')

    used = sum(a.size_bytes for a in rows)
    if used + upload.size > MAX_LIBRARY_BYTES:
        return _err(
            'This studio holds up to %d GB. Delete something first.'
            % (MAX_LIBRARY_BYTES // (1024 ** 3)), 'LIBRARY_FULL', field='file')

    tags = request.data.get('tags') or ''
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace(',', ' ').split() if t.strip()]

    player = None
    username = str(request.data.get('player') or '').strip().lstrip('@')
    if username:
        player = Users.objects.filter(username__iexact=username).first()
        if player is None:
            # 404 and this code, the same as naming a scorekeeper, so one
            # translation covers "we looked and there is no such person".
            return _err('There is no account with that username.', 'USER_NOT_FOUND',
                        status.HTTP_404_NOT_FOUND, field='player')

    try:
        duration = int(request.data.get('duration_ms') or 0)
    except (TypeError, ValueError):
        duration = 0

    asset = StudioAsset.objects.create(
        tournament=owner if kind == 'tournament' else None,
        event=owner if kind == 'event' else None,
        kind=ACCEPTED[extension],
        name=str(request.data.get('name') or upload.name or 'Untitled')[:140],
        file=upload,
        size_bytes=upload.size,
        duration_ms=max(0, duration),
        tags=tags,
        team_tag=str(request.data.get('team_tag') or '')[:40],
        player=player,
        uploaded_by=user,
    )
    rows = library_for(owner, kind)
    return _ok({'assets': [serialize(a, request) for a in rows],
                'added': serialize(asset, request),
                'used_bytes': sum(a.size_bytes for a in rows),
                'limit_bytes': MAX_LIBRARY_BYTES,
                'max_file_bytes': MAX_FILE_BYTES,
                'accepts': sorted(ACCEPTED)}, 'Added.')


def _asset_delete(request, owner, kind, asset_id):
    if owner is None:
        return _err('Not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if user is None:
        return _err('Sign in first.', 'AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if not may_run_production(user, owner):
        noun = 'event' if kind == 'event' else 'tournament'
        return _err('Only the organiser can manage the media for this %s.' % noun,
                    'NOT_ORGANIZER' if kind == 'event' else 'NOT_TOURNAMENT_ORGANIZER',
                    status.HTTP_403_FORBIDDEN)
    library_for(owner, kind).filter(pk=asset_id).delete()
    rows = library_for(owner, kind)
    return _ok({'assets': [serialize(a, request) for a in rows],
                'used_bytes': sum(a.size_bytes for a in rows),
                'limit_bytes': MAX_LIBRARY_BYTES,
                'max_file_bytes': MAX_FILE_BYTES,
                'accepts': sorted(ACCEPTED)}, 'Removed.')


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def assets(request, tournament_id):
    return _assets(request, find_owner('tournament', tournament_id), 'tournament')


@api_view(['DELETE'])
def asset_detail(request, tournament_id, asset_id):
    return _asset_delete(request, find_owner('tournament', tournament_id),
                         'tournament', asset_id)


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def event_assets(request, event_id):
    return _assets(request, find_owner('event', event_id), 'event')


@api_view(['DELETE'])
def event_asset_detail(request, event_id, asset_id):
    return _asset_delete(request, find_owner('event', event_id), 'event', asset_id)

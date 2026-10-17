"""The studio half of the partner API: `/api/v1/studio/...`, by key.

Design section 14: a studio's servers talk to V-ENT with a partner API key.
The rest of `/api/v1/` reads; this part also writes, but only about the
studio's own games, players and sessions, and only within the environment the
key belongs to: a test key (`vent_pk_test_`) reaches sandbox games, a live key
reaches live games, never the other way round.

Every endpoint is rate limited per key by the same limiter as the read API
(owner rule R59) and answers the site envelope with coded refusals.
"""
import functools

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.slugs import find_by_ref

from . import studio_delivery, studio_events, studio_schema, studio_scoring, studio_signing
from .auth import _fail, _rate_limited, _touch, resolve_key
from .models import SANDBOX_SCOPES, StudioDelivery, StudioGame
from .views_studios import delivery_page


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def requires_studio(scope=None):
    """Guard a studio endpoint: a valid key holding `scope` (or any studio scope)."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            key, error = resolve_key(request)
            if error is not None:
                return error
            wanted = [scope] if scope else SANDBOX_SCOPES
            if not any(key.allows(s) for s in wanted):
                return _fail('This key cannot reach the studio API.', 'SCOPE_REQUIRED',
                             status.HTTP_403_FORBIDDEN)
            if _rate_limited(key):
                return _fail('Rate limit of %d requests a minute reached.' % key.rate_limit_per_minute,
                             'RATE_LIMITED', status.HTTP_429_TOO_MANY_REQUESTS)
            _touch(key)
            request.partner_key = key
            request.partner = key.partner
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def studio_game(request, slug):
    """(game, error): one of this key's partner's games, in the key's environment.

    Another partner's game answers exactly as a game that does not exist.
    """
    game = find_by_ref(slug, entity_type='studio_game', id_field='id', model=StudioGame,
                       queryset=StudioGame.objects.filter(partner=request.partner)
                       .select_related('partner', 'game'))
    if game is None:
        return None, _fail('No such game.', 'STUDIO_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if request.partner_key.environment != game.environment:
        return None, _fail('A test key reaches sandbox games and a live key reaches live games.',
                           'STUDIO_ENVIRONMENT_MISMATCH', status.HTTP_403_FORBIDDEN)
    return game, None


@api_view(['GET'])
@permission_classes([AllowAny])
def studio_index(request):
    """GET /api/v1/studio/ - what the studio API offers. No key needed.

    The developer pages read this, so the tables a studio reads cannot drift
    from what the server does.
    """
    return _ok({
        'events': studio_events.EVENTS,
        'subscribable': studio_events.SUBSCRIBABLE,
        'delivery_types': [kind for kind, _ in StudioDelivery.KIND_CHOICES],
        'signature': {
            'algorithm': 'HMAC-SHA256, base64',
            'string_to_sign': '{METHOD}\\n{path and query}\\n{timestamp};{host};{base64 SHA-256 of the body}',
            'headers': [studio_signing.HEADER_TIMESTAMP, studio_signing.HEADER_CONTENT,
                        studio_signing.HEADER_SIGNATURE, studio_signing.HEADER_DELIVERY],
            'window_seconds': studio_signing.WINDOW_SECONDS,
        },
        'backoff_seconds': studio_delivery.BACKOFF,
        'result': {
            'envelope': {'lineups': 'two objects, side 1 and side 2',
                         'competitors': 'optional, each with a side',
                         'general': 'optional object'},
            'max_characters': studio_schema.MAX_RESULT_CHARS,
            'schema_keywords': list(studio_schema.KEYWORDS),
        },
        'expression': {
            'names': list(studio_scoring.NAMES),
            'functions': sorted(studio_scoring.FUNCTIONS),
            'list_methods': sorted(studio_scoring.LIST_METHODS),
            'max_length': studio_scoring.MAX_LENGTH,
            'score_range': [studio_scoring.SCORE_MIN, studio_scoring.SCORE_MAX],
        },
        'test_key_prefix': 'vent_pk_test_',
    }, 'V-ENT studio API')


@api_view(['GET'])
@requires_studio()
def game_deliveries(request, slug):
    """GET /api/v1/studio/games/<slug>/deliveries/ - the game's delivery log."""
    game, err = studio_game(request, slug)
    if err:
        return err
    return _ok(delivery_page(request, game), 'Deliveries')

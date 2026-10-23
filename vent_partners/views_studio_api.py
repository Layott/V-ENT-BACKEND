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
import json

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from vent_auth import inputs
from vent_auth.slugs import find_by_ref
from vent_tournament import match_shape

from . import (studio_delivery, studio_events, studio_links, studio_schema, studio_scoring,
               studio_signing)
from .auth import _fail, _touch, rate_state, resolve_key, with_rate_headers
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
            limited, remaining, reset = rate_state(key)
            if limited:
                return with_rate_headers(
                    _fail('Rate limit of %d requests a minute reached.' % key.rate_limit_per_minute,
                          'RATE_LIMITED', status.HTTP_429_TOO_MANY_REQUESTS),
                    key, 0, reset, limited=True)
            _touch(key)
            request.partner_key = key
            request.partner = key.partner
            return with_rate_headers(view(request, *args, **kwargs), key, remaining, reset)
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


# ---------------------------------------------------------------------------
# Game sessions (P6d)
# ---------------------------------------------------------------------------

SESSION_MESSAGES = {
    'STATE_UNKNOWN': 'Send a state: creating, created, starting, started, finished or cancelled.',
    'STATE_NOT_ALLOWED': 'A session cannot move to that state from where it is.',
    'SESSION_FINISHED': 'This game already finished with another result.',
    'RESULT_REQUIRED': 'A finished game needs its result.',
    'STUDIO_RESULT_INVALID': 'The result does not fit the shape V-ENT and your schema expect.',
    'JOIN_INVALID': 'Join details are an object of at most 2000 characters.',
    'SESSION_ID_INVALID': 'A session id is text of at most 190 characters.',
}


@api_view(['GET', 'POST'])
@requires_studio('studio:sessions')
def session_view(request, session_ref):
    """GET or POST /api/v1/studio/sessions/<session_ref>/ - a game session.

    A POST is the studio telling us what happened, so on top of the key it must
    be signed over the raw body with the game's secret, inside five minutes,
    and carry a request id it has not used before (owner rule R72).
    """
    from . import studio_sessions
    from .models import StudioSession

    session = (StudioSession.objects
               .select_related('game', 'game__partner', 'link', 'match', 'match__tournament')
               .filter(ref=str(session_ref)[:24], game__partner=request.partner).first())
    if session is None:
        return _fail('No such game session.', 'SESSION_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if request.partner_key.environment != session.game.environment:
        return _fail('A test key reaches sandbox games and a live key reaches live games.',
                     'STUDIO_ENVIRONMENT_MISMATCH', status.HTTP_403_FORBIDDEN)
    if request.method == 'GET':
        return _ok({'session': studio_sessions.session_row(session)}, 'Game session')

    raw = request.body
    try:
        studio_signing.verify(session.game.signing_secrets(), 'POST', request.get_full_path(),
                              request.get_host(), request.headers, raw)
    except studio_signing.SignatureError as exc:
        return _fail('The request is not signed the way V-ENT expects.', exc.code,
                     status.HTTP_401_UNAUTHORIZED)
    nonce = request.headers.get(studio_signing.HEADER_DELIVERY, '').strip()
    if not nonce:
        return _fail('Send a request id in %s.' % studio_signing.HEADER_DELIVERY,
                     'SIGNATURE_MISSING', status.HTTP_401_UNAUTHORIZED)
    if not studio_sessions.claim_nonce(session.game, nonce):
        return _fail('That request id was already used.', 'REPLAYED', status.HTTP_401_UNAUTHORIZED)
    try:
        body = json.loads(raw or b'{}')
    except ValueError:
        raise inputs.BadInput('body', 'not JSON') from None
    try:
        session = studio_sessions.update(session, body)
    except studio_sessions.SessionRefused as exc:
        return Response({'status': 'error', 'code': exc.code,
                         'message': SESSION_MESSAGES.get(exc.code, 'That update was refused.'),
                         'data': exc.data}, status=exc.http)
    session.refresh_from_db()
    return _ok({'session': studio_sessions.session_row(session)}, 'Updated.')


# ---------------------------------------------------------------------------
# A linked player's next match (P6e)
# ---------------------------------------------------------------------------

@api_view(['GET'])
@requires_studio('studio:accounts')
def next_match(request, slug, account_id):
    """GET /api/v1/studio/games/<slug>/accounts/<account_id>/next-match/

    For a player who linked their account to this game: the earliest match
    they still have to play, so the game can say "your match is at 18:00".
    Their tournaments that are public, plus any private one this game runs.
    """
    from django.db.models import F, Q

    from vent_tournament.models import BracketMatch, TournamentRegistration
    from . import studio_sessions

    game, err = studio_game(request, slug)
    if err:
        return err
    link = game.account_links.select_related('user').filter(external_id=str(account_id)[:190]).first()
    if link is None:
        return _fail('No V-ENT account is linked with that game account.', 'ACCOUNT_NOT_LINKED',
                     status.HTTP_404_NOT_FOUND)
    user = link.user
    places = (TournamentRegistration.objects
              .filter(Q(user=user) | Q(team__teammembers__user=user) | Q(squad__members__user=user))
              .exclude(status__in=('withdrawn', 'disqualified')).values('id'))
    match = (BracketMatch.objects
             .filter(Q(participant_1__in=places) | Q(participant_2__in=places),
                     status__in=studio_sessions.OPEN_MATCH, tournament__is_draft=False)
             .filter(Q(tournament__studio_link__game=game) | ~Q(tournament__tournament_visibility='private'))
             .select_related('tournament', 'participant_1', 'participant_2')
             .order_by(F('scheduled_at').asc(nulls_last=True), 'round_number', 'id')
             .distinct().first())
    if match is None:
        return _ok({'next_match': None}, 'Nothing to play')
    side = 1 if match.participant_1 is not None and match.participant_1.plays_for(user) else 2
    other = match.participant_2 if side == 1 else match.participant_1
    session = (match.studio_sessions.filter(game=game)
               .order_by('-game_number', '-attempt').first())
    return _ok({'next_match': {
        'tournament': {'slug': match.tournament.slug, 'title': match.tournament.tournament_title},
        'match': {'round': match.round_number, 'side': match.bracket_side,
                  'number': match.match_number, 'group': match.group_number,
                  'best_of': match.best_of or 1, 'scheduled_at': match.scheduled_at,
                  'check_in_deadline': match.check_in_deadline},
        'side': side,
        'opponent': {'name': match_shape.names(other)[0]},
        'session': {'ref': session.ref, 'state': session.state, 'join': session.join or {}}
        if session is not None else None,
    }}, 'Next match')


# ---------------------------------------------------------------------------
# Account linking (P6c)
# ---------------------------------------------------------------------------

def _refusal(exc):
    status_by_code = {'ACCOUNT_TAKEN': status.HTTP_409_CONFLICT,
                      'STUDIO_ALREADY_LINKED': status.HTTP_409_CONFLICT,
                      'SANDBOX_TESTERS_ONLY': status.HTTP_403_FORBIDDEN,
                      'LINK_CODE_NOT_FOUND': status.HTTP_404_NOT_FOUND}
    messages = {
        'ACCOUNT_TAKEN': 'That game account is linked to another V-ENT account.',
        'STUDIO_ALREADY_LINKED': 'This V-ENT account already has a game account linked in this game.',
        'SANDBOX_TESTERS_ONLY': 'A sandbox game links only its studio and the testers it named.',
        'LINK_CODE_NOT_FOUND': 'That code is not valid, or it has run out.',
        'OAUTH_TOKEN_INVALID': 'That Sign in with V-ENT token is not valid for this studio.',
    }
    return _fail(messages.get(exc.code, 'That link was refused.'), exc.code,
                 status_by_code.get(exc.code, status.HTTP_400_BAD_REQUEST))


def _account(request):
    account_id = inputs.read_text(request.data, 'account_id', max_length=190, required=True)
    name = inputs.read_text(request.data, 'display_name', max_length=120)
    return account_id, name


@api_view(['POST'])
@requires_studio('studio:accounts')
def create_link_code(request, slug):
    """POST /api/v1/studio/games/<slug>/link-codes/ - a code to show in the game.

    The code is shown to the player in the game, never put in an address; the
    page they type it on is `link_page`.
    """
    game, err = studio_game(request, slug)
    if err:
        return err
    account_id, name = _account(request)
    row, code = studio_links.issue(game, account_id, name)
    return _ok({'code': code, 'code_ref': row.ref, 'expires_at': row.expires_at,
                'link_page': '%s/link-game' % FRONTEND_URL.rstrip('/')},
               'Show this code in the game.', status.HTTP_201_CREATED)


@api_view(['GET'])
@requires_studio('studio:accounts')
def link_code_status(request, slug, code_ref):
    """GET /api/v1/studio/games/<slug>/link-codes/<code_ref>/ - used yet?"""
    game, err = studio_game(request, slug)
    if err:
        return err
    row = game.link_codes.select_related('link', 'link__user').filter(ref=str(code_ref)[:24]).first()
    if row is None:
        return _fail('No such code for this game.', 'LINK_CODE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if row.claimed_at and row.link_id:
        state = 'linked'
    elif row.expires_at <= timezone.now():
        state = 'expired'
    else:
        state = 'pending'
    return _ok({'status': state, 'expires_at': row.expires_at,
                'link': studio_links.link_row(row.link) if state == 'linked' else None}, 'Code')


@api_view(['GET', 'POST'])
@requires_studio('studio:accounts')
def links(request, slug):
    """GET finds a link by account_id or username; POST links by a Sign in
    with V-ENT access token. /api/v1/studio/games/<slug>/links/"""
    game, err = studio_game(request, slug)
    if err:
        return err
    if request.method == 'POST':
        token = inputs.read_text(request.data, 'access_token', max_length=200, required=True)
        account_id, name = _account(request)
        try:
            link = studio_links.link_by_token(game, token, account_id, name)
        except studio_links.LinkRefused as exc:
            return _refusal(exc)
        return _ok({'link': studio_links.link_row(link)}, 'Linked.', status.HTTP_201_CREATED)

    rows = game.account_links.select_related('user')
    account_id = inputs.read_text(request.GET, 'account_id', max_length=190)
    username = inputs.read_text(request.GET, 'username', max_length=128)
    if account_id:
        link = rows.filter(external_id=account_id).first()
    elif username:
        link = rows.filter(user__username__iexact=username).first()
    else:
        raise inputs.BadInput('account_id', 'required')
    if link is None:
        return _fail('Nobody is linked with that account.', 'STUDIO_LINK_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({'link': studio_links.link_row(link)}, 'Link')


@api_view(['POST'])
@requires_studio('studio:accounts')
def unlink(request, slug, link_ref):
    """POST /api/v1/studio/games/<slug>/links/<link_ref>/unlink/"""
    game, err = studio_game(request, slug)
    if err:
        return err
    link = game.account_links.select_related('user', 'game').filter(ref=str(link_ref)[:24]).first()
    if link is None:
        return _fail('No such link for this game.', 'STUDIO_LINK_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    ref = link.ref
    studio_links.unlink(link, by='studio')
    return _ok({'unlinked': ref}, 'Unlinked.')

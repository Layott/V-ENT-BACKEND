"""The Studios section of the partner portal, and its review.

Design section 14: "A new Studios section in the Partners portal, on top of the
existing partner API keys (9 scopes) and Sign in with V-ENT."

Two audiences, the same split as `views_manage`:

- the studio, signed in with its ordinary V-ENT session, registers and runs its
  own games: settings, the signing secret, a test key, asking to go live;
- a super admin decides whether a game goes live, because a live game puts
  results into real matches and ties game accounts to real people.

Every answer is the site envelope, every refusal carries a code the portal
translates, and a game somebody else owns answers exactly as a game that does
not exist (owner rule R88).
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import inputs
from vent_auth.models import AdminAction, Games, Users
from vent_auth.slugs import find_by_ref, resolve_or_redirect
from vent_auth.throttle import limited
from vent_auth.views_profile import _user_from_bearer

from . import studio_events, studio_schema, studio_scoring, studio_urls
from .models import (
    SANDBOX_SCOPES, SCOPES, STUDIO_GAME_LIMIT, STUDIO_TESTER_LIMIT,
    Partner, PartnerApiKey, StudioGame,
)
from .studio_scoring import ExpressionError

logger = logging.getLogger(__name__)

#: Live keys a partner may hold at once, the same ceiling as `create_key`.
KEY_LIMIT = 5

#: Plain sentences for the codes a studio's settings can be refused with. The
#: portal translates by code; these are for anybody reading the API directly.
MESSAGES = {
    'URL_NOT_HTTPS': 'A webhook address must be https.',
    'URL_HAS_CREDENTIALS': 'Put no username or password in a webhook address.',
    'URL_PRIVATE_HOST': 'That address points inside a private network, so we will not call it.',
    'URL_TOO_LONG': 'That address is longer than 300 characters.',
    'SCHEMA_NOT_OBJECT': 'A schema is a JSON object.',
    'SCHEMA_UNKNOWN_KEYWORD': 'The schema uses a keyword V-ENT does not check. Use only the listed keywords.',
    'SCHEMA_TOO_DEEP': 'The schema nests more than 12 levels deep.',
    'SCHEMA_TOO_LARGE': 'The schema is longer than 8000 characters.',
    'SCHEMA_BAD_TYPE': 'A keyword in the schema has a value of the wrong kind.',
}


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, *, field=None, data=None,
         publish=None):
    """A refusal. `publish` puts values at the top of the body, where the
    portal's translation reads its placeholders (`apiMessage` fills from there)."""
    body = {'status': 'error', 'code': code, 'message': message, 'data': data}
    if field:
        body['field'] = field
    body.update(publish or {})
    return Response(body, status=http_status)


def _not_found():
    return _err('No such game.', 'STUDIO_NOT_FOUND', status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

def game_row(game, now=None):
    """One game, as every studio screen reads it. Never a secret."""
    now = now or timezone.now()
    overlap = (game.previous_secret_expires_at
               if game.previous_secret_encrypted and game.previous_secret_expires_at
               and game.previous_secret_expires_at > now else None)
    return {
        'slug': game.slug,
        'name': game.name,
        'game': {'id': game.game_id, 'title': game.game.game_title} if game.game_id else None,
        'mode': game.mode,
        'live_status': game.live_status,
        'environment': game.environment,
        'session_webhook_url': game.session_webhook_url,
        'events_webhook_url': game.events_webhook_url,
        'secret_hint': game.secret_hint,
        'previous_valid_until': overlap,
        'result_schema': game.result_schema or {},
        'scoring_expression': game.scoring_expression,
        'settings_schema': game.settings_schema or {},
        'subscribed_events': game.subscribed_events or [],
        'testers': game.testers or [],
        'review_note': game.review_note,
        'created_at': game.created_at,
        'updated_at': game.updated_at,
    }


def _key_row(key):
    return {'id': key.id, 'name': key.name, 'key_id': key.key_id, 'scopes': key.scopes,
            'environment': key.environment}


# ---------------------------------------------------------------------------
# Who is asking
# ---------------------------------------------------------------------------

def _studio_partner(user):
    """The partner record this account runs studios under: the approved one
    if there is one, otherwise the latest application."""
    rows = list(Partner.objects.filter(owner=user).order_by('-created_at'))
    return next((p for p in rows if p.status == 'approved'), None) or (rows[0] if rows else None)


def _partner_refusal(partner):
    if partner is None:
        return 'NOT_A_PARTNER'
    if not partner.is_active:
        return 'STUDIO_PARTNER_NOT_APPROVED'
    return None


def _refuse_partner(code):
    if code == 'NOT_A_PARTNER':
        return _err('Apply to the partner programme first; a game belongs to a partner.',
                    code, status.HTTP_403_FORBIDDEN)
    return _err('Your partner application has not been approved yet.',
                code, status.HTTP_403_FORBIDDEN)


def _games_of(user):
    return (StudioGame.objects.select_related('partner', 'game')
            .filter(partner__owner=user))


def _own_game(request, slug):
    """(user, game, error) for a sub-resource: a renamed game is followed."""
    user, err = _user_from_bearer(request)
    if err:
        return None, None, err
    game = find_by_ref(slug, entity_type='studio_game', id_field='id', model=StudioGame,
                       queryset=_games_of(user))
    if game is None:
        return user, None, _not_found()
    return user, game, None


# ---------------------------------------------------------------------------
# Reading the settings a studio sends
# ---------------------------------------------------------------------------

def _read_settings(data, *, creating):
    """(values, error) from the named fields a studio may set, and no others."""
    out = {}

    if creating or 'name' in data:
        name = inputs.read_text(data, 'name', max_length=120)
        if not name:
            return None, _err('Give the game a name.', 'NAME_REQUIRED', field='name')
        out['name'] = name

    if 'game_id' in data:
        game_id = inputs.read_int(data, 'game_id', minimum=1)
        if game_id is None:
            out['game'] = None
        else:
            title = Games.objects.filter(pk=game_id).first()
            if title is None:
                return None, _err('There is no such game on V-ENT.', 'GAME_NOT_FOUND',
                                  field='game_id')
            out['game'] = title

    for field in ('session_webhook_url', 'events_webhook_url'):
        if field in data:
            url = inputs.read_text(data, field, max_length=400)
            if url:
                problem = studio_urls.webhook_url_problem(url)
                if problem:
                    return None, _err(MESSAGES[problem], problem, field=field)
            out[field] = url

    for field in ('result_schema', 'settings_schema'):
        if field in data:
            raw = data.get(field)
            if raw in (None, '', {}):
                out[field] = {}
                continue
            problems = studio_schema.check_schema(raw)
            if problems:
                code = problems[0].split(':')[0]
                return None, _err(MESSAGES.get(code, 'That schema cannot be used.'), code,
                                  field=field, data={'problems': problems})
            out[field] = raw

    if 'scoring_expression' in data:
        text = inputs.read_text(data, 'scoring_expression',
                                max_length=studio_scoring.MAX_LENGTH + 100) or 'team.score'
        try:
            studio_scoring.compile_expression(text)
        except ExpressionError as exc:
            return None, _err('That scoring expression cannot be used.', exc.code,
                              field='scoring_expression', data={'position': exc.position},
                              publish={'position': exc.position})
        out['scoring_expression'] = text

    if 'subscribed_events' in data:
        raw = data.get('subscribed_events') or []
        if not isinstance(raw, list) or len(raw) > 50:
            raise inputs.BadInput('subscribed_events', 'not a short list')
        unknown = studio_events.unknown_subscriptions(raw)
        if unknown:
            return None, _err('Some of those events do not exist.', 'UNKNOWN_EVENT',
                              field='subscribed_events', data={'unknown': unknown})
        out['subscribed_events'] = studio_events.clean_subscriptions(raw)

    if 'testers' in data:
        raw = data.get('testers') or []
        if not isinstance(raw, list):
            raise inputs.BadInput('testers', 'not a list')
        if len(raw) > STUDIO_TESTER_LIMIT:
            return None, _err('A game may name at most %d testers.' % STUDIO_TESTER_LIMIT,
                              'TOO_MANY_TESTERS', field='testers',
                              publish={'limit': STUDIO_TESTER_LIMIT})
        names = list(dict.fromkeys(
            inputs.read_text({'tester': n}, 'tester', max_length=128) for n in raw))
        names = [n for n in names if n]
        query = Q()
        for name in names:
            query |= Q(username__iexact=name)
        found = {u.username.lower(): u.username
                 for u in (Users.objects.filter(query) if names else [])}
        missing = [n for n in names if n.lower() not in found]
        if missing:
            return None, _err('Some testers have no V-ENT account.', 'TESTER_NOT_FOUND',
                              field='testers', data={'missing': missing})
        out['testers'] = [found[n.lower()] for n in names]

    return out, None


# ---------------------------------------------------------------------------
# The studio's own pages
# ---------------------------------------------------------------------------

@api_view(['GET'])
@limited('studio-portal-read', 120)
def studios_home(request):
    """GET /partners/studios/ - my games, and whether I may register one."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    partner = _studio_partner(user)
    refusal = _partner_refusal(partner)
    return _ok({
        'games': [game_row(g) for g in _games_of(user)],
        'can_register': refusal is None,
        'refusal_code': refusal,
        'partner': {'name': partner.name, 'slug': partner.slug} if partner else None,
        'scopes': {s: SCOPES[s] for s in SANDBOX_SCOPES},
        'events': studio_events.SUBSCRIBABLE,
        'limits': {'games': STUDIO_GAME_LIMIT, 'testers': STUDIO_TESTER_LIMIT},
        'schema_keywords': list(studio_schema.KEYWORDS),
    }, 'Studios')


@api_view(['POST'])
@limited('studio-portal', 30)
def register_game(request):
    """POST /partners/studios/register/ - connect a game. It starts in the sandbox."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    partner = _studio_partner(user)
    refusal = _partner_refusal(partner)
    if refusal:
        return _refuse_partner(refusal)
    if partner.studio_games.count() >= STUDIO_GAME_LIMIT:
        return _err('A partner may connect at most %d games.' % STUDIO_GAME_LIMIT, 'STUDIO_LIMIT',
                    publish={'limit': STUDIO_GAME_LIMIT})

    values, err = _read_settings(request.data, creating=True)
    if err:
        return err

    game = StudioGame(partner=partner, created_by=user, **values)
    secret = game.set_secret()
    game.save()
    return _ok({'game': game_row(game), 'signing_secret': secret},
               'Copy the signing secret now. It is not shown again.',
               status.HTTP_201_CREATED)


@api_view(['GET'])
@limited('studio-portal-read', 120)
def game_detail(request, slug):
    """GET /partners/studios/<slug>/ - one of my games."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    game, moved = resolve_or_redirect(slug, entity_type='studio_game', id_field='id',
                                      model=StudioGame, queryset=_games_of(user))
    if moved:
        return Response({
            'status': 'moved', 'code': 'SLUG_CHANGED',
            'message': 'This game has been renamed.',
            'data': {'slug': moved, 'url': '/partners/studios/%s' % moved},
        }, status=status.HTTP_200_OK)
    if game is None:
        return _not_found()
    return _ok({'game': game_row(game), 'events': studio_events.SUBSCRIBABLE,
                'schema_keywords': list(studio_schema.KEYWORDS),
                'limits': {'testers': STUDIO_TESTER_LIMIT,
                           'expression': studio_scoring.MAX_LENGTH}}, 'Game')


@api_view(['POST'])
@limited('studio-portal', 30)
def update_game(request, slug):
    """POST /partners/studios/<slug>/update/ - change the named settings."""
    user, game, err = _own_game(request, slug)
    if err:
        return err
    values, err = _read_settings(request.data, creating=False)
    if err:
        return err
    for field, value in values.items():
        setattr(game, field, value)
    if values:
        game.save()
    return _ok({'game': game_row(game)}, 'Saved.')


@api_view(['POST'])
@limited('studio-portal', 30)
def rotate_secret(request, slug):
    """POST /partners/studios/<slug>/rotate-secret/ - a new secret, the old one for a day."""
    user, game, err = _own_game(request, slug)
    if err:
        return err
    secret = game.rotate_secret()
    return _ok({'game': game_row(game), 'signing_secret': secret,
                'previous_valid_until': game.previous_secret_expires_at},
               'Copy the new secret now. The old one keeps working for 24 hours.')


#: What a game needs before a person can usefully review it for live.
LIVE_NEEDS = ('game', 'session_webhook_url', 'scoring_expression')


@api_view(['POST'])
@limited('studio-portal', 30)
def request_live(request, slug):
    """POST /partners/studios/<slug>/request-live/ - ask a super admin to make it live."""
    user, game, err = _own_game(request, slug)
    if err:
        return err
    if game.is_live:
        return _err('This game is already live.', 'ALREADY_LIVE', status.HTTP_409_CONFLICT)
    missing = [f for f in LIVE_NEEDS
               if not (game.game_id if f == 'game' else getattr(game, f))]
    if missing:
        return _err('Fill these in before asking to go live.', 'STUDIO_NOT_READY',
                    data={'missing': missing})
    game.live_status = 'requested'
    game.save(update_fields=['live_status', 'updated_at'])
    return _ok({'game': game_row(game)}, 'Sent for review.')


@api_view(['POST'])
@limited('studio-portal', 60)
def try_scoring(request, slug):
    """POST /partners/studios/<slug>/try-scoring/ - score a sample without saving.

    The expression and schema may be sent with the sample, so a studio can try
    one before saving it; otherwise the game's own are used.
    """
    user, game, err = _own_game(request, slug)
    if err:
        return err
    result = request.data.get('result')
    inputs.bounded_json(result, 'result', max_chars=studio_schema.MAX_RESULT_CHARS + 100)
    settings_value = request.data.get('settings') or {}
    if not isinstance(settings_value, dict):
        raise inputs.BadInput('settings', 'not an object')

    expression = game.scoring_expression
    if 'scoring_expression' in request.data:
        expression = inputs.read_text(request.data, 'scoring_expression',
                                      max_length=studio_scoring.MAX_LENGTH + 100)
    schema = game.result_schema or {}
    if 'result_schema' in request.data:
        schema = request.data.get('result_schema') or {}
        problems = studio_schema.check_schema(schema)
        if problems:
            code = problems[0].split(':')[0]
            return _err(MESSAGES.get(code, 'That schema cannot be used.'), code,
                        field='result_schema', data={'problems': problems})

    envelope = studio_schema.envelope_problems(result)
    schema_errors = studio_schema.validate(result, schema) if not envelope else []
    scores = None
    if not envelope:
        try:
            scores = list(studio_scoring.score_sides(expression, result, settings_value))
        except ExpressionError as exc:
            return _err('The expression could not score that result.', exc.code,
                        field='scoring_expression', data={'position': exc.position},
                        publish={'position': exc.position})
    return _ok({'scores': scores, 'envelope_errors': envelope, 'schema_errors': schema_errors},
               'Scored.')


@api_view(['POST'])
@limited('studio-portal', 30)
def sandbox_key(request):
    """POST /partners/studios/sandbox-key/ - a test key, shown once."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    partner = _studio_partner(user)
    refusal = _partner_refusal(partner)
    if refusal:
        return _refuse_partner(refusal)
    if partner.api_keys.filter(revoked_at__isnull=True).count() >= KEY_LIMIT:
        return _err('Five live keys is the limit. Revoke one first.', 'TOO_MANY_KEYS')
    key, plaintext = PartnerApiKey.issue(
        partner, name='Sandbox key', scopes=SANDBOX_SCOPES, created_by=user,
        environment='sandbox')
    return _ok({'key': _key_row(key), 'secret': plaintext},
               'Copy this test key now. It is not shown again.', status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# The delivery log (P6b)
# ---------------------------------------------------------------------------

DELIVERY_PAGE = 50
DELIVERY_STATES = ('pending', 'delivered', 'failed', 'cancelled')


def delivery_row(delivery):
    """One delivery as the studio reads it. The body is not repeated here: the
    studio received it, and the log is for what happened to it."""
    return {
        'ref': delivery.ref,
        'kind': delivery.kind,
        'event': delivery.event or None,
        'status': delivery.status,
        'attempts': delivery.attempts,
        'last_status_code': delivery.last_status_code,
        'last_error': delivery.last_error or None,
        'response_excerpt': delivery.response_excerpt,
        'created_at': delivery.created_at,
        'delivered_at': delivery.delivered_at,
        'next_attempt_at': delivery.next_attempt_at if delivery.status == 'pending' else None,
    }


def delivery_page(request, game):
    """A page of a game's log, newest first, shared by the portal and the API."""
    state = inputs.read_choice(request.GET, 'status', DELIVERY_STATES)
    page = inputs.read_int(request.GET, 'page', minimum=1, maximum=10000, default=1)
    rows = game.deliveries.all()
    if state:
        rows = rows.filter(status=state)
    total = rows.count()
    start = (page - 1) * DELIVERY_PAGE
    return {
        'deliveries': [delivery_row(d) for d in rows[start:start + DELIVERY_PAGE]],
        'page': page,
        'page_size': DELIVERY_PAGE,
        'total': total,
        'has_more': start + DELIVERY_PAGE < total,
    }


@api_view(['GET'])
@limited('studio-portal-read', 120)
def game_deliveries(request, slug):
    """GET /partners/studios/<slug>/deliveries/ - what we sent, and what came back."""
    user, game, err = _own_game(request, slug)
    if err:
        return err
    return _ok(delivery_page(request, game), 'Deliveries')


@api_view(['POST'])
@limited('studio-portal', 20)
def resend_delivery(request, slug, delivery_ref):
    """POST /partners/studios/<slug>/deliveries/<ref>/resend/ - send it again now.

    A new delivery with the same body, sent while the studio waits, so the
    portal can show what their server answered this time.
    """
    from . import studio_delivery

    user, game, err = _own_game(request, slug)
    if err:
        return err
    original = game.deliveries.filter(ref=str(delivery_ref)[:24]).first()
    if original is None:
        return _err('No such delivery for this game.', 'DELIVERY_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    again = studio_delivery.resend(original)
    studio_delivery.attempt_ref(again.ref)
    again.refresh_from_db()
    return _ok({'delivery': delivery_row(again)}, 'Sent again.', status.HTTP_201_CREATED)


@api_view(['POST'])
@limited('studio-portal', 10)
def ping_game(request, slug):
    """POST /partners/studios/<slug>/ping/ - a signed test event, sent now.

    How a studio proves its signature check before any real event arrives.
    """
    from . import studio_delivery

    user, game, err = _own_game(request, slug)
    if err:
        return err
    delivery = studio_delivery.queue(game, 'event', {'test': True}, event='ping')
    if delivery is None:
        return _err('Give the game an events or game session address first.',
                    'STUDIO_NO_ADDRESS', field='events_webhook_url')
    studio_delivery.attempt_ref(delivery.ref)
    delivery.refresh_from_db()
    return _ok({'delivery': delivery_row(delivery)}, 'Test event sent.')


@api_view(['GET'])
@limited('studio-portal-read', 120)
def game_usage(request, slug):
    """GET /partners/studios/<slug>/usage/ - linked players and sessions by
    month, counted from the first session (design section 14, item 8)."""
    from . import studio_usage

    user, game, err = _own_game(request, slug)
    if err:
        return err
    return _ok({'months': studio_usage.monthly(game, months=12)}, 'Usage')


# ---------------------------------------------------------------------------
# The player's side of linking (P6c)
# ---------------------------------------------------------------------------

LINK_REFUSALS = {
    'LINK_CODE_NOT_FOUND': (status.HTTP_404_NOT_FOUND, 'That code is not valid, or it has run out.'),
    'ACCOUNT_TAKEN': (status.HTTP_409_CONFLICT, 'That game account is linked to another V-ENT account.'),
    'STUDIO_ALREADY_LINKED': (status.HTTP_409_CONFLICT,
                              'You already have a game account linked in this game. Unlink it first.'),
    'SANDBOX_TESTERS_ONLY': (status.HTTP_403_FORBIDDEN,
                             'This game is still being tested, and only its testers can link.'),
}


def _link_refusal(code):
    http, message = LINK_REFUSALS.get(code, (status.HTTP_400_BAD_REQUEST, 'That link was refused.'))
    return _err(message, code, http)


def _guessing(request, user):
    """Ten tries a minute for one account, beside the per-address limiter, so a
    code cannot be walked from many addresses by one person either."""
    from django.conf import settings
    from vent_auth.throttle import too_many

    return (getattr(settings, 'AUTH_THROTTLE_ENABLED', True)
            and too_many(request, 'studio-link-user', 10, extra=str(user.pk)))


def _typed_code(request, user):
    """(code row, error) for the code in the body."""
    from . import studio_links

    if _guessing(request, user):
        return None, _err('Too many attempts. Wait a minute and try again.',
                          'TOO_MANY_ATTEMPTS', status.HTTP_429_TOO_MANY_REQUESTS)
    code = inputs.read_text(request.data, 'code', max_length=20, required=True)
    row = studio_links.find(code)
    if row is None:
        return None, _link_refusal('LINK_CODE_NOT_FOUND')
    return row, None


@api_view(['POST'])
@limited('studio-link', 10)
def link_preview(request):
    """POST /partners/studios/link/preview/ - whose game account is this code?

    Nothing is linked here. The person sees the game and the account name and
    decides whether it is theirs.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err
    row, err = _typed_code(request, user)
    if err:
        return err
    game = row.game
    return _ok({
        'game': {'name': game.name, 'studio': game.partner.name,
                 'title': game.game.game_title if game.game_id else None},
        'account': {'display_name': row.external_name},
        'expires_at': row.expires_at,
    }, 'Is this your account?')


@api_view(['POST'])
@limited('studio-link', 10)
def link_confirm(request):
    """POST /partners/studios/link/confirm/ - link it."""
    from . import studio_links

    user, err = _user_from_bearer(request)
    if err:
        return err
    row, err = _typed_code(request, user)
    if err:
        return err
    try:
        link = studio_links.claim(row, user)
    except studio_links.LinkRefused as exc:
        return _link_refusal(exc.code)
    return _ok({'link': studio_links.my_link_row(link)}, 'Linked.', status.HTTP_201_CREATED)


@api_view(['GET'])
@limited('studio-portal-read', 120)
def my_links(request):
    """GET /partners/studios/my-links/ - the game accounts linked to me."""
    from . import studio_links
    from .models import StudioAccountLink

    user, err = _user_from_bearer(request)
    if err:
        return err
    rows = (StudioAccountLink.objects.filter(user=user)
            .select_related('game', 'game__partner', 'game__game').order_by('-linked_at'))
    return _ok({'links': [studio_links.my_link_row(l) for l in rows]}, 'Your game accounts')


@api_view(['POST'])
@limited('studio-portal', 30)
def my_link_unlink(request, link_ref):
    """POST /partners/studios/my-links/<link_ref>/unlink/ - remove one of mine."""
    from . import studio_links
    from .models import StudioAccountLink

    user, err = _user_from_bearer(request)
    if err:
        return err
    link = (StudioAccountLink.objects.select_related('game', 'user')
            .filter(user=user, ref=str(link_ref)[:24]).first())
    if link is None:
        return _err('No such link.', 'STUDIO_LINK_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    ref = link.ref
    studio_links.unlink(link, by='player')
    return _ok({'unlinked': ref}, 'Unlinked.')


# ---------------------------------------------------------------------------
# An organiser connecting a tournament to a game (P6d)
# ---------------------------------------------------------------------------

SESSION_LIST = 50


def _organiser(request, tournament_ref):
    """(user, tournament, error): only somebody who may run the tournament.
    Anybody else gets the answer a tournament that does not exist gets."""
    from vent_tournament import lookup
    from vent_tournament.access import may_manage

    user, err = _user_from_bearer(request)
    if err:
        return None, None, err
    tournament = lookup.find(tournament_ref)
    if tournament is None or not may_manage(user, tournament):
        return user, None, _err('Tournament not found.', 'TOURNAMENT_NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)
    return user, tournament, None


def _offer_row(game):
    return {'slug': game.slug, 'name': game.name, 'studio': game.partner.name,
            'environment': game.environment, 'settings_schema': game.settings_schema or {}}


def _tournament_studio(user, tournament):
    from . import studio_sessions
    from .models import StudioSession, StudioTournamentLink

    link = (StudioTournamentLink.objects.select_related('game', 'game__partner')
            .filter(tournament=tournament).first())
    sessions = (StudioSession.objects.filter(match__tournament=tournament)
                .select_related('match', 'match__tournament').order_by('-updated_at')[:SESSION_LIST])
    return {
        'link': {'game': _offer_row(link.game), 'settings': link.settings or {},
                 'active': link.active, 'created_at': link.created_at} if link else None,
        'available': [_offer_row(g) for g in studio_sessions.available_games(user, tournament)],
        'sessions': [studio_sessions.session_row(s) for s in sessions],
    }


@api_view(['GET'])
@limited('studio-portal-read', 120)
def tournament_studio(request, tournament_ref):
    """GET /partners/studios/tournament/<ref>/ - which game runs this
    tournament's matches, which may, and the sessions so far."""
    user, tournament, err = _organiser(request, tournament_ref)
    if err:
        return err
    return _ok(_tournament_studio(user, tournament), 'Game integration')


@api_view(['POST'])
@limited('studio-portal', 30)
def tournament_connect(request, tournament_ref):
    """POST /partners/studios/tournament/<ref>/connect/ - run the matches through a game."""
    from . import studio_sessions
    from .models import StudioTournamentLink

    user, tournament, err = _organiser(request, tournament_ref)
    if err:
        return err
    slug = inputs.read_text(request.data, 'game', max_length=160, required=True)
    game = next((g for g in studio_sessions.available_games(user, tournament)
                 if g.slug == slug), None)
    if game is None:
        return _err('That game cannot run this tournament.', 'STUDIO_NOT_AVAILABLE', field='game')
    chosen = request.data.get('settings') or {}
    if not isinstance(chosen, dict):
        raise inputs.BadInput('settings', 'not an object')
    inputs.bounded_json(chosen, 'settings', max_chars=4000)
    problems = studio_schema.validate(chosen, game.settings_schema or {})
    if problems:
        return _err('Those settings do not fit the game.', 'STUDIO_SETTINGS_INVALID',
                    field='settings', data={'errors': problems})

    with transaction.atomic():
        link = StudioTournamentLink.objects.select_for_update().filter(tournament=tournament).first()
        if link is not None and link.game_id != game.pk:
            for session in link.sessions.filter(state__in=studio_sessions.OPEN_STATES):
                studio_sessions.cancel(session, 'disconnected')
        if link is None:
            link = StudioTournamentLink(tournament=tournament)
        link.game, link.settings, link.active, link.enabled_by = game, chosen, True, user
        link.save()
    return _ok(_tournament_studio(user, tournament), 'Connected.')


@api_view(['POST'])
@limited('studio-portal', 30)
def tournament_disconnect(request, tournament_ref):
    """POST /partners/studios/tournament/<ref>/disconnect/ - stop; open lobbies are cancelled."""
    from . import studio_sessions
    from .models import StudioTournamentLink

    user, tournament, err = _organiser(request, tournament_ref)
    if err:
        return err
    link = StudioTournamentLink.objects.filter(tournament=tournament).first()
    if link is None or not link.active:
        return _err('This tournament is not connected to a game.', 'STUDIO_NOT_CONNECTED')
    link.active = False
    link.save(update_fields=['active', 'updated_at'])
    for session in link.sessions.select_related('game').filter(state__in=studio_sessions.OPEN_STATES):
        studio_sessions.cancel(session, 'disconnected')
    return _ok(_tournament_studio(user, tournament), 'Disconnected.')


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------

def _admin(request):
    from .views_manage import _admin as partner_admin
    return partner_admin(request)


def _admin_row(game):
    from . import studio_usage

    row = game_row(game)
    row['partner'] = {'name': game.partner.name, 'slug': game.partner.slug,
                      'owner': game.partner.owner.username if game.partner.owner_id else None}
    this_month = studio_usage.month_of(timezone.now()).isoformat()
    current = next((m for m in studio_usage.monthly(game, months=1) if m['month'] == this_month),
                   {'active_players': 0, 'sessions': 0})
    row['active_players_this_month'] = current['active_players']
    row['sessions_this_month'] = current['sessions']
    return row


@api_view(['GET'])
def admin_studios(request):
    """GET /partners/admin/studios/ - every game, waiting ones first."""
    admin, err = _admin(request)
    if err:
        return err
    games = list(StudioGame.objects.select_related('partner', 'partner__owner', 'game'))
    games.sort(key=lambda g: (g.live_status != 'requested', g.name.lower()))
    return _ok({
        'games': [_admin_row(g) for g in games],
        'counts': {
            'requested': sum(1 for g in games if g.live_status == 'requested'),
            'live': sum(1 for g in games if g.is_live),
            'sandbox': sum(1 for g in games if not g.is_live),
        },
    }, 'Studios')


@api_view(['POST'])
def admin_review_game(request, slug):
    """POST /partners/admin/studios/<slug>/review/ - approve, reject or take off live."""
    admin, err = _admin(request)
    if err:
        return err
    game = find_by_ref(slug, entity_type='studio_game', id_field='id', model=StudioGame,
                       queryset=StudioGame.objects.select_related('partner', 'partner__owner'))
    if game is None:
        return _not_found()

    decision = inputs.read_text(request.data, 'decision', max_length=20).lower()
    if decision not in ('approved', 'rejected', 'suspended'):
        return _err('Decision must be approved, rejected or suspended.', 'STUDIO_BAD_DECISION')
    note = inputs.read_text(request.data, 'note', max_length=2000, strip=False)

    with transaction.atomic():
        if decision == 'approved':
            game.mode, game.live_status = 'live', 'approved'
            partner = game.partner
            granted = list(partner.approved_scopes or [])
            for scope in SANDBOX_SCOPES:
                if scope not in granted:
                    granted.append(scope)
            partner.approved_scopes = granted
            partner.save(update_fields=['approved_scopes', 'updated_at'])
        else:
            game.mode, game.live_status = 'sandbox', 'rejected'
        game.review_note = note
        game.reviewed_by = admin
        game.reviewed_at = timezone.now()
        game.save(update_fields=['mode', 'live_status', 'review_note', 'reviewed_by',
                                 'reviewed_at', 'updated_at'])
        AdminAction.objects.create(
            admin=admin, action_type='review_studio_game', target_model='StudioGame',
            target_id=str(game.pk), reason=note,
            metadata={'decision': decision, 'partner': game.partner_id})

    return _ok({'game': _admin_row(game)}, 'Game %s.' % decision)

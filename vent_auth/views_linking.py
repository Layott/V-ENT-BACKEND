"""Linking an external account to a V-ENT profile, for real.

Only two platforms here, and that is deliberate. Discord has a normal OAuth2
flow, and Steam has OpenID 2.0 that anyone may use. PSN, Xbox, Riot, Epic, EA
and Activision have no public way for a site to confirm that a handle belongs to
the person typing it, so those stay hand-typed on the profile and never claim to
be verified.

Both flows are guarded on their credentials being present. Until they are, the
start endpoint answers 503 with `configured: false`, the button says so, and
nothing pretends to work.

The state parameter is signed with the Django secret and carries the user id and
a timestamp, so a callback cannot be replayed against a different account and an
abandoned flow expires on its own.
"""
import logging
import os
from urllib.parse import urlencode

import requests as http
from django.core import signing
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.shortcuts import redirect

from vent.settings import FRONTEND_URL
from .models import PlatformAccount
from .views_profile import _user_from_bearer

logger = logging.getLogger(__name__)

STATE_SALT = 'vent.account-linking'
STATE_MAX_AGE = 60 * 15          # a link flow nobody finishes in 15 minutes is abandoned
API_BASE = os.environ.get('BACKEND_PUBLIC_URL', 'https://api.v-ent.co').rstrip('/')

DISCORD_AUTHORIZE = 'https://discord.com/api/oauth2/authorize'
DISCORD_TOKEN = 'https://discord.com/api/oauth2/token'
DISCORD_ME = 'https://discord.com/api/users/@me'
STEAM_OPENID = 'https://steamcommunity.com/openid/login'
STEAM_SUMMARY = 'https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/'


def _discord_credentials():
    return (os.environ.get('DISCORD_CLIENT_ID', ''), os.environ.get('DISCORD_CLIENT_SECRET', ''))


def _steam_key():
    return os.environ.get('STEAM_API_KEY', '')


def provider_status():
    """What can actually be linked right now, for the settings page to render."""
    client_id, secret = _discord_credentials()
    return {
        'discord': {'configured': bool(client_id and secret)},
        # Steam's OpenID needs no key at all; the key only buys the display name,
        # so linking works without it and the handle is the numeric id.
        'steam': {'configured': True, 'names': bool(_steam_key())},
    }


def _sign(user):
    return signing.dumps({'uid': user.user_id}, salt=STATE_SALT)


def _unsign(state):
    try:
        return signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE).get('uid')
    except signing.BadSignature:
        return None


def _finish(outcome, provider):
    """Send the browser back to the settings page with the result on it."""
    return redirect(f'{FRONTEND_URL}/settings?panel=linked&{urlencode({provider: outcome})}')


@api_view(['GET'])
def link_status(request):
    """GET /auth/link/status/ - what is linked, and what can be."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    linked = {
        row.platform: {
            'connected': row.connected,
            'verified': row.verified,
            'label': row.display_name or row.gamertag,
        }
        for row in PlatformAccount.objects.filter(user=user)
    }

    # Google is not a PlatformAccount - it is how the account signs in.
    google_connected = user.signup_type == 'google'
    linked['google'] = {
        'connected': google_connected,
        'verified': google_connected,
        'label': user.email if google_connected else '',
    }

    # Signing in with an outside community is a linked account too, and it was
    # the one thing this panel did not know about: somebody could sign in with
    # their African Free Fire Community account and find nothing here saying so.
    from vent_partners.models import ExternalIdentity
    from vent_partners.views_sso import INBOUND_PROVIDERS, inbound_config

    identities = {
        row.provider: row
        for row in ExternalIdentity.objects.filter(user=user)
    }
    external = {}
    for slug in INBOUND_PROVIDERS:
        cfg = inbound_config(slug)
        row = identities.get(slug)
        # A provider switched off is hidden, unless this person is already
        # linked to it: their account genuinely is connected and the panel must
        # not quietly stop saying so.
        if not cfg['enabled'] and row is None:
            continue
        external[slug] = {
            'label': cfg['label'],
            'short': cfg['short'],
            'configured': cfg['configured'],
            'connected': row is not None,
            # What it is connected as, so the row proves the link rather than
            # asserting it.
            'handle': (row.external_username or row.external_email) if row else '',
            # True when this is how the account signs in. Those cannot be
            # unlinked without a password, and the panel says so instead of
            # offering a button that answers 409.
            'is_sign_in_method': (
                row is not None
                and user.signup_type == slug
                and not (bool(user.password) and user.has_usable_password())
            ),
        }

    return Response({
        'status': 'success',
        'data': {'linked': linked, 'providers': provider_status(),
                 'external': external},
        'message': 'Linked accounts.',
    })


@api_view(['GET'])
def link_start(request, provider):
    """GET /auth/link/<provider>/start/ - where to send the browser."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    provider = provider.lower()
    state = _sign(user)

    if provider == 'discord':
        client_id, secret = _discord_credentials()
        if not (client_id and secret):
            return Response({ 'code': 'DISCORD_LINKING_NOT_SET',
                'status': 'error',
                'configured': False,
                'message': 'Discord linking is not set up yet.',
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        params = {
            'client_id': client_id,
            'redirect_uri': f'{API_BASE}/auth/link/discord/callback/',
            'response_type': 'code',
            'scope': 'identify',
            'state': state,
            'prompt': 'consent',
        }
        return Response({
            'status': 'success',
            'data': {'url': f'{DISCORD_AUTHORIZE}?{urlencode(params)}'},
            'message': 'Continue at Discord.',
        })

    if provider == 'steam':
        callback = f'{API_BASE}/auth/link/steam/callback/?state={state}'
        params = {
            'openid.ns': 'http://specs.openid.net/auth/2.0',
            'openid.mode': 'checkid_setup',
            'openid.return_to': callback,
            'openid.realm': API_BASE,
            'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
            'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
        }
        return Response({
            'status': 'success',
            'data': {'url': f'{STEAM_OPENID}?{urlencode(params)}'},
            'message': 'Continue at Steam.',
        })

    return Response(
        {'status': 'error', 'message': f'{provider} cannot be linked.'},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _claim_or_taken(user, platform, handle, defaults):
    """Give this account the handle, unless somebody else already holds it.

    `PlatformAccount` is unique on `(user, platform)`, which stops one person
    linking two Discords and does NOTHING about two people linking one. Both
    callbacks went straight to `update_or_create(user=user, ...)`, so the
    second person to arrive with a handle got it, and both profiles then read
    `verified: True` for the same external account.

    That empties the word. The whole difference between a linked account and a
    hand-typed one is that the platform confirmed it, and a confirmation two
    people can hold confirms nothing. Somebody could have worn a known
    player's handle.

    Returns True when the claim stands, False when it is taken. A handle held
    by a row that is no longer connected is free again: unlinking has to
    release it, or the first person to link anything owns it for ever.

    The `taken` outcome has been handled by `LinkedAccountsPanel` since the day
    it was written. The backend simply had no path that could send it, which is
    the tell: an outcome the interface handles and the server never emits.
    """
    if handle:
        held_by_someone_else = (PlatformAccount.objects
                                .filter(platform=platform, gamertag=handle, connected=True)
                                .exclude(user=user)
                                .exists())
        if held_by_someone_else:
            return False

    PlatformAccount.objects.update_or_create(
        user=user, platform=platform, defaults=defaults)
    return True


@api_view(['GET'])
@permission_classes([AllowAny])
def discord_callback(request):
    """Where Discord sends the browser back. Signed state carries the account."""
    from .models import Users

    code = request.query_params.get('code')
    uid = _unsign(request.query_params.get('state') or '')
    if not code or not uid:
        return _finish('failed', 'discord')

    user = Users.objects.filter(user_id=uid).first()
    if user is None:
        return _finish('failed', 'discord')

    client_id, secret = _discord_credentials()
    try:
        token_res = http.post(DISCORD_TOKEN, data={
            'client_id': client_id,
            'client_secret': secret,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': f'{API_BASE}/auth/link/discord/callback/',
        }, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=15)
        if token_res.status_code != 200:
            logger.warning('discord token exchange failed: %s', token_res.text[:200])
            return _finish('failed', 'discord')

        access = token_res.json().get('access_token')
        me = http.get(DISCORD_ME, headers={'Authorization': f'Bearer {access}'}, timeout=15)
        if me.status_code != 200:
            return _finish('failed', 'discord')
        profile = me.json()
    except Exception:
        logger.exception('discord linking failed')
        return _finish('failed', 'discord')

    handle = profile.get('username') or ''
    claimed = _claim_or_taken(user, 'discord', handle, {
        'display_name': profile.get('global_name') or handle,
        'gamertag': handle,
        'connected': True,
        # Discord told us this handle belongs to whoever just signed in
        # there, which is the whole difference between this and typing it.
        'verified': True,
    })
    return _finish('linked' if claimed else 'taken', 'discord')


@api_view(['GET'])
@permission_classes([AllowAny])
def steam_callback(request):
    """Steam's OpenID 2.0 return. The assertion has to be checked back with Steam."""
    from .models import Users

    uid = _unsign(request.query_params.get('state') or '')
    if not uid:
        return _finish('failed', 'steam')

    user = Users.objects.filter(user_id=uid).first()
    if user is None:
        return _finish('failed', 'steam')

    # Hand every openid.* parameter back with mode=check_authentication. Steam
    # answers is_valid:true only for an assertion it actually issued, which is
    # what stops anyone from calling this URL with a steamid they made up.
    params = {k: v for k, v in request.query_params.items() if k.startswith('openid.')}
    params['openid.mode'] = 'check_authentication'
    try:
        verify = http.post(STEAM_OPENID, data=params, timeout=15)
        if 'is_valid:true' not in verify.text:
            logger.warning('steam assertion rejected')
            return _finish('failed', 'steam')
    except Exception:
        logger.exception('steam verification failed')
        return _finish('failed', 'steam')

    claimed = request.query_params.get('openid.claimed_id', '')
    steam_id = claimed.rstrip('/').split('/')[-1]
    if not steam_id.isdigit():
        return _finish('failed', 'steam')

    display = ''
    key = _steam_key()
    if key:
        try:
            summary = http.get(STEAM_SUMMARY, params={'key': key, 'steamids': steam_id}, timeout=15)
            players = summary.json().get('response', {}).get('players', [])
            if players:
                display = players[0].get('personaname') or ''
        except Exception:
            logger.warning('steam summary lookup failed', exc_info=True)

    claimed = _claim_or_taken(user, 'steam', steam_id, {
        'display_name': display,
        'gamertag': steam_id,
        'connected': True,
        'verified': True,
    })
    return _finish('linked' if claimed else 'taken', 'steam')


@api_view(['POST'])
def link_disconnect(request, provider):
    """POST /auth/link/<provider>/disconnect/ - drop the row entirely."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    provider = provider.lower()
    if provider == 'google':
        return Response({ 'code': 'GOOGLE_HOW_ACCOUNT_SIGNS',
            'status': 'error',
            'message': 'Google is how this account signs in and cannot be unlinked here.',
        }, status=status.HTTP_400_BAD_REQUEST)

    deleted, _ = PlatformAccount.objects.filter(user=user, platform=provider).delete()
    return Response({
        'status': 'success',
        'data': {'removed': bool(deleted)},
        'message': f'{provider} disconnected.' if deleted else 'Nothing was linked.',
    })

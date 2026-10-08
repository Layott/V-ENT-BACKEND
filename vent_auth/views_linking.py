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
from . import inputs

logger = logging.getLogger(__name__)

STATE_SALT = 'vent.account-linking'
STATE_MAX_AGE = 60 * 15          # a link flow nobody finishes in 15 minutes is abandoned
API_BASE = os.environ.get('BACKEND_PUBLIC_URL', 'https://api.v-ent.co').rstrip('/')

DISCORD_AUTHORIZE = 'https://discord.com/api/oauth2/authorize'
DISCORD_TOKEN = 'https://discord.com/api/oauth2/token'
DISCORD_ME = 'https://discord.com/api/users/@me'
STEAM_OPENID = 'https://steamcommunity.com/openid/login'
STEAM_SUMMARY = 'https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/'
# A public Steam profile answers in XML with its name and picture and needs no
# key. The CEO cannot get a Web API key for now (8 October 2026), so this is
# where a linked Steam account's name comes from until one is set.
STEAM_PROFILE_XML = 'https://steamcommunity.com/profiles/%s/?xml=1'
DISCORD_AVATAR = 'https://cdn.discordapp.com/avatars/%s/%s.png?size=128'

# Where the browser comes back to after Discord or Steam: the page somebody
# pressed Connect on. Named, never taken from the request, so the return
# address cannot be pointed anywhere else.
BACK = {
    'settings': '/settings?panel=linked',
    'profile': '/edit-user-profile?panel=accounts',
}


def _discord_credentials():
    return (os.environ.get('DISCORD_CLIENT_ID', ''), os.environ.get('DISCORD_CLIENT_SECRET', ''))


def _steam_key():
    return os.environ.get('STEAM_API_KEY', '')


def _discord_invite():
    from .discord import INVITE
    return INVITE


def _dm_configured():
    from .discord import dm_configured
    return dm_configured()


def provider_status():
    """What can actually be linked right now, for the settings page to render."""
    client_id, secret = _discord_credentials()
    return {
        'discord': {'configured': bool(client_id and secret)},
        # Steam's OpenID needs no key at all; the key only buys the display name,
        # so linking works without it and the handle is the numeric id.
        'steam': {'configured': True, 'names': bool(_steam_key())},
    }


def _sign(user, back='settings'):
    return signing.dumps({'uid': user.user_id,
                          'back': back if back in BACK else 'settings'}, salt=STATE_SALT)


def _state(state):
    """(user id, where to return) from a signed state, or (None, 'settings')."""
    try:
        data = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.BadSignature:
        return None, 'settings'
    back = data.get('back')
    return data.get('uid'), back if back in BACK else 'settings'


def _unsign(state):
    return _state(state)[0]


def _finish(outcome, provider, back='settings'):
    """Send the browser back where Connect was pressed, with the result on it."""
    path = BACK.get(back, BACK['settings'])
    return redirect(f'{FRONTEND_URL}{path}&{urlencode({provider: outcome})}')


def _steam_profile(steam_id):
    """(name, picture) for a Steam account, from the Web API when a key is set,
    otherwise from the public profile XML. ('', '') when Steam will not say.

    The XML is read by pattern from at most 64 KB rather than parsed, so a
    hostile or broken answer cannot expand into anything; and a picture is
    kept only from Steam's own image hosts.
    """
    import re

    key = _steam_key()
    if key:
        try:
            summary = http.get(STEAM_SUMMARY, params={'key': key, 'steamids': steam_id}, timeout=15)
            players = summary.json().get('response', {}).get('players', [])
            if players:
                return (players[0].get('personaname') or '')[:64], _steam_picture(
                    players[0].get('avatarmedium') or '')
        except Exception:
            logger.warning('steam summary lookup failed', exc_info=True)
    try:
        res = http.get(STEAM_PROFILE_XML % steam_id, timeout=15)
        if res.status_code != 200:
            return '', ''
        text = (res.text or '')[:65536]
    except Exception:
        logger.warning('steam profile lookup failed', exc_info=True)
        return '', ''

    def field(name):
        m = re.search(r'<%s>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))\s*</%s>' % (name, name), text, re.S)
        return ((m.group(1) if m and m.group(1) is not None else (m.group(2) if m else '')) or '').strip()

    return field('steamID')[:64], _steam_picture(field('avatarMedium'))


def _steam_picture(url):
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url or '')
    except ValueError:
        return ''
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host.endswith('.steamstatic.com') or host.endswith('.akamaihd.net')):
        return ''
    return url[:400]


@api_view(['GET'])
def link_status(request):
    """GET /auth/link/status/ - what is linked, and what can be."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    linked = {
        row.platform: {
            # Connected means proven: a handle typed into the old profile boxes
            # sat here as "Connected" beside real links (walk, 8 Oct, inbox 417).
            'connected': bool(row.connected and row.verified),
            'verified': row.verified,
            'label': row.display_name or row.gamertag,
            'avatar': row.avatar_url,
            # So the settings panel can draw the direct-message switch in the
            # right position without a second request, and can say why it is
            # unavailable rather than showing a control that cannot work.
            'dm_enabled': row.dm_enabled,
            'dm_available': bool(row.provider_user_id),
            'dm_error': row.dm_error,
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
                 # Whether the SERVER can send direct messages at all, which is
                 # a different question from whether this person wants them.
                 'dm_configured': _dm_configured(),
                 # Where to join, so the direct-message switch can offer the
                 # one thing that makes it work. Discord refuses a DM from a
                 # bot that shares no server with the recipient.
                 'discord_invite': _discord_invite(),
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
    back = inputs.read_text(request.query_params, 'back', max_length=16)
    state = _sign(user, back)

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

    code = inputs.read_text(request.query_params, 'code', max_length=500) or None
    uid, back = _state(inputs.read_text(request.query_params, 'state', max_length=2000))
    if not code or not uid:
        return _finish('failed', 'discord', back)

    user = Users.objects.filter(user_id=uid).first()
    if user is None:
        return _finish('failed', 'discord', back)

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
            return _finish('failed', 'discord', back)

        access = token_res.json().get('access_token')
        me = http.get(DISCORD_ME, headers={'Authorization': f'Bearer {access}'}, timeout=15)
        if me.status_code != 200:
            return _finish('failed', 'discord', back)
        profile = me.json()
    except Exception:
        logger.exception('discord linking failed')
        return _finish('failed', 'discord', back)

    handle = profile.get('username') or ''
    discord_id = str(profile.get('id') or '')
    avatar_hash = str(profile.get('avatar') or '')
    avatar = (DISCORD_AVATAR % (discord_id, avatar_hash)
              if discord_id.isdigit() and avatar_hash.replace('_', '').isalnum() else '')
    claimed = _claim_or_taken(user, 'discord', handle, {
        # Discord's own id for this account. A handle is renameable and
        # reusable; the snowflake is neither, so it is what a direct message is
        # addressed to and what recognises a returning person at sign-in.
        'provider_user_id': discord_id,
        'display_name': profile.get('global_name') or handle,
        'gamertag': handle,
        'avatar_url': avatar,
        'connected': True,
        # Discord told us this handle belongs to whoever just signed in
        # there, which is the whole difference between this and typing it.
        'verified': True,
    })
    return _finish('linked' if claimed else 'taken', 'discord', back)


@api_view(['GET'])
@permission_classes([AllowAny])
def steam_callback(request):
    """Steam's OpenID 2.0 return. The assertion has to be checked back with Steam."""
    from .models import Users

    uid, back = _state(inputs.read_text(request.query_params, 'state', max_length=2000))
    if not uid:
        return _finish('failed', 'steam', back)

    user = Users.objects.filter(user_id=uid).first()
    if user is None:
        return _finish('failed', 'steam', back)

    # Hand every openid.* parameter back with mode=check_authentication. Steam
    # answers is_valid:true only for an assertion it actually issued, which is
    # what stops anyone from calling this URL with a steamid they made up.
    params = {k: v for k, v in request.query_params.items() if k.startswith('openid.')}
    params['openid.mode'] = 'check_authentication'
    try:
        verify = http.post(STEAM_OPENID, data=params, timeout=15)
        if 'is_valid:true' not in verify.text:
            logger.warning('steam assertion rejected')
            return _finish('failed', 'steam', back)
    except Exception:
        logger.exception('steam verification failed')
        return _finish('failed', 'steam', back)

    claimed = inputs.read_text(request.query_params, 'openid.claimed_id', max_length=500)
    steam_id = claimed.rstrip('/').split('/')[-1]
    if not steam_id.isdigit():
        return _finish('failed', 'steam', back)

    display, picture = _steam_profile(steam_id)

    claimed = _claim_or_taken(user, 'steam', steam_id, {
        'provider_user_id': steam_id,
        'display_name': display,
        'gamertag': steam_id,
        'avatar_url': picture,
        'connected': True,
        'verified': True,
    })
    return _finish('linked' if claimed else 'taken', 'steam', back)


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

@api_view(['POST'])
def link_dm_toggle(request, provider):
    """POST /auth/link/<provider>/dm/ - turn direct messages on or off.

    Off until somebody says otherwise. An unasked-for direct message from a
    platform is the fastest way to be blocked, and being blocked costs the
    channel for everything afterwards including the messages people did want.

    Refuses when the account is not linked, rather than storing a preference
    that can never be honoured: a switch that saves and does nothing is worse
    than a switch that explains itself.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    provider = provider.lower()
    if provider != 'discord':
        return Response({'status': 'error', 'code': 'UNSUPPORTED',
                         'message': f'{provider} cannot send direct messages.'},
                        status=status.HTTP_400_BAD_REQUEST)

    from .discord import dm_configured
    if not dm_configured():
        return Response({'status': 'error', 'code': 'DISCORD_DM_NOT_SET',
                         'configured': False,
                         'message': 'Direct messages are not set up yet.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)

    row = PlatformAccount.objects.filter(user=user, platform='discord',
                                         connected=True).first()
    if row is None:
        return Response({'status': 'error', 'code': 'NOT_LINKED',
                         'message': 'Connect your Discord account first.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not row.provider_user_id:
        # Linked before the id was stored. Reconnecting is the only way to get
        # it, and saying so beats a switch that turns on and never delivers.
        return Response({'status': 'error', 'code': 'RECONNECT_NEEDED',
                         'message': 'Disconnect and connect Discord again to '
                                    'turn on direct messages.'},
                        status=status.HTTP_400_BAD_REQUEST)

    row.dm_enabled = inputs.read_bool(request.data, 'enabled')
    row.dm_error = ''
    row.save(update_fields=['dm_enabled', 'dm_error', 'updated_at'])

    return Response({
        'status': 'success',
        'data': {'dm_enabled': row.dm_enabled},
        'message': ('Direct messages on.' if row.dm_enabled
                    else 'Direct messages off.'),
    })

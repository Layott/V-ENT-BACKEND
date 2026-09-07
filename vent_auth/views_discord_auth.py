"""Signing in and signing up with Discord.

CEO, 7 September 2026: "DO THE BAOVE TWO ALSO" on sign-in, then "users should
be able to sign up with discord also".

## How this differs from linking, which already existed

Linking proves a Discord handle belongs to somebody who is ALREADY signed in to
V-ENT. It creates no session and cannot create an account. This is the other
direction: arriving with nothing but a Discord account and leaving with a V-ENT
session.

They share the OAuth application, the secret and the state signing, and they
deliberately do NOT share a callback: the linking callback attaches to whoever
was signed in, and letting that same URL create accounts would mean one route
with two very different security stories.

## The refusal that matters

**An email that already belongs to a password account is refused.**

Discord verifies email addresses, but "Discord says this address is theirs" and
"this is the person who holds the V-ENT account on that address" are not the
same claim. Merging on a matching email means anybody who can get a Discord
account onto your email address owns your V-ENT account, and V-ENT accounts
hold a wallet.

So the answer is a sentence telling them what to do instead: sign in the way
they already do, then connect Discord in Settings, which is the flow that was
built first and which proves both sides. It is one extra step for the honest
case and a closed door for the dishonest one.

Google's `social_auth` merges on email, which is the same shape. That is
existing behaviour and out of scope here, but it is written down in the
handover so somebody decides about it deliberately rather than by not noticing.

## Everything else is the ordinary way in

The same `generate_session_token`, the same `record_login`, the same daily
location refresh, the same sign-in alert email. A second authentication path
that skips the alerting is a second place to get security wrong.
"""
import logging
import os
from urllib.parse import urlencode

import requests as http
from django.core import signing
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from . import emails
from .models import PlatformAccount, Users
from .views_helpers import (create_user_wallet, generate_session_token,
                            generate_unique_username)
from .views_linking import (API_BASE, DISCORD_AUTHORIZE, DISCORD_ME,
                            DISCORD_TOKEN, _discord_credentials)

logger = logging.getLogger(__name__)

STATE_SALT = 'vent.discord-signin'
STATE_MAX_AGE = 60 * 15

#: `identify` gives the id, username and avatar. `email` is what makes an
#: account possible at all: without it there is nothing to send a receipt to,
#: nothing to recover the account with, and nothing to match a returning person
#: on if they ever unlink.
SCOPES = 'identify email'


def _finish(outcome, token='', username=''):
    """Back to the frontend with the result on the address.

    A redirect rather than JSON, because the browser arrives here from Discord
    rather than from our own fetch. `/auth/external` is the existing screen for
    exactly this: it reads the outcome and hands the session to NextAuth.
    """
    params = {'provider': 'discord', 'outcome': outcome}
    if token:
        params['token'] = token
    if username:
        params['username'] = username
    return redirect(f'{FRONTEND_URL}/auth/external?{urlencode(params)}')


@api_view(['GET'])
@permission_classes([AllowAny])
def discord_signin_start(request):
    """GET /auth/discord/start/ - where to send somebody who pressed the button.

    Public, because the whole point is that they have no account yet.
    """
    client_id, secret = _discord_credentials()
    if not (client_id and secret):
        return Response({
            'status': 'error',
            'code': 'DISCORD_SIGNIN_NOT_SET',
            'configured': False,
            'message': 'Signing in with Discord is not set up yet.',
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    # `next` is carried through the signed state rather than as a bare query
    # parameter, so it cannot be swapped for somebody else's destination on the
    # way back.
    nxt = str(request.query_params.get('next') or '')[:200]
    state = signing.dumps({'next': nxt, 'at': timezone.now().isoformat()},
                          salt=STATE_SALT)

    params = {
        'client_id': client_id,
        'redirect_uri': f'{API_BASE}/auth/discord/callback/',
        'response_type': 'code',
        'scope': SCOPES,
        'state': state,
        'prompt': 'consent',
    }
    return Response({
        'status': 'success',
        'data': {'url': f'{DISCORD_AUTHORIZE}?{urlencode(params)}'},
        'message': 'Continue at Discord.',
    })


def _profile_from_code(code):
    """Exchange the code and read who it belongs to. None on any failure."""
    client_id, secret = _discord_credentials()
    try:
        token_res = http.post(DISCORD_TOKEN, data={
            'client_id': client_id,
            'client_secret': secret,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': f'{API_BASE}/auth/discord/callback/',
        }, headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=15)
        if token_res.status_code != 200:
            logger.warning('discord signin token exchange: %s',
                           token_res.text[:200])
            return None

        access = token_res.json().get('access_token')
        me = http.get(DISCORD_ME, headers={'Authorization': f'Bearer {access}'},
                      timeout=15)
        if me.status_code != 200:
            return None
        return me.json()
    except Exception:                                           # noqa: BLE001
        logger.exception('discord signin failed')
        return None


def _sign_in(user, request, created=False):
    """One session, made the same way every other sign-in makes one."""
    user.login_session_token = generate_session_token()
    user.login_session_created_at = timezone.now()
    user.save()

    try:
        from .geo import record_login, refresh_daily_location
        refresh_daily_location(user, request)
        if record_login(user, request, method='discord') and not created:
            emails.send_login_alert(user, request)
    except Exception:                                           # noqa: BLE001
        # A failure to record where somebody signed in from must not stop them
        # signing in. It is logged and the session still stands.
        logger.exception('discord signin: login record failed')

    return user.login_session_token


@api_view(['GET'])
@permission_classes([AllowAny])
def discord_signin_callback(request):
    """GET /auth/discord/callback/ - Discord sends the browser back here."""
    code = request.query_params.get('code')
    state = request.query_params.get('state') or ''
    try:
        signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.BadSignature:
        return _finish('failed')

    if not code:
        return _finish('failed')

    profile = _profile_from_code(code)
    if not profile:
        return _finish('failed')

    discord_id = str(profile.get('id') or '')
    email = (profile.get('email') or '').strip().lower()
    handle = profile.get('username') or ''
    display = profile.get('global_name') or handle

    if not discord_id:
        return _finish('failed')

    # 1. Somebody who has linked this Discord already. Matched on the ID, never
    #    on the handle, because a handle can be renamed and re-registered by
    #    somebody else.
    row = (PlatformAccount.objects
           .filter(platform='discord', provider_user_id=discord_id,
                   connected=True)
           .select_related('user').first())
    if row:
        return _finish('ok', _sign_in(row.user, request), row.user.username)

    # 2. Somebody who signed up with Discord before.
    existing = Users.objects.filter(signup_type='discord',
                                    provider_id=discord_id).first()
    if existing:
        return _finish('ok', _sign_in(existing, request), existing.username)

    # 3. That email already belongs to somebody. REFUSED, deliberately.
    #    See the module docstring: merging on a matching email hands the
    #    account, and its wallet, to whoever can get a Discord onto that
    #    address.
    if email and Users.objects.filter(email__iexact=email).exists():
        return _finish('email_taken')

    if not email:
        # Discord did not give an address, which happens when the account has
        # none verified. There is nothing to build an account on.
        return _finish('no_email')

    # 4. A new person.
    try:
        user = Users.objects.create(
            full_name=display or handle,
            email=email,
            username=generate_unique_username(email),
            signup_type='discord',
            provider_id=discord_id,
            is_active=True,
        )
        create_user_wallet(user)

        # Record the link too, so Settings shows Discord connected and a direct
        # message has an id to go to. One description of the connection, rather
        # than sign-up and linking each keeping their own.
        PlatformAccount.objects.update_or_create(
            user=user, platform='discord',
            defaults={
                'provider_user_id': discord_id,
                'display_name': display,
                'gamertag': handle,
                'connected': True,
                'verified': True,
            },
        )
    except Exception:                                           # noqa: BLE001
        logger.exception('discord signup failed')
        return _finish('failed')

    return _finish('created', _sign_in(user, request, created=True),
                   user.username)

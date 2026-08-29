"""V-ENT as a sign-in provider, and V-ENT as a sign-in client.

**Outbound** (a partner puts "sign in with V-ENT" on their site) is an OAuth2
authorization-code flow with PKCE. The partner never sees a password, gets a
code that dies in ten minutes and can only be spent once, and the token it
receives reads a small profile: username, display name, country, avatar. No
email unless the person's scope says so, no wallet, ever.

**Inbound** (somebody signs in to V-ENT with an African Free Fire Community
account) is the mirror. It is written against plain OAuth2 and configured
entirely by environment variables, so the day AFC hands over a client id and
secret it works without a code change. Until then every endpoint answers 503
with `configured: false` and the button does not appear.
"""
import base64
import hashlib
import logging
import os
import secrets
from urllib.parse import urlencode

import requests as http
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from vent_auth.models import Users
from vent_auth.views_helpers import (
    create_user_wallet,
    generate_session_token,
    generate_unique_username,
)
from vent_auth.views_profile import _user_from_bearer

from vent_auth.throttle import too_many

from .models import (
    ExternalIdentity,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    Partner,
    valid_scopes,
)

logger = logging.getLogger(__name__)

# Scopes a partner may ask for about a person, as opposed to about the platform.
IDENTITY_SCOPES = {
    'identity': 'Your username, display name, country and avatar',
    'identity:email': 'Your email address',
    'identity:teams': 'The teams you belong to',
}


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(message, code='ERROR', http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': None},
                    status=http_status)


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


# ===========================================================================
# Outbound: V-ENT is the provider
# ===========================================================================

@api_view(['GET'])
@permission_classes([AllowAny])
def sso_metadata(request):
    """Everything a partner needs to wire this up, in one place."""
    base = os.environ.get('BACKEND_PUBLIC_URL', 'https://api.v-ent.co').rstrip('/')
    return _ok({
        'issuer': base,
        'authorization_endpoint': f'{FRONTEND_URL}/partners/authorize',
        'token_endpoint': f'{base}/partners/sso/token/',
        'userinfo_endpoint': f'{base}/partners/sso/userinfo/',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code'],
        'code_challenge_methods_supported': ['S256'],
        'scopes_supported': list(IDENTITY_SCOPES),
        'token_endpoint_auth_methods_supported': ['client_secret_post'],
    }, 'V-ENT SSO')


@api_view(['GET'])
@permission_classes([AllowAny])
def sso_authorize_info(request):
    """What the consent screen shows: who is asking, and for what.

    Called by our own frontend before anybody approves anything, so a person is
    never asked to approve a name they cannot see.
    """
    client_id = request.GET.get('client_id', '')
    redirect_uri = request.GET.get('redirect_uri', '')
    scopes = valid_identity_scopes(request.GET.get('scope', 'identity'))

    # This endpoint has to answer before anybody signs in, so it cannot be
    # gated by a key or a session - and in answering it confirms whether a
    # client_id exists. Unlimited, that is an enumeration tool with no cost.
    # 30 a minute is far more than a consent screen ever needs and far less
    # than a sweep wants.
    if too_many(request, 'sso-authorize-info', 30):
        return _err('Too many requests. Wait a minute and try again.',
                    'RATE_LIMITED', status.HTTP_429_TOO_MANY_REQUESTS)

    partner = Partner.objects.filter(sso_client_id=client_id).first()
    if partner is None or not partner.sso_enabled:
        return _err('That application cannot sign people in with V-ENT.', 'UNKNOWN_CLIENT')
    if redirect_uri not in (partner.redirect_uris or []):
        return _err('That redirect address is not registered for this application.',
                    'BAD_REDIRECT')

    return _ok({
        'partner': {
            'name': partner.name,
            'website': partner.website,
            'privacy_policy_url': partner.privacy_policy_url,
            'terms_url': partner.terms_url,
        },
        'scopes': [{'key': s, 'label': IDENTITY_SCOPES[s]} for s in scopes],
        'redirect_uri': redirect_uri,
    }, 'Consent details')


def valid_identity_scopes(raw):
    wanted = {s.strip() for s in str(raw or '').replace(',', ' ').split() if s.strip()}
    scopes = [s for s in IDENTITY_SCOPES if s in wanted]
    return scopes or ['identity']


@api_view(['POST'])
def sso_approve(request):
    """The signed-in person approves, and we mint the code the partner collects."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    client_id = request.data.get('client_id', '')
    redirect_uri = request.data.get('redirect_uri', '')
    scopes = valid_identity_scopes(request.data.get('scope', 'identity'))
    state = request.data.get('state', '')
    challenge = (request.data.get('code_challenge') or '')[:128]
    challenge_method = (request.data.get('code_challenge_method') or '')[:10]

    partner = Partner.objects.filter(sso_client_id=client_id).first()
    if partner is None or not partner.sso_enabled:
        return _err('That application cannot sign people in with V-ENT.', 'UNKNOWN_CLIENT')
    if redirect_uri not in (partner.redirect_uris or []):
        return _err('That redirect address is not registered.', 'BAD_REDIRECT')
    if challenge and challenge_method.upper() != 'S256':
        return _err('Only S256 is accepted for PKCE.', 'BAD_CHALLENGE_METHOD')

    code = secrets.token_urlsafe(32)
    OAuthAuthorizationCode.objects.create(
        partner=partner,
        user=user,
        code_hash=_hash(code),
        redirect_uri=redirect_uri,
        scopes=scopes,
        code_challenge=challenge,
        code_challenge_method=challenge_method.upper() if challenge else '',
    )

    params = {'code': code}
    if state:
        params['state'] = state
    return _ok({'redirect_to': f'{redirect_uri}?{urlencode(params)}'}, 'Approved.')


@api_view(['POST'])
@permission_classes([AllowAny])
def sso_token(request):
    """Trade the code for a token. Client secret or PKCE verifier, not neither."""
    code = request.data.get('code', '')
    client_id = request.data.get('client_id', '')
    client_secret = request.data.get('client_secret', '')
    redirect_uri = request.data.get('redirect_uri', '')
    verifier = request.data.get('code_verifier', '')

    partner = Partner.objects.filter(sso_client_id=client_id).first()
    if partner is None or not partner.sso_enabled:
        return _err('Unknown client.', 'UNKNOWN_CLIENT', status.HTTP_401_UNAUTHORIZED)

    record = (
        OAuthAuthorizationCode.objects
        .select_related('user', 'partner')
        .filter(code_hash=_hash(code), partner=partner)
        .first()
    )
    if record is None or not record.is_valid():
        return _err('That code is expired or already used.', 'BAD_CODE',
                    status.HTTP_400_BAD_REQUEST)
    if record.redirect_uri != redirect_uri:
        return _err('The redirect address does not match the one the code was issued for.',
                    'BAD_REDIRECT')

    # PKCE and the client secret are separate checks, not alternatives.
    #
    # This was `if challenge: verify PKCE / elif: verify secret`, so a partner
    # that sends a challenge never had its secret checked at all - and AFC always
    # sends one. PKCE proves the caller is the same party that started the flow;
    # it does not prove which application is calling. RFC 6749 4.1.3 wants a
    # confidential client authenticated, with PKCE on top rather than instead.
    #
    # A public client - a browser or a mobile app - holds no secret and cannot,
    # so it is authenticated by PKCE alone. That is the only case where one
    # check stands on its own, and it is decided by whether the partner has a
    # secret at all rather than by what the caller chose to send.
    if record.code_challenge:
        digest = hashlib.sha256(str(verifier).encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
        if not secrets.compare_digest(expected, record.code_challenge):
            return _err('The PKCE verifier does not match.', 'BAD_VERIFIER',
                        status.HTTP_401_UNAUTHORIZED)

    if partner.sso_client_secret_hash:
        if not partner.sso_secret_matches(client_secret):
            return _err('Bad client secret.', 'BAD_SECRET',
                        status.HTTP_401_UNAUTHORIZED)
    elif not record.code_challenge:
        # No secret on file and no challenge: nothing identifies the caller.
        return _err('Send a PKCE verifier, or a client secret.', 'BAD_SECRET',
                    status.HTTP_401_UNAUTHORIZED)

    # Spending the code is one conditional UPDATE, and the row count decides.
    #
    # `is_valid()` was checked above, outside any transaction, and the write that
    # marks the code used was a plain save. Two requests arriving together both
    # passed the check and both minted a token from one code - a code that is
    # meant to be worth exactly one session. Whoever loses the race now gets
    # BAD_CODE, which is the same answer as replaying it, because that is what
    # it is.
    with transaction.atomic():
        spent = (
            OAuthAuthorizationCode.objects
            .filter(pk=record.pk, used_at__isnull=True)
            .update(used_at=timezone.now())
        )
        if not spent:
            return _err('That code is expired or already used.', 'BAD_CODE',
                        status.HTTP_400_BAD_REQUEST)

        token = secrets.token_urlsafe(40)
        OAuthAccessToken.objects.create(
            partner=partner, user=record.user, token_hash=_hash(token), scopes=record.scopes,
        )

    return _ok({
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': int(OAuthAccessToken.LIFETIME.total_seconds()),
        'scope': ' '.join(record.scopes),
    }, 'Token issued.')


@api_view(['GET'])
@permission_classes([AllowAny])
def sso_userinfo(request):
    """The small profile a partner is allowed to read."""
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return _err('Send the access token as a bearer token.', 'MISSING_TOKEN',
                    status.HTTP_401_UNAUTHORIZED)

    record = (
        OAuthAccessToken.objects
        .select_related('user', 'partner')
        .filter(token_hash=_hash(header.split(' ', 1)[1].strip()))
        .first()
    )
    if record is None or not record.is_valid():
        return _err('That token is expired or unknown.', 'BAD_TOKEN',
                    status.HTTP_401_UNAUTHORIZED)

    user = record.user
    profile = getattr(user, 'userprofile', None)
    data = {
        'sub': str(user.user_id),
        'username': user.username,
        'name': user.full_name,
        'country': user.country,
        # `state` is a region, not a city. It was being handed to partners under
        # the wrong name, so anybody storing it got a region in a city column.
        'region': user.state,
        'picture': (
            request.build_absolute_uri(profile.profile_picture.url)
            if profile and profile.profile_picture else None
        ),
        'is_founding_member': user.is_founding_member,
    }
    if 'identity:email' in (record.scopes or []):
        data['email'] = user.email
        # This used to answer `user.is_active`, which is Django's
        # account-is-enabled flag and says nothing about whether the address was
        # ever confirmed. Two ways that went wrong, in opposite directions:
        #
        #   * a Google signup and an inbound partner sign-in both create the
        #     account with is_active=True without V-ENT confirming anything, so
        #     it claimed verified for an address we had never checked
        #   * a verified member who is later disabled would have been reported
        #     as unverified
        #
        # A partner matching accounts by email and trusting this could attach
        # the wrong person, which is why it is the one worth fixing first.
        #
        # V-ENT only knows an address is real when it was confirmed through the
        # e-mail flow, which is what `signup_type == 'normal'` plus an active
        # account means here. Anything else is reported honestly as unverified
        # rather than optimistically as verified.
        data['email_verified'] = bool(
            user.is_active and (user.signup_type or 'normal') == 'normal'
        )
    if 'identity:teams' in (record.scopes or []):
        from vent_auth.models import TeamMembers
        data['teams'] = list(
            TeamMembers.objects.filter(user=user).values_list('team__team_name', flat=True)
        )
    return _ok(data, 'Profile')


# ===========================================================================
# Inbound: signing in to V-ENT with an outside account
# ===========================================================================
# Written for the African Free Fire Community, and configured rather than
# hardcoded, so a second community is four environment variables away.

INBOUND_PROVIDERS = {
    'afc': {
        'label': 'African Free Fire Community',
        'client_id': 'AFC_CLIENT_ID',
        'client_secret': 'AFC_CLIENT_SECRET',
        'authorize_url': 'AFC_AUTHORIZE_URL',
        'token_url': 'AFC_TOKEN_URL',
        'userinfo_url': 'AFC_USERINFO_URL',
        'scope': 'AFC_SCOPE',
        # Read out of AFC's partner integration guide, version 1.2, issued
        # 4 August 2026. The SSO surface is on the API host with an /sso/
        # prefix, NOT on the website origin, and the trailing slashes are part
        # of the path. Defaults rather than blanks so a missing environment
        # variable is a missing credential, not a silently wrong endpoint.
        'default_authorize_url': 'https://api.africanfreefirecommunity.com/sso/authorize/',
        'default_token_url': 'https://api.africanfreefirecommunity.com/sso/token/',
        'default_userinfo_url': 'https://api.africanfreefirecommunity.com/sso/userinfo/',
        # AFC's claim names do not match its scope names: the scopes use dots
        # and the claims use underscores. Asked for here; read in
        # link_or_create_user.
        #
        # openid and profile give the identity and the in-game name.
        # afc.freefire gives ff_uid, which is what matches a registration to
        # the right player. afc.team gives the team they actually play for.
        # afc.standing says whether AFC has sanctioned them. Nothing else is
        # requested: every extra scope is one more thing the player can refuse,
        # and widening later forces a fresh consent screen anyway.
        'default_scope': 'openid profile email afc.freefire afc.team afc.standing',
    },
}


def _new_pkce_pair():
    """A PKCE verifier and its S256 challenge.

    RFC 7636: the verifier is 43 to 128 characters from an unreserved
    alphabet, and the challenge is the base64url of its SHA-256 with the
    padding stripped. `token_urlsafe(64)` lands inside that range and uses only
    permitted characters.
    """
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
    return verifier, challenge


def inbound_config(slug):
    spec = INBOUND_PROVIDERS.get(slug)
    if spec is None:
        return None
    cfg = {
        'label': spec['label'],
        'client_id': os.environ.get(spec['client_id'], ''),
        'client_secret': os.environ.get(spec['client_secret'], ''),
        'authorize_url': os.environ.get(spec['authorize_url'],
                                        spec.get('default_authorize_url', '')),
        'token_url': os.environ.get(spec['token_url'],
                                    spec.get('default_token_url', '')),
        'userinfo_url': os.environ.get(spec['userinfo_url'],
                                       spec.get('default_userinfo_url', '')),
        'scope': os.environ.get(spec['scope'], spec['default_scope']),
    }
    cfg['configured'] = all([
        cfg['client_id'], cfg['client_secret'], cfg['authorize_url'],
        cfg['token_url'], cfg['userinfo_url'],
    ])
    return cfg


@api_view(['GET'])
@permission_classes([AllowAny])
def inbound_providers(request):
    """Which outside sign-ins are live. The login page renders from this."""
    rows = {}
    for slug in INBOUND_PROVIDERS:
        cfg = inbound_config(slug)
        rows[slug] = {'label': cfg['label'], 'configured': cfg['configured']}
    return _ok({'providers': rows}, 'Sign-in providers')


@api_view(['GET'])
@permission_classes([AllowAny])
def inbound_start(request, provider):
    """Where to send somebody who chose "continue with <provider>"."""
    cfg = inbound_config(provider)
    if cfg is None:
        return _err('Unknown provider.', 'UNKNOWN_PROVIDER', status.HTTP_404_NOT_FOUND)
    if not cfg['configured']:
        return Response(
            {'status': 'error', 'configured': False,
             'message': f"{cfg['label']} sign-in is not set up yet.",
             'code': 'NOT_CONFIGURED', 'data': {'provider': cfg['label']}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    from django.core import signing
    from .models import InboundLogin

    state = signing.dumps({'p': provider, 'n': secrets.token_urlsafe(12)},
                          salt='vent.inbound-sso')

    # The verifier is kept here and never leaves the server. It cannot ride in
    # `state`, which goes out through the player's browser and is signed rather
    # than encrypted, and it cannot go in the cache, which is per-process local
    # memory on this deployment. See InboundLogin.
    verifier, challenge = _new_pkce_pair()
    InboundLogin.sweep()
    InboundLogin.objects.create(provider=provider, state=state,
                                code_verifier=verifier)

    base = os.environ.get('BACKEND_PUBLIC_URL', 'https://api.v-ent.co').rstrip('/')
    params = {
        'client_id': cfg['client_id'],
        'redirect_uri': f'{base}/partners/inbound/{provider}/callback/',
        'response_type': 'code',
        'scope': cfg['scope'],
        'state': state,
        # AFC sets PKCE_REQUIRED. Without these two the authorize request is
        # refused outright, which their guide names as the most common reason a
        # first integration fails.
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return _ok({'url': f"{cfg['authorize_url']}?{urlencode(params)}"}, f"Continue at {cfg['label']}")


@api_view(['GET'])
@permission_classes([AllowAny])
def inbound_callback(request, provider):
    """Come back from the provider, find or make the account, hand over a session."""
    from django.core import signing

    cfg = inbound_config(provider)
    if cfg is None or not cfg['configured']:
        return redirect(f'{FRONTEND_URL}/login?error=sso-unavailable')

    from .models import InboundLogin

    code = request.GET.get('code')
    state = request.GET.get('state', '')
    try:
        signing.loads(state, salt='vent.inbound-sso', max_age=900)
    except Exception:
        return redirect(f'{FRONTEND_URL}/login?error=sso-state')

    if not code:
        return redirect(f'{FRONTEND_URL}/login?error=sso-cancelled')

    # Single use. Claimed by deleting it, so a replayed callback finds nothing
    # and the same authorization code cannot be exchanged twice.
    attempt = InboundLogin.objects.filter(provider=provider, state=state).first()
    if attempt is None:
        return redirect(f'{FRONTEND_URL}/login?error=sso-state')
    verifier = attempt.code_verifier
    attempt.delete()

    base = os.environ.get('BACKEND_PUBLIC_URL', 'https://api.v-ent.co').rstrip('/')
    try:
        token_res = http.post(cfg['token_url'], data={
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
            'redirect_uri': f'{base}/partners/inbound/{provider}/callback/',
            # The other half of the PKCE pair. AFC checks it against the
            # challenge sent on the authorize request and refuses the exchange
            # if it does not match.
            'code_verifier': verifier,
        }, timeout=15)
        if token_res.status_code != 200:
            logger.warning('%s token exchange failed: %s', provider, token_res.text[:200])
            return redirect(f'{FRONTEND_URL}/login?error=sso-failed')
        access = token_res.json().get('access_token')

        me = http.get(cfg['userinfo_url'],
                      headers={'Authorization': f'Bearer {access}'}, timeout=15)
        if me.status_code != 200:
            return redirect(f'{FRONTEND_URL}/login?error=sso-failed')
        profile = me.json()
    except Exception:
        logger.exception('%s inbound sign-in failed', provider)
        return redirect(f'{FRONTEND_URL}/login?error=sso-failed')

    user = link_or_create_user(provider, profile)
    if user is None:
        return redirect(f'{FRONTEND_URL}/login?error=sso-no-identity')

    session_token = generate_session_token()
    user.login_session_token = session_token
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_token', 'login_session_created_at'])

    return redirect(f'{FRONTEND_URL}/auth/external?token={session_token}&username={user.username}')


def link_or_create_user(provider, profile):
    """Match an outside account to a V-ENT one, or make a new one.

    Matching is by the provider's own id first, then by a verified email, and an
    account is only created when neither exists. The email path matters: someone
    who already has a V-ENT account should get their account, not a duplicate.
    """
    external_id = str(
        profile.get('id') or profile.get('sub') or profile.get('user_id') or ''
    ).strip()
    if not external_id:
        return None

    email = (profile.get('email') or '').strip().lower()
    handle = (profile.get('username') or profile.get('preferred_username')
              or profile.get('name') or '').strip()

    identity = (
        ExternalIdentity.objects
        .select_related('user')
        .filter(provider=provider, external_id=external_id)
        .first()
    )
    if identity is not None:
        identity.last_login_at = timezone.now()
        identity.save(update_fields=['last_login_at'])
        return identity.user

    user = Users.objects.filter(email__iexact=email).first() if email else None

    if user is None:
        from vent_auth.views_helpers import normalize_username, username_problem

        candidate = normalize_username(handle)
        if username_problem(candidate):
            candidate = generate_unique_username(email or f'{provider}_{external_id}')
        else:
            candidate = generate_unique_username(candidate)

        user = Users.objects.create(
            username=candidate,
            email=email or f'{candidate}@{provider}.external',
            full_name=(profile.get('name') or handle or candidate)[:148],
            signup_type=provider,
            social_id=external_id,
            is_active=True,
        )
        create_user_wallet(user=user)

    ExternalIdentity.objects.create(
        user=user,
        provider=provider,
        external_id=external_id,
        external_username=handle[:190],
        external_email=email[:254],
        last_login_at=timezone.now(),
    )
    return user

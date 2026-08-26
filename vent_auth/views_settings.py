"""Settings + device/session endpoints for the frontend /settings page.

Mounted at ROOT (no /auth prefix) because the FE calls `/setting/`, `/device/…`,
`/user/<id>/update/` directly. Auth is the standard Bearer login_session_token.
"""
import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, UserSetting
from .views_profile import _user_from_bearer

logger = logging.getLogger(__name__)

# Sensible defaults so a brand-new user (no row) gets a complete settings object.
DEFAULT_SETTINGS = {
    'notifications': {
        'email_tournaments': True,
        'email_events': True,
        'email_wallet': True,
        'email_marketing': False,
        'push_matches': True,
        'push_mentions': True,
        'push_wallet': True,
    },
    'privacy': {
        'profile_visibility': 'public',   # public | followers | private
        'show_online_status': True,
        'show_wallet_balance': False,
        'allow_team_invites': True,
        'allow_direct_messages': True,
    },
    'security': {
        'two_factor_enabled': False,
        'login_alerts': True,
    },
    'payments': {
        'default_currency': 'NGN',
        'auto_topup': False,
    },
    'language': 'en',
    'region': 'NG',
    'timezone': 'Africa/Lagos',
    # The first-run walkthrough. Kept on the account rather than in
    # localStorage so somebody who signs in on their phone after finishing it on
    # a laptop is not walked through the whole platform a second time.
    #
    # `version` is the part that earns its keep: when the walkthrough gains a
    # chapter for something genuinely new, bumping it here shows that chapter to
    # people who finished the old one, without showing them the rest again.
    'walkthrough': {
        'completed_at': None,
        'skipped': False,
        'version': 0,
        'chapters_seen': [],
    },
}

_SECTION_KEYS = ('notifications', 'privacy', 'security', 'payments')


def _merged(data):
    """Deep-merge stored data over DEFAULT_SETTINGS so every key is present."""
    out = {}
    for k, v in DEFAULT_SETTINGS.items():
        if isinstance(v, dict):
            out[k] = {**v, **(data.get(k) or {})}
        else:
            out[k] = data.get(k, v)
    # carry any extra top-level keys the FE may have saved
    for k, v in (data or {}).items():
        if k not in out:
            out[k] = v
    return out


def _get_or_create(user):
    obj, _ = UserSetting.objects.get_or_create(user=user, defaults={'data': {}})
    return obj


@api_view(['GET'])
def get_settings(request):
    user, err = _user_from_bearer(request)
    if err:
        return err
    obj = _get_or_create(user)
    return Response({
        'status': 'success',
        'data': {'settings': _merged(obj.data or {})},
        'message': 'Settings loaded.',
    })


def _update_section(request, section):
    user, err = _user_from_bearer(request)
    if err:
        return err
    obj = _get_or_create(user)
    data = dict(obj.data or {})
    incoming = request.data if isinstance(request.data, dict) else {}
    if section:
        data[section] = {**(data.get(section) or {}), **incoming}
    else:
        # top-level merge (language / region / timezone + any generic keys)
        data.update(incoming)
    obj.data = data
    obj.save(update_fields=['data', 'updated_at'])
    merged = _merged(data)
    payload = {'settings': merged}
    if section:
        payload[section] = merged[section]
    return Response({
        'status': 'success',
        'data': payload,
        'message': 'Settings updated.',
    })


@api_view(['POST'])
def update_settings(request):
    return _update_section(request, None)


@api_view(['POST'])
def update_notifications(request):
    return _update_section(request, 'notifications')


@api_view(['POST'])
def update_privacy(request):
    return _update_section(request, 'privacy')


@api_view(['POST'])
def update_security(request):
    return _update_section(request, 'security')


@api_view(['POST'])
def update_payments(request):
    return _update_section(request, 'payments')


# ---------------------------------------------------------------------------
# Account info (settings → Account panel posts to /user/<id>/update/)
# ---------------------------------------------------------------------------

_ACCOUNT_FIELDS = ('full_name', 'country', 'state')


@api_view(['POST'])
def update_user_account(request, user_id):
    user, err = _user_from_bearer(request)
    if err:
        return err
    # A user may only edit their own account here.
    if str(user.user_id) != str(user_id) and user_id not in ('me', str(user.pk)):
        return Response({'status': 'error', 'message': 'Forbidden.'},
                        status=status.HTTP_403_FORBIDDEN)
    changed = []
    for f in _ACCOUNT_FIELDS:
        if f in request.data and request.data.get(f) is not None:
            setattr(user, f, request.data.get(f))
            changed.append(f)
    if changed:
        user.save(update_fields=changed)
    return Response({
        'status': 'success',
        'data': {'updated': changed, 'user': {f: getattr(user, f) for f in _ACCOUNT_FIELDS}},
        'message': 'Account updated.',
    })


# ---------------------------------------------------------------------------
# Devices / sessions
# ---------------------------------------------------------------------------
# The auth model is single-session (one `login_session_token` per user), so the
# only live session is the current one. We surface it honestly as one device
# rather than faking a multi-device list.

def _current_device(user):
    return {
        'id': 'current',
        'label': 'This device',
        'is_current': True,
        'last_active': user.login_session_created_at.isoformat()
        if user.login_session_created_at else None,
        'created_at': user.login_session_created_at.isoformat()
        if user.login_session_created_at else None,
    }


@api_view(['GET'])
def list_devices(request):
    user, err = _user_from_bearer(request)
    if err:
        return err
    return Response({
        'status': 'success',
        'data': {'devices': [_current_device(user)]},
        'message': 'Active sessions.',
    })


@api_view(['POST'])
def revoke_device(request, device_id):
    user, err = _user_from_bearer(request)
    if err:
        return err
    # Revoking the current session = sign out (clears the token). Any other id is
    # a no-op success (single-session model - nothing else to revoke).
    if device_id in ('current', str(getattr(user, 'login_session_token', ''))):
        user.login_session_token = None
        user.login_session_created_at = None
        user.save(update_fields=['login_session_token', 'login_session_created_at'])
        return Response({
            'status': 'success',
            'data': {'devices': [], 'signed_out': True},
            'message': 'Signed out of this device.',
        })
    return Response({
        'status': 'success',
        'data': {'devices': [_current_device(user)]},
        'message': 'No such active session.',
    })


@api_view(['GET'])
def login_activity(request):
    """GET /setting/login-activity/ - the last ten sign-ins on this account.

    Real rows. The panel used to ship a fixed list of invented devices, which
    made the one thing this table is for - spotting a sign-in that was not
    yours - impossible.
    """
    from .emails import _short_agent
    from .models import LoginEvent

    user, err = _user_from_bearer(request)
    if err:
        return err

    events = LoginEvent.objects.filter(user=user)[:10]
    current_token_time = user.login_session_created_at

    rows = []
    for e in events:
        where = ', '.join(p for p in [e.city, e.country] if p)
        rows.append({
            'id': e.id,
            'device': _short_agent(e.user_agent),
            'browser': '',
            'ip': e.ip or '',
            'location': where or 'Unknown location',
            'time': e.created_at.isoformat(),
            'method': e.method,
            'current': bool(
                current_token_time
                and abs((e.created_at - current_token_time).total_seconds()) < 90
            ),
        })

    return Response({
        'status': 'success',
        'data': {'events': rows},
        'message': 'Recent sign-ins.',
    })


@api_view(['POST'])
def change_username(request):
    """POST /setting/username/ - change the handle, with the rules applied.

    The account panel had a Save next to the username that posted into
    update_user_account, which only ever wrote full_name, country and state - so
    the button appeared to work and changed nothing.
    """
    from .views_helpers import normalize_username, username_problem, username_taken

    user, err = _user_from_bearer(request)
    if err:
        return err

    raw = request.data.get('username')
    problem = username_problem(raw)
    if problem:
        return Response({'status': 'error', 'message': problem},
                        status=status.HTTP_400_BAD_REQUEST)

    name = normalize_username(raw)
    if name == normalize_username(user.username):
        return Response({'status': 'success', 'data': {'username': user.username},
                         'message': 'That is already your username.'})

    if username_taken(name, exclude_user=user):
        return Response({'status': 'error', 'message': 'That username is taken.'},
                        status=status.HTTP_409_CONFLICT)

    user.username = name
    user.save(update_fields=['username'])
    return Response({
        'status': 'success',
        'data': {'username': user.username},
        'message': 'Username updated.',
    })


@api_view(['GET'])
def account_overview(request):
    """GET /setting/account/ - the identity half of the settings page.

    Member ID and Date joined rendered as "-" because nothing served them.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    kyc = user.kyc_documents.order_by('-submitted_at').first() if hasattr(user, 'kyc_documents') else None
    wallet = getattr(user, 'wallet', None)
    profile = getattr(user, 'userprofile', None)

    return Response({
        'status': 'success',
        'data': {
            'account': {
                'user_id': user.user_id,
                'username': user.username,
                'email': user.email,
                'email_verified': user.is_active,
                'full_name': user.full_name,
                'date_joined': user.date_joined,
                'country': user.country,
                'state': user.state,
                # KYC is parked, so it reports parked rather than an eternal
                # "Pending" that nobody is working through.
                'kyc_status': 'parked' if kyc is None else kyc.status,
                'kyc_verified': bool(getattr(wallet, 'kyc_verified', False)),
                'penalty_points': getattr(profile, 'penalty_point', 0) if profile else 0,
                'is_founding_member': user.is_founding_member,
                'is_founder': getattr(user, 'is_founder', False),
                'founder_badge': bool(getattr(user, 'is_founder', False) and user.show_founder_badge),
                'founding_position': user.founding_position,
            },
        },
        'message': 'Account overview.',
    })

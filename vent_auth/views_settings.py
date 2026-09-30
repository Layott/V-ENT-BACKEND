"""Settings + device/session endpoints for the frontend /settings page.

Mounted at ROOT (no /auth prefix) because the FE calls `/setting/`, `/device/…`,
`/user/<id>/update/` directly. Auth is the standard Bearer login_session_token.
"""
import logging
import re

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import inputs
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
        # The same defaults privacy_of() reads with, so the panel shows what
        # is actually being obeyed.
        'allow_direct_messages': 'anyone',   # anyone | followers | nobody
        'show_email': False,
        'show_location': True,
        'show_birthday': False,
        'indexable': True,
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

    # How a bare date is ordered: '' for the reader's own language, or one of
    # 'DD/MM/YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD'.
    #
    # It has to be listed HERE and not only accepted on the way in, because
    # `_merged` builds its answer by walking DEFAULT_SETTINGS. A key absent
    # from this dict is stored perfectly well and then dropped on every read,
    # which is precisely what happened: the panel saved a date format, the API
    # never returned it, and nothing on the site could have honoured it even
    # if something had been looking.
    'date_format': '',
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
    data = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (data or {}).items()}
    # A value saved under an old name reads as the name that is obeyed.
    for section, names in LEGACY_NAMES.items():
        held = data if section is None else (data.get(section) or {})
        for old, (where, new) in names.items():
            target = data if where is None else data.setdefault(where, {})
            if old in held and new not in target:
                target[new] = held[old]
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
        'data': {'settings': _with_real_twofactor(_merged(obj.data or {}), user)},
        'message': 'Settings loaded.',
    })


def _with_real_twofactor(settings, user):
    """Report whether two-factor is ACTUALLY on, not a flag nobody writes.

    CEO, 7 September 2026, looking at the Security panel: "but i have auth
    already on my account."

    They were right, and the panel was wrong for everybody. `two_factor_enabled`
    existed only as a default in DEFAULT_SETTINGS: grep the whole app and it is
    written in exactly zero places. Meanwhile real enrolment lives in
    `UserTOTP.confirmed`, set by `views_account_security.twofactor_confirm`.

    So the row read "Disabled" no matter what, including for an account with a
    confirmed authenticator since 27 August.

    This is the same shape as the attendance bug on the door screen: two sources
    of truth for one fact, and the screen reading the one that is not the
    truth. The stored flag is now ignored entirely rather than kept in step,
    because a second copy that has to be synchronised is a second copy that
    eventually is not.
    """
    from .models import UserTOTP

    on = UserTOTP.objects.filter(user=user, confirmed=True).exists()
    security = dict(settings.get('security') or {})
    security['two_factor_enabled'] = on
    return {**settings, 'security': security}


def _currency(src, key):
    """A three letter currency code, like NGN, or '' for the site's default."""
    value = inputs.read_text(src, key, max_length=3).upper()
    if value and not re.fullmatch(r'[A-Z]{3}', value):
        raise inputs.BadInput(key, 'not a currency code')
    return value


def _walkthrough(src, key):
    raw = src.get(key)
    if not isinstance(raw, dict):
        raise inputs.BadInput(key, 'not a set of named values')
    seen = raw.get('chapters_seen') or []
    if not isinstance(seen, list) or len(seen) > 100:
        raise inputs.BadInput(key, 'too many chapters')
    return {
        'completed_at': inputs.read_text(raw, 'completed_at', max_length=40) or None,
        'skipped': inputs.read_bool(raw, 'skipped'),
        'version': inputs.read_int(raw, 'version', minimum=0, maximum=10000, default=0),
        'chapters_seen': [inputs.read_text({'c': c}, 'c', max_length=60) for c in seen],
    }


def _choice(*choices):
    return lambda src, key: inputs.read_choice(src, key, choices)


def _flag(src, key):
    return inputs.read_bool(src, key)


#: What each settings section may hold, and how each value is read (owner rule
#: R69). Until 30 September 2026 these endpoints stored every key a body
#: carried, of any size, into the account's settings. Anything not listed here
#: is dropped and named back in `ignored`, the way the notification grid already
#: worked. `two_factor_enabled` is absent on purpose: it is read from UserTOTP,
#: never from a request.
SECTION_FIELDS = {
    'privacy': {
        'profile_visibility': _choice('public', 'followers', 'private'),
        'allow_direct_messages': _choice('anyone', 'followers', 'nobody'),
        'show_online_status': _flag,
        'show_wallet_balance': _flag,
        'allow_team_invites': _flag,
        'show_email': _flag,
        'show_location': _flag,
        'show_birthday': _flag,
        'indexable': _flag,
    },
    'security': {
        'login_alerts': _flag,
    },
    'payments': {
        'default_method': _choice('wallet', 'card', 'bank'),
        'default_currency': _currency,
        'auto_topup': _flag,
    },
    None: {
        'language': _choice('en', 'fr', 'pt'),
        'region': lambda src, key: inputs.read_text(src, key, max_length=8),
        'timezone': inputs.read_timezone,
        'date_format': lambda src, key: inputs.read_choice(
            src, key, ('DD/MM/YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD'), default=''),
        'walkthrough': _walkthrough,
    },
}

#: Names a screen sent that the readers never looked for. The Privacy panel
#: saved `allow_dm_from` and `search_indexable` while privacy_of() read
#: `allow_direct_messages` and `indexable`, and the Language panel saved
#: `currency` at the top while money.js read payments.default_currency, so all
#: three switches were stored and obeyed by nothing. Old names still arrive
#: from a page loaded before the fix; they are written where they are read.
LEGACY_NAMES = {
    'privacy': {'allow_dm_from': ('privacy', 'allow_direct_messages'),
                'search_indexable': ('privacy', 'indexable')},
    None: {'currency': ('payments', 'default_currency')},
}


def clean_settings(section, incoming):
    """`{section: {key: value}}` for what may be stored, and the ignored names."""
    fields = SECTION_FIELDS[section]
    legacy = LEGACY_NAMES.get(section, {})
    out, ignored = {}, []
    for key in incoming:
        name = str(key)
        if name in fields:
            out.setdefault(section, {})[name] = fields[name](incoming, name)
        elif name in legacy:
            where, real = legacy[name]
            out.setdefault(where, {})[real] = SECTION_FIELDS[where][real](
                {real: incoming[key]}, real)
        else:
            ignored.append(name)
    return out, sorted(ignored)


def _update_section(request, section):
    user, err = _user_from_bearer(request)
    if err:
        return err
    obj = _get_or_create(user)
    data = dict(obj.data or {})
    incoming = request.data if isinstance(request.data, dict) else {}
    cleaned, ignored = clean_settings(section, incoming)
    for where, values in cleaned.items():
        if where is None:
            data.update(values)
        else:
            data[where] = {**(data.get(where) or {}), **values}
    obj.data = data
    obj.save(update_fields=['data', 'updated_at'])
    merged = _merged(data)
    payload = {'settings': merged, 'ignored': ignored}
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
    """Save notification switches: `<row>__<channel>` booleans only.

    Every switch is read at delivery now (CEO, 30 September 2026), so what is
    stored is exactly what the grid offers: known rows, the four channels, and
    never a locked one (account notices always reach the inbox and email).
    Anything else is dropped rather than stored to be misread later.
    """
    from . import notify_prefs
    user, err = _user_from_bearer(request)
    if err:
        return err
    incoming = request.data if isinstance(request.data, dict) else {}
    clean = {}
    locked = {r['id']: set(r.get('locked', ())) for r in notify_prefs.ROWS}
    for key, value in incoming.items():
        row_id, _, channel = str(key).partition('__')
        if (row_id in locked and channel in notify_prefs.CHANNELS
                and channel not in locked[row_id] and isinstance(value, bool)):
            clean[key] = value
    obj = _get_or_create(user)
    data = dict(obj.data or {})
    data['notifications'] = {**(data.get('notifications') or {}), **clean}
    obj.data = data
    obj.save(update_fields=['data', 'updated_at'])
    return Response({
        'status': 'success',
        'code': 'OK',
        'data': {'notifications': data['notifications'],
                 'grid': notify_prefs.matrix_for(user),
                 'ignored': sorted(set(map(str, incoming)) - set(clean))},
        'message': 'Settings updated.',
    })


@api_view(['GET'])
def notification_grid(request):
    """The rows and channels this person sees on Settings > Notifications, as set."""
    from . import notify_prefs, push
    user, err = _user_from_bearer(request)
    if err:
        return err
    grid = notify_prefs.matrix_for(user)
    grid['push'] = {'configured': push.configured(), 'public_key': push.public_key(),
                    'devices': user.push_subscriptions.count()}
    return Response({'status': 'success', 'code': 'OK', 'data': grid, 'message': 'Notification grid'})


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

#: The account fields this form edits, each with its column's length.
_ACCOUNT_FIELDS = {'full_name': 148, 'country': 256, 'state': 256}


@api_view(['POST'])
def update_user_account(request, user_id):
    user, err = _user_from_bearer(request)
    if err:
        return err
    # A user may only edit their own account here.
    if str(user.user_id) != str(user_id) and user_id not in ('me', str(user.pk)):
        return Response({ 'code': 'FORBIDDEN','status': 'error', 'message': 'Forbidden.'},
                        status=status.HTTP_403_FORBIDDEN)
    changed = []
    for f, most in _ACCOUNT_FIELDS.items():
        if f in request.data and request.data.get(f) is not None:
            setattr(user, f, inputs.read_text(request.data, f, max_length=most))
            changed.append(f)
    # Saying where you are settles it. The country stops being a guess the
    # moment somebody sets it themselves, so the screen stops offering to
    # correct something they have already corrected.
    if 'country' in changed and user.country_is_guess:
        user.country_is_guess = False
        changed.append('country_is_guess')
    if changed:
        user.save(update_fields=changed)
    return Response({
        'status': 'success',
        'data': {
            'updated': changed,
            'user': {f: getattr(user, f) for f in _ACCOUNT_FIELDS},
            'country_is_guess': user.country_is_guess,
        },
        'message': 'Account updated.',
    })


@api_view(['GET'])
def location_suggestion(request):
    """Where this sign-in looks like it is coming from, offered rather than set.

    The platform will not write a city onto somebody's profile from an address:
    Nigerian mobile data routes through a handful of carrier gateways, so a
    Lagos phone resolves to Ilorin, and asserting that is the platform saying
    something false about a person on their own profile.

    Offering it is a different thing entirely. The person can see the guess, and
    a single press accepts it - which is the only honest use of a city that
    might be right. Nothing is written here.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    from .geo import client_ip, locate

    ip = client_ip(request)
    country, city = locate(ip)

    return Response({
        'status': 'success',
        'data': {
            'country': country,
            'city': city,
            # What the account currently holds, so the screen can stay quiet
            # when the guess agrees with it.
            'current_country': user.country,
            'current_city': user.state,
            'country_is_guess': user.country_is_guess,
        },
        'message': 'Location suggestion retrieved.',
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
    from .views_helpers import normalize_username, username_problem, username_refusal

    user, err = _user_from_bearer(request)
    if err:
        return err

    raw = inputs.read_text(request.data, 'username', max_length=inputs.LONGEST_TEXT, strip=False)
    problem = username_problem(raw)
    if problem:
        return Response({'status': 'error', 'message': problem},
                        status=status.HTTP_400_BAD_REQUEST)

    name = normalize_username(raw)
    if name == normalize_username(user.username):
        return Response({'status': 'success', 'data': {'username': user.username},
                         'message': 'That is already your username.'})

    # Not just "taken". A name reserved before launch on the waitlist says so,
    # because that is a different situation and a different next step.
    refusal = username_refusal(name, email=user.email, exclude_user=user)
    if refusal:
        code, message, data = refusal
        return Response({'code': code, 'status': 'error', 'message': message,
                         'data': data, 'field': 'username'},
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
                # Not `is_active`. An account created by signing in with an
                # outside provider is active immediately and its address may
                # never have been confirmed - or, when the provider sends no
                # address at all, may be a synthetic one this platform invented.
                # Reporting that as Verified is how a made-up address came to
                # wear a green badge on the CEO's own settings page.
                'email_verified': bool(
                    user.is_active
                    and (user.email or '').strip()
                    and not (user.email or '').lower().endswith('.external')
                    and (user.signup_type or 'normal') in ('normal', 'google')
                ),
                'full_name': user.full_name,
                'date_joined': user.date_joined,
                'country': user.country,
                'state': user.state,
                # True when the country came from the sign-in address rather
                # than from the person. The screen says so and invites a
                # correction instead of presenting a guess as a fact.
                'country_is_guess': user.country_is_guess,
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


@api_view(['GET', 'POST'])
def birthday(request):
    """The signed-in person's own date of birth: read it, or set it once.

    Inbox 351 (29 September 2026): nothing on the site could set a date of
    birth, and the age rules for going together at events (inbox 305) read it.
    The only writer was an unrouted view that took a `user_id` from the body,
    so anybody could have set anybody's; it is gone.

    Set once: a date that could be changed at will would let somebody step past
    an age rule and back. A mistake is corrected by support.
    """
    import datetime as _dt

    from .models import UserProfile
    user, err = _user_from_bearer(request)
    if err:
        return err
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if request.method == 'GET':
        born = profile.date_of_birth
        return Response({'status': 'success', 'code': 'OK',
                         'data': {'date_of_birth': born.isoformat() if born else None,
                                  'locked': bool(born)},
                         'message': 'Date of birth'})
    if profile.date_of_birth:
        return Response({'status': 'error', 'code': 'BIRTHDAY_LOCKED', 'data': {},
                         'message': 'Your date of birth is already set. Contact support to correct it.'},
                        status=status.HTTP_409_CONFLICT)
    raw = inputs.read_text(request.data, 'date_of_birth', max_length=20)
    try:
        born = _dt.date.fromisoformat(raw)
    except ValueError:
        return Response({'status': 'error', 'code': 'BIRTHDAY_INVALID', 'data': {},
                         'message': 'Give the date as day, month and year.'},
                        status=status.HTTP_400_BAD_REQUEST)
    today = _dt.date.today()
    if born > today or born.year < today.year - 120:
        return Response({'status': 'error', 'code': 'BIRTHDAY_INVALID', 'data': {},
                         'message': 'That date of birth is not possible.'},
                        status=status.HTTP_400_BAD_REQUEST)
    profile.date_of_birth = born
    profile.save(update_fields=['date_of_birth'])
    return Response({'status': 'success', 'code': 'OK',
                     'data': {'date_of_birth': born.isoformat(), 'locked': True},
                     'message': 'Saved.'})

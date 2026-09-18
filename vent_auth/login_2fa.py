"""The second factor, asked for once, at the front door.

The console used to have a door of its own: an admin signed in to the site, and
then signed in again - password and authenticator code - to reach the dashboard.
Two doors for one person, and the second one asked for a password the session
had already proved a moment earlier.

So the challenge moved to the ordinary sign-in, and the console reads the same
session as the rest of the site. That is not the second factor being dropped;
it is the second factor becoming unavoidable:

  before   site login (password)  ->  console login (password + code)
  now      site login (password + code)  ->  console opens

An admin cannot sign in to anything without their authenticator now, where
before they could use the whole site with a password alone and only met the code
if they went looking for the dashboard.

Who is challenged:

  * anybody who turned on two-factor for their own account - which the Security
    page has been promising and the login never actually did
  * every admin, always, whether they turned it on or not

An admin with no authenticator set up is not let in and is not let past: they
are handed the secret and refused a session until they confirm a live code. That
is the "compulsory before they can do anything else" part, and it is enforced
here rather than by a screen that can be navigated around.

One secret per person. `UserTOTP` is the account's factor and every member has
one; migration 0054 folded the old console-only secrets into it so no existing
admin has to enrol again.
"""
import math
from datetime import timedelta

from django.core import signing
from django.utils import timezone

from . import totp as totp_lib
from .models import UserTOTP, Users

# The window between the password and the code. Long enough to open an
# authenticator app and read from it, short enough that a pending token left in
# a log or a history entry is worthless by the time anybody finds it.
PENDING_LOGIN_SALT = 'vent.login.2fa'
PENDING_LOGIN_MAX_AGE = 10 * 60


def is_admin(user):
    """Whether this account can reach the console at all."""
    return bool(
        getattr(user, 'is_superuser', False)
        or (getattr(user, 'is_staff', False) and getattr(user, 'admin_role', None))
    )


def factor_for(user):
    """The account's second factor, or None if it has never been started."""
    return UserTOTP.objects.filter(user=user).first()


def challenge_required(user):
    """Whether this sign-in has to produce a code.

    Two reasons, and they are different: a member chose this, an admin has it
    chosen for them.
    """
    factor = factor_for(user)
    if factor is not None and factor.confirmed:
        return True
    return is_admin(user)


def pending_payload(user):
    """What the sign-in screen needs to ask for the code.

    Carries the secret only when there is nothing confirmed yet, which is the
    one moment the person is ever shown it. An admin being promoted meets this
    on their next sign-in and cannot get past it.
    """
    factor, _ = UserTOTP.objects.get_or_create(
        user=user, defaults={'secret': totp_lib.generate_secret()}
    )

    payload = {
        'requires_2fa': True,
        'pending_token': signing.TimestampSigner(salt=PENDING_LOGIN_SALT).sign(
            str(user.user_id)),
        'expires_in': PENDING_LOGIN_MAX_AGE,
        'username': user.username,
        'email': user.email,
    }

    if not factor.confirmed:
        payload['enrollment_required'] = True
        payload['secret'] = factor.secret
        payload['provisioning_uri'] = totp_lib.provisioning_uri(
            factor.secret, user.email or user.username)
        # Said plainly, because "set up two-factor" reads as a suggestion and
        # this is not one.
        payload['enrollment_reason'] = (
            'admin' if is_admin(user) else 'account'
        )

    return payload


def user_from_pending(pending):
    """(user, error_code). The account a pending token stands for."""
    if not pending:
        return None, 'PENDING_TOKEN_REQUIRED'
    try:
        user_id = signing.TimestampSigner(salt=PENDING_LOGIN_SALT).unsign(
            pending, max_age=PENDING_LOGIN_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'SIGN_ATTEMPT_EXPIRED_START'
    except signing.BadSignature:
        return None, 'INVALID_SIGN_ATTEMPT'

    user = Users.objects.filter(user_id=user_id).first()
    if user is None:
        return None, 'INVALID_SIGN_ATTEMPT'
    return user, None


#: Wrong codes before the factor locks, and for how long.
CODE_TRIES = 10
CODE_LOCK_MINUTES = 15


def code_lock_minutes_left(factor, now=None):
    """Minutes until this factor takes codes again, or 0 if it does now."""
    now = now or timezone.now()
    until = factor.code_locked_until
    if until is None or until <= now:
        return 0
    return max(1, math.ceil((until - now).total_seconds() / 60))


def spend_code(user, code):
    """Check a code and burn its step. (ok, error_code).

    Confirms a first-time enrolment on the way through, so somebody setting up
    an authenticator proves it works in the same breath as using it.

    And counts. `last_used_step` stops one code being replayed; nothing
    stopped a million of them being tried. Ten wrong in a row lock the
    factor for fifteen minutes (`TWO_FACTOR_LOCKED`), whichever door asked:
    sign-in, the settings page, or a withdrawal. A right code clears the
    count. On the row rather than in a cache, so a restart does not hand
    out a fresh ten. Owner rule R59, 17 September 2026.
    """
    factor = factor_for(user)
    if factor is None:
        return False, 'TWO_FACTOR_NOT_SET_UP'

    now = timezone.now()
    if code_lock_minutes_left(factor, now):
        return False, 'TWO_FACTOR_LOCKED'

    matched = totp_lib.verify(factor.secret, code, factor.last_used_step)
    if matched is None:
        failures = int(factor.code_failures or 0) + 1
        if failures >= CODE_TRIES:
            factor.code_failures = 0
            factor.code_locked_until = now + timedelta(minutes=CODE_LOCK_MINUTES)
            factor.save(update_fields=['code_failures', 'code_locked_until'])
            return False, 'TWO_FACTOR_LOCKED'
        factor.code_failures = failures
        factor.save(update_fields=['code_failures'])
        return False, 'BAD_CODE'

    fields = ['last_used_step']
    if factor.code_failures or factor.code_locked_until is not None:
        factor.code_failures = 0
        factor.code_locked_until = None
        fields += ['code_failures', 'code_locked_until']
    factor.last_used_step = matched
    if not factor.confirmed:
        factor.confirmed = True
        factor.confirmed_at = timezone.now()
        fields += ['confirmed', 'confirmed_at']
    factor.save(update_fields=fields)
    return True, None

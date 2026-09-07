"""Turning two-factor on and off, for an ordinary member.

CEO, 7 September 2026: "please fix the 2fa." And before that, looking at the
Security panel: "but i have auth already on my account."

Both sentences describe the same thing. The LOGIN half of two-factor has been
built and correct for a while: `login_2fa.challenge_required` asks for a code,
`pending_payload` walks somebody through first-time enrolment, and `spend_code`
confirms the factor on the way past. It works, and every admin uses it daily.

What did not exist was any way for a member to TURN IT ON. The Security panel's
switch called `onSave({two_factor_enabled: true})`, which wrote a boolean into
the settings JSON. No secret was generated, the QR in the modal was decorative,
no code was ever checked, and no `UserTOTP` row was created. The switch then
read back as Disabled - correctly, because nothing was enrolled - so it looked
like the toggle was broken when the toggle was the only part working.

Three endpoints, and the shape matters:

    start    creates the factor UNCONFIRMED and hands back the secret. An
             unconfirmed factor guards nothing, so this is safe to call and
             safe to abandon halfway.
    confirm  takes a code, proves the authenticator is really set up, and only
             then does the account start demanding one. Enrolling without
             proving it works is how somebody locks themselves out.
    disable  takes a code, because turning protection OFF is exactly the thing
             an attacker with a stolen session wants to do first.

`start` deliberately refuses to re-roll a CONFIRMED secret. Somebody pressing
the button again out of curiosity must not invalidate the authenticator they
are relying on.
"""
from django.utils import timezone as tz
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import login_2fa, totp as totp_lib
from .models import UserTOTP

SESSION_TIMEOUT_MINUTES = 120


def _error(message, code, http_status):
    return Response({'status': 'error', 'data': {}, 'message': message,
                     'code': code}, status=http_status)


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _viewer(request):
    """The signed-in account, or (None, response). Mirrors the house pattern."""
    from .models import Users

    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None, _error('Sign in to change this.',
                            'AUTHENTICATION_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first()
    if user is None:
        return None, _error('Sign in to change this.',
                            'AUTHENTICATION_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


@api_view(['POST'])
def two_factor_start(request):
    """POST /auth/2fa/start/ - begin enrolment, hand back the secret.

    Idempotent while unconfirmed: pressing it twice gives the same secret, so
    somebody who closed the modal and reopened it is not told their
    authenticator is suddenly wrong.
    """
    user, err = _viewer(request)
    if err:
        return err

    factor = UserTOTP.objects.filter(user=user).first()
    if factor is not None and factor.confirmed:
        # Never re-roll a working secret. Turning it off is a separate,
        # deliberate act that costs a code.
        return _error('Two-factor is already on for this account.',
                      'TWO_FACTOR_ALREADY_ON', status.HTTP_400_BAD_REQUEST)

    if factor is None:
        factor = UserTOTP.objects.create(user=user,
                                         secret=totp_lib.generate_secret())

    return _ok({
        'secret': factor.secret,
        'provisioning_uri': totp_lib.provisioning_uri(
            factor.secret, user.email or user.username, issuer='V-ENT'),
        'confirmed': False,
    }, 'Scan the code, then enter what your app shows.')


@api_view(['POST'])
def two_factor_confirm(request):
    """POST /auth/2fa/confirm/ - prove the authenticator works, then switch on.

    The account does not start demanding codes until this succeeds. That
    ordering is the whole safety of the feature: enrolling first and proving
    later is how somebody ends up locked out of their own account by a phone
    whose clock is wrong.
    """
    user, err = _viewer(request)
    if err:
        return err

    code = str(request.data.get('code') or '').strip()
    if not code:
        return _error('Enter the code from your authenticator app.',
                      'CODE_REQUIRED', status.HTTP_400_BAD_REQUEST)

    factor = UserTOTP.objects.filter(user=user).first()
    if factor is None:
        return _error('Start setting up two-factor first.',
                      'TWO_FACTOR_NOT_STARTED', status.HTTP_400_BAD_REQUEST)

    # Through the same code path sign-in uses, so a code that works here works
    # there. Two implementations of the same check is how they drift.
    ok, problem = login_2fa.spend_code(user, code)
    if not ok:
        return _error('That code is not right. Check your app and try again.',
                      problem or 'BAD_CODE', status.HTTP_400_BAD_REQUEST)

    factor.refresh_from_db()
    return _ok({'confirmed': bool(factor.confirmed)},
               'Two-factor is on. You will be asked for a code when you sign in.')


@api_view(['POST'])
def two_factor_disable(request):
    """POST /auth/2fa/disable/ - turn it off, with a current code.

    A code, not just a session. Somebody who has stolen a signed-in session and
    can switch the protection off without the phone has taken nothing away from
    the attacker, which is the same as not having it.

    An admin cannot turn it off at all: `challenge_required` forces a code for
    them whatever this row says, so removing the factor would mean their next
    sign-in silently enrols a NEW one and shows them the secret. Refused by
    name rather than half-working.
    """
    user, err = _viewer(request)
    if err:
        return err

    if login_2fa.is_admin(user):
        return _error('Console accounts must keep two-factor on.',
                      'TWO_FACTOR_REQUIRED_FOR_ADMIN', status.HTTP_403_FORBIDDEN)

    factor = UserTOTP.objects.filter(user=user).first()
    if factor is None or not factor.confirmed:
        # Nothing to take away. Reported as success rather than an error,
        # because the caller asked for a state and that state is what they
        # have; failing here would make an idempotent action look broken.
        return _ok({'confirmed': False}, 'Two-factor is off.')

    code = str(request.data.get('code') or '').strip()
    if not code:
        return _error('Enter a current code to turn two-factor off.',
                      'CODE_REQUIRED', status.HTTP_400_BAD_REQUEST)

    ok, problem = login_2fa.spend_code(user, code)
    if not ok:
        return _error('That code is not right.', problem or 'BAD_CODE',
                      status.HTTP_400_BAD_REQUEST)

    factor.delete()
    return _ok({'confirmed': False}, 'Two-factor is off.')


@api_view(['GET'])
def two_factor_status(request):
    """GET /auth/2fa/status/ - what the Security panel draws.

    Reports real enrolment, never the stored settings flag. The flag is what
    said Enabled for accounts with nothing set up.
    """
    user, err = _viewer(request)
    if err:
        return err

    factor = UserTOTP.objects.filter(user=user).first()
    return _ok({
        'enabled': bool(factor and factor.confirmed),
        'started': factor is not None,
        # An admin cannot switch it off, and the screen should say so rather
        # than offering a control that answers 403.
        'required': login_2fa.is_admin(user),
        'confirmed_at': factor.confirmed_at if factor else None,
    }, 'Two-factor status.')

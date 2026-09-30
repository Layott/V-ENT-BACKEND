"""A bot check on the public forms, verified here and nowhere else (owner rule R68).

Signup, feedback and both waitlists could be filled by a script as fast as it
could post. The usual answer is a captcha service, and every one of them
(Turnstile, hCaptcha, reCAPTCHA) is somebody else's server, which the VPS brief
rules out: everything runs on the box.

So a proof of work, in the shape ALTCHA published:

1. `GET /auth/challenge/` hands out a puzzle: a random salt carrying its own
   expiry, the SHA-256 of that salt plus a secret number below `MAX_NUMBER`,
   and an HMAC of that digest under a key only this server holds.
2. The browser tries numbers until the digest matches. A person's phone
   spends a fraction of a second on it once; a script posting ten thousand
   signups spends ten thousand of them.
3. The form sends the answer back. `verify_challenge` checks the signature
   (so the puzzle was ours), the expiry, the arithmetic, and that the puzzle
   has never been used before (a row in UsedChallenge, so it holds across
   every gunicorn worker, which an in-memory cache would not).

A hidden `website` field rides along as a honeypot: a person never sees it, a
form-filling script fills it, and a filled one is refused.

Off under the test runner, the same way the rate limiter is
(`BOT_CHECK_ENABLED`); `tests_bot_check` switches it on.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import security_log
from .throttle import limited

#: The largest number the browser may have to try. About 50,000 hashes on
#: average: well under a second in a phone browser, and the whole cost of the
#: check for a person.
MAX_NUMBER = 100_000
LIFETIME_SECONDS = 20 * 60
HONEYPOT_FIELD = 'website'


def _key():
    return hashlib.sha256(('vent.bot-check:' + settings.SECRET_KEY).encode()).digest()


def _sign(challenge):
    return hmac.new(_key(), challenge.encode(), hashlib.sha256).hexdigest()


def _digest(salt, number):
    return hashlib.sha256(('%s%d' % (salt, number)).encode()).hexdigest()


def issue(now=None):
    """A fresh puzzle, as the browser needs it."""
    expires = int(now if now is not None else time.time()) + LIFETIME_SECONDS
    salt = '%s?expires=%d' % (secrets.token_hex(12), expires)
    number = secrets.randbelow(MAX_NUMBER)
    challenge = _digest(salt, number)
    return {'algorithm': 'SHA-256', 'challenge': challenge, 'salt': salt,
            'signature': _sign(challenge), 'maxnumber': MAX_NUMBER}


def solve(puzzle):
    """What a browser does, for tests and for anybody reading this."""
    for number in range(puzzle['maxnumber'] + 1):
        if _digest(puzzle['salt'], number) == puzzle['challenge']:
            return dict(puzzle, number=number)
    return None


def _parse(raw):
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw or len(raw) > 2000:
        return None
    for text in (raw, None):
        try:
            if text is None:
                text = base64.b64decode(raw + '=' * (-len(raw) % 4)).decode()
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except (ValueError, UnicodeDecodeError):
            continue
    return None


def problem(raw, honeypot='', now=None):
    """None when the answer is good, otherwise why not (a word, for the log)."""
    if honeypot:
        return 'honeypot'
    data = _parse(raw)
    if data is None:
        return 'missing'
    salt = str(data.get('salt') or '')
    challenge = str(data.get('challenge') or '')
    signature = str(data.get('signature') or '')
    try:
        number = int(data.get('number'))
    except (TypeError, ValueError):
        return 'unreadable'
    if not (salt and challenge and signature) or not 0 <= number <= MAX_NUMBER:
        return 'unreadable'
    if not hmac.compare_digest(_sign(challenge), signature):
        return 'not ours'
    try:
        expires = int(salt.rsplit('?expires=', 1)[1])
    except (IndexError, ValueError):
        return 'unreadable'
    if expires < int(now if now is not None else time.time()):
        return 'expired'
    if not hmac.compare_digest(_digest(salt, number), challenge):
        return 'wrong answer'

    from .models import UsedChallenge
    UsedChallenge.objects.filter(expires_at__lt=timezone.now()).delete()
    try:
        # Its own savepoint: a second use raises IntegrityError, and without
        # this the caller's whole transaction would be left unusable.
        with db_transaction.atomic():
            UsedChallenge.objects.create(
                digest=challenge,
                expires_at=timezone.now() + timedelta(seconds=LIFETIME_SECONDS))
    except IntegrityError:
        return 'used twice'
    return None


def verify_challenge(request):
    """None when the request carries a good answer, otherwise the refusal.

    A door calls it before it stores or emails anything:

        refused = bot_check.verify_challenge(request)
        if refused:
            return refused
    """
    if not getattr(settings, 'BOT_CHECK_ENABLED', True):
        return None
    why = problem(request.data.get('challenge'),
                  honeypot=str(request.data.get(HONEYPOT_FIELD) or '').strip())
    if why is None:
        return None
    security_log.refused('bot_check', request, why=why.replace(' ', '_'))
    return Response({
        'status': 'error', 'code': 'BOT_CHECK_FAILED',
        'message': 'We could not confirm this came from a person. Reload the page and try again.',
    }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@limited('bot-challenge', 60)
def challenge(request):
    """GET /auth/challenge/ - a puzzle for the next form this browser sends."""
    response = Response({'status': 'success', 'data': issue(), 'message': 'Challenge'})
    # One per form, never reused, never cached by anything in between.
    response['Cache-Control'] = 'no-store'
    return response

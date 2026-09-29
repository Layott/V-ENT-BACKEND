"""What a person is told when something fails that was not their doing.

CEO, 29 September 2026: "I DONT LIKE THAT USERS ARE SEEING THIS KIND OF ERROR:
The payment could not be started: Format is Authorization Bearer [secret key]
... users see proper messages for errors instead of code."

The exception, or the gateway's own sentence, goes to the log, where it is
useful. The person gets a plain sentence and a code the screen translates
(api.<CODE> in all three dictionaries). `tools/check-raw-errors.py` fails on
any view that sends exception or gateway text instead.
"""
import logging

from rest_framework import status
from rest_framework.response import Response

log = logging.getLogger('vent.errors')


def _answer(code, message, http):
    return Response({'status': 'error', 'code': code, 'data': {}, 'message': message}, status=http)


def server_error(exc=None):
    """Our fault, not theirs: logged in full, said plainly."""
    log.exception('server error: %r', exc)
    return _answer('SERVER_ERROR',
                   'Something went wrong on our side. Nothing was changed. Try again in a moment.',
                   status.HTTP_500_INTERNAL_SERVER_ERROR)


def bad_input(exc=None):
    """Something sent could not be read (a date, a number)."""
    log.info('unreadable input: %r', exc)
    return _answer('INVALID_INPUT',
                   'Some of the details could not be read. Check the dates and numbers and try again.',
                   status.HTTP_400_BAD_REQUEST)


GATEWAY_REFUSED = ('The payment could not be started. Nothing was charged. '
                   'Try again, or choose another way to pay.')
GATEWAY_DOWN = 'The payment service could not be reached. Nothing was charged. Try again in a moment.'


def gateway_refused(exc=None):
    """A payment gateway said no. Its own words are for the log only."""
    log.warning('payment gateway refused: %s', exc)
    return _answer('PAYMENT_REFUSED', GATEWAY_REFUSED, status.HTTP_502_BAD_GATEWAY)


def gateway_down(exc=None):
    log.warning('payment gateway unreachable: %s', exc)
    return _answer('PAYMENT_GATEWAY', GATEWAY_DOWN, status.HTTP_502_BAD_GATEWAY)

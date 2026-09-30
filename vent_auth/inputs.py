"""Reading what a request sent: type, length and format, refused with a code.

Owner rule R69: every body, query and form is parsed on the server before it
touches the database. Until 30 September 2026 most views read
`request.data.get('x')` and handed it straight to the ORM, so a word where a
number belonged (`{"id": "abc"}`) reached `filter(id=...)`, raised ValueError
and answered a 500, and a 40,000 character "name" was stored as sent.

Each reader takes the source (`request.data`, `request.GET`,
`request.query_params`) and a key, and returns a clean value or raises
`BadInput`. `exception_handler` below turns that into the site's envelope:

    {"status": "error", "code": "INVALID_INPUT", "field": "id", "message": ...}

so a view needs no try/except of its own; the frontend already translates
INVALID_INPUT through apiMessage.

    from vent_auth import inputs
    one = inputs.read_int(request.data, 'id', minimum=1)
    name = inputs.read_text(request.data, 'name', max_length=120, required=True)
"""
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger(__name__)

#: Longest free text anything on the site stores in one field. A description,
#: a rules document or a post body; anything longer is not a field, it is an
#: attack or a paste gone wrong.
LONGEST_TEXT = 20000

_MISSING = object()


class BadInput(ValueError):
    """One field that could not be read. `field` names it for the screen.

    A ValueError on purpose: a view that already wraps a conversion in
    `except (TypeError, ValueError)` with its own message keeps that message,
    and one that catches nothing gets INVALID_INPUT from exception_handler
    instead of the 500 an uncaught int('abc') used to be."""

    def __init__(self, field, reason='unreadable'):
        super().__init__(f'{field}: {reason}')
        self.field = field
        self.reason = reason


def _raw(src, key):
    if src is None:
        return _MISSING
    try:
        value = src.get(key, _MISSING)
    except AttributeError:
        return _MISSING
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return _MISSING
    return value


def read_int(src, key, *, required=False, default=None, minimum=None, maximum=None):
    """A whole number. `True`, `1.5` and `"12abc"` are refused, not rounded."""
    raw = _raw(src, key)
    if raw is _MISSING:
        if required:
            raise BadInput(key, 'required')
        return default
    if isinstance(raw, bool):
        raise BadInput(key, 'not a whole number')
    try:
        if isinstance(raw, float):
            if not raw.is_integer():
                raise ValueError(raw)
            value = int(raw)
        else:
            value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise BadInput(key, 'not a whole number') from None
    if minimum is not None and value < minimum:
        raise BadInput(key, 'too small')
    if maximum is not None and value > maximum:
        raise BadInput(key, 'too large')
    return value


def finite_float(raw, field='value'):
    """float(raw) for a value already taken out of a request, refusing NaN and
    infinity: float() accepts both, NaN then passes every `< 0` and `> 100`
    check and is stored, and int(float('inf')) raises OverflowError."""
    if isinstance(raw, bool):
        raise BadInput(field, 'not a number')
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise BadInput(field, 'not a number') from None
    if value != value or value in (float('inf'), float('-inf')):
        raise BadInput(field, 'not a number')
    return value


def read_float(src, key, *, required=False, default=None, minimum=None, maximum=None):
    """A finite float, for the places that already did float arithmetic."""
    raw = _raw(src, key)
    if raw is _MISSING:
        if required:
            raise BadInput(key, 'required')
        return default
    if isinstance(raw, bool):
        raise BadInput(key, 'not a number')
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise BadInput(key, 'not a number') from None
    if value != value or value in (float('inf'), float('-inf')):
        raise BadInput(key, 'not a number')
    if minimum is not None and value < minimum:
        raise BadInput(key, 'too small')
    if maximum is not None and value > maximum:
        raise BadInput(key, 'too large')
    return value


def read_decimal(src, key, *, required=False, default=None, minimum=None,
                 maximum=None, places=2):
    """An amount. Refuses NaN and infinity, which Decimal would otherwise take."""
    raw = _raw(src, key)
    if raw is _MISSING:
        if required:
            raise BadInput(key, 'required')
        return default
    if isinstance(raw, bool):
        raise BadInput(key, 'not a number')
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise BadInput(key, 'not a number') from None
    if not value.is_finite():
        raise BadInput(key, 'not a number')
    if minimum is not None and value < Decimal(str(minimum)):
        raise BadInput(key, 'too small')
    if maximum is not None and value > Decimal(str(maximum)):
        raise BadInput(key, 'too large')
    return value.quantize(Decimal(1).scaleb(-places)) if places is not None else value


def read_text(src, key, *, max_length, required=False, default='', strip=True):
    """Text of a known greatest length. Control bytes other than tab and newline are refused."""
    raw = _raw(src, key)
    if raw is _MISSING:
        if required:
            raise BadInput(key, 'required')
        return default
    if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
        raise BadInput(key, 'not text')
    value = str(raw)
    if strip:
        value = value.strip()
    if len(value) > min(max_length, LONGEST_TEXT):
        raise BadInput(key, 'too long')
    if any(ord(c) < 32 and c not in '\t\n\r' for c in value):
        raise BadInput(key, 'unreadable characters')
    return value


def read_choice(src, key, choices, *, required=False, default=None):
    """One of a fixed set. The set is the allowlist; nothing else passes."""
    raw = _raw(src, key)
    if raw is _MISSING:
        if required:
            raise BadInput(key, 'required')
        return default
    value = str(raw).strip()
    if value not in choices:
        raise BadInput(key, 'not one of the choices')
    return value


def read_bool(src, key, *, default=False):
    """true/false in the shapes a form, a query string or JSON send them."""
    raw = _raw(src, key)
    if raw is _MISSING:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('0', 'false', 'no', 'off'):
        return False
    raise BadInput(key, 'not true or false')


def read_datetime(src, key, *, required=False, default=None):
    """An ISO instant with its zone (the frontend sends localInputToISO)."""
    raw = _raw(src, key)
    if raw is _MISSING:
        if required:
            raise BadInput(key, 'required')
        return default
    if isinstance(raw, datetime):
        return raw
    value = parse_datetime(str(raw).strip()) if isinstance(raw, str) else None
    if value is None:
        raise BadInput(key, 'not a date and time')
    return value


def read_ids(src, key, *, max_items=500):
    """A list of whole numbers, as JSON sends it or as a comma list in a query."""
    raw = _raw(src, key)
    if raw is _MISSING:
        return []
    if isinstance(raw, str):
        raw = [p for p in raw.split(',') if p.strip()]
    if not isinstance(raw, (list, tuple)):
        raise BadInput(key, 'not a list')
    if len(raw) > max_items:
        raise BadInput(key, 'too many')
    out = []
    for item in raw:
        if isinstance(item, bool):
            raise BadInput(key, 'not a whole number')
        try:
            out.append(int(str(item).strip()))
        except (TypeError, ValueError):
            raise BadInput(key, 'not a whole number') from None
    return out


def read_json(src, key, *, max_chars=4000, required=False, default=None):
    """A small value stored as sent into a JSON column: text, a number,
    true/false, a list or an object. Measured as JSON, so a nested object
    cannot hide its size, and NaN or anything JSON cannot hold is refused.

    For fields whose shape depends on something else (an entry requirement's
    answer is an ID for one kind and a set of accounts for another), where the
    shape is checked downstream and the size is the part nothing else holds.
    """
    raw = _raw(src, key)
    if raw is _MISSING or raw in ({}, []):
        if required:
            raise BadInput(key, 'required')
        return default
    bounded_json(raw, key, max_chars=max_chars)
    if isinstance(raw, str):
        return read_text(src, key, max_length=max_chars)
    return raw


def bounded_json(value, key, *, max_chars=4000):
    """`value`, if it is something JSON can hold within `max_chars`.

    For a value already taken out of a request in a shape of its own (social
    links arrive as a list, an object, or the JSON text of either when a form
    is multipart)."""
    try:
        text = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise BadInput(key, 'unreadable') from None
    if len(text) > max_chars:
        raise BadInput(key, 'too long')
    return value


def refusal(exc):
    """The envelope for one unreadable field."""
    log.info('refused input %s (%s)', exc.field, exc.reason)
    return Response({
        'status': 'error',
        'code': 'INVALID_INPUT',
        'field': exc.field,
        'message': 'Some of the details could not be read. Check the dates and numbers and try again.',
    }, status=status.HTTP_400_BAD_REQUEST)


def exception_handler(exc, context):
    """DRF's handler, with BadInput answered as INVALID_INPUT naming the field."""
    if isinstance(exc, BadInput):
        return refusal(exc)
    return drf_exception_handler(exc, context)

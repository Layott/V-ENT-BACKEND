"""Reading a Tournament's own columns out of a request (inbox 398, owner rule R69).

Create and edit both set columns straight from the body: a title longer than
its 148 characters reached MySQL and failed there as a 500, a start time that
was not a date did the same at save, and a number typed as NaN passed Decimal()
and then raised at the first comparison. Each reader here takes its limits from
the column itself, so the two doors cannot bound a field differently and a
column widened in a migration is read at its new width the same day.
"""
from datetime import datetime, time
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from vent_auth import inputs

from .models import Tournament

#: Whole-number columns hold a signed 32 bit value on MySQL.
MOST_WHOLE = 2_000_000_000


def _field(name):
    return Tournament._meta.get_field(name)


def read_column(data, name, *, column=None, default=None):
    """The value for a text or date column, read for its type and length.

    `column` names the model field when the request uses another name for it
    (the wizard sends `reg_start_date_and_time` for `registration_opens_at`).
    Absent or blank gives `default`.
    """
    field = _field(column or name)
    if isinstance(field, models.DateTimeField):
        return read_when(data, name, default=default)
    most = field.max_length or inputs.LONGEST_TEXT
    value = inputs.read_text(data, name, max_length=most, strip=not isinstance(field, models.TextField))
    return value if value else default


def blank_for(name):
    """What an emptied field stores: no instant for a date, '' for text."""
    return None if isinstance(_field(name), models.DateTimeField) else ''


def read_when(data, name, *, default=None):
    """An instant: an ISO date and time, or a bare date meaning its midnight.

    A time with no zone is taken in the server's zone, as Django would take it
    at save; the wizard sends every date through localInputToISO, with a zone.
    """
    raw = inputs.read_text(data, name, max_length=40)
    if not raw:
        return default
    try:
        when = parse_datetime(raw)
        if when is None:
            day = parse_date(raw)
            when = datetime.combine(day, time.min) if day else None
    except ValueError:
        when = None
    if when is None:
        raise inputs.BadInput(name, 'not a date and time')
    if timezone.is_naive(when):
        when = timezone.make_aware(when)
    return when


def read_number(data, name, *, column=None, default=None, minimum=0):
    """A whole number or an amount for a numeric column, at or above `minimum`."""
    field = _field(column or name)
    if isinstance(field, models.DecimalField):
        most = Decimal(10) ** (field.max_digits - field.decimal_places) - 1
        return inputs.read_decimal(data, name, minimum=minimum, maximum=most,
                                   places=field.decimal_places, default=default)
    return inputs.read_int(data, name, minimum=minimum, maximum=MOST_WHOLE, default=default)

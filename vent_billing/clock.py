"""When the next charge lands, worked out once so it cannot be worked out twice.

Recurring billing is mostly a calendar problem wearing a money costume, and the
calendar is where it goes wrong. Two dates break every naive implementation:

1. **Somebody subscribes on the 31st.** Adding "one month" to 31 January has no
   answer. `timedelta(days=30)` walks the renewal backwards through the year -
   a January subscriber ends up charged on the 26th by December - and clamping
   to the 28th permanently loses the anchor, so a January subscriber is billed
   on the 28th for ever afterwards. Both are wrong in a way nobody notices for
   months.

2. **A yearly subscription taken out on 29 February.** There is no 29 February
   in the next three years. Clamping to the 28th is right; FORGETTING that the
   anchor was the 29th is not, because 2032 has a 29th and the subscription
   should go back to it.

So the period is never computed from the last charge date. It is computed from
the ANCHOR - the day of the month (and, for a yearly plan, the month) the
subscription was first charged on - which is stored once and never moves. The
last charge date only decides which month we are stepping into.

    31 Jan -> 28 Feb -> 31 Mar -> 30 Apr -> 31 May
              ^ clamped                     ^ anchor restored, because the
                                              anchor is 31 and May has one

Everything here is pure: no database, no `now()`, no timezone lookups beyond
preserving the one it was handed. That is deliberate, because it is the half
that has to be tested against dates nobody will be around for.
"""
import calendar
from datetime import datetime

MONTHLY = 'monthly'
YEARLY = 'yearly'
INTERVALS = (MONTHLY, YEARLY)

#: How long a plan change or a cancellation is described in, for copy that has
#: to name the unit. The interface translates these; they are never shown raw.
INTERVAL_DAYS_HINT = {MONTHLY: 30, YEARLY: 365}


def days_in_month(year, month):
    return calendar.monthrange(year, month)[1]


def anchor_of(moment):
    """The (day, month) a subscription's whole future is measured from.

    Taken from the first charge and then never recomputed. A subscription that
    is clamped to 28 February in a non-leap year still carries anchor 29, which
    is what lets it return to the 29th four years later.
    """
    return moment.day, moment.month


def add_period(start, interval, anchor_day, anchor_month=None):
    """The end of the period that begins at `start`.

    `start` keeps its time of day and its tzinfo: a subscription charged at
    09:15 stays charged at 09:15, so a renewal never drifts across a day
    boundary by accumulating rounding.
    """
    if interval not in INTERVALS:
        raise ValueError('unknown interval: %r' % (interval,))

    if interval == MONTHLY:
        year = start.year + (1 if start.month == 12 else 0)
        month = 1 if start.month == 12 else start.month + 1
    else:
        year = start.year + 1
        # A yearly plan keeps its month as well as its day, so a subscription
        # taken out in March is always charged in March even if a renewal was
        # once clamped.
        month = anchor_month or start.month

    day = min(anchor_day or start.day, days_in_month(year, month))
    return start.replace(year=year, month=month, day=day)


def periods_between(start, end, interval, anchor_day, anchor_month=None, limit=64):
    """Every period boundary from `start` up to and including the first one at
    or after `end`.

    Used by the renewal command when a subscription has been missed for longer
    than one period - a box that was off for six weeks, or a command nobody ran
    over a holiday. Without this, catching up would charge one period and leave
    the subscription still overdue, and the next run would do the same.

    `limit` is a guard rather than a policy: a subscription that has been
    unattended for five years is a thing to look at, not a thing to charge
    sixty times in one pass.
    """
    out = []
    cursor = start
    while cursor < end and len(out) < limit:
        cursor = add_period(cursor, interval, anchor_day, anchor_month)
        out.append(cursor)
    return out


def parse_moment(value):
    """An ISO string or a datetime, for `--now` on the renewal command.

    Only used by the command line, so a bad value is an error the operator sees
    immediately rather than something that silently means "today".
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))

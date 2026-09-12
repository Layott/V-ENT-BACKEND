"""Who actually came, counted one way for the whole platform.

CEO, 7 September 2026:

    "someone that admiotted themslves doesnt mean they are checkedin by the
    organizer, just means that maybe they want to show and announce to their
    followers and frienfds on the platform that they are at this event. The
    ticket scanning or ticket code entering is still the baseline for a proper
    check in that the person actally came ... the organizers should be able to
    selecet options like total checkins, actual verified and self verified."

## The bug this file exists to correct

Attendance was one number. `admitted` counted every ticket with
`status='checked_in'`, and self check-in writes exactly that status, so a person
who tapped "I am here" from their sofa was counted as having walked through the
door. The door screen then showed that total as **Checked in**, which is the
number an organiser reads as "this many people came".

It is not that number. It cannot be: nothing about a self check-in is evidence
of anybody being anywhere. Reporting it as attendance overstates the door, and
overstating the door is exactly the failure the Rivalry Series post-mortem was
about, only in the opposite direction.

## The three numbers, and which one means attendance

| | |
|---|---|
| `verified` | A member of staff scanned a QR code or typed the ticket code. **This is attendance.** It is the only one backed by somebody being physically present at a gate |
| `self_reported` | The holder tapped "I am here" in the app. A social act, addressed to their followers, and a useful signal of intent. Never evidence |
| `total` | Both together. Honest only when it is labelled as both |

`verified` is deliberately the harder-to-forge one and deliberately the one a
headline number is built from. If a screen shows a single figure, it shows
`verified`.

## Why this is a module rather than three lines in a view

Because it was three lines in a view, twice, and the two disagreed: the door
summary called the door-only figure `at_the_door` and the metrics endpoint
called the same thing `at_door`, while both called the inflated total
`checked_in`. A concept counted in two places drifts, and this one drifted into
the number an organiser makes decisions on.

`SELF_GATE` also lived in two files with the same literal in each, which is the
same fault one level down.
"""
from django.db.models import Count, Q

#: The gate a self check-in records. One definition, imported by everything.
#: It was written out separately in `views_door` and `views_self_check_in`, and
#: a marker that means "not evidence" is precisely the string that must not be
#: allowed to differ between the thing that writes it and the thing that reads
#: it.
SELF_GATE = 'self'

#: The ticket status a check-in of either kind writes.
CHECKED_IN = 'checked_in'


def verified_q():
    """Somebody was scanned in or had their code typed by staff. Attendance."""
    return Q(status=CHECKED_IN) & ~Q(checked_in_gate=SELF_GATE)


def self_reported_q():
    """The holder said they are here. Not evidence, and never counted as it."""
    return Q(status=CHECKED_IN, checked_in_gate=SELF_GATE)


def total_q():
    """Both kinds. Only ever shown under a label that says it is both."""
    return Q(status=CHECKED_IN)


def counts(tickets):
    """The three numbers for a ticket queryset, in one query.

    Returns `{'verified', 'self_reported', 'total'}`. Every screen that reports
    attendance reads these keys, so renaming one renames it everywhere rather
    than leaving a second surface saying something different.
    """
    row = tickets.aggregate(
        verified=Count('id', filter=verified_q()),
        self_reported=Count('id', filter=self_reported_q()),
        total=Count('id', filter=total_q()),
    )
    return {
        'verified': row['verified'] or 0,
        'self_reported': row['self_reported'] or 0,
        'total': row['total'] or 0,
    }


def annotations(prefix=''):
    """The same three, as annotation kwargs for a grouped query.

    `prefix` lets a caller that already has a `total` column keep both, e.g.
    `annotations('attend_')`.
    """
    return {
        f'{prefix}verified': Count('id', filter=verified_q()),
        f'{prefix}self_reported': Count('id', filter=self_reported_q()),
        f'{prefix}total': Count('id', filter=total_q()),
    }

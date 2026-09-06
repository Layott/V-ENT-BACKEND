# -*- coding: utf-8 -*-
"""Asking about a ticket without admitting anybody.

## What happened, and why this file exists

RIVALRY SERIES SEASON 2 ran on 4 and 5 September 2026 and recorded **one
check-in out of 1422 tickets**. Everybody else was admitted by eye, so nobody
can say how many people came.

The API was never at fault. The door page filtered a list downloaded once when
the page opened, so a ticket bought after that moment could not be found, and
the page said "Nobody matches that search" without a single request leaving the
phone. On the Saturday the server saw two requests from the door device all day,
both of them sign-ins.

There was also no way to ask the server a question. The only endpoint that could
confirm a code was `check-in/`, which admits the person as a side effect of
answering. So a steward who wanted to check a name before letting somebody
through had to let them through to find out.

This file is the missing half:

- `ticket_lookup` answers about one code and admits nobody.
- `door_search` takes what a steward actually types - part of a name, an email,
  a phone number, a code - and answers from the whole table rather than from
  whatever the browser downloaded an hour ago.
- `door_summary` is the headcount: how many came, by gate, by day, by tier, and
  how many admitted themselves.
- `door_lookups` reads back what the door asked for, which is the thing that
  could not be answered afterwards this time.

## Everything here refuses to admit

No route in this file writes `status`, `checked_in_at`, `checked_in_gate` or
`checked_in_by`. Admitting somebody stays in `views_tickets.check_in_ticket`
and `views_self_check_in.self_check_in`, which are the two doors, and there is
a test that fails if a lookup ever changes a ticket.
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Event, Ticket, DoorLookup
from .permissions import may_work_the_door, may_run_event
from .views_tickets import _authenticate, _error, _ok, _holder

# A steward types into a phone at a gate. Two characters would match half the
# room and cost a full table scan to say so, which is the opposite of useful.
MIN_TERM = 2

# What one search may return. A term matching hundreds of people is a term that
# needs narrowing, and sending hundreds of rows to a phone is how the original
# fault happened.
MAX_MATCHES = 25

# How many past lookups the console shows without being asked for more.
LOOKUP_PAGE = 100


def _event_or_error(event_id):
    """The event, addressed by slug or by number.

    Both, because the door is reached from `/events/<slug>/attendees` while
    older links and the console still carry the id. A route that took only one
    of the two answered 404 for the other, which is a fault this codebase has
    already shipped twice.
    """
    event = (Event.objects.filter(event_id=int(event_id)).first()
             if str(event_id).isdigit()
             else Event.objects.filter(slug=str(event_id)).first())
    if event is None:
        return None, _error('Event not found.', 'NOT_FOUND',
                            status.HTTP_404_NOT_FOUND)
    return event, None


def _row(ticket):
    """One ticket, in the shape the door reads.

    Deliberately the same key names `event_attendees` uses. A steward looking at
    a row that came from a search and a row that came from the list must not be
    able to tell which is which, and the page must not need two mappers.
    """
    return {
        'code': ticket.code,
        'username': ticket.user.username if ticket.user_id else '',
        'guest': not ticket.user_id,
        'attendee_name': (ticket.attendee_name
                          or (ticket.user.full_name or ticket.user.username
                              if ticket.user_id else '')
                          or ticket.attendee_email),
        'attendee_email': ticket.attendee_email,
        'attendee_phone': ticket.attendee_phone,
        'tier': ticket.tier.name if ticket.tier_id else '',
        'tier_day': (ticket.tier.day.isoformat()
                     if ticket.tier_id and ticket.tier.day else None),
        'tier_day_label': ticket.tier.day_label or '' if ticket.tier_id else '',
        'status': ticket.status,
        'purchased_at': ticket.purchased_at,
        'checked_in_at': ticket.checked_in_at,
        'checked_in_gate': ticket.checked_in_gate,
        'checked_in_by': (ticket.checked_in_by.username
                          if ticket.checked_in_by_id else ''),
        # Whether this person admitted themselves. Derived in one place rather
        # than every screen comparing a magic string of its own.
        'self_check_in': ticket.checked_in_gate == SELF_GATE,
    }


# Imported late rather than at module load: `views_self_check_in` imports from
# `.models` too, and pulling it in at the top makes a cycle through urls.py.
SELF_GATE = 'self'


@api_view(['GET'])
def ticket_lookup(request, code):
    """One code, everything known about it, and nobody let in.

    This is what `check-in/` should have been sitting beside from the start. A
    steward reading a code off a phone screen wants to know whether it is real,
    whether it is for today, and whether it has already been used, and none of
    those questions should cost the answer to the others.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    ticket = (Ticket.objects
              .select_related('event', 'tier', 'user', 'checked_in_by')
              .filter(code=str(code).upper()).first())
    if ticket is None:
        return _error('No ticket with that code.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    if not may_work_the_door(user, ticket.event):
        return _error('Only the event organizer or their door staff can look '
                      'tickets up.', 'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    gate = str(request.query_params.get('gate') or '').strip()[:60]
    DoorLookup.objects.create(event=ticket.event, term=str(code)[:120],
                              asked_by=user, matched=1, ticket=ticket,
                              gate=gate)

    row = _row(ticket)
    # Whether this ticket would be admitted right now, worked out here so the
    # phone does not have to reimplement the day rule and get it subtly wrong.
    tier_day = ticket.tier.day if ticket.tier_id else None
    today = timezone.localdate()
    return _ok(
        {
            'ticket': row,
            'holder': _holder(ticket),
            'already_checked_in': ticket.status == 'checked_in',
            'wrong_day': bool(tier_day and tier_day != today),
            'admissible': (ticket.status == 'valid'
                           and not (tier_day and tier_day != today)),
        },
        'Ticket found.',
    )


@api_view(['GET'])
def door_search(request, event_id):
    """What a steward typed, answered from the table rather than from a snapshot.

    The local list stays: it is what makes the common case instant and what
    keeps the door working when the venue's connection does not. This is the
    fallback for a miss, which on 4 and 5 September was every ticket bought
    after the page had loaded.

    Admits nobody, which is why Search may call it freely. The previous shape -
    fall back to `check-in/` - would have meant typing a name let that person
    in, which is not a search.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event, event_error = _event_or_error(event_id)
    if event_error:
        return event_error

    if not may_work_the_door(user, event):
        return _error('Only the event organizer or their door staff can search '
                      'the attendee list.', 'NOT_ORGANIZER',
                      status.HTTP_403_FORBIDDEN)

    term = str(request.query_params.get('q') or '').strip()
    if len(term) < MIN_TERM:
        return _error('Type at least %d characters.' % MIN_TERM,
                      'TERM_TOO_SHORT', status.HTTP_400_BAD_REQUEST,
                      extra={'min': MIN_TERM})

    gate = str(request.query_params.get('gate') or '').strip()[:60]

    # A code is typed in full and read off a screen, so it matches exactly and
    # case does not count. Everything else is a fragment of something longer.
    matches = (Ticket.objects
               .filter(event=event)
               .filter(Q(code__iexact=term)
                       | Q(code__icontains=term)
                       | Q(attendee_name__icontains=term)
                       | Q(attendee_email__icontains=term)
                       | Q(attendee_phone__icontains=term)
                       | Q(user__username__icontains=term)
                       | Q(user__full_name__icontains=term))
               .select_related('tier', 'user', 'checked_in_by')
               .order_by('attendee_name', 'code'))

    total = matches.count()
    rows = [_row(t) for t in matches[:MAX_MATCHES]]

    DoorLookup.objects.create(
        event=event, term=term[:120], asked_by=user, matched=total,
        # The ticket, but only when the term picked out exactly one. Two
        # matches means the door still had a decision to make, and recording
        # one of them as the answer would be a fiction.
        ticket=(matches.first() if total == 1 else None),
        gate=gate,
    )

    return _ok(
        {
            'attendees': rows,
            'count': total,
            'truncated': total > MAX_MATCHES,
            'term': term,
        },
        'Search complete.',
    )


@api_view(['GET'])
def door_summary(request, event_id):
    """How many people actually came.

    CEO, 6 September 2026: "because we could not check in poeople we cannot
    count how many people actually showed up for the event."

    One endpoint rather than the console counting rows it happens to hold,
    because the door page holds a page of the list and the answer must not
    depend on how much of it was downloaded. Every number here is counted in
    the database.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event, event_error = _event_or_error(event_id)
    if event_error:
        return event_error

    if not may_work_the_door(user, event):
        return _error('Only the event organizer or their door staff can read '
                      'the door numbers.', 'NOT_ORGANIZER',
                      status.HTTP_403_FORBIDDEN)

    tickets = Ticket.objects.filter(event=event).select_related('tier')

    sold = tickets.exclude(status__in=('refunded', 'cancelled')).count()
    admitted = tickets.filter(status='checked_in')

    by_gate = {}
    by_day = {}
    by_tier = {}
    self_admitted = 0
    for ticket in admitted.select_related('tier'):
        gate = ticket.checked_in_gate or ''
        if gate == SELF_GATE:
            self_admitted += 1
        by_gate[gate or 'unnamed'] = by_gate.get(gate or 'unnamed', 0) + 1

        day = (ticket.tier.day.isoformat()
               if ticket.tier_id and ticket.tier.day else 'any')
        by_day[day] = by_day.get(day, 0) + 1

        name = ticket.tier.name if ticket.tier_id else ''
        by_tier[name] = by_tier.get(name, 0) + 1

    admitted_count = admitted.count()
    return _ok(
        {
            'sold': sold,
            'admitted': admitted_count,
            # Sold minus admitted, stated rather than left to be worked out.
            # It is the number an organiser is actually asking for when they
            # ask how the door went.
            'not_admitted': max(sold - admitted_count, 0),
            'at_the_door': admitted_count - self_admitted,
            'self_admitted': self_admitted,
            'by_gate': by_gate,
            'by_day': by_day,
            'by_tier': by_tier,
            'refunded': tickets.filter(status='refunded').count(),
            'cancelled': tickets.filter(status='cancelled').count(),
        },
        'Door summary.',
    )


@api_view(['GET'])
def door_lookups(request, event_id):
    """What the door asked for, newest first.

    This exists because on 6 September the question "which names were searched
    for at the gate" had no answer at all. It has one now, and an organiser can
    read it while the door is still open: a run of terms matching nothing is a
    door in trouble, and that is worth seeing at 19:00 rather than in a
    post-mortem.

    Organiser rather than door staff. A steward needs to admit people, not to
    read what every other steward has been typing.
    """
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    event, event_error = _event_or_error(event_id)
    if event_error:
        return event_error

    if not may_run_event(user, event):
        return _error('Only the event organizer can read the door log.',
                      'NOT_ORGANIZER', status.HTTP_403_FORBIDDEN)

    try:
        limit = min(max(int(request.query_params.get('limit') or LOOKUP_PAGE), 1), 500)
    except (TypeError, ValueError):
        limit = LOOKUP_PAGE

    only_misses = str(request.query_params.get('misses') or '').lower() in ('1', 'true', 'yes')

    rows = DoorLookup.objects.filter(event=event).select_related('asked_by', 'ticket')
    if only_misses:
        rows = rows.filter(matched=0)

    total = rows.count()
    return _ok(
        {
            'lookups': [
                {
                    'term': row.term,
                    'matched': row.matched,
                    'code': row.ticket.code if row.ticket_id else '',
                    'asked_by': row.asked_by.username if row.asked_by_id else '',
                    'gate': row.gate,
                    'at': row.at,
                }
                for row in rows[:limit]
            ],
            'count': total,
            'misses': DoorLookup.objects.filter(event=event, matched=0).count(),
        },
        'Door lookups.',
    )

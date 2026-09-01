"""How many tickets one email address may hold, per type, per day, or at all.

The event carried one number. An organiser running a three day convention that
sells Standard and VIP on each day has six ticket types and three days, and one
number cannot say what they mean: "one VIP each, four Standard, and no more than
four on any single day" is three different rules, and forcing them through one
field applies the strictest of them to everything.

CEO, 1 September: "if there is several different days or types of ticket, the
option to set this for each ticket type and day should be available. for all
tickets and days at once also."

So three scopes, and they **stack rather than override**. A purchase must
satisfy every rule that has a number. That is the only reading that does not
surprise somebody: an organiser who sets "one VIP each" and then sets a day
limit has not quietly cancelled the first rule, and the buyer holding a VIP is
still refused a second one.

`None` at any scope means that scope sets no rule. It does not mean unlimited,
because the wider scopes still apply. The screen says so in those words.

Enforcement lives in `checkout.room_for_email`, in one place, so the guest
checkout and the signed-in one cannot disagree about what the organiser set.
"""
from datetime import date

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import EventDayLimit


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _event_and_permission(request, event_id):
    user, err = actor_from_request(request)
    if err:
        return None, None, err

    from .views import _event_by_ref
    event = _event_by_ref(event_id)
    if event is None:
        return None, None, _err('Event not found.', 'NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)
    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return None, None, _err(
            'Only the event organizer can change its ticket limits.',
            'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)
    return event, user, None


def _read_limit(raw, field):
    """A limit, or None for "no rule at this scope".

    Returns `(value, error)`. Zero is refused rather than read as "none",
    because a zero typed into a box that says "how many" means the organiser
    thinks they are allowing none, and silently turning that into "no limit" is
    the opposite of what they pressed.
    """
    if raw in (None, ''):
        return None, None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return None, _err('That has to be a number.', 'INVALID_NUMBER',
                          field=field)
    if limit < 1:
        return None, _err(
            'A limit is at least one ticket. Clear the box for no limit.',
            'INVALID_NUMBER', field=field)
    if limit > 999:
        return None, _err('That is higher than any limit needs to be.',
                          'INVALID_NUMBER', field=field)
    return limit, None


def _days_of(event):
    """Every day this event sells a ticket for, from the types themselves.

    A day is not a record anywhere: it is `TicketTier.day`, carried by the types
    that admit you on it. Reading it from the types rather than storing a second
    list is what stops the two disagreeing when a type is added or its date
    corrected.
    """
    seen = event.ticket_tiers.exclude(day=None).values_list('day', flat=True)
    return sorted(set(seen))


def _serialize(event):
    days = _days_of(event)
    set_days = {row.day: row.max_tickets_per_email
                for row in event.day_limits.all()}
    return {
        # Across the whole event, whatever type and whatever day. This is the
        # "all tickets and days at once" rule, and it is the field that already
        # existed, so an event that had one keeps it.
        'event': event.max_tickets_per_email,
        'tiers': [{
            'id': t.id,
            'name': t.name,
            'day': t.day.isoformat() if t.day else None,
            'day_label': t.day_label or '',
            'max_tickets_per_email': t.max_tickets_per_email,
        } for t in event.ticket_tiers.all()],
        'days': [{
            'day': d.isoformat(),
            # The label the organiser gave that date on any of its types, so
            # the screen can say "Day 2" the way the rest of the product does
            # rather than printing a bare date nobody chose.
            'label': next((t.day_label for t in event.ticket_tiers.all()
                           if t.day == d and t.day_label), ''),
            'max_tickets_per_email': set_days.get(d),
        } for d in days],
        # Whether a per-day rule can be set at all. An event whose types carry
        # no date has one day, and offering a per-day section for it is offering
        # an empty box.
        'has_days': bool(days),
    }


@api_view(['GET', 'POST'])
def email_limits(request, event_id):
    """GET/POST /event/<id>/email-limits/

    GET returns every scope at once, including the types and days that exist,
    so the screen can draw the whole rule set without three requests.

    POST is partial in the same way the tier endpoint is: a scope that is not
    mentioned is not touched, and a scope sent empty is cleared. Sending
    `all_tiers` or `all_days` stamps one number across every type or every day,
    which is the difference between setting a rule and typing it six times.
    """
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    if request.method == 'GET':
        return _ok(_serialize(event), 'Ticket limits')

    changed = []

    # ------------------------------------------------- across the whole event
    if 'event' in request.data:
        limit, err = _read_limit(request.data.get('event'), 'event')
        if err:
            return err
        event.max_tickets_per_email = limit
        event.save(update_fields=['max_tickets_per_email'])
        changed.append('event')

    # ------------------------------------------------------------- every type
    if 'all_tiers' in request.data:
        limit, err = _read_limit(request.data.get('all_tiers'), 'all_tiers')
        if err:
            return err
        event.ticket_tiers.all().update(max_tickets_per_email=limit)
        changed.append('all_tiers')

    # --------------------------------------------------------- one type each
    tiers = request.data.get('tiers')
    if isinstance(tiers, dict):
        for raw_id, raw_limit in tiers.items():
            tier = event.ticket_tiers.filter(pk=str(raw_id)).first()
            if tier is None:
                return _err('No such ticket type on this event.', 'NOT_FOUND',
                            status.HTTP_404_NOT_FOUND, field='tiers')
            limit, err = _read_limit(raw_limit, 'tiers')
            if err:
                return err
            tier.max_tickets_per_email = limit
            tier.save(update_fields=['max_tickets_per_email'])
        changed.append('tiers')

    # -------------------------------------------------------------- every day
    if 'all_days' in request.data:
        limit, err = _read_limit(request.data.get('all_days'), 'all_days')
        if err:
            return err
        days = _days_of(event)
        if limit is None:
            EventDayLimit.objects.filter(event=event).delete()
        else:
            for day in days:
                EventDayLimit.objects.update_or_create(
                    event=event, day=day,
                    defaults={'max_tickets_per_email': limit})
        changed.append('all_days')

    # ---------------------------------------------------------- one day each
    days = request.data.get('days')
    if isinstance(days, dict):
        known = set(_days_of(event))
        for raw_day, raw_limit in days.items():
            try:
                day = date.fromisoformat(str(raw_day))
            except ValueError:
                return _err('That is not a date.', 'INVALID_DATE', field='days')
            # A limit on a day the event does not sell for would never be
            # checked against anything, and would sit in the list looking like
            # a rule that is running.
            if day not in known:
                return _err('This event has no tickets for that day.',
                            'NOT_FOUND', status.HTTP_404_NOT_FOUND, field='days')
            limit, err = _read_limit(raw_limit, 'days')
            if err:
                return err
            if limit is None:
                EventDayLimit.objects.filter(event=event, day=day).delete()
            else:
                EventDayLimit.objects.update_or_create(
                    event=event, day=day,
                    defaults={'max_tickets_per_email': limit})
        changed.append('days')

    if not changed:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    return _ok(_serialize(event), 'Ticket limits saved.')

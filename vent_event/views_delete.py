"""Deleting an event, and putting it back.

The twin of `vent_tournament/views_delete.py`, written in the same pass and to
the same rules, because an organiser deleting an event and an organiser
deleting a tournament is one job with two nouns. Building the careful half on
one side and leaving the other is the fault recorded in the "fix the model, not
the record" rule, and `tools/check-parity.py` now holds a row for this pair.

Who may: the event's creator, the owner of the organisation it belongs to, or
an admin holding `cancel_event`. Deliberately NOT everybody who may run the
event - `permissions.py` has said since it was written that RUN is "everything
except deleting the event", and door staff added for one day should not be able
to remove it.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth import softdelete

from .models import Event


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'data': data or {}, 'message': message,
                     'code': code}, status=http_status)


def _find(reference, include_deleted=False):
    """An event by slug or id. `Event.objects` cannot see a deleted one."""
    manager = Event.all_objects if include_deleted else Event.objects
    raw = str(reference or '').strip()
    if not raw:
        return None
    if raw.isdigit():
        found = manager.filter(event_id=int(raw)).first()
        if found is not None:
            return found
    return manager.filter(slug=raw).first()


def _may_delete(user, event):
    if event.creator_id == user.user_id:
        return True
    org = getattr(event, 'organization', None)
    if org is not None and org.org_owner_id == user.user_id:
        return True
    return may_override(user, 'cancel_event')


def _counts(event):
    """Seats sold, split by whether money changed hands.

    A comp ticket at zero costs nobody anything, so an event with only comps
    asks a second time. One paid ticket refuses outright: the seat belongs to
    somebody who is owed a refund, and cancelling is the path that does that.
    """
    live = event.tickets.filter(status__in=('valid', 'checked_in'))
    paid = live.filter(price_vc__gt=0).count()
    return paid, live.filter(price_vc=0).count()


@api_view(['POST', 'DELETE'])
def delete_event(request, event_id):
    """POST /event/<ref>/delete/ - take it out of sight, keep the row."""
    user, err = actor_from_request(request)
    if err:
        return err

    event = _find(event_id)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if not _may_delete(user, event):
        return _err('This is not your event to delete.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    paid, unpaid = _counts(event)
    confirmed = bool(request.data.get('confirm'))
    refusal = softdelete.deletion_guard(paid, unpaid, confirmed)
    if refusal:
        code, http_status, payload = refusal
        if code == 'PAID_ENTRANTS':
            return _err(
                'Tickets have been paid for. Cancel the event first, which '
                'refunds the holders, and then it can be deleted.',
                code, http_status, payload)
        return _err(
            'Tickets have been issued for this event. Confirm to delete it and '
            'those tickets with it.',
            code, http_status, payload)

    softdelete.mark_deleted(event, by=user,
                            reason=str(request.data.get('reason') or ''))
    return _ok({'event_id': event.pk, 'slug': event.slug,
                **softdelete.deletion_row(event)}, 'Event deleted.')


@api_view(['POST'])
def restore_event(request, event_id):
    """POST /event/<ref>/restore/ - an admin puts it back."""
    user, err = actor_from_request(request)
    if err:
        return err
    if not may_override(user, 'cancel_event'):
        return _err('Only an admin can restore a deleted event.', 'ADMIN_ONLY',
                    status.HTTP_403_FORBIDDEN)

    event = _find(event_id, include_deleted=True)
    if event is None:
        return _err('No such event.', 'EVENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not softdelete.is_deleted(event):
        return _err('That event is not deleted.', 'NOT_DELETED')

    softdelete.restore(event)
    return _ok({'event_id': event.pk, 'slug': event.slug}, 'Event restored.')

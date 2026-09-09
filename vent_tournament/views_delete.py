"""Deleting a tournament, and putting it back.

CEO, 8 September 2026: "there should be a way for peopl to delete events, of
course it is a soft delete thata dmins should be able to restore or still
check". Written against the model rather than the screen that reported it, so
the event half in `vent_event/views_delete.py` is the same code with different
nouns and `tools/check-parity.py` holds the two together.

What made this worth doing carefully: the only deletion that existed was
`delete-draft/`, which called `tournament.delete()` and destroyed the row. A
draft that took an hour to fill in was gone with no way back, and a published
tournament could not be deleted at all - the organiser was told to ask an admin
to cancel it, which is a different thing and leaves it on every listing.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth import softdelete

from . import lookup
from .models import Tournament


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'data': data or {}, 'message': message,
                     'code': code}, status=http_status)


def _find(reference, include_deleted=False):
    """A tournament by slug or id.

    `lookup.find` reads `Tournament.objects`, which cannot see a deleted row,
    so restoring one has to ask the unfiltered manager. That is deliberate:
    every ordinary view loses sight of a deleted tournament without any of them
    having to remember to filter.
    """
    if not include_deleted:
        return lookup.find(reference)

    raw = str(reference or '').strip()
    if not raw:
        return None
    if raw.isdigit():
        found = Tournament.all_objects.filter(tournament_id=int(raw)).first()
        if found is not None:
            return found
    return Tournament.all_objects.filter(slug=raw).first()


def _counts(tournament):
    """Who is in it, split by whether they paid to be there.

    An entry fee of zero cannot have been paid, so a free tournament with fifty
    entrants asks a second time rather than refusing - and a paid one refuses,
    whatever box was ticked.
    """
    live = tournament.registrations.filter(status__in=('pending', 'confirmed'))
    total = live.count()
    paid = total if (tournament.entry_fee_price or 0) > 0 else 0
    return paid, (0 if paid else total)


@api_view(['POST', 'DELETE'])
def delete_tournament(request, tournament_id):
    """POST /tournament/<ref>/delete/ - take it out of sight, keep the row."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = _find(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    is_owner = tournament.tournament_creator_id == user.user_id
    if not (is_owner or may_override(user, 'cancel_tournament')):
        return _err('This is not your tournament to delete.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    paid, unpaid = _counts(tournament)
    confirmed = bool(request.data.get('confirm'))
    refusal = softdelete.deletion_guard(paid, unpaid, confirmed)
    if refusal:
        code, http_status, payload = refusal
        if code == 'PAID_ENTRANTS':
            return _err(
                'People have paid to enter this tournament. Cancel it first, '
                'which refunds them, and then it can be deleted.',
                code, http_status, payload)
        return _err(
            'There are entrants registered. Confirm to delete the tournament '
            'and their registrations with it.',
            code, http_status, payload)

    softdelete.mark_deleted(tournament, by=user,
                            reason=str(request.data.get('reason') or ''))
    return _ok({'tournament_id': tournament.pk, 'slug': tournament.slug,
                **softdelete.deletion_row(tournament)},
               'Tournament deleted.')


@api_view(['POST'])
def restore_tournament(request, tournament_id):
    """POST /tournament/<ref>/restore/ - an admin puts it back.

    Admin only, and deliberately not the organiser's: somebody who deleted
    their own tournament by accident has an admin to ask, and an organiser who
    can undelete at will can also delete to hide something and put it back
    afterwards.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    if not may_override(user, 'cancel_tournament'):
        return _err('Only an admin can restore a deleted tournament.',
                    'ADMIN_ONLY', status.HTTP_403_FORBIDDEN)

    tournament = _find(tournament_id, include_deleted=True)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not softdelete.is_deleted(tournament):
        return _err('That tournament is not deleted.', 'NOT_DELETED')

    softdelete.restore(tournament)
    return _ok({'tournament_id': tournament.pk, 'slug': tournament.slug},
               'Tournament restored.')

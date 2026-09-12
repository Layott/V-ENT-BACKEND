"""Deleting something without destroying it.

CEO, 8 September 2026, on a screenshot of My Tournaments:

    "there should be a way for peopl to delete events, of course it is a soft
    delete thata dmins should be able to restore or still check"

Two halves, and the second is the one that is usually skipped.

**It leaves.** The organiser presses delete and it is gone from every listing,
every search, the sitemap, and its own address. Nothing about it half renders.

**It survives.** The row stays, marked, with who deleted it and when. Tickets
sold against it still resolve, the money still reconciles, and an admin can put
it back exactly where it was.

The mechanism is a manager rather than a filter written at each read site,
because there are 122 places that query a tournament or an event and a rule
that has to be remembered at 122 sites is a rule that holds at 121.
`Model.objects` cannot see deleted rows at all; `Model.all_objects` sees
everything and is what the admin console and the restore path use.

`base_manager_name` is set to the unfiltered manager on purpose: a ticket must
still be able to reach the event it was sold for, or a deleted event would
break every row that points at it rather than simply disappearing from view.
"""
from django.db import models
from django.utils import timezone


class LiveManager(models.Manager):
    """Everything that has not been deleted."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class DeletedManager(models.Manager):
    """Only what has been deleted. What the admin console lists."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=False)


def soft_delete_fields():
    """The three columns, so both models carry exactly the same ones."""
    return {
        'deleted_at': models.DateTimeField(null=True, blank=True, db_index=True),
        'deleted_by': models.ForeignKey(
            'vent_auth.Users', on_delete=models.SET_NULL, null=True, blank=True,
            related_name='+'),
        'deleted_reason': models.CharField(max_length=200, blank=True, default=''),
    }


def mark_deleted(obj, by=None, reason=''):
    """Take it out of sight, keeping everything about it.

    `update_fields` names all three, so a model whose `save()` recomputes a slug
    cannot quietly drop one of them.
    """
    obj.deleted_at = timezone.now()
    obj.deleted_by = by
    obj.deleted_reason = (reason or '')[:200]
    obj.save(update_fields=['deleted_at', 'deleted_by', 'deleted_reason'])
    return obj


def restore(obj):
    """Put it back exactly where it was.

    The reason is cleared with the deletion, because a reason left behind on a
    live record reads as a note about the live record.
    """
    obj.deleted_at = None
    obj.deleted_by = None
    obj.deleted_reason = ''
    obj.save(update_fields=['deleted_at', 'deleted_by', 'deleted_reason'])
    return obj


def is_deleted(obj):
    return getattr(obj, 'deleted_at', None) is not None


def deletion_row(obj):
    """What an admin needs to decide whether to restore it."""
    by = getattr(obj, 'deleted_by', None)
    return {
        'deleted_at': obj.deleted_at,
        'deleted_by': by.username if by else None,
        'deleted_reason': obj.deleted_reason or '',
    }


# ---------------------------------------------------------------------------
# When a delete is refused, and when it asks a second time
# ---------------------------------------------------------------------------
#
# One rule for both models, because a tournament with entrants and an event
# with ticket holders are the same situation with different nouns, and this
# repo's most repeated fault is building the careful half on one of the two.

def deletion_guard(paid, unpaid, confirmed=False):
    """`None` to go ahead, or `(code, http_status, payload)` to refuse.

    Three outcomes, and the ordering is the point:

    * **Somebody paid.** Refused outright, whatever is confirmed. Deleting it
      would take a seat away from somebody who is owed money, and the platform
      already has a path that does this properly: cancel, which refunds, and
      then delete what is left. A confirmation box is not consent from the
      person who paid.
    * **People are in it but nothing was paid.** Asks a second time and says
      how many, because "delete" and "delete along with 34 registered players"
      are different decisions and only one of them was read.
    * **Empty.** Goes ahead.
    """
    if paid:
        return ('PAID_ENTRANTS', 409, {'paid': paid, 'unpaid': unpaid})
    if unpaid and not confirmed:
        return ('CONFIRM_REQUIRED', 409, {'paid': 0, 'unpaid': unpaid})
    return None

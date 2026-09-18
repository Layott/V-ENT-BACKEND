"""One answer to "which event is this", for every door under /event/<ref>/.

Sixteen views each carried their own copy of "by slug, or by id", and none of
them read the slug history. The public page followed a rename (18 September
2026: `walk-con-wizard-17-sept` became `-18-sept` and the event page moved
with it), while the console at the old address answered "Could not load this
event" because all twenty of its endpoints answered 404. The rule is that
every address an event has ever had keeps working, and a rule kept in sixteen
places is kept in none of them.

The public page still answers `{status: 'moved'}` so the browser rewrites its
address (see `resolve_or_redirect`). Everything else resolves the old slug
transparently: a sub-resource has no address of its own to rewrite, and the
page that called it will already have been moved by the public answer.
"""
from .models import Event


def event_by_ref(ref, queryset=None, **extra):
    """The event at this address, or None.

    `ref` is a slug, an id shared before the slug rule, or a slug the event
    used to have. `extra` narrows the queryset (`is_active=True` on public
    doors), `queryset` replaces it (`Event.all_objects` to reach a deleted
    one), and both apply on the history path too: a retired slug never
    reaches an event the caller may not see by its live slug.
    """
    from vent_auth.slugs import find_by_ref

    ref = str(ref or '').strip()
    if not ref:
        return None
    rows = (queryset if queryset is not None else Event.objects).filter(**extra)
    return find_by_ref(ref, entity_type='event', id_field='event_id',
                       model=Event, queryset=rows)

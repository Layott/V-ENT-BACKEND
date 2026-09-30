"""POST /event/<ref>/site/ - the event's website settings (inbox 360).

Read by everybody through the event payload (`site` in serialize_event_detail);
written here by the people who run the event. The shape and the rules live in
vent_event/site.py.
"""
from rest_framework.decorators import api_view

from . import site
from .views_tiers import _event_and_permission, _ok


@api_view(['POST'])
def event_site(request, event_id):
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err
    changed = site.apply(event, request.data)
    if changed:
        event.save(update_fields=changed)
    return _ok(site.public(event), 'Website saved.')

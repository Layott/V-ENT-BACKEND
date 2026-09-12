# -*- coding: utf-8 -*-
"""Who may run an event, answered in one place.

CEO, 4 September 2026: "URGENTLY I NEED TO BBE ABLE TO ADD PEOPLE TO MANAGE AN
EVENT", then "but there is no way to add events to an organization", then the
decision: "dont ulock it, instead do a way to add events to an oganizatio and
the whe ou add people to your organization you can then have them manage events
ad they will see everyrthing".

So there are two ways somebody reaches an event they did not create:

1. **Through the organisation it belongs to.** An owner, an admin, or a manager
   whose scopes include `events` runs every event that organisation holds, with
   no per event row anywhere. This is the answer for a team: add the person to
   the organisation once and they see everything it runs.
2. **Named on the one event.** `EventManager` is still there for the other
   case, which is usually door staff for a single day.

## Why this file exists at all

The same question was being answered in six places, each with its own
`EventManager.objects.filter(...)`, and each with a slightly different idea of
which roles counted. Adding the organisation to five of the six would have left
one screen quietly refusing the person every other screen had just admitted.
The rule is written once here, with the levels named, and every one of those
six reads it.

The three levels, and they are ordered:

    RUN    everything except deleting the event. Owner, org owner, org admin,
           org events manager, or somebody named `manager` on the event.
    DOOR   check tickets in and read the door numbers. Everybody above, plus
           somebody named `door` on the event.
    READ   see the console at all, including a member of the organisation who
           runs nothing.
"""
from vent_auth.models import OrgMember


def _org_role(event, user):
    """This person's standing in the organisation the event belongs to.

    None for a personal event, for an anonymous caller, or for somebody with no
    membership. The organisation's owner is an owner whether or not a row says
    so, the same rule `org_link.role_of` uses, because the owner column is the
    record and a membership row is not always written for them.
    """
    if user is None or getattr(event, 'organization_id', None) is None:
        return None

    org = event.organization
    if org is None:
        return None
    if org.org_owner_id == getattr(user, 'user_id', None):
        return OrgMember.ROLE_OWNER

    row = OrgMember.objects.filter(org_id=event.organization_id,
                                   user=user).first()
    return row.role if row else None


def org_may_run_events(event, user):
    """Whether the organisation's own roles let this person run this event.

    A manager reaches only the areas named in their scopes: an organisation
    with a tournaments manager and an events manager is the whole point of the
    role, and a tournaments manager must not inherit the door list.
    """
    role = _org_role(event, user)
    if role in (OrgMember.ROLE_OWNER, OrgMember.ROLE_ADMIN):
        return True
    if role != OrgMember.ROLE_MANAGER:
        return False

    row = OrgMember.objects.filter(org_id=event.organization_id,
                                   user=user).first()
    scopes = (row.scopes if row else None) or []
    return OrgMember.SCOPE_EVENTS in scopes


def may_run_event(user, event):
    """Everything except deleting the event."""
    if user is None or event is None:
        return False
    if event.creator_id == getattr(user, 'user_id', None):
        return True
    if org_may_run_events(event, user):
        return True

    from .models import EventManager
    return EventManager.objects.filter(
        event=event, user=user, role='manager').exists()


def may_work_the_door(user, event):
    """Check tickets in, and read the numbers that go with that.

    Wider than `may_run_event` by exactly one role: somebody put on the door
    for the day, who can admit people and do nothing else.
    """
    if may_run_event(user, event):
        return True

    from .models import EventManager
    return EventManager.objects.filter(
        event=event, user=user, role__in=('manager', 'door')).exists()


def may_read_event_console(user, event):
    """See the console, including somebody who runs nothing in it.

    A member of the organisation is not given the door list by being a member,
    which is why this is a third level rather than the same one: it decides
    whether the screen opens, and each control on it still asks its own
    question.
    """
    if may_work_the_door(user, event):
        return True
    return _org_role(event, user) is not None

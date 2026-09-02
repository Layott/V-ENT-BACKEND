"""Attaching a tournament or an event to an organisation.

CEO, 2 September 2026: "Users shuould be able to follow an organization, in
which that particular orgs events, tournaments and anything about that org
should show constantly."

The follow was built. `OrgFollower` works, `/organization/following/` lists
them, `/organization/following/feed/` reads events and tournaments filtered by
`organization_id__in`, ordered soonest first. Walking it signed in, the feed
came back with **zero items**, and the reason was at the other end entirely:

    tournaments with an organisation: 0 of 10
    events with an organisation:      0 of 5

Nothing anywhere could set it. Not the tournament wizard, not the event wizard,
not either console, and neither create endpoint accepted the field. So the
follow was a counter rather than a subscription, exactly as the comment on
`followingIds` in the organisations page had warned, and the person who pressed
it could not tell.

`Tournament.tournament_organization` and `Event.organization` both existed the
whole time. This module is the missing middle: one resolver, used by both
creates and both edits, so the rule about who may attach an organisation to
what is written once.
"""
from vent_auth.models import Organization, OrgMember

#: Roles that may run something in an organisation's name. A member cannot:
#: putting the org's name on a tournament is speaking for it.
MAY_LINK = ('owner', 'admin', 'manager')


def role_of(org, user):
    """This person's standing in that organisation, or None."""
    if user is None or org is None:
        return None
    if org.org_owner_id == user.user_id:
        return 'owner'
    membership = OrgMember.objects.filter(org=org, user=user).first()
    return membership.role if membership else None


def resolve(value, user):
    """The organisation this person means, or an error to answer with.

    Returns `(organization, error_code)`. Exactly one is not None, except when
    nothing was asked for, which is `(None, None)` and is the normal case: most
    tournaments belong to a person rather than an organisation.

    A slug or an id, because a wizard sends what it has and the two ends should
    not have to agree on which.
    """
    raw = '' if value is None else str(value).strip()
    if not raw or raw.lower() in ('none', 'null', '0'):
        return None, None

    org = None
    if raw.isdigit():
        org = Organization.objects.filter(org_id=int(raw)).first()
    if org is None:
        org = Organization.objects.filter(slug=raw).first()
    if org is None:
        org = Organization.objects.filter(org_name__iexact=raw).first()
    if org is None:
        return None, 'ORG_NOT_FOUND'

    if role_of(org, user) not in MAY_LINK:
        # Refused rather than ignored. Silently dropping it would create the
        # tournament under the person's own name and tell them it worked, and
        # they would find out when it never appeared on the organisation.
        return None, 'ORG_NOT_YOURS'

    return org, None


def mine(user):
    """The organisations this person may run something in the name of.

    What the picker in each wizard is filled from. Somebody in none of them
    never sees the field at all, which is most people.
    """
    if user is None:
        return []

    owned = Organization.objects.filter(org_owner=user)
    member_ids = (OrgMember.objects
                  .filter(user=user, role__in=MAY_LINK)
                  .values_list('org_id', flat=True))
    joined = Organization.objects.filter(org_id__in=list(member_ids))

    seen = {}
    for org in list(owned) + list(joined):
        seen[org.org_id] = org
    return sorted(seen.values(), key=lambda o: (o.org_name or '').lower())

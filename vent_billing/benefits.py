"""What a membership grants, and the one place that decides whether you have it.

Gate C1: a member's benefits are enforced on the API, not only hidden in the
interface. A lapsed member loses the benefit on the NEXT REQUEST, not at the
next deploy and not when a screen happens to re-render.

That means two things, and the second is the one that gets skipped:

1. There is a single function that answers "does this person hold this benefit
   right now", and it reads the dates every time rather than a cached flag on
   the account. A flag written when somebody subscribed is a flag nobody
   remembers to clear.
2. Every endpoint that grants something asks it. A control hidden in the
   interface is a courtesy; anybody can call the endpoint directly, and the
   whole reason this file exists is that a membership is worth money to the
   person who did not pay for it.

## The catalogue

Benefit keys are a closed set, held here and nowhere else. A plan stores keys
from this list; the interface translates them. Two copies of a label list is the
fault this codebase has produced repeatedly - five label maps for tournament
formats, two copies of the event console tabs - so the API sends the catalogue
and the screen renders what it is sent.

`value` benefits carry a number the organiser chooses (a discount percentage,
a number of guest passes). `flag` benefits are simply held or not.
"""
from django.utils import timezone

from . import states

FLAG = 'flag'
VALUE = 'value'

#: key -> {kind, english, max}
#:
#: `english` is the fallback the interface shows when a translation is missing,
#: and it is the ONLY English in this file. The keys themselves are what travel.
CATALOGUE = {
    'members_area': {
        'kind': FLAG,
        'english': 'The members area, with whatever the organiser puts in it',
    },
    'priority_registration': {
        'kind': FLAG,
        'english': 'Register for this organiser\'s tournaments before anybody else',
    },
    'member_ticket_discount': {
        'kind': VALUE,
        'english': 'A discount on this organiser\'s tickets',
        'max': 100,
    },
    'free_entry': {
        'kind': FLAG,
        'english': 'No entry fee on this organiser\'s open tournaments',
    },
    'member_badge': {
        'kind': FLAG,
        'english': 'A member badge beside your name on this organiser\'s pages',
    },
    'early_announcements': {
        'kind': FLAG,
        'english': 'Announcements before they are public',
    },
}

#: Every plan grants this whether it says so or not: a plan with a members area
#: written into it but the key left off would otherwise hold content nobody can
#: reach, which reads as a bug rather than as a decision.
IMPLIED = ('members_area',)


def catalogue_rows():
    """The catalogue in the shape a screen reads. Sent, never duplicated."""
    return [{'key': key, 'kind': meta['kind'], 'english': meta['english'],
             'max': meta.get('max')}
            for key, meta in CATALOGUE.items()]


def clean(raw):
    """Normalise what an organiser submitted into storable benefits.

    Accepts either a list of keys or a list of `{key, value}`. Anything not in
    the catalogue is dropped rather than stored: a benefit key nobody can
    render is a promise on a page that means nothing.
    """
    out = []
    seen = set()
    for item in (raw or []):
        if isinstance(item, str):
            key, value = item, None
        elif isinstance(item, dict):
            key, value = item.get('key'), item.get('value')
        else:
            continue
        meta = CATALOGUE.get(key)
        if meta is None or key in seen:
            continue
        seen.add(key)
        row = {'key': key}
        if meta['kind'] == VALUE:
            try:
                number = int(value or 0)
            except (TypeError, ValueError):
                number = 0
            row['value'] = max(0, min(number, meta.get('max', 10 ** 9)))
        out.append(row)
    return out


def keys_of(plan):
    held = {row['key'] if isinstance(row, dict) else row for row in (plan.benefits or [])}
    return held | set(IMPLIED)


def value_of(plan, key):
    for row in (plan.benefits or []):
        if isinstance(row, dict) and row.get('key') == key:
            return row.get('value') or 0
    return 0


def live_subscriptions(user, at=None):
    """Every subscription this person currently holds access under.

    Reads the dates on every call. That is the whole point: a subscription that
    lapsed an hour ago is not in this list an hour ago, without anything having
    run in between.
    """
    from .models import Subscription

    if user is None or not getattr(user, 'pk', None):
        return []
    at = at or timezone.now()
    rows = Subscription.objects.filter(
        subscriber=user, state__in=states.LIVE_STATES,
    ).select_related('plan', 'plan__org')
    return [row for row in rows if row.has_access(at)]


def has_benefit(user, key, *, org=None, owner=None, at=None):
    """Does this person hold `key` right now, from this organiser?

    `org` or `owner` narrows it to one seller, which is almost always what a
    caller means: a membership of one organisation does not grant free entry to
    somebody else's tournaments. Called with neither, it asks whether they hold
    the benefit from anybody, which is only correct for platform-wide perks.
    """
    for sub in live_subscriptions(user, at=at):
        plan = sub.plan
        if org is not None and plan.org_id != getattr(org, 'org_id', org):
            continue
        if owner is not None and plan.owner_id != getattr(owner, 'user_id', owner):
            continue
        if key in keys_of(plan):
            return True
    return False


def benefit_value(user, key, *, org=None, owner=None, at=None):
    """The best value this person holds for a `value` benefit, or 0.

    The BEST rather than the first: somebody on two plans from the same
    organiser gets the larger discount, because that is what any person would
    expect and the alternative depends on row order.
    """
    best = 0
    for sub in live_subscriptions(user, at=at):
        plan = sub.plan
        if org is not None and plan.org_id != getattr(org, 'org_id', org):
            continue
        if owner is not None and plan.owner_id != getattr(owner, 'user_id', owner):
            continue
        if key in keys_of(plan):
            best = max(best, value_of(plan, key))
    return best


def entitlements(user, at=None):
    """Everything this person holds, in the shape a screen reads.

    One request answers the whole question, so a page does not make six.
    """
    out = []
    for sub in live_subscriptions(user, at=at):
        plan = sub.plan
        out.append({
            'subscription_token': sub.token,
            'plan_slug': plan.slug,
            'plan_name': plan.name,
            'org_slug': plan.org.slug if plan.org_id else None,
            'org_name': plan.org.org_name if plan.org_id else None,
            'owner': plan.owner.username if plan.owner_id else None,
            'state': sub.state,
            'renews': sub.renews,
            'until': sub.period_end.isoformat(),
            'benefits': [
                {'key': key, 'value': value_of(plan, key)}
                for key in sorted(keys_of(plan))
            ],
        })
    return out

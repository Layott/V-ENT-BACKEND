"""The subscription state machine, written down in one table.

A state machine that lives in `if` statements spread across four files is not a
state machine, it is a rumour. Every transition this system can make is in
`TRANSITIONS` below, every one of them is applied through `move()`, and every
one of them writes a row saying when it happened and why.

## The states

| state      | means                                                       |
|------------|-------------------------------------------------------------|
| `trialing` | inside a free trial. Nothing has been charged yet           |
| `active`   | paid up to `period_end`                                     |
| `past_due` | a charge failed. Being retried on the dunning schedule      |
| `cancelled`| will not renew. Access runs to `period_end` and stops       |
| `expired`  | over. No access, and nothing further will be attempted      |

## The transitions

    (new) --trial_started--> trialing --trial_converted--> active
    (new) --first_charge---------------------------------> active

    active   --charge_failed--> past_due --charge_recovered--> active
    trialing --charge_failed--> past_due
    past_due --dunning_exhausted--> cancelled

    trialing|active|past_due --cancelled_by_subscriber--> cancelled
    trialing|active|past_due --cancelled_by_organiser---> cancelled
    trialing|active|past_due --refunded-----------------> cancelled

    cancelled --period_ended--> expired

## Why cancelled and expired are two states rather than one

Because they answer different questions. `cancelled` means "this will not
renew", and somebody in that state has usually paid for time they have not used
yet - taking their access away the moment they press Cancel is taking something
they bought. `expired` means the time is gone. Collapsing the two is how a
cancel button ends up stealing a fortnight, which is precisely what gate B4
exists to stop.
"""
from django.utils import timezone

TRIALING = 'trialing'
ACTIVE = 'active'
PAST_DUE = 'past_due'
CANCELLED = 'cancelled'
EXPIRED = 'expired'

ALL = (TRIALING, ACTIVE, PAST_DUE, CANCELLED, EXPIRED)

CHOICES = [
    (TRIALING, 'Trialing'),
    (ACTIVE, 'Active'),
    (PAST_DUE, 'Past due'),
    (CANCELLED, 'Cancelled'),
    (EXPIRED, 'Expired'),
]

# Reasons. Codes, never sentences: a reason is read by a screen that may be in
# French, and it is also read by whoever is working out what happened six weeks
# later. See `feedback_server_strings_need_codes`.
TRIAL_STARTED = 'trial_started'
FIRST_CHARGE = 'first_charge'
TRIAL_CONVERTED = 'trial_converted'
CHARGE_FAILED = 'charge_failed'
CHARGE_RECOVERED = 'charge_recovered'
DUNNING_EXHAUSTED = 'dunning_exhausted'
CANCELLED_BY_SUBSCRIBER = 'cancelled_by_subscriber'
CANCELLED_BY_ORGANISER = 'cancelled_by_organiser'
REFUNDED = 'refunded'
PERIOD_ENDED = 'period_ended'
PLAN_CHANGED = 'plan_changed'
# Undoing a cancellation. Three reasons rather than one, because a resume lands
# back where it came FROM: somebody who cancelled mid-trial gets their trial
# back, and somebody who cancelled while a payment was failing is still failing
# it. `resume()` used to write `state = ACTIVE` by hand, which gave a trial
# away as a paid period and gave a past_due account access nobody had paid for.
RESUMED_TO_TRIAL = 'resumed_to_trial'
RESUMED_TO_ACTIVE = 'resumed_to_active'
RESUMED_TO_PAST_DUE = 'resumed_to_past_due'

#: reason -> (states it may be applied from, the state it moves to)
#:
#: `None` on the left means "from nothing", which is only the subscribe path.
TRANSITIONS = {
    TRIAL_STARTED: ((None,), TRIALING),
    FIRST_CHARGE: ((None,), ACTIVE),
    TRIAL_CONVERTED: ((TRIALING,), ACTIVE),
    CHARGE_FAILED: ((TRIALING, ACTIVE, PAST_DUE), PAST_DUE),
    CHARGE_RECOVERED: ((PAST_DUE,), ACTIVE),
    DUNNING_EXHAUSTED: ((PAST_DUE,), CANCELLED),
    CANCELLED_BY_SUBSCRIBER: ((TRIALING, ACTIVE, PAST_DUE), CANCELLED),
    CANCELLED_BY_ORGANISER: ((TRIALING, ACTIVE, PAST_DUE), CANCELLED),
    REFUNDED: ((TRIALING, ACTIVE, PAST_DUE, CANCELLED), CANCELLED),
    PERIOD_ENDED: ((CANCELLED, PAST_DUE), EXPIRED),
    # A plan change never changes the state. It is recorded here so the history
    # holds it: "why is this person on a different plan than last month" is a
    # question somebody asks, and an unrecorded change cannot answer it.
    PLAN_CHANGED: ((TRIALING, ACTIVE, PAST_DUE), None),
    RESUMED_TO_TRIAL: ((CANCELLED,), TRIALING),
    RESUMED_TO_ACTIVE: ((CANCELLED,), ACTIVE),
    RESUMED_TO_PAST_DUE: ((CANCELLED,), PAST_DUE),
}

#: Where a resume goes back to, by the state it was cancelled from.
RESUME_REASON = {
    TRIALING: RESUMED_TO_TRIAL,
    ACTIVE: RESUMED_TO_ACTIVE,
    PAST_DUE: RESUMED_TO_PAST_DUE,
}

#: States that let somebody through the door, subject to the dates. Never used
#: on its own - `Subscription.has_access` also asks the clock, because
#: `cancelled` grants access until the period ends and `past_due` grants it
#: only for the length of the dunning window.
LIVE_STATES = (TRIALING, ACTIVE, PAST_DUE, CANCELLED)


class IllegalTransition(Exception):
    """A move the table above does not allow.

    Raised rather than logged. A billing state machine that quietly ignores a
    move it does not understand is one that ends up in a state nobody can
    explain, and by then the money has moved.
    """


def may(from_state, reason):
    allowed, _to = TRANSITIONS.get(reason, ((), None))
    return from_state in allowed


def move(subscription, reason, *, actor=None, note='', at=None, save=True):
    """Apply a transition and record it. Returns the SubscriptionEvent.

    Every state change on a subscription goes through here. Nothing else may
    write `subscription.state`, which is what makes the history complete rather
    than mostly complete.
    """
    from .models import SubscriptionEvent

    allowed, to_state = TRANSITIONS.get(reason, (None, None))
    if allowed is None:
        raise IllegalTransition('unknown reason: %r' % (reason,))

    # "From nothing" means no transition has been recorded yet, not "no primary
    # key yet". A subscription row exists before its first charge - the invoice
    # has to hang off something - so asking the database whether it has a
    # history is the only honest way to tell a birth from a move. It is one
    # cheap indexed query on a path that runs once per state change.
    was = (subscription.state
           if subscription.pk and subscription.events.exists() else None)
    if was not in allowed:
        raise IllegalTransition('%s cannot %s' % (was, reason))

    at = at or timezone.now()
    if to_state is not None and to_state != was:
        subscription.state = to_state
        if to_state == CANCELLED:
            subscription.cancelled_at = subscription.cancelled_at or at
        if to_state == EXPIRED:
            subscription.ended_at = subscription.ended_at or at
        if save and subscription.pk:
            subscription.save(update_fields=['state', 'cancelled_at', 'ended_at'])

    return SubscriptionEvent.objects.create(
        subscription=subscription,
        from_state=was or '',
        to_state=subscription.state,
        reason=reason,
        actor=actor,
        note=note[:200],
        at=at,
    )

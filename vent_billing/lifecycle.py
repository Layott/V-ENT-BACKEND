"""Starting, stopping and renewing a subscription.

Everything that changes what somebody is paying for lives here, so the rules
are in one place rather than spread across the views that happen to call them.

## Cancelling was written before charging, and this is what that bought

`cancel` is four lines. It sets a flag, records the transition, and stops. It
does not touch `period_end`, it does not refund, and it does not take anything
away, because somebody who paid for a month has bought that month whether or
not they want another one. `has_access` reads `period_end` for every state, so
cancelling correctly grants access to the end of the period without cancelling
having to know that.

The alternative - ending access on the press - is what a cancel button does
when it is written after the charging half and bolted on. It is also
indistinguishable from a bug, because from the subscriber's side it IS one.

## Renewing is idempotent by construction

The renewal command is safe to run twice, three times, or on a cron that
overlaps itself. Three things make that true, in this order:

1. **Due-ness is a date, and the date moves inside the same transaction as the
   charge.** A subscription that has just been renewed is not due, so a second
   run in the same minute finds nothing.
2. **An invoice is `get_or_create` on (subscription, period_start, attempt).**
   Two runs that genuinely race both find the same row, and the one that lost
   sees it already paid and stops.
3. **A unique constraint on those three columns**, so if both of the above are
   somehow wrong the database refuses rather than charging twice.

Guard 1 alone would be enough on a single worker. Guard 3 alone would turn a
double charge into a crash. Having all three is why the answer to "what if the
cron fires twice" is a test rather than an argument.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import charging, clock, dunning, states
from .models import Invoice, Plan, Subscription


class SubscribeError(Exception):
    """A subscribe that cannot proceed, carrying a CODE for the interface."""

    def __init__(self, code, detail=''):
        super().__init__(code)
        self.code = code
        self.detail = detail


def existing_subscription(user, plan):
    """A live subscription this person already holds to this plan, if any."""
    return Subscription.objects.filter(
        subscriber=user, plan=plan, state__in=states.LIVE_STATES,
    ).order_by('-created_at').first()


def subscribe(user, plan, *, at=None):
    """Start a subscription, taking the first charge.

    Returns (subscription, invoice). The invoice is None only for a plan with a
    free trial, where nothing has been charged yet.

    The subscription becomes ACTIVE only when the money arrived. There is no
    path here that takes a browser's word for a payment: the wallet is debited
    by this process, and a card is charged by a request this server made and
    answered by Paystack rather than by a redirect.
    """
    at = at or timezone.now()

    if plan.status != Plan.STATUS_PUBLIC:
        raise SubscribeError('PLAN_NOT_AVAILABLE')

    already = existing_subscription(user, plan)
    if already is not None and already.has_access(at):
        raise SubscribeError('ALREADY_SUBSCRIBED')

    anchor_day, anchor_month = clock.anchor_of(at)

    if plan.trial_days:
        # A trial is time, not money. Nothing is charged and nothing is
        # promised: the first charge happens when the trial ends, through the
        # same renewal path as every charge after it.
        subscription = Subscription(
            plan=plan, subscriber=user, state=states.TRIALING,
            period_start=at, period_end=at + timedelta(days=plan.trial_days),
            anchor_day=anchor_day, anchor_month=anchor_month,
            source=Subscription.SOURCE_WALLET,
        )
        subscription.save()
        states.move(subscription, states.TRIAL_STARTED, actor=user, at=at)
        return subscription, None

    period_end = clock.add_period(at, plan.interval, anchor_day, anchor_month)

    with transaction.atomic():
        subscription = Subscription(
            plan=plan, subscriber=user, state=states.ACTIVE,
            period_start=at, period_end=period_end,
            anchor_day=anchor_day, anchor_month=anchor_month,
        )
        # Saved before the charge so the invoice has something to hang off, and
        # rolled back with it if the charge does not happen at all.
        subscription.save()
        invoice = charging.attempt_charge(
            subscription, period_start=at, period_end=period_end, attempt=1, at=at)

        if invoice.state != Invoice.STATE_PAID:
            # Nothing is left behind. A subscription in no state, with a failed
            # invoice attached, would show up on the organiser's member list as
            # somebody who never paid and never subscribed.
            transaction.set_rollback(True)
            raise SubscribeError(invoice.failure_code or charging.NO_PAYMENT_METHOD,
                                 invoice.failure_detail)

        states.move(subscription, states.FIRST_CHARGE, actor=user, at=at)

    return subscription, invoice


def cancel(subscription, *, actor=None, by_organiser=False, reason='', at=None):
    """Stop it renewing. Access runs to the end of the period they paid for.

    Deliberately does not touch `period_end`. See the note at the top of this
    file: taking away time somebody has already paid for is the failure this
    whole feature was built in the order it was to avoid.
    """
    at = at or timezone.now()
    if subscription.state == states.EXPIRED:
        return subscription
    if subscription.cancel_at_period_end and subscription.state == states.CANCELLED:
        return subscription

    subscription.cancel_at_period_end = True
    subscription.next_attempt_at = None
    subscription.pending_plan = None
    subscription.save(update_fields=['cancel_at_period_end', 'next_attempt_at',
                                     'pending_plan'])
    reason_code = (states.CANCELLED_BY_ORGANISER if by_organiser
                   else states.CANCELLED_BY_SUBSCRIBER)
    states.move(subscription, reason_code, actor=actor, note=reason, at=at)
    return subscription


def resume(subscription, *, actor=None, at=None):
    """Undo a cancellation that has not run out yet.

    Somebody who cancels on the 2nd and changes their mind on the 5th should
    not have to buy a second period. Only possible while the period they paid
    for is still running; after that it is a new subscription, honestly.
    """
    at = at or timezone.now()
    if subscription.state != states.CANCELLED:
        raise SubscribeError('NOT_CANCELLED')
    if at >= subscription.period_end:
        raise SubscribeError('PERIOD_OVER')

    # Back to the state it was cancelled FROM, not to active.
    #
    # This used to write `state = ACTIVE` by hand, which did two things wrong.
    # It bypassed `states.move`, the only writer of `state` and the thing that
    # makes the history complete rather than mostly complete. And it landed
    # everybody on active: somebody who cancelled three days into a trial got a
    # paid period they had not paid for, and somebody who cancelled while a
    # charge was failing came back with full access and a failed payment.
    #
    # The history already knows where they were. The cancelling event carries
    # it, so it is read rather than stored a second time.
    from .models import SubscriptionEvent
    cancelled_from = (
        SubscriptionEvent.objects
        .filter(subscription=subscription, to_state=states.CANCELLED)
        .order_by('-at', '-pk')
        .values_list('from_state', flat=True)
        .first()
    )
    reason = states.RESUME_REASON.get(cancelled_from, states.RESUMED_TO_ACTIVE)

    subscription.cancel_at_period_end = False
    subscription.cancelled_at = None
    subscription.save(update_fields=['cancel_at_period_end', 'cancelled_at'])
    states.move(subscription, reason, actor=actor, at=at)
    return subscription


def change_plan(subscription, new_plan, *, actor=None, at=None):
    """Queue a plan change for the start of the next period.

    No proration, and the screen says so with the date before anybody presses.
    The reason is written at the top of `charging.py`: VENT COINS are whole
    numbers worth 1,000 NGN, so a part-period is not expressible and rounding
    it takes money from one side on every single change.
    """
    at = at or timezone.now()
    if new_plan.status != Plan.STATUS_PUBLIC:
        raise SubscribeError('PLAN_NOT_AVAILABLE')
    if new_plan.pk == subscription.plan_id:
        subscription.pending_plan = None
        subscription.save(update_fields=['pending_plan'])
        return subscription
    seller = (subscription.plan.org_id, subscription.plan.owner_id)
    if (new_plan.org_id, new_plan.owner_id) != seller:
        # Moving between two different organisers is not a plan change, it is
        # cancelling one membership and starting another, and pretending
        # otherwise would carry one organiser's paid period onto another's
        # books.
        raise SubscribeError('DIFFERENT_SELLER')

    subscription.pending_plan = new_plan
    subscription.save(update_fields=['pending_plan'])
    states.move(subscription, states.PLAN_CHANGED, actor=actor,
                note=new_plan.name, at=at)
    return subscription


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def due(at=None):
    """Every subscription with something to do right now.

    Two kinds: a period that has ended and is up for renewal, and a past_due
    subscription whose next retry has come round.
    """
    at = at or timezone.now()
    from django.db.models import Q
    return Subscription.objects.filter(
        Q(state__in=(states.TRIALING, states.ACTIVE), period_end__lte=at)
        | Q(state=states.PAST_DUE, next_attempt_at__lte=at)
        | Q(state=states.CANCELLED, period_end__lte=at),
    ).select_related('plan', 'subscriber', 'pending_plan').order_by('subscription_id')


def process(subscription, *, at=None):
    """Do whatever this subscription is due for. Returns a short verb.

    One of: 'expired', 'renewed', 'recovered', 'failed', 'cancelled', 'nothing'.
    The verbs are for the command's own report and for the tests; nothing else
    reads them.
    """
    at = at or timezone.now()

    # A cancelled subscription whose period has run out. Nothing is charged and
    # nothing is asked; it simply stops.
    if subscription.state == states.CANCELLED:
        if at >= subscription.period_end:
            states.move(subscription, states.PERIOD_ENDED, at=at)
            return 'expired'
        return 'nothing'

    if not subscription.renews:
        # Cancelled mid-period and now at the end of it. Same as above; the
        # flag is what matters, not which state carried it.
        if at >= subscription.period_end:
            states.move(subscription, states.PERIOD_ENDED, at=at)
            return 'expired'
        return 'nothing'

    was_past_due = subscription.state == states.PAST_DUE

    if was_past_due:
        # A retry against the SAME period. The period does not move until the
        # money arrives, which is what stops a failing subscription silently
        # walking its own renewal date forward.
        period_start = subscription.period_end
        period_end = clock.add_period(period_start, subscription.plan.interval,
                                      subscription.anchor_day,
                                      subscription.anchor_month)
        attempt = (subscription.dunning_attempt or 0) + 1
    else:
        period_start = subscription.period_end
        if subscription.state == states.TRIALING:
            # The anchor moves to the day the trial ends, because that is the
            # first day money changed hands. Left at the sign-up day, a trial
            # started on the 1st and ending on the 15th would bill a fortnight
            # later and then a full month, which nobody agreed to.
            subscription.anchor_day, subscription.anchor_month = clock.anchor_of(
                period_start)
            subscription.save(update_fields=['anchor_day', 'anchor_month'])
        period_end = clock.add_period(period_start, subscription.plan.interval,
                                      subscription.anchor_day,
                                      subscription.anchor_month)
        attempt = 1

    # A queued plan change takes effect here, at the period boundary, which is
    # the only moment it can happen without proration.
    if subscription.pending_plan_id and not was_past_due:
        subscription.plan = subscription.pending_plan
        subscription.pending_plan = None
        subscription.save(update_fields=['plan', 'pending_plan'])
        period_end = clock.add_period(period_start, subscription.plan.interval,
                                      subscription.anchor_day,
                                      subscription.anchor_month)

    with transaction.atomic():
        locked = Subscription.objects.select_for_update().get(pk=subscription.pk)
        # Re-read under the lock. Another run that got here first has already
        # moved `period_end`, so this one has nothing to do - which is guard 1
        # from the note at the top of this file.
        if locked.period_end != subscription.period_end:
            return 'nothing'

        invoice = charging.attempt_charge(
            subscription, period_start=period_start, period_end=period_end,
            attempt=attempt, at=at)

        if invoice.state == Invoice.STATE_PAID:
            subscription.period_start = period_start
            subscription.period_end = period_end
            subscription.save(update_fields=['period_start', 'period_end'])
            if was_past_due:
                dunning.on_recovery(subscription, invoice, at=at)
                return 'recovered'
            if subscription.state == states.TRIALING:
                states.move(subscription, states.TRIAL_CONVERTED, at=at)
            return 'renewed'

    # Outside the transaction: a failure must be recorded even though the
    # charge did not happen, and the notice it sends is not part of the money.
    dunning.on_failure(subscription, invoice, at=at)
    return 'cancelled' if subscription.state == states.CANCELLED else 'failed'


def run_renewals(at=None, limit=None):
    """Every due subscription, processed once. Safe to run repeatedly.

    Returns a count per verb, which is what the command prints and what the
    idempotency test asserts on: running it twice in the same period must
    report a second pass that charged nobody.
    """
    at = at or timezone.now()
    tally = {}
    rows = due(at)
    if limit:
        rows = rows[:limit]
    for subscription in list(rows):
        verb = process(subscription, at=at)
        tally[verb] = tally.get(verb, 0) + 1
    return tally

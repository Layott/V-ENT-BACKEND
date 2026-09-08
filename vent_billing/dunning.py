"""What happens when a charge fails, on a schedule that is written down.

A failed charge has exactly three wrong answers and one right one:

- **Cut them off immediately.** A card expires, a bank declines once, a wallet
  is a coin short. Ending a membership on the first decline punishes somebody
  for a thing they can fix in a minute, and they will not come back.
- **Keep charging for ever.** A card that has been declined nine times is not
  going to work on the tenth, and each attempt costs the gateway's fee and
  looks, from the bank's side, like somebody hammering a dead card.
- **Keep granting access and say nothing.** This is the one that actually
  happens, because it is what "do nothing" looks like. The organiser is giving
  away a membership and does not know it, and the subscriber finds out months
  later when somebody notices.

The right answer is a bounded, written schedule where the subscriber is told
every time, and where the end is a state everybody can see.

## The schedule

    charge fails at period end
      -> past_due, attempt 1 written, subscriber told, retry in 1 day
      -> fails again: attempt 2, told again, retry in 2 more days
      -> fails again: attempt 3, told again, retry in 4 more days
      -> fails again: cancelled. Access already ended at day 7.

`GRACE_DAYS` on the subscription is 7, and the schedule adds up to 7, so the
last retry lands on the day access ends rather than after it. That is not a
coincidence and it must stay true: a final retry that happens after access has
already lapsed asks somebody to pay for time they did not get.

## Telling them, in their own language

The notification carries a translation CODE and its parameters in `metadata`,
never a built English sentence. A sentence assembled in Python cannot be
translated - see `feedback_server_strings_need_codes` - and the whole point of
dunning is that the person reads it. The title and body are written in English
as the fallback for anything that has not learned to read the code yet.
"""
import logging

from datetime import timedelta

from django.utils import timezone

from . import states

logger = logging.getLogger(__name__)

#: Days after the previous attempt that the next one is made. The length of
#: this list is how many retries there are, and its sum must not exceed
#: `Subscription.GRACE_DAYS`.
SCHEDULE = [1, 2, 4]

#: Translation codes for the notices. Codes, not sentences.
CODE_FAILED = 'billing.charge_failed'
CODE_LAST_TRY = 'billing.charge_failed_last'
CODE_CANCELLED = 'billing.dunning_cancelled'
CODE_RECOVERED = 'billing.charge_recovered'


def total_days():
    return sum(SCHEDULE)


def next_attempt_after(attempt, at):
    """When the next retry is due after failure number `attempt`, or None.

    `attempt` counts failures, so the first failure is 1 and the gap to the
    retry after it is `SCHEDULE[0]`. There are `len(SCHEDULE)` retries, which
    makes `len(SCHEDULE) + 1` attempts in total, the last of them landing on
    day `total_days()` - the day access ends.
    """
    if attempt > len(SCHEDULE):
        return None
    return at + timedelta(days=SCHEDULE[attempt - 1])


def attempts_left(attempt):
    """Retries still to come after failure number `attempt`."""
    return max(0, len(SCHEDULE) - attempt + 1)


def _notify(subscription, code, params, title, body):
    """One notice. Never raises: a mail failure must not undo a state change."""
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            subscription.subscriber,
            category='wallet',
            title=title,
            body=body,
            link='/memberships',
            metadata={'code': code, 'params': params,
                      'subscription': subscription.token},
        )
    except Exception:
        logger.exception('dunning notice failed for %s', subscription.token)


def on_failure(subscription, invoice, *, at=None):
    """Record a failed charge and decide what happens next.

    Returns the subscription. The state is moved through `states.move` so the
    transition is on the record with its reason, which is what the support
    screen reads when somebody asks why they were cut off.
    """
    at = at or timezone.now()
    subscription.dunning_attempt = (subscription.dunning_attempt or 0) + 1
    attempt = subscription.dunning_attempt
    following = next_attempt_after(attempt, at)

    if subscription.state != states.PAST_DUE:
        states.move(subscription, states.CHARGE_FAILED,
                    note=invoice.failure_code, at=at)

    if following is None:
        # Out of retries. Cancelled rather than expired: the subscription stops
        # renewing, and `has_access` has already stopped granting anything
        # because the grace window closed on the same day.
        subscription.next_attempt_at = None
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=['dunning_attempt', 'next_attempt_at',
                                         'cancel_at_period_end'])
        states.move(subscription, states.DUNNING_EXHAUSTED,
                    note=invoice.failure_code, at=at)
        _notify(subscription, CODE_CANCELLED,
                {'plan': subscription.plan.name, 'attempts': attempt},
                'Your membership has ended',
                'We could not collect the payment for %s after %d tries.'
                % (subscription.plan.name, attempt))
        return subscription

    subscription.next_attempt_at = following
    subscription.save(update_fields=['dunning_attempt', 'next_attempt_at'])

    left = attempts_left(attempt)
    code = CODE_LAST_TRY if left == 1 else CODE_FAILED
    _notify(subscription, code,
            {'plan': subscription.plan.name,
             'amount_vc': invoice.amount_vc,
             'attempt': attempt,
             'left': left,
             'next': following.isoformat()},
            'A payment for %s did not go through' % subscription.plan.name,
            'We will try again. %d more %s will be made.'
            % (left, 'attempt' if left == 1 else 'attempts'))
    return subscription


def on_recovery(subscription, invoice, *, at=None):
    """A retry worked. Clear the counter and say so."""
    at = at or timezone.now()
    subscription.dunning_attempt = 0
    subscription.next_attempt_at = None
    subscription.save(update_fields=['dunning_attempt', 'next_attempt_at'])
    if subscription.state == states.PAST_DUE:
        states.move(subscription, states.CHARGE_RECOVERED, at=at)
    _notify(subscription, CODE_RECOVERED,
            {'plan': subscription.plan.name, 'amount_vc': invoice.collected_vc},
            'Your membership is active again',
            'The payment for %s went through.' % subscription.plan.name)
    return subscription

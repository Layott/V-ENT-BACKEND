"""Organiser subscriptions and memberships.

Inbox row 202. This was named as deliberately NOT built on 8 September, and the
reason it was refused is what it is built around rather than around what it
sells: recurring billing is the one feature where the half that ships first is
the CHARGING half. A subscription that can take money and cannot cancel, cannot
fail gracefully and cannot prove what it charged is worse than no subscription
at all, because the person on the other end has no way out and no way to argue.

So the order the code was written in is the order it is safe in:

1. the models, including the one that records every state change
2. cancellation, and a test that proves a cancelled member keeps what they paid
3. dunning, so a failed charge fails in the open rather than silently
4. and only then the charging

## Five tables, and why each one exists

- **Plan** - what an organiser sells. Belongs to an organisation OR to a person,
  because not every organiser has made an organisation and making them create
  one to sell a membership is a wall in front of the feature.
- **Subscription** - one person's relationship with one plan, and the clock it
  runs on. Carries an ANCHOR rather than a renewal date arithmetic, for the
  reasons in `clock.py`.
- **SubscriptionEvent** - every state change, with a timestamp and a reason
  code. This is the table that answers "why did this person lose access", and
  the reason it is a table rather than a log line is that the answer is needed
  by the support screen, not by whoever is reading the server.
- **Invoice** - one row per CHARGE ATTEMPT, not per successful charge. A charge
  that is not on an invoice did not happen; a failure that is not on an invoice
  is a failure nobody can count. Attempts are how dunning is auditable.
- **BillingLedgerEntry** / **BillingSettlement** - who is owed what out of each
  paid invoice, and paying them once. The same shape as the event ledger built
  on 7 September, deliberately: a balance incremented at the till drifts, and a
  refund is where it drifts first.

## Money

VENT COINS, as integers, exactly as everywhere else on this platform. Never a
float: `0.1 + 0.2` is not `0.3` and a subscription is the one thing that
performs the same arithmetic every month for years.

A plan carries both a coin price and a naira price because a card is charged in
naira and a wallet is debited in coins, and the two must agree by construction
rather than by whoever wrote the screen. `Plan.save` normalises them, and the
create endpoint refuses a naira price that is not a whole number of coins
rather than silently repricing what somebody typed.
"""
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from vent_auth.models import Organization, Users

from . import clock, states


def _token(prefix):
    """An opaque public identifier.

    A subscription and an invoice have no name, so they cannot carry a slug,
    and they must not carry their primary key: sequential ids in an address let
    anybody walk the whole table by counting, and this table holds what people
    pay for. Sixteen hex characters is not guessable in any useful number of
    tries.
    """
    return '%s_%s' % (prefix, secrets.token_hex(8))


class Plan(models.Model):
    """What an organiser sells, and what it grants.

    Draft until it is published. A plan somebody is still writing must not be
    subscribable, and it must not be readable by anybody who could subscribe to
    it, because a price seen once is a price somebody expects to be charged.
    """

    STATUS_DRAFT = 'draft'
    STATUS_PUBLIC = 'public'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLIC, 'Public'),
        # Retired means "no new subscribers". Everybody already on it stays on
        # it and keeps being charged, because pulling a plan is a decision
        # about who may JOIN, never about cutting off the people who already
        # did. Deleting a plan people are paying for is not offered at all.
        (STATUS_RETIRED, 'Retired'),
    ]

    INTERVAL_CHOICES = [
        (clock.MONTHLY, 'Monthly'),
        (clock.YEARLY, 'Yearly'),
    ]

    plan_id = models.AutoField(primary_key=True)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True,
                            db_index=True)

    # One of these two is set, never both and never neither. `owner` is the
    # person accountable for the plan either way, so there is always somebody to
    # pay and somebody to ask.
    org = models.ForeignKey(Organization, on_delete=models.CASCADE,
                            related_name='plans', null=True, blank=True)
    owner = models.ForeignKey(Users, on_delete=models.CASCADE,
                              related_name='plans_owned')

    name = models.CharField(max_length=120)
    tagline = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, default='')

    price_vc = models.IntegerField(default=0)
    price_ngn = models.IntegerField(default=0)
    interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES,
                                default=clock.MONTHLY)
    trial_days = models.PositiveIntegerField(default=0)

    #: Benefit keys from `benefits.CATALOGUE`. A list rather than a set of
    #: boolean columns, so adding a benefit is a row in one catalogue and not a
    #: migration plus five screens that have to learn about it.
    benefits = models.JSONField(default=list, blank=True)

    #: What a member actually receives that cannot be expressed as a flag: a
    #: Discord invite, a code, an address, a joining instruction. Readable ONLY
    #: by somebody whose subscription is live, enforced on the API.
    member_content = models.TextField(blank=True, default='')

    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['price_vc', 'name']
        indexes = [
            models.Index(fields=['org', 'status']),
            models.Index(fields=['owner', 'status']),
        ]

    def __str__(self):
        return '%s (%s VC / %s)' % (self.name, self.price_vc, self.interval)

    def save(self, *args, **kwargs):
        # The two prices are one price. Whichever the caller set, the other
        # follows, so a screen reading `price_ngn` and a charge reading
        # `price_vc` can never disagree about what this costs.
        from vent_auth.views_wallet import NGN_PER_COIN

        self.price_vc = max(0, int(self.price_vc or 0))
        self.price_ngn = self.price_vc * NGN_PER_COIN

        # Addresses carry the name, and every name it has had keeps working.
        from vent_auth.slugs import sync_slug
        changed = sync_slug(self, self.name, entity_type='plan',
                            id_attr='plan_id')
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)

    @property
    def is_free(self):
        return self.price_vc <= 0

    @property
    def seller_name(self):
        return self.org.org_name if self.org_id else self.owner.username

    def live_subscriptions(self, at=None):
        """Everybody who has access to this plan right now.

        The state alone is not the answer. A cancelled subscription is in
        LIVE_STATES and grants access only until its period ends, and a refund
        moves that period end to the moment of the refund - so a query on the
        state alone counts somebody who was refunded an hour ago as a member.
        Found on the walk of 8 September, where the plan page still said "1
        members" after the only member had been refunded.

        The clauses mirror `Subscription.has_access` exactly. If one changes,
        the other has to, and there is a test that holds them together.
        """
        from django.db.models import Q
        at = at or timezone.now()
        return self.subscriptions.filter(
            Q(state__in=(states.TRIALING, states.CANCELLED), period_end__gt=at)
            | Q(state=states.ACTIVE,
                period_end__gt=at - timedelta(days=Subscription.RENEWAL_SLACK_DAYS))
            | Q(state=states.PAST_DUE,
                period_end__gt=at - timedelta(days=Subscription.GRACE_DAYS)))


class Subscription(models.Model):
    """One person's relationship with one plan, and the clock it runs on.

    `period_end` is the moment paid-for access stops. It is the single date
    every access question is answered from, which is why cancelling only has to
    set a flag: a cancelled subscription keeps its `period_end` and therefore
    keeps its access, and the renewal command simply does not renew it.
    """

    subscription_id = models.AutoField(primary_key=True)
    #: The public identifier. See `_token`.
    token = models.CharField(max_length=32, unique=True, db_index=True)

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT,
                             related_name='subscriptions')
    subscriber = models.ForeignKey(Users, on_delete=models.CASCADE,
                                   related_name='subscriptions')

    state = models.CharField(max_length=12, choices=states.CHOICES,
                             default=states.ACTIVE, db_index=True)

    period_start = models.DateTimeField()
    period_end = models.DateTimeField(db_index=True)

    #: The day of the month, and for a yearly plan the month, that every future
    #: period is measured from. Set once at the first charge and never
    #: recomputed. See `clock.py` for why this is not derived from the last
    #: renewal.
    anchor_day = models.PositiveSmallIntegerField(default=1)
    anchor_month = models.PositiveSmallIntegerField(default=1)

    #: Where the money comes from. `wallet` debits VENT COINS; `card` charges a
    #: saved Paystack authorization. Nothing else can pay for a subscription,
    #: and that is a deliberate limit rather than an omission - see
    #: `charging.py`.
    SOURCE_WALLET = 'wallet'
    SOURCE_CARD = 'card'
    SOURCE_NONE = 'none'
    SOURCE_CHOICES = [
        (SOURCE_WALLET, 'VENT COINS wallet'),
        (SOURCE_CARD, 'Saved card'),
        (SOURCE_NONE, 'Nothing to charge'),
    ]
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES,
                              default=SOURCE_WALLET)
    card = models.ForeignKey('vent_auth.SavedCard', on_delete=models.SET_NULL,
                             null=True, blank=True,
                             related_name='subscriptions')

    #: Set the moment somebody presses Cancel. The subscription stays live
    #: until `period_end`; this is what stops it renewing.
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    #: A plan change takes effect at the start of the next period, never
    #: mid-period. See `PRORATION` in `charging.py` for why, and note that the
    #: screen says the date before anybody presses.
    pending_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL,
                                     null=True, blank=True,
                                     related_name='pending_subscriptions')

    #: How many times the current period's charge has failed, and when the next
    #: retry is due. Reset to 0 the moment a charge succeeds.
    dunning_attempt = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['state', 'period_end']),
            models.Index(fields=['subscriber', 'state']),
        ]

    def __str__(self):
        return '%s on %s (%s)' % (self.subscriber_id, self.plan_id, self.state)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = _token('sub')
        super().save(*args, **kwargs)

    # -- access -----------------------------------------------------------
    #
    # One question, one answer, asked by the API rather than by the interface.
    # A benefit that is only hidden in the interface is not a benefit, it is a
    # decoration: anybody can call the endpoint directly.

    #: How long past a failed renewal somebody keeps access while the retries
    #: run. Bounded and written down: dunning is telling somebody their payment
    #: failed, not giving them the month for nothing.
    GRACE_DAYS = 7

    #: How late the renewal command may be before an active subscription starts
    #: being refused. A cron that runs an hour late must not lock a paying
    #: member out; a cron that has been dead a week must not give away a month.
    RENEWAL_SLACK_DAYS = 1

    def has_access(self, at=None):
        at = at or timezone.now()
        if self.state == states.EXPIRED:
            return False
        if self.state == states.PAST_DUE:
            return at < self.period_end + timedelta(days=self.GRACE_DAYS)
        if self.state in (states.TRIALING, states.CANCELLED):
            return at < self.period_end
        if self.state == states.ACTIVE:
            return at < self.period_end + timedelta(days=self.RENEWAL_SLACK_DAYS)
        return False

    @property
    def renews(self):
        """Whether a renewal will be attempted at `period_end`."""
        return (not self.cancel_at_period_end
                and self.state in (states.TRIALING, states.ACTIVE, states.PAST_DUE))

    @property
    def next_charge_at(self):
        """When money is next taken, or None when none ever will be.

        During dunning it is the next RETRY, not the period end, because that
        is the date the subscriber actually needs to know.
        """
        if not self.renews:
            return None
        if self.state == states.PAST_DUE:
            return self.next_attempt_at
        return self.period_end

    def effective_plan(self):
        """The plan being charged for the CURRENT period.

        A pending change does not move until the period turns, so this is
        always `plan`. It exists as a method because reading `plan` at a call
        site that means "what are they on now" is the shape that goes wrong the
        first time somebody wires the pending change into a screen.
        """
        return self.plan


class SubscriptionEvent(models.Model):
    """Every state change, with a timestamp and a reason code.

    Written by `states.move` and by nothing else. `reason` is a code rather
    than a sentence because it is read by a screen that may be in French and by
    a person reconstructing what happened, and a sentence serves neither.
    """

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE,
                                     related_name='events')
    from_state = models.CharField(max_length=12, blank=True, default='')
    to_state = models.CharField(max_length=12)
    reason = models.CharField(max_length=40)
    #: Who caused it. Null for anything the clock did, which is most of them.
    actor = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                              blank=True, related_name='+')
    note = models.CharField(max_length=200, blank=True, default='')
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-at', '-id']

    def __str__(self):
        return '%s -> %s (%s)' % (self.from_state, self.to_state, self.reason)


class Invoice(models.Model):
    """One charge ATTEMPT. A charge that is not here did not happen.

    Per attempt rather than per success, because a subscription that failed
    three times and succeeded on the fourth is a story, and a table that only
    holds the fourth row cannot tell it. It is also what makes dunning
    auditable: the retries are rows, not a counter.

    `unique_together` on (subscription, period_start, attempt) is the backstop
    under the renewal command's idempotency. The command's real guard is that a
    subscription whose period has already been advanced is not due; this is what
    catches a second run that races the first.
    """

    STATE_OPEN = 'open'
    STATE_PAID = 'paid'
    STATE_FAILED = 'failed'
    STATE_REFUNDED = 'refunded'
    STATE_VOID = 'void'
    STATE_CHOICES = [
        (STATE_OPEN, 'Open'),
        (STATE_PAID, 'Paid'),
        (STATE_FAILED, 'Failed'),
        (STATE_REFUNDED, 'Refunded'),
        (STATE_VOID, 'Void'),
    ]

    invoice_id = models.AutoField(primary_key=True)
    token = models.CharField(max_length=32, unique=True, db_index=True)

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE,
                                     related_name='invoices')
    #: Copied off the plan at the time, not read through the subscription. A
    #: plan whose price changes next month must not rewrite what was charged
    #: last month, which is the same rule the event ledger is built to.
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='invoices')
    plan_name = models.CharField(max_length=120, blank=True, default='')

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    attempt = models.PositiveSmallIntegerField(default=1)

    amount_vc = models.IntegerField(default=0)
    amount_ngn = models.IntegerField(default=0)
    collected_vc = models.IntegerField(default=0)

    state = models.CharField(max_length=10, choices=STATE_CHOICES,
                             default=STATE_OPEN, db_index=True)
    source = models.CharField(max_length=10, default=Subscription.SOURCE_WALLET)
    #: The Paystack reference, or the wallet transaction reference. Whatever
    #: else is true, this is the string somebody reconciles against.
    provider_reference = models.CharField(max_length=64, blank=True, default='')
    #: A code, never a sentence. `failure_detail` keeps the gateway's own words
    #: for whoever is investigating; it is never shown to a subscriber.
    failure_code = models.CharField(max_length=40, blank=True, default='')
    failure_detail = models.CharField(max_length=300, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-invoice_id']
        unique_together = ('subscription', 'period_start', 'attempt')
        indexes = [models.Index(fields=['subscription', 'state'])]

    def __str__(self):
        return '%s %s VC (%s)' % (self.token, self.amount_vc, self.state)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = _token('inv')
        if not self.plan_name and self.plan_id:
            self.plan_name = self.plan.name
        super().save(*args, **kwargs)


class BillingSettlement(models.Model):
    """One pass that paid everybody owed on a plan's subscriptions.

    Same shape and the same reason as `EventSettlement`: a RUN is safe to
    repeat where a queue of individual payouts is not, because each line it
    pays is stamped with the run that paid it inside the transaction that moved
    the coins.
    """

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE,
                             related_name='settlements')
    run_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='+')
    amount_vc = models.IntegerField(default=0)
    lines_paid = models.IntegerField(default=0)
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class BillingLedgerEntry(models.Model):
    """Who is owed what out of one paid invoice.

    A ledger, not a balance. Every charge writes the lines it created and a
    balance is the SUM of unsettled lines, because a running total incremented
    at the till drifts the first time a refund lands and there is then no way
    to find out by how much. A subscription charges the same person every month
    for years, so it has more chances to drift than anything else here.
    """

    KIND_ORGANISER = 'organiser'
    KIND_PLATFORM = 'platform'
    KIND_REVERSAL = 'reversal'
    KIND_CHOICES = [
        (KIND_ORGANISER, 'Organiser'),
        (KIND_PLATFORM, 'Platform fee'),
        (KIND_REVERSAL, 'Reversal'),
    ]

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE,
                             related_name='ledger_entries')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name='ledger_entries')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    #: Who the line is payable to. Null for the platform's own cut, which is
    #: not paid through anybody's wallet.
    user = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name='+')
    #: Null unless the plan belongs to an organisation, in which case the money
    #: is the organisation's rather than the person who happens to own it.
    org = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True,
                            blank=True, related_name='+')

    amount_vc = models.IntegerField(default=0)
    gross_vc = models.IntegerField(default=0)
    fee_vc = models.IntegerField(default=0)
    fee_pct = models.FloatField(default=0)

    settlement = models.ForeignKey(BillingSettlement, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='lines')
    settled_at = models.DateTimeField(null=True, blank=True)

    #: A reversal is a NEW line with the opposite sign, never an edit to the
    #: original. Editing a settled line rewrites a payment already made;
    #: editing an unsettled one erases the fact that the charge happened.
    reverses = models.ForeignKey('self', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='+')
    reversed_by = models.ForeignKey('self', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='+')

    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['plan', 'settled_at']),
            models.Index(fields=['user', 'settled_at']),
        ]

    def __str__(self):
        return '%s %s VC' % (self.kind, self.amount_vc)

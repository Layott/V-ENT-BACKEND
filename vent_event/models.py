from datetime import datetime, timedelta

from django.db import models
from vent_auth.models import Users, Games, Teams, Organization
from django.utils import timezone


class Event(models.Model):
    event_id = models.AutoField(primary_key=True)  # Event ID
    name = models.CharField(max_length=40)  # Name of the event
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    # An event may belong to an organisation rather than to one person. Only
    # then may the creator hand management of it to somebody else.
    organization = models.ForeignKey(
        'vent_auth.Organization', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events')
    series = models.ForeignKey(
        'vent_auth.GameSeries', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events')
    creator = models.ForeignKey(Users, on_delete=models.CASCADE)  # Creator of the event
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)  # Last updated timestamp
    event_type = models.CharField(max_length=8)  # physical | virtual | hybrid
    category = models.CharField(max_length=20, null=True, blank=True)  # esports | anime | concert | convention | other
    desc = models.TextField(null=True, blank=True)  # Description of the event
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, null=True, blank=True)  # Entry fee
    # Which currency the organiser typed the prices in. Everything is still
    # settled in naira; this says what the numbers on the form meant, so a
    # reader elsewhere can be shown the same price in their own money.
    currency = models.CharField(max_length=3, default='NGN')

    # Canonical event schedule (the FE sends a single start/end datetime).
    start_date = models.DateTimeField(null=True, blank=True)  # Event start (canonical)
    end_date = models.DateTimeField(null=True, blank=True)  # Event end (canonical)

    # Legacy split fields - kept for back-compat, auto-derived from start_date/end_date on create.
    reg_start_date = models.DateTimeField(null=True, blank=True)  # Registration start date
    reg_end_date = models.DateTimeField(null=True, blank=True)  # Registration end date
    event_date = models.DateField(null=True, blank=True)  # Date of the event
    start_time = models.TimeField(null=True, blank=True)  # Start time of the event
    end_time = models.TimeField(null=True, blank=True)  # End time of the event

    # For physical events, location is required; for virtual events, event_link is required.
    # For hybrid events, both location and event_link are required.
    location = models.CharField(max_length=255, null=True, blank=True)  # Location for physical events
    event_link = models.CharField(max_length=255, null=True, blank=True)  # Link for virtual events
    capacity = models.PositiveIntegerField(null=True, blank=True)  # Max attendees

    # What that capacity counts, which is the organiser's to decide and not
    # ours to assume.
    #
    # A venue holding 5000 over two days usually means 5000 people on Saturday
    # who go home, and 5000 more on Sunday: 10000 tickets sold against one
    # 5000-seat room. That is PER_DAY, and it is the common case for anything
    # with a daily programme.
    #
    # But a residential weekend, a camp, or anything where the same people stay
    # throughout is bounded by 5000 across the whole engagement however many
    # days it runs. That is TOTAL.
    #
    # Guessing wrongly is expensive in both directions: guess TOTAL and half
    # the tickets never go on sale, guess PER_DAY and the room is oversold.
    CAPACITY_PER_DAY = 'per_day'
    CAPACITY_TOTAL = 'total'
    CAPACITY_MODES = (
        (CAPACITY_PER_DAY, 'Each day starts afresh'),
        (CAPACITY_TOTAL, 'Counted across the whole event'),
    )
    capacity_mode = models.CharField(
        max_length=10, choices=CAPACITY_MODES, default=CAPACITY_PER_DAY)
    logo = models.ImageField(upload_to='event_logos/', null=True, blank=True)  # Event logo upload path
    banner = models.ImageField(upload_to='event_banners/', null=True, blank=True)  # Event banner upload path
    banner_url = models.URLField(max_length=500, null=True, blank=True)  # External banner URL (used when no file upload)
    is_active = models.BooleanField(default=True)  # To mark if the event is active or not
    is_featured = models.BooleanField(default=False)  # Manually spotlight an event on the listing
    interaction_count = models.PositiveIntegerField(default=0)

    # How many tickets one email address may hold for this event.
    #
    # CEO: "it should be just one per email, so if a ticket has been sent to an
    # email before, it should not be sent again, even if they refresh and type
    # in that same email again... the owner should be able to set if one person
    # can get multiple tickets or its limited to one per mail."
    #
    # Stored as a number rather than a boolean because the organiser's real
    # question is "how many", and a family of four is the next thing anybody
    # asks for. `None` means no limit at all.
    max_tickets_per_email = models.PositiveIntegerField(null=True, blank=True, default=None)

    # Getting there.
    #
    # `location` is a line of text an organiser typed, which is enough to print
    # on a ticket and not enough to travel to. These three are the rest of the
    # answer, and they are separate fields because they are three different
    # things: where the pin drops, what the building is called on the day, and
    # everything a map cannot tell you.
    map_link = models.URLField(max_length=500, blank=True, default='')
    venue_name = models.CharField(max_length=140, blank=True, default='')
    directions = models.TextField(blank=True, default='')

    # Where the pin actually goes.
    #
    # `map_link` is a URL somebody pasted, which opens a map somewhere else. It
    # cannot be drawn on a map here, so "Getting there" was a heading with a
    # link under it and nothing to look at. These two are what a map needs.
    #
    # Filled from the pasted link where it carries a coordinate, which most
    # Google and Apple Maps URLs do, so an organiser is not asked to type
    # numbers they have already given us.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Arriving without a steward scanning you.
    #
    # The door flow already works and is the right one for a gate with staff on
    # it. This is for the rest: a virtual event, a meet-up of thirty people, a
    # session inside a venue somebody is already inside. Off by default, because
    # an attendee who can admit themselves can admit themselves from home.
    self_check_in = models.BooleanField(default=False)
    # How long before the doors somebody may do it. Not a free window: a ticket
    # marked used at 9am for a 7pm event tells the organiser nothing about who
    # actually turned up, and attendance is the number they act on.
    self_check_in_opens_minutes = models.PositiveIntegerField(default=120)

    def starts_at(self):
        """The moment the event begins, timezone aware, or None.

        The date and the time are separate columns, which is how the form asks
        for them. Everything that reasons about "before the event" needs them
        put back together, and doing it in each caller is how two of them end up
        disagreeing.
        """
        if not self.event_date or not self.start_time:
            return None
        naive = datetime.combine(self.event_date, self.start_time)
        return timezone.make_aware(naive, timezone.get_current_timezone()) \
            if timezone.is_naive(naive) else naive

    def ends_at(self):
        """The moment it finishes. Rolls past midnight when it has to.

        An event running 21:00 to 02:00 ends the following day. Comparing the
        two times numerically would make it end five hours before it started,
        and every window computed from it would be closed.
        """
        started = self.starts_at()
        if started is None or not self.end_time:
            return None
        day = self.event_date
        if self.start_time and self.end_time <= self.start_time:
            day = day + timedelta(days=1)
        naive = datetime.combine(day, self.end_time)
        return timezone.make_aware(naive, timezone.get_current_timezone()) \
            if timezone.is_naive(naive) else naive

    def self_check_in_window(self):
        """(opens, closes) for admitting yourself, or (None, None).

        Closes at the end of the event rather than at its start. Somebody
        arriving late is still somebody who came.
        """
        started = self.starts_at()
        if started is None:
            return None, None
        opens = started - timedelta(minutes=self.self_check_in_opens_minutes or 0)
        return opens, (self.ends_at() or started + timedelta(hours=6))

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # The slug follows the name. Whatever it replaces is kept in SlugHistory
        # and redirects here, so a renamed event keeps every link ever shared.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, self.name, entity_type='event', id_attr='event_id',
        )
        # A caller that named its fields (edit_event does) would otherwise
        # compute the new slug and never write it, which is the whole rename path.
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})

        # A pasted map link usually already carries the coordinate. Reading it
        # saves the organiser from typing numbers they have just given us, and
        # typing a coordinate by hand is the step where a venue ends up in the
        # Gulf of Guinea. Never overwrites a coordinate somebody set.
        if self.map_link and (self.latitude is None or self.longitude is None):
            from .geo import point_from_map_link

            point = point_from_map_link(self.map_link)
            if point:
                self.latitude, self.longitude = point
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields'] = list(
                        set(kwargs['update_fields']) | {'latitude', 'longitude'})
        super().save(*args, **kwargs)


class TicketTier(models.Model):
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ticket_tiers")
    name = models.CharField(max_length=60)  # e.g. General Admission, VIP, Backstage
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Price in NGN
    quantity = models.PositiveIntegerField(default=0)  # Total tickets in this tier
    sold = models.PositiveIntegerField(default=0)  # Tickets sold (Phase 2 ticketing increments this)
    perks = models.CharField(max_length=255, blank=True, default='')  # Comma / bullet separated perks

    # Which day of the event this admits you to. Null means the whole run, which
    # is what a single-day event and a full-festival pass both want. A dated
    # tier is what lets a three-day convention price Friday and Sunday
    # differently and count each door separately.
    day = models.DateField(null=True, blank=True)
    day_label = models.CharField(max_length=60, blank=True, default='')  # "Day 1", "Finals day"

    # ---------------------------------------------------------------- pricing
    #
    # Three ways a price moves, all on the tier because all three answer the
    # same question: what does this type cost, and who may see it.

    # Early bird: the price after `early_bird_quantity` have gone. Zero means
    # the price never moves, which is what almost every type does.
    early_bird_quantity = models.PositiveIntegerField(default=0)
    early_bird_price = models.DecimalField(max_digits=10, decimal_places=2,
                                           null=True, blank=True)

    # Group rate: at or above `group_min`, each ticket costs `group_price`.
    # Per ticket rather than a total, because that is how somebody buying six
    # thinks about it and how every platform states it.
    group_min = models.PositiveIntegerField(default=0)
    group_price = models.DecimalField(max_digits=10, decimal_places=2,
                                      null=True, blank=True)

    # Access code: while set, this type is invisible and unbuyable until
    # somebody types the code. For a members' presale or a sponsor's allocation.
    access_code = models.CharField(max_length=40, blank=True, default='')

    # How many of THIS type one email address may hold.
    #
    # CEO: "if there is several different days or types of ticket, the option to
    # set this for each ticket type and day should be available. for all tickets
    # and days at once also."
    #
    # The event-wide number could not express the thing organisers actually
    # want, which is a different rule per type: one VIP each, four General
    # Admission, and a day pass capped per day. A single number for the whole
    # event forces the strictest of those onto all of them.
    #
    # `None` means this type sets no rule of its own and is bounded only by the
    # event-wide number and by its day. It is not "unlimited": the wider scopes
    # still apply, and every scope that has a number is checked.
    max_tickets_per_email = models.PositiveIntegerField(null=True, blank=True,
                                                        default=None)

    # A ticket type only an influencer's audience can buy.
    #
    # CEO: "there should also be an option where a ticket is locked behind an
    # influencers link or if the influencer will have codes attributed to them
    # and so only those who have those codes, can use them to redeem a ticket."
    #
    # A pointer to the referral rather than a second copy of its code, so
    # rotating the influencer's code cannot leave a tier unlockable by a code
    # nobody is handing out any more. Null means the tier is not locked to
    # anybody, which is every ordinary tier.
    unlocked_by = models.ForeignKey(
        'EventReferral', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='unlocks_tiers',
    )

    def price_for(self, quantity=1):
        """What one ticket costs right now, at this quantity.

        Group rate wins over early bird when both apply, because somebody
        buying ten is the case the organiser most wants to reward and the two
        discounts stacking is never what anybody meant.
        """
        if self.group_min and self.group_price is not None and quantity >= self.group_min:
            return self.group_price
        if (self.early_bird_quantity and self.early_bird_price is not None
                and int(self.sold) >= int(self.early_bird_quantity)):
            return self.early_bird_price
        return self.price

    @property
    def is_hidden(self):
        # Hidden means "not on the public list": either it wants a code of its
        # own, or it belongs to an influencer and only their audience sees it.
        return bool(self.access_code) or self.unlocked_by_id is not None

    def opened_by(self, code):
        """Whether `code` unlocks this tier.

        Two ways in and they are deliberately different things: a tier's own
        access_code is a password the organiser set, and `unlocked_by` points at
        an influencer whose referral code is the key. Checking the referral by
        pointer rather than by a copied string means rotating that influencer's
        code takes effect immediately, everywhere.
        """
        given = str(code or '').strip().lower()
        if not self.is_hidden:
            return True
        if not given:
            return False
        if self.access_code and given == self.access_code.strip().lower():
            return True
        if self.unlocked_by_id is not None:
            referral = self.unlocked_by
            if referral and referral.is_active:
                if given == str(referral.code or '').strip().lower():
                    return True
        return False

    class Meta:
        ordering = ['day', 'id']

    def __str__(self):
        return f"{self.name} - {self.event.name}"


class EventReferral(models.Model):
    """An influencer or a link that sells tickets, and what they are owed credit for.

    Separate from the promo code because the person and the code are different
    things: one influencer may run several codes over a campaign, and a code can
    exist with nobody to credit. Folded together, half the columns would be
    empty in both directions.
    """
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='referrals')
    name = models.CharField(max_length=120)  # the person or outlet
    code = models.CharField(max_length=40)  # what goes in the link: /events/x?ref=CODE
    url = models.URLField(max_length=500, blank=True, default='')  # their channel, for the organiser's own records
    sponsor = models.ForeignKey('Sponsor', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='referrals')

    # How many tickets are set aside for them. 0 means no cap, which is the
    # ordinary case: most links are tracking, not an allocation.
    allocation = models.PositiveIntegerField(default=0)
    sold = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'code')
        ordering = ['name']

    @property
    def remaining(self):
        """None when uncapped, otherwise how many are left to sell."""
        if not self.allocation:
            return None
        return max(self.allocation - self.sold, 0)

    def __str__(self):
        return f"{self.name} ({self.code})"


class ReferralDay(models.Model):
    """How one influencer link did on one day.

    A day per link, not a row per visitor. The alternative - a row carrying an
    address and a user agent for every arrival - is a log of who read what,
    which is a thing to be subpoenaed rather than a thing to be useful. The
    organiser's question is "did this influencer bring anybody", and a daily
    count answers it exactly.

    `visitors` counts arrivals whose browser had not been here before, which
    the browser itself reports by whether it already holds the link cookie.
    Nothing about the person is stored to work that out.
    """
    id = models.AutoField(primary_key=True)
    referral = models.ForeignKey('EventReferral', on_delete=models.CASCADE,
                                 related_name='days')
    day = models.DateField(db_index=True)
    visits = models.PositiveIntegerField(default=0)
    visitors = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('referral', 'day')
        ordering = ['day']

    def __str__(self):
        return f"{self.referral.code} {self.day}: {self.visits}"


class EventPromo(models.Model):
    """A discount code, optionally credited to a referral.

    `max_tickets` is the number of TICKETS the code may be used on, not the
    number of times it may be redeemed, because one order can carry several
    tickets and the organiser is budgeting seats rather than transactions.
    """
    PERCENT = 'percent'
    AMOUNT = 'amount'
    KIND_CHOICES = [(PERCENT, 'Percent off'), (AMOUNT, 'Amount off')]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='promos')
    code = models.CharField(max_length=40)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=PERCENT)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    referral = models.ForeignKey(EventReferral, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='promos')
    tier = models.ForeignKey(TicketTier, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='promos')  # null = every tier

    max_tickets = models.PositiveIntegerField(default=0)  # 0 = no limit
    used_tickets = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'code')
        ordering = ['code']

    @property
    def remaining(self):
        if not self.max_tickets:
            return None
        return max(self.max_tickets - self.used_tickets, 0)

    def is_usable(self, when=None, quantity=1):
        """Whether the code may be applied right now, and why not if it cannot.

        Returns (True, None) or (False, reason). The reason is a code rather
        than a sentence, so the answer can be translated on the way out.
        """
        from django.utils import timezone as _tz

        when = when or _tz.now()
        if not self.is_active:
            return False, 'PROMO_INACTIVE'
        if self.starts_at and when < self.starts_at:
            return False, 'PROMO_NOT_STARTED'
        if self.ends_at and when > self.ends_at:
            return False, 'PROMO_EXPIRED'
        if self.max_tickets and self.used_tickets + quantity > self.max_tickets:
            return False, 'PROMO_EXHAUSTED'
        return True, None

    def discount_for(self, unit_price, quantity=1):
        """What comes off the total. Never more than the total itself."""
        from decimal import Decimal

        total = Decimal(unit_price) * quantity
        if self.kind == self.PERCENT:
            off = total * (Decimal(self.value) / Decimal(100))
        else:
            off = Decimal(self.value) * quantity
        return min(off, total)

    def __str__(self):
        return f"{self.code} on {self.event.name}"


class EventManager(models.Model):
    """Somebody the organiser has let help run the event.

    Only allowed when the event belongs to an organisation. A personal event is
    one person's, and handing a stranger the door list, the attendee data and
    the promo codes on a personal event is not something to allow by accident.
    """
    ROLE_CHOICES = [
        ('manager', 'Manager'),      # everything except deleting the event
        ('door', 'Door staff'),      # check tickets in, nothing else
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='managers')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='managed_events')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='manager')
    added_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='event_managers_added')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'user')
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.username} on {self.event.name} ({self.role})"


class Sponsor(models.Model):
    """An organisation behind the event: a sponsor, or a partner.

    One model rather than two, because a partner is a sponsor with a different
    word on it. Splitting them would mean writing every screen, serializer and
    admin control twice, and the first field added to one would silently be
    missing from the other.
    """
    KIND_CHOICES = [
        ('sponsor', 'Sponsor'),
        ('partner', 'Partner'),
    ]

    sponsor_id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sponsors")
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='sponsor')
    logo = models.ImageField(upload_to='sponsor_logos/', null=True, blank=True)  # Sponsor logo upload path
    logo_url = models.URLField(max_length=500, null=True, blank=True)  # External sponsor logo URL
    website = models.URLField(max_length=500, null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['kind', 'sort_order', 'sponsor_id']

    def __str__(self):
        return '%s (%s)' % (self.name, self.kind)


class SponsorLink(models.Model):
    """Where a sponsor's logo sends you. Mirrors the event's own SocialLink.

    A table rather than a column per platform: most organisations use two or
    three of them, and a fixed row of columns would be mostly empty and still
    missing whichever one somebody actually has.
    """
    sponsor_link_id = models.AutoField(primary_key=True)
    sponsor = models.ForeignKey(Sponsor, on_delete=models.CASCADE, related_name='links')
    platform = models.CharField(max_length=50)  # e.g., twitter, instagram, youtube
    url = models.URLField(max_length=500)

    def __str__(self):
        return f"{self.platform} - {self.url}"


class SocialLink(models.Model):
    social_link_id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='social_links')
    platform = models.CharField(max_length=50)  # e.g., twitter, instagram, youtube
    url = models.URLField()

    def __str__(self):
        return f"{self.platform} - {self.url}"


class VendorInvite(models.Model):
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='vendor_invites')
    name = models.CharField(max_length=100)
    email = models.EmailField(null=True, blank=True)
    booth = models.CharField(max_length=40, blank=True, default='')

    def __str__(self):
        return f"{self.name} @ {self.event.name}"


class Ticket(models.Model):
    """A ticket somebody actually bought.

    One row per admitted person (buying 3 tickets creates 3 rows) so each has its
    own code and check-in state. Paid for in VENT COINS via the wallet, with the
    NGN tier price converted at the platform rate at purchase time - the rate is
    stored so a later rate change never rewrites history.
    """

    STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('checked_in', 'Checked in'),
        ('refunded', 'Refunded'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='tickets')
    tier = models.ForeignKey(TicketTier, on_delete=models.PROTECT, related_name='tickets')
    # Null for a guest. Somebody buying a ticket to a one-off event should not
    # have to make an account to do it, and a platform that insists loses the
    # sale rather than gaining a member. The attendee columns below carry them
    # instead, and `claim_for` attaches the ticket if they sign up later.
    user = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE,
                             related_name='event_tickets', null=True, blank=True)
    code = models.CharField(max_length=18, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='valid')
    price_vc = models.PositiveIntegerField(default=0)
    price_ngn = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Who this ticket admits - the buyer may be booking for other people, and
    # the door needs a name to check against.
    attendee_name = models.CharField(max_length=120, blank=True, default='')
    attendee_email = models.EmailField(blank=True, default='')
    attendee_phone = models.CharField(max_length=40, blank=True, default='')
    # What the organiser asked for at checkout, keyed by field id. Kept on the
    # ticket rather than on an order, because the door list is per person and a
    # dietary requirement or a jersey size belongs to the person it is about.
    answers = models.JSONField(default=dict, blank=True)
    # The payment this ticket came from, for a guest paying by card. It is what
    # makes issuing idempotent: the browser returning and Paystack calling back
    # are two arrivals for one payment, and issuing twice would put two people
    # through one door. Empty for a wallet purchase and for a free ticket.
    payment_reference = models.CharField(max_length=64, blank=True, default='',
                                         db_index=True)
    # The influencer link this sale came through, if any.
    #
    # On the ticket rather than only as a counter on the link, because a
    # counter drifts: a refund, a double-issue or a failed payment leaves it
    # wrong with no way to find out which. The organiser's numbers are counted
    # from these rows, so they are always the truth about what was sold.
    # EventReferral.sold stays as well, but only as the allocation guard.
    referral = models.ForeignKey('EventReferral', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='tickets')
    purchased_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    # Which door. "Already scanned" sends a steward to a supervisor; "scanned at
    # Gate B, 19:42" lets them decide in three seconds, which is the whole
    # difference at a busy entrance.
    checked_in_gate = models.CharField(max_length=60, blank=True, default='')
    checked_in_by = models.ForeignKey(
        'vent_auth.Users', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tickets_checked_in',
    )

    class Meta:
        ordering = ['-purchased_at']

    def __str__(self):
        return f"{self.code} · {self.tier.name} · {self.event.name}"


class Vendor(models.Model):
    """A stall at an event, with its own storefront.

    `VendorInvite` (above) is just a name on the organizer's list. A Vendor is
    the real thing: an owner who can list products and take orders paid in
    VENT COINS.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('live', 'Live'),
        ('closed', 'Closed'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='vendors')
    owner = models.ForeignKey(
        'vent_auth.Users', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendor_stalls',
    )
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=60, blank=True, default='')
    description = models.TextField(blank=True, default='')
    booth = models.CharField(max_length=40, blank=True, default='')
    logo = models.ImageField(upload_to='vendor_logos/', null=True, blank=True)
    banner = models.ImageField(upload_to='vendor_banners/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} @ {self.event.name}"


class VendorProduct(models.Model):
    id = models.AutoField(primary_key=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=140)
    description = models.TextField(blank=True, default='')
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # NGN
    image = models.ImageField(upload_to='vendor_products/', null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    sold = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} · {self.vendor.name}"


class VendorOrder(models.Model):
    """One purchase from one stall. Items live on VendorOrderItem."""

    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('ready', 'Ready for collection'),
        ('collected', 'Collected'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.AutoField(primary_key=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='orders')
    buyer = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE, related_name='vendor_orders')
    code = models.CharField(max_length=18, unique=True, db_index=True)
    total_vc = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='paid')
    created_at = models.DateTimeField(auto_now_add=True)
    collected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} · {self.vendor.name}"


class VendorOrderItem(models.Model):
    id = models.AutoField(primary_key=True)
    order = models.ForeignKey(VendorOrder, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(VendorProduct, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveIntegerField(default=1)
    unit_vc = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"


class EventTournamentLink(models.Model):
    """A tournament running inside an event.

    The event organizer owns the link, so only they can attach or detach a
    tournament. A tournament belongs to at most one event (hence `unique=True`);
    an event can carry as many tournaments as it likes.

    `shared_ticketing` is the money-relevant flag: with it on, holding a valid
    ticket for the event pays the tournament's entry fee, so the registration
    flow skips both the wallet debit and the PIN.
    """

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='tournament_links')
    tournament = models.OneToOneField(
        'vent_tournament.Tournament', on_delete=models.CASCADE, related_name='event_link',
    )
    shared_ticketing = models.BooleanField(default=False)

    # How somebody gets INTO the tournament when it sits inside an event.
    #
    # CEO: "an organizer can decide if the players in the tournament will have
    # to buy tickets to pay or the tournament will have its own registeration
    # fee, or if them getting to like the finals gets the players that got
    # there automatic tickets or not and what level of tickets for everything."
    #
    # Three answers, and they are genuinely different arrangements rather than
    # shades of one setting:
    #
    #   ticket    the event ticket IS the entry. No separate fee. This is the
    #             convention model: pay at the door, play what is on.
    #   own_fee   the tournament charges its own entry, and the event ticket is
    #             a separate purchase. This is the tournament-inside-a-festival
    #             model, where not every attendee is competing.
    #   free      entry costs nothing either way.
    ENTRY_TICKET = 'ticket'
    ENTRY_OWN_FEE = 'own_fee'
    ENTRY_FREE = 'free'
    ENTRY_CHOICES = [
        (ENTRY_TICKET, 'An event ticket is the entry'),
        (ENTRY_OWN_FEE, 'The tournament charges its own entry fee'),
        (ENTRY_FREE, 'Free either way'),
    ]
    entry_mode = models.CharField(max_length=10, choices=ENTRY_CHOICES,
                                  default=ENTRY_FREE)

    # Which ticket counts as entry, when the mode is `ticket`. Null means any
    # ticket to the event does - which is the ordinary case, and a organiser
    # naming a tier means only that tier admits you to the competition.
    entry_tier = models.ForeignKey(
        'TicketTier', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='admits_to_tournaments',
    )

    # Getting far enough earns a ticket. Null means no such reward.
    #
    # Stored as the round number a player must REACH, because that is how an
    # organiser says it: "everyone who makes the semi-finals gets a weekend
    # pass". Which pass is `reward_tier`; without one there is nothing to give,
    # so both are needed for the reward to mean anything.
    reward_from_round = models.PositiveIntegerField(null=True, blank=True)
    reward_tier = models.ForeignKey(
        'TicketTier', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='awarded_by_tournaments',
    )
    linked_by = models.ForeignKey(
        'vent_auth.Users', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='event_tournament_links',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.tournament.tournament_title} @ {self.event.name}"


class EventSession(models.Model):
    """One thing happening at an event, at a time, in a place.

    The Schedule tab was a blueprint: a function that took the event's start
    date and invented a two day programme around it. Every event on the platform
    showed the same "Doors open + Vendor zone activation", "Cosplay parade",
    "After-party + DJ set", whoever ran it and whatever it was about.

    That is worse than an empty tab. An empty tab says the organiser has not
    published a schedule; an invented one says they published this, and somebody
    turns up at 8pm for a DJ set that was never going to happen.

    Ordered by when it starts. The "day" a session belongs to is derived from
    its start time rather than stored, because a session at 1am after a Friday
    night belongs to Friday in every way that matters to somebody reading a
    schedule, and asking an organiser to resolve that is asking the wrong
    person.
    """

    session_id = models.AutoField(primary_key=True)
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='sessions')

    title = models.CharField(max_length=140)
    description = models.CharField(max_length=400, blank=True, default='')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)

    # Where in the venue. Free text because a venue's own names for its rooms
    # are the names on its signs, and a fixed list would be wrong everywhere.
    stage = models.CharField(max_length=100, blank=True, default='')

    # A session that is part of a tournament running at the event, so the two
    # are not maintained separately and cannot disagree about when a final is.
    tournament = models.ForeignKey(
        'vent_tournament.Tournament', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='event_sessions')

    # Some sessions are capped separately from the event: a panel room holds 80
    # when the venue holds 900. Zero means it is bounded by the event.
    capacity = models.PositiveIntegerField(default=0)

    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['starts_at', 'session_id']

    def __str__(self):
        return '%s: %s' % (self.event_id, self.title)


class TicketHold(models.Model):
    """Tickets taken off sale without being sold.

    Every real event has a guest list, press, the venue's own allocation and the
    artist's family. Without holds an organiser fakes it by buying their own
    tickets, which corrupts the sales figures they then report to a sponsor.

    Eventbrite's definition is the one to build to: a hold removes tickets from
    sale so you can release them later or give them to specific people. So a
    hold has two exits and both matter - **release** puts them back on sale,
    **issue** turns them into real tickets for named people.

    The influencer allocation on `EventReferral` is the same mechanism seen from
    a different angle: tickets reserved for somebody to sell. It keeps its own
    columns because it also tracks who is owed credit, but both are counted
    against what is sellable by the same function, rather than by two rules that
    can disagree.
    """

    KINDS = (
        ('guest', 'Guest list'),
        ('press', 'Press'),
        ('venue', 'Venue allocation'),
        ('artist', 'Artist and crew'),
        ('other', 'Other'),
    )

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='holds')
    # Null means the hold is against the event's capacity rather than one type,
    # which is what a venue allocation usually is.
    tier = models.ForeignKey(
        TicketTier, on_delete=models.CASCADE, null=True, blank=True,
        related_name='holds')

    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=KINDS, default='guest')
    quantity = models.PositiveIntegerField(default=0)
    # How many of the held tickets have been turned into real ones. The rest are
    # still held, and releasing gives back only what has not been issued.
    issued = models.PositiveIntegerField(default=0)

    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(
        'vent_auth.Users', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='event_holds')
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '%s: %s x%s' % (self.event_id, self.name, self.quantity)

    @property
    def outstanding(self):
        """Still held, so still not sellable."""
        if self.released_at:
            return 0
        return max(int(self.quantity) - int(self.issued), 0)


class WaitlistEntry(models.Model):
    """A place in the queue for a sold-out event.

    Built in the DICE shape rather than the usual one. A waitlist is normally a
    way to capture demand you cannot serve; DICE uses it as the RETURN VALVE that
    makes a face-value-only policy workable. Somebody whose plans change has a
    way out that is not a resale site, and the ticket goes to the next person in
    the queue at the price it was always sold at.

    That matters more here than it would elsewhere. This audience cannot afford
    to lose money to touts, and a platform with no return path pushes every
    changed plan onto WhatsApp at whatever price somebody will pay.

    Ordered by when somebody joined. First come, deliberately, unlike
    Ticketmaster's randomised lottery: a lottery exists to defeat bots at a
    scale V-ENT is nowhere near, and at this size "I was first" is both fairer
    and easier to explain.
    """

    STATUSES = (
        ('waiting', 'Waiting'),
        ('offered', 'Offered'),
        ('taken', 'Taken'),
        ('missed', 'Missed the offer'),
        ('left', 'Left the queue'),
    )

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='waitlist')
    # Null means any type. Most people want in at all, not in on one tier.
    tier = models.ForeignKey(
        TicketTier, on_delete=models.CASCADE, null=True, blank=True,
        related_name='waitlist')
    user = models.ForeignKey(
        'vent_auth.Users', on_delete=models.CASCADE, related_name='event_waitlist')

    status = models.CharField(max_length=20, choices=STATUSES, default='waiting')
    joined_at = models.DateTimeField(auto_now_add=True)

    # An offer is a held place with a clock on it. Without the clock one person
    # who stops reading their email freezes the queue behind them for ever.
    offered_at = models.DateTimeField(null=True, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['joined_at', 'id']
        # One place per person per event. Joining twice does not get you two
        # chances, and letting it would be the first thing anybody tried.
        unique_together = ('event', 'user')

    def __str__(self):
        return '%s: %s (%s)' % (self.event_id, self.user_id, self.status)


class EventCheckoutField(models.Model):
    """Something the organiser asks a buyer for.

    CEO: "they'll need to submit emails and Maybe full name and number. Or
    better still, the organizer decides what fields he wants to be collected."

    So: a list the organiser composes, the same shape as a tournament's entry
    requirements, rather than three fixed columns. A five-a-side needs a shirt
    size, a conference needs a dietary requirement, a con needs to know which
    day - and none of those is a column anybody could have guessed in advance.

    Email is not in this list. It is always collected and always required,
    because a ticket with no way to reach the holder is not a ticket: no
    receipt, no code to re-send, and nothing to attach to an account later.
    Making it optional is the one setting that would break everything after the
    sale, so it is not offered.
    """

    KINDS = (
        ('text', 'Text'),
        ('phone', 'Phone number'),
        ('number', 'A number'),
        ('choice', 'One of a list'),
        ('checkbox', 'A yes or no'),
    )

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='checkout_fields')

    label = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=KINDS, default='text')
    help_text = models.CharField(max_length=200, blank=True, default='')
    required = models.BooleanField(default=False)
    # For `choice`. Stored as a list so the order the organiser wrote them is
    # the order the buyer sees.
    options = models.JSONField(default=list, blank=True)

    # Asked once for the whole order, or once per ticket. A jersey size is per
    # person; a company name on the receipt is per order, and asking it six
    # times is how somebody abandons a basket.
    per_ticket = models.BooleanField(default=True)

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return '%s: %s' % (self.event_id, self.label)


class EventAnnouncement(models.Model):
    """A message from the organiser to everybody holding a ticket.

    PRD section 4: notifications to registered attendees.

    "The venue gate has changed", "doors are an hour later", "bring ID". These
    are the messages that decide whether people arrive at the right place, and
    until now an organiser had no way to send one except by finding everybody
    themselves.

    Two decisions worth stating:

    **It is a record, not a send.** The row is written first and the emails go
    afterwards, so an announcement that half sent is a row with a count and an
    error rather than a thing nobody can see happened. It is never edited after
    sending: recipients already have the old text in their inbox, and a message
    that says something different on the site than in the email is worse than
    the original mistake.

    **Guests get it too.** Most ticket holders on this platform have no account,
    and an announcement that only reached members would miss the majority of the
    room. Account holders additionally get it in their notification inbox.
    """

    AUDIENCE_CHOICES = [
        ('all', 'Everybody holding a ticket'),
        ('checked_in', 'People who have arrived'),
        ('not_checked_in', 'People who have not arrived yet'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='announcements')
    sent_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                null=True, related_name='event_announcements')
    subject = models.CharField(max_length=140)
    body = models.TextField(max_length=2000)
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES,
                                default='all')
    # How many addresses it went to, counted at send time. Recorded rather than
    # derived, because the ticket list moves afterwards and the honest answer to
    # "who got this" is who held a ticket when it was sent.
    recipients = models.PositiveIntegerField(default=0)
    notified_in_app = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(auto_now_add=True)
    email_error = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.event.name}: {self.subject}"


class EventPoll(models.Model):
    """A question the organiser puts to the room.

    PRD section 4: polls for attendees.

    Closed rather than deleted when it is over, because the answers are the
    point and deleting the question throws them away. `closes_at` is optional:
    plenty of polls are closed by hand when the organiser has seen enough.
    """

    #: What kind of question this is.
    #
    # CEO, 29 August 2026: the poll mechanism should be "a lot more detailed
    # with a lot more options for polling, just like google forms". It could ask
    # exactly one thing: pick one of these. That answers "which day suits you"
    # and nothing else - not "how was it out of five", not "what should we play
    # next" when the answer is a sentence, not "pick every day you can make".
    #
    # `single` is the original behaviour and the default, so every poll that
    # already exists keeps working without being touched.
    SINGLE = 'single'
    MULTIPLE = 'multiple'
    SCALE = 'scale'
    SHORT_TEXT = 'short_text'
    LONG_TEXT = 'long_text'
    RANKING = 'ranking'

    KIND_CHOICES = [
        (SINGLE, 'Pick one'),
        (MULTIPLE, 'Pick several'),
        (SCALE, 'Rate on a scale'),
        (SHORT_TEXT, 'Short answer'),
        (LONG_TEXT, 'Long answer'),
        (RANKING, 'Put in order'),
    ]

    #: Kinds that need a list of options, and kinds that must not have one.
    OPTION_KINDS = {SINGLE, MULTIPLE, RANKING}
    TEXT_KINDS = {SHORT_TEXT, LONG_TEXT}

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='polls')
    question = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=SINGLE)

    # Help text under the question, for the ones that need explaining. A poll
    # asking people to rank five things needs a sentence saying so.
    help_text = models.CharField(max_length=280, blank=True, default='')

    # `multiple` only. Zero means no bound, which is the common case.
    min_choices = models.PositiveIntegerField(default=0)
    max_choices = models.PositiveIntegerField(default=0)

    # `scale` only. One to five by default, because that is what people expect
    # when they are asked to rate something.
    scale_min = models.PositiveIntegerField(default=1)
    scale_max = models.PositiveIntegerField(default=5)
    scale_min_label = models.CharField(max_length=40, blank=True, default='')
    scale_max_label = models.CharField(max_length=40, blank=True, default='')

    # Whether the reader is expected to answer. Nothing is enforced server-side
    # by this; it exists so the screen can mark a question and so an organiser
    # can tell the difference between "nobody answered" and "nobody had to".
    required = models.BooleanField(default=False)

    # Whether people see the running count before they answer. Off by default:
    # a visible tally moves later answers toward the leader, and an organiser
    # asking "which day suits you" wants the answer, not the bandwagon.
    show_results_before_voting = models.BooleanField(default=False)

    # One question shown because of how an earlier one was answered.
    #
    # CEO, 29 August 2026: "should also be able to link questions together,
    # based off like their answers in one question and then it shows then
    # another question." Asking "which day" and then "which session on that
    # day" is two questions where the second only makes sense for some answers
    # to the first, and asking it of everybody gets noise back.
    #
    # `depends_on` is the earlier question. Then either an option that had to be
    # chosen, or a range on a scale. Both null means "shown once the earlier one
    # has been answered at all", which is the third useful case.
    depends_on = models.ForeignKey('self', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='reveals')
    depends_on_option = models.ForeignKey(
        'EventPollOption', on_delete=models.CASCADE, null=True, blank=True,
        related_name='reveals')
    depends_on_min = models.IntegerField(null=True, blank=True)
    depends_on_max = models.IntegerField(null=True, blank=True)
    is_open = models.BooleanField(default=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                   null=True, related_name='event_polls')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def visible_for(self, ticket):
        """Whether this question should be put to this ticket holder.

        A question with no condition is always shown. A question with one is
        shown only once the earlier question has been answered in the way the
        organiser named. The organiser's own view of the poll list ignores this;
        it is about what an attendee is asked.

        Both the serializer and the vote endpoint call this, because a question
        somebody must not see is also one they must not be able to answer -
        hiding it in the page would leave the endpoint open to anybody who
        looked.
        """
        if self.depends_on_id is None:
            return True
        if ticket is None:
            return False

        earlier = (EventPollVote.objects
                   .filter(poll_id=self.depends_on_id, ticket=ticket)
                   .prefetch_related('choices').first())
        if earlier is None:
            return False

        if self.depends_on_option_id is not None:
            picked = {earlier.option_id} | {
                c.option_id for c in earlier.choices.all()}
            return self.depends_on_option_id in picked

        if self.depends_on_min is not None or self.depends_on_max is not None:
            if earlier.number is None:
                return False
            if self.depends_on_min is not None and earlier.number < self.depends_on_min:
                return False
            if self.depends_on_max is not None and earlier.number > self.depends_on_max:
                return False
            return True

        # No option and no range: answered at all is enough.
        return True

    def closed(self):
        """Open, and not past its own deadline."""
        if not self.is_open:
            return True
        if self.closes_at and timezone.now() > self.closes_at:
            return True
        return False

    def __str__(self):
        return self.question


class EventPollOption(models.Model):
    """One thing somebody may pick. Ordered by the organiser, not alphabetically."""

    id = models.AutoField(primary_key=True)
    poll = models.ForeignKey(EventPoll, on_delete=models.CASCADE,
                             related_name='options')
    text = models.CharField(max_length=140)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.text


class EventPollVote(models.Model):
    """One answer.

    Identified by the TICKET, not by the account. Most people holding a ticket
    here have no account, and a poll only members could answer would be a poll
    of the wrong room. One ticket is one vote, which is also the only definition
    that cannot be gamed by signing up twice.
    """

    id = models.AutoField(primary_key=True)
    poll = models.ForeignKey(EventPoll, on_delete=models.CASCADE,
                             related_name='votes')
    # Null for every kind except `single`. Kept as the home of a single answer
    # so no existing vote has to be migrated into a new shape.
    option = models.ForeignKey(EventPollOption, on_delete=models.CASCADE,
                               related_name='votes', null=True, blank=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE,
                               related_name='poll_votes')

    # What was actually answered, which depends on the kind of question.
    #
    # `option` above still carries a `single` answer, which is why every vote
    # that already exists keeps working and keeps counting. The rest are here:
    # a number for `scale`, a sentence for the two text kinds, and rows in
    # `EventPollChoice` for `multiple` and `ranking`, where one answer is
    # several options and the order can be the point.
    number = models.IntegerField(null=True, blank=True)
    text = models.TextField(blank=True, default='')

    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One ticket, one answer per poll. Changing your mind updates the row.
        unique_together = [('poll', 'ticket')]

    def __str__(self):
        return f"{self.ticket.code} -> poll {self.poll_id}"


class EventPollChoice(models.Model):
    """One option inside one answer, for the questions where an answer is several.

    `position` is what makes `ranking` different from `multiple`: the same rows,
    read in the order the person put them in rather than as a set.
    """

    id = models.AutoField(primary_key=True)
    vote = models.ForeignKey(EventPollVote, on_delete=models.CASCADE,
                             related_name='choices')
    option = models.ForeignKey(EventPollOption, on_delete=models.CASCADE,
                               related_name='choices')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [('vote', 'option')]
        ordering = ['position', 'id']


class EventAttendeeOrigin(models.Model):
    """Roughly where one attendee is coming from, for the map on the event.

    Deliberately not a location. What is stored is the centre of a cell about
    5km across, computed by `vent_event.geo.to_cell`, and the point that was
    rounded is never written anywhere. See that module for why.

    A row exists only because somebody asked for one. Removing it is how you
    stop sharing, and there is nothing else to undo.
    """

    event = models.ForeignKey('Event', on_delete=models.CASCADE, related_name='attendee_origins')
    user = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE,
                             related_name='event_origins')
    # The cell centre. Not the attendee's location.
    cell_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    cell_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'user')
        indexes = [models.Index(fields=['event', 'cell_latitude', 'cell_longitude'])]

    def __str__(self):
        return 'origin for event %s' % self.event_id


class EventDayLimit(models.Model):
    """How many tickets one email address may hold for one day of an event.

    A day has no model of its own: it is `TicketTier.day`, a date carried by
    every type that admits you on it. So the per-day rule cannot live on a
    column somewhere, and it needs a row keyed by the date itself.

    This is deliberately not folded into `TicketTier.max_tickets_per_email`.
    They answer different questions. A three-day convention selling Standard
    and VIP on each day has six types and three days; "two tickets per day,
    whichever type" is one rule, and writing it onto six types both repeats it
    and stops being true the moment a seventh is added.

    The three scopes stack rather than override. A purchase must satisfy every
    one that has a number: the type's, the day's, and the event's. The organiser
    setting "one VIP each" and "four per day" means both, and the buyer who
    already holds a VIP is refused a second whatever the day allows.
    """

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='day_limits')
    day = models.DateField()
    max_tickets_per_email = models.PositiveIntegerField()

    class Meta:
        unique_together = ('event', 'day')
        ordering = ['day']

    def __str__(self):
        return '%s on %s: %s per email' % (
            self.event_id, self.day, self.max_tickets_per_email)


class ShortLink(models.Model):
    """A short address that stands in for a longer one on this platform.

    CEO: "add an option for people to be able to shorten their ticket links, so
    you create very short versions of the ticket links."

    A ticket link is long by the time it is worth sharing. The event carries a
    readable slug, the tickets sit behind a tab, and an influencer's link adds a
    code on the end, so what an organiser is asked to read out on a livestream
    or print on a flyer is seventy characters of which sixty are punctuation.

    Three things this is careful about.

    **The target is a path on this site, never a URL.** Storing somewhere to
    redirect to, and letting a caller choose it, is an open redirect: anybody
    could hand out a v-ent.co address that lands on a page they control, with
    the platform's name lending it credibility. `target` is validated to start
    with a single `/` and is resolved against our own origin at redirect time.

    **The token is opaque and not a counter.** Sequential short links can be
    walked by counting, which publishes every unlisted event anybody shortened.
    Same alphabet as the rest of the platform, minus the characters that are
    misread when a link is read aloud, which is exactly what these are for.
    Length is not fixed here: `views_short_links.TOKEN_LENGTH` decides it, and
    codes of different lengths coexist because a lookup is an exact match.

    **A short link is not a tracker.** It counts arrivals and nothing else. No
    address, no user agent, no row per visitor. The organiser's question is
    "did the flyer work", and a count answers it without keeping a log of who
    read what.
    """

    id = models.AutoField(primary_key=True)
    token = models.CharField(max_length=24, unique=True, db_index=True)
    # Which event this belongs to, so an organiser can find and retire their own
    # links, and so deleting an event does not leave an address pointing at a
    # page that is gone.
    # Exactly one of the two is set. It was event-only, which is why a
    # tournament's Share dialog had no shorten option at all: there was
    # nowhere to hang the link. A tournament is long in the same way and worth
    # shortening for the same reasons - read aloud on a stream, printed on a
    # flyer - so it gets the same mechanism rather than a second one that can
    # drift from it.
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              null=True, blank=True,
                              related_name='short_links')
    tournament = models.ForeignKey('vent_tournament.Tournament',
                                   on_delete=models.CASCADE,
                                   null=True, blank=True,
                                   related_name='short_links')
    # A path on this site, always beginning with '/'.
    target = models.CharField(max_length=500)
    # What the organiser calls it: "flyer", "Temi's story", "radio read".
    # One event usually wants several, and a list of identical short codes with
    # nothing to tell them apart is a list nobody can use.
    label = models.CharField(max_length=80, blank=True, default='')
    created_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                   null=True, blank=True,
                                   related_name='short_links')
    hits = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Exactly one owner. A link belonging to both, or to neither, has
            # no answer to "who may retire this" and nothing to delete with.
            models.CheckConstraint(
                check=(
                    models.Q(event__isnull=False, tournament__isnull=True)
                    | models.Q(event__isnull=True, tournament__isnull=False)
                ),
                name='shortlink_has_exactly_one_owner',
            ),
        ]

    @property
    def owner(self):
        """The thing this link points at, whichever kind it is."""
        return self.event or self.tournament

    def __str__(self):
        return '%s -> %s' % (self.token, self.target)

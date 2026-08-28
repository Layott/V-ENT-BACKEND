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
    logo = models.ImageField(upload_to='event_logos/', null=True, blank=True)  # Event logo upload path
    banner = models.ImageField(upload_to='event_banners/', null=True, blank=True)  # Event banner upload path
    banner_url = models.URLField(max_length=500, null=True, blank=True)  # External banner URL (used when no file upload)
    is_active = models.BooleanField(default=True)  # To mark if the event is active or not
    is_featured = models.BooleanField(default=False)  # Manually spotlight an event on the listing
    interaction_count = models.PositiveIntegerField(default=0)  # To track user interactions

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
    user = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE, related_name='event_tickets')
    code = models.CharField(max_length=18, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='valid')
    price_vc = models.PositiveIntegerField(default=0)
    price_ngn = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Who this ticket admits - the buyer may be booking for other people, and
    # the door needs a name to check against.
    attendee_name = models.CharField(max_length=120, blank=True, default='')
    attendee_email = models.EmailField(blank=True, default='')
    attendee_phone = models.CharField(max_length=40, blank=True, default='')
    purchased_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
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

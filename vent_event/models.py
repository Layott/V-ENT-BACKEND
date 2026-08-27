from django.db import models
from vent_auth.models import Users, Games, Teams, Organization
from django.utils import timezone


class Event(models.Model):
    event_id = models.AutoField(primary_key=True)  # Event ID
    name = models.CharField(max_length=40)  # Name of the event
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
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

    def __str__(self):
        return f"{self.name} - {self.event.name}"


class Sponsor(models.Model):
    sponsor_id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sponsors")
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='sponsor_logos/', null=True, blank=True)  # Sponsor logo upload path
    logo_url = models.URLField(max_length=500, null=True, blank=True)  # External sponsor logo URL

    def __str__(self):
        return self.name


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

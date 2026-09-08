from datetime import datetime, timedelta

from django.core.exceptions import ValidationError

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
    # CEO, 7 September 2026, from the ticketing research: who bears the
    # platform fee. 'organiser' is what every event has done implicitly since
    # ticketing shipped, so it stays the default; 'buyer' adds it on top and
    # the checkout says the number BEFORE anybody pays, never as a surprise
    # line on a receipt. A free ticket carries no fee either way.
    FEE_ORGANISER = 'organiser'
    FEE_BUYER = 'buyer'
    FEE_BEARER_CHOICES = [(FEE_ORGANISER, 'The organiser absorbs it'),
                          (FEE_BUYER, 'The buyer pays it on top')]
    fee_bearer = models.CharField(max_length=16, choices=FEE_BEARER_CHOICES,
                                  default=FEE_ORGANISER)

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

        The canonical column is read FIRST. `save()` keeps the trio in step
        with it, so the two agree - but reading the canonical one means a row
        written by something that bypassed `save()` (a bulk update, a
        migration, a fixture) still reasons from the same field the rest of the
        platform displays, rather than from a stale copy of it.
        """
        if self.start_date is not None:
            return self.start_date
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
        if self.end_date is not None:
            return self.end_date
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

        # And when there is no link at all, the ADDRESS itself.
        #
        # CEO, 8 September 2026: "when an event organizer puts an address it
        # should be located on the map and shown". Until now the map appeared
        # only for somebody who pasted a Google Maps URL, and most organisers
        # simply type where it is - so "Landmark Centre, Victoria Island,
        # Lagos" produced "There is no map of this venue."
        #
        # Never overwrites a coordinate somebody set, exactly as above. The
        # venue name goes in front of the address because "Landmark Centre,
        # Victoria Island" finds the building and "Victoria Island" alone
        # finds the district, and a pin on a district looks precise while
        # being wrong.
        if self.latitude is None or self.longitude is None:
            from .geo import geocode

            point = geocode(self.venue_name, self.location)
            if point:
                self.latitude, self.longitude = point
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields'] = list(
                        set(kwargs['update_fields']) | {'latitude', 'longitude'})

        # ONE answer to when this event happens.
        #
        # CEO, 7 September 2026: "The model has two ways to say when an event
        # happens - fix this."
        #
        # `start_date` and `end_date` were commented "canonical" while
        # `starts_at()` and `ends_at()` - which every window, every door and
        # every reminder is computed from - read the LEGACY trio
        # `event_date` / `start_time` / `end_time`. Nothing reconciled them, so
        # a caller that set the canonical pair and not the trio moved the event
        # everywhere it is DISPLAYED and nowhere it is REASONED about. The five
        # events in production happen to agree today, which is luck rather than
        # design, and it is the kind of luck that ends on the day somebody
        # writes a second edit endpoint.
        #
        # `start_date` wins. The trio is derived from it and kept in step, so
        # both halves of the model always say the same thing and older callers
        # that still read the trio keep working.
        derived = self._sync_when()
        if derived and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | derived)

        super().save(*args, **kwargs)

        # What is stored is now what was just written, so a second edit on the
        # same instance compares against the right baseline rather than against
        # the state two saves ago.
        self._loaded_when = {
            'event_date': self.event_date, 'start_time': self.start_time,
            'end_time': self.end_time, 'start_date': self.start_date,
            'end_date': self.end_date,
        }

    @classmethod
    def from_db(cls, db, field_names, values):
        """Remember when this event said it happened, as loaded.

        `_sync_when` needs to know which half of the model an edit touched, and
        the only way to know is to compare against what was there before.
        """
        instance = super().from_db(db, field_names, values)
        instance._loaded_when = {
            'event_date': instance.event_date,
            'start_time': instance.start_time,
            'end_time': instance.end_time,
            'start_date': instance.start_date,
            'end_date': instance.end_date,
        }
        return instance

    def _sync_when(self):
        """Make the canonical pair and the legacy trio agree. Returns the set
        of fields written, so `save()` can add them to `update_fields`.

        Without that last part a save that named its fields computes the new
        values and silently drops them, which is the same trap the slug helper
        documents and the reason a rename used to do nothing.
        """
        touched = set()
        current = timezone.get_current_timezone()

        # `save()` runs BEFORE Django coerces what a caller passed in, so
        # `Event.objects.create(start_time='19:00')` reaches here as a string
        # and `datetime.combine` refuses it. The old `starts_at()` never met
        # this because it only ran after a database round trip had already
        # turned everything into real objects.
        #
        # Each field converts its own value, which is exactly what
        # `to_python` is for, and a value it cannot read is left alone rather
        # than raising - a date that will not parse is the database's refusal
        # to give, not this helper's.
        def coerce(name):
            raw = getattr(self, name)
            if raw is None or raw == '':
                return None
            try:
                value = self._meta.get_field(name).to_python(raw)
            except (ValidationError, TypeError, ValueError):
                return None
            if value is not None and value is not raw:
                setattr(self, name, value)
            return value

        coerce('event_date')
        coerce('start_time')
        coerce('end_time')
        coerce('start_date')
        coerce('end_date')

        def as_local(value):
            if value is None:
                return None
            return timezone.localtime(value, current) if timezone.is_aware(value)                 else timezone.make_aware(value, current)

        start = as_local(self.start_date)
        end = as_local(self.end_date)

        # WHICH SIDE MOVED.
        #
        # "`start_date` wins" is right for a new row and wrong for an edit that
        # touched only the trio: the canonical column still holds the OLD
        # moment, so blindly preferring it reverts the change and reports
        # success. That is the same fault this helper exists to stop, pointed
        # the other way, and it broke three self check-in tests that move an
        # event by setting `event_date` and `start_time` alone.
        #
        # So the side that actually CHANGED is the side that wins. `_loaded_when`
        # is what the database gave us; anything differing from it is what this
        # save is trying to say.
        loaded = getattr(self, '_loaded_when', None)
        if loaded is not None:
            trio_moved = (
                self.event_date != loaded['event_date']
                or self.start_time != loaded['start_time']
                or self.end_time != loaded['end_time']
            )
            canonical_moved = (
                self.start_date != loaded['start_date']
                or self.end_date != loaded['end_date']
            )
            if trio_moved and not canonical_moved:
                start = None      # fall through to composing from the trio
                end = None

        if start is not None:
            # The canonical answer, pushed down into the trio the form uses.
            if self.event_date != start.date():
                self.event_date = start.date()
                touched.add('event_date')
            if self.start_time != start.time().replace(microsecond=0):
                self.start_time = start.time().replace(microsecond=0)
                touched.add('start_time')
            if end is not None and self.end_time != end.time().replace(microsecond=0):
                self.end_time = end.time().replace(microsecond=0)
                touched.add('end_time')
        elif self.event_date and self.start_time:
            # Only the trio was given, which is what the older create path and
            # every existing row do. Compose the canonical pair from it rather
            # than leaving it null, so the two halves still agree.
            naive = datetime.combine(self.event_date, self.start_time)
            self.start_date = timezone.make_aware(naive, current)
            touched.add('start_date')
            if self.end_time:
                day = self.event_date
                # An event running 21:00 to 02:00 ends the following day.
                if self.end_time <= self.start_time:
                    day = day + timedelta(days=1)
                self.end_date = timezone.make_aware(
                    datetime.combine(day, self.end_time), current)
                touched.add('end_date')

        return touched


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

    # What this link earns per sale, as a percentage of the ticket price. 0 is
    # the ordinary case and means the link only tracks, which is what every
    # link on the platform did before this. Of the TICKET price, never of the
    # buyer's total: an affiliate did not earn a share of the platform's fee.
    commission_pct = models.FloatField(default=0)
    # Who the commission is paid to. Null while nobody has claimed the link,
    # which is the ordinary state for a code handed to somebody who has not
    # signed up yet - the money accrues and is paid the day they claim it.
    payee = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                              null=True, blank=True,
                              related_name='affiliate_links')
    # Who it is FOR, when they have no account yet. An organiser knows the
    # streamer's email address, not their V-ENT username, and telling them to
    # go and find out first is how the link never gets made - the same
    # argument as every other invite on the platform (rows 145 and 146).
    #
    # Without this column the "it is paid the day they make an account"
    # sentence on the earnings screen was a promise with nothing behind it:
    # `payee` could only ever be set to somebody who already had an account,
    # so nothing could arrive later to claim. Found by walking it.
    payee_email = models.EmailField(blank=True, default='')
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
    """Somebody the organiser has let help run one event.

    Only allowed when the event belongs to an organisation. A personal event is
    one person's, and handing a stranger the door list, the attendee data and
    the promo codes on a personal event is not something to allow by accident.

    Until 4 September that was a trap rather than a rule, because no screen
    could move an event into an organisation. It can now, and an organisation's
    own managers reach its events without a row here at all: this table is for
    naming ONE person on ONE event, usually door staff for the day.
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
    # When anything on this row last moved, which is what lets a door ask for
    # only what changed.
    #
    # The attendee list was 648KB and was fetched once at page load, so a
    # ticket bought after that load was invisible to the door for the rest of
    # the day. Refreshing it on a timer would have been worse, not better: a
    # phone on a venue connection re-downloading two thirds of a megabyte every
    # few seconds starves the very request the door needs. A delta is what
    # makes refreshing affordable, so this column is not a convenience, it is
    # the thing the refresh is built on.
    #
    # `auto_now` rather than a hand-set field because a check-in, a void, a
    # reinstate and an attendee edit are all changes a door should see, and
    # only the model can be relied on to notice all four.
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
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

    def save(self, *args, **kwargs):
        """Keep `updated_at` honest even when only some fields are written.

        `auto_now` is applied in `pre_save`, but a save carrying `update_fields`
        writes ONLY the named columns, so the new timestamp is computed and
        then dropped. Every check-in path here saves that way - check_in_ticket,
        self_check_in, the void and reinstate in the console - so without this
        the column would sit at the purchase time for ever and the delta would
        never return a check-in. The door would look fixed and refresh nothing.

        This is the same fault, and the same remedy, as `sync_slug` adding
        `slug` to `update_fields` in `vent_auth/slugs.py`. It has bitten this
        codebase once already; it does not get to bite it twice.
        """
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            fields = set(update_fields)
            fields.add('updated_at')
            kwargs['update_fields'] = tuple(fields)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} · {self.tier.name} · {self.event.name}"


class VendorSlot(models.Model):
    """A pitch an organiser is SELLING, with their own rules attached.

    CEO, 7 September 2026: "event owners should be able to sell vendor slots to
    other people, they can list prices for vendor slots with their rules and
    conditions and other users should be able to buy and use the site to run
    the shop or they can invite people too."

    A slot is the OFFER; `Vendor` is the stall somebody ends up running. Two
    models rather than one because the offer outlives any single sale - "Food
    stall, 3x3m, 5 available" is one row that becomes five stalls - and because
    a slot exists before anybody has bought it, which a stall cannot.

    Invitation is the other door into exactly the same room. Both routes end
    with a `Vendor` owned by a person, and everything downstream - products,
    stock, orders, delivery - cannot tell which way they came in. That is
    deliberate: build them apart and the invited vendor and the paying vendor
    get two different products, and one of them stops being maintained.
    """

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='vendor_slots')
    name = models.CharField(max_length=120)          # "Food stall, 3x3m"
    description = models.TextField(blank=True, default='')   # what it includes
    category = models.CharField(max_length=60, blank=True, default='')

    # Priced in NGN like a ticket tier, converted to coins at purchase time at
    # the platform rate. The rate is stored on the purchase so a later change
    # never rewrites what somebody paid.
    price_ngn = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    quantity = models.PositiveIntegerField(default=1)
    sold = models.PositiveIntegerField(default=0)

    # The organiser's conditions, in their own words. Free text on purpose: an
    # events organiser knows what they need from a caterer and a platform does
    # not, and a fixed set of checkboxes would be wrong for most events.
    rules = models.TextField(blank=True, default='')

    # Bumped whenever `rules` changes. A purchase stores BOTH the version and
    # the full wording as it stood, so "what did they agree to" is answerable
    # from the purchase alone. Pointing at the live text would mean an
    # organiser editing their rules silently changes what past vendors agreed
    # to, which is the fault the gallery consent row exists to avoid.
    rules_version = models.PositiveIntegerField(default=1)

    # Some events sell a pitch to anybody; some want to see who it is first.
    requires_approval = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} @ {self.event.name}'

    @property
    def remaining(self):
        return max(self.quantity - self.sold, 0)

    @property
    def is_sold_out(self):
        return self.remaining <= 0

    def save(self, *args, **kwargs):
        """Bump the rules version when the wording changes.

        Read from the database rather than tracked in memory, because the
        organiser edits this through an endpoint that loads the row, changes
        one field and saves - and the version has to move exactly when the text
        does, not when anything else on the row does.
        """
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values_list(
                'rules', flat=True).first()
            if previous is not None and previous != self.rules:
                self.rules_version = (self.rules_version or 1) + 1
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields'] = list(
                        set(kwargs['update_fields']) | {'rules_version'})
        super().save(*args, **kwargs)


class VendorSlotPurchase(models.Model):
    """Somebody bought a pitch, and what they agreed to when they did.

    The stall it created is on `vendor`. Kept as its own row rather than as
    columns on `Vendor` because a stall can also arrive by invitation, and half
    the payment columns would be empty on those - the same reason
    `EventReferral` is separate from `EventPromo`.
    """

    id = models.AutoField(primary_key=True)
    slot = models.ForeignKey(VendorSlot, on_delete=models.CASCADE, related_name='purchases')
    buyer = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE,
                              related_name='vendor_slot_purchases')
    vendor = models.OneToOneField('Vendor', on_delete=models.CASCADE, null=True,
                                  blank=True, related_name='slot_purchase')

    price_vc = models.PositiveIntegerField(default=0)
    price_ngn = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # What they actually agreed to, word for word, on the day. Not a pointer to
    # the organiser's live text: that can be edited afterwards, and then
    # nobody can answer what was agreed.
    rules_accepted = models.TextField(blank=True, default='')
    rules_version_accepted = models.PositiveIntegerField(default=1)
    accepted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.buyer_id} bought {self.slot_id}'


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
    # A stall has a name, so it has an address made of that name. It was linked
    # as `?vendor=14`, which is the slug rule's prohibition and also useless to
    # anybody looking at the address bar wondering whose stall they opened.
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
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

    def save(self, *args, **kwargs):
        # The slug follows the name, and whatever it replaces is remembered so
        # a stall that gets renamed keeps every link already shared.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(self, self.name, entity_type='vendor', id_attr='id')
        # Without this a save that named its fields computes the new slug and
        # silently drops it, which is the entire rename path.
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)
        # A brand new stall has no primary key while the slug is being built,
        # so `build_slug` cannot disambiguate it and a second stall with the
        # same name would collide. Written once more now the key exists.
        if not self.slug:
            sync_slug(self, self.name, entity_type='vendor', id_attr='id')
            super().save(update_fields=['slug'])

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

    # A shirt has sizes; a plate of jollof does not.
    #
    # A LIST of names on the product rather than a row per variant, because
    # what a stall at an event actually needs is "which size did they ask
    # for", written on the order. Per-variant stock is a different and much
    # larger feature - a stallholder counting mediums separately from larges -
    # and inventing it here would be building a warehouse for a table.
    #
    # Empty means the product has no choices, which is the common case.
    variants = models.JSONField(default=list, blank=True)

    # Whether this can be posted. A plate of hot food cannot, and a stall that
    # offers delivery on everything will be asked to post one.
    can_deliver = models.BooleanField(default=False)

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
        # Delivery. CEO, row 146: "when people have bought, if they re doing
        # delivery there has to be a way for these thingd to works."
        #
        # Two states rather than one, because "we have posted it" and "it
        # arrived" are different facts and a stallholder can only ever know the
        # first. Collapsing them would make the vendor assert something they
        # cannot see.
        ('sent', 'Sent for delivery'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    # How the buyer gets it. Chosen at checkout, because it decides whether an
    # address is needed at all - asking everybody for one when most people are
    # collecting from a table ten metres away is how a checkout loses people.
    FULFILMENT_CHOICES = [
        ('collect', 'Collect from the stall'),
        ('deliver', 'Delivered'),
    ]

    id = models.AutoField(primary_key=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='orders')
    buyer = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE, related_name='vendor_orders')
    code = models.CharField(max_length=18, unique=True, db_index=True)
    total_vc = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='paid')
    created_at = models.DateTimeField(auto_now_add=True)
    collected_at = models.DateTimeField(null=True, blank=True)

    fulfilment = models.CharField(max_length=10, choices=FULFILMENT_CHOICES,
                                  default='collect')
    # Kept on the ORDER, not read off the buyer's profile. Somebody may have a
    # parcel sent to an office, to a friend, or to the venue, and a profile
    # address silently used as a delivery address is how a package goes to the
    # wrong place with nobody having typed anything wrong.
    delivery_name = models.CharField(max_length=120, blank=True, default='')
    delivery_phone = models.CharField(max_length=40, blank=True, default='')
    delivery_address = models.TextField(blank=True, default='')
    delivery_note = models.CharField(max_length=200, blank=True, default='')
    delivery_fee_vc = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    tracking = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} · {self.vendor.name}"


class VendorOrderItem(models.Model):
    id = models.AutoField(primary_key=True)
    order = models.ForeignKey(VendorOrder, on_delete=models.CASCADE, related_name='items')
    # Which choice they asked for, as text. On the ITEM because that is where
    # the stallholder reads it when they are packing, and because a product
    # renamed or a variant removed afterwards must not change what was ordered.
    variant = models.CharField(max_length=60, blank=True, default='')
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


class DoorLookup(models.Model):
    """Something the door asked the server about, and what came back.

    CEO, 6 September 2026, after an event admitted 1421 people by eye and
    recorded one: "once Search asks the server on a local miss, every lookup
    becomes a log line - Do it."

    The question behind it was "can we see who was searched for", and the answer
    on 4 and 5 September was no. Not because a log had rotated: because the door
    page filtered a list already sitting in the browser, so a search was never a
    request and there was nothing anywhere to read. Making Search ask the server
    fixes the door AND makes the question answerable, and those are the same
    change.

    A row rather than a log line, deliberately. A log line is grep on a box the
    organiser cannot reach; a row is something their own console can show them
    while the door is still open, which is when it is worth anything.

    What it does NOT do is admit anybody. That is the entire point of the
    endpoint it belongs to: before this, the only way to ask the server about a
    code was to check it in, so a steward confirming a name had to let them
    through to find out.
    """

    # What the door did. A search and a lookup are questions; an undo is a
    # correction, and it belongs on the same log because they are one story:
    # somebody was looked up, admitted, and then that was taken back. Keeping
    # undos somewhere else would mean reading two files to answer "what
    # happened at the gate", which is the only question anybody ever asks of
    # this table.
    KIND_CHOICES = [
        ('search', 'Search'),
        ('lookup', 'Code lookup'),
        ('undo', 'Check-in undone'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='door_lookups')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES,
                            default='search', db_index=True)
    # What was typed. Capped rather than validated: a steward's typo is exactly
    # the thing worth keeping, so nothing here rejects a term for being odd.
    term = models.CharField(max_length=120)
    # Who asked. Null survives the staff account being deleted later, which
    # should never quietly delete the record of a door.
    asked_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='door_lookups')
    # How many tickets the term matched. Zero is the interesting number: it is
    # somebody at the gate being turned away, and a run of them is a door in
    # trouble.
    matched = models.PositiveIntegerField(default=0)
    # The one ticket, when the term picked out exactly one. Kept so "who did we
    # look up" is answerable without re-running a search against a list that
    # has changed since.
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='lookups')
    # Which gate the asking device said it was, so two doors are separable.
    gate = models.CharField(max_length=60, blank=True, default='')
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-at']
        indexes = [models.Index(fields=['event', '-at'])]

    def __str__(self):
        return '%s "%s" -> %d' % (self.event_id, self.term, self.matched)


class EventFunnelDay(models.Model):
    """How many people did one thing on one event on one day.

    CEO, 7 September 2026: "organizers hsould be able o see mad metric for
    thier events and tickets, how many clicks, how many people opened it up,
    how many tapped buy, how many check out vendor, stuff like that, very
    detailed stuff."

    The tickets table already answers what was SOLD. What it cannot answer is
    what happened before that: two hundred people opened the page, forty tapped
    Buy, twelve reached checkout and nine paid. Those first three numbers exist
    nowhere until something records them, and the gap between any two of them
    is the only thing that says WHERE an event is losing people. Selling nine
    tickets because forty people wanted one is a checkout problem; selling nine
    because eleven people opened the page is a marketing problem, and the
    ticket table reads identically in both cases.

    Shaped exactly like `ReferralDay`, and for the same reason: a day per step,
    never a row per visitor. A row carrying an address and a user agent for
    every arrival is a log of who read what, which is a thing to be subpoenaed
    rather than a thing to be useful.

    `people` counts the arrivals whose browser said it had not done this step
    on this event before. Nothing is stored to work that out - the browser
    knows, and it is the only party that needs to.

    `ref` names the sub-thing where a step has one, and is '' everywhere else.
    Today that is the stall slug on `vendor_stall`, which is how an organiser
    sees WHICH vendor people walked to rather than only that some did.
    """
    STEP_OPEN = 'page_open'
    STEP_BUY = 'buy_tap'
    STEP_CHECKOUT = 'checkout_start'
    STEP_VENDORS = 'vendor_open'
    STEP_STALL = 'vendor_stall'
    STEP_SHARE = 'share'
    STEP_DIRECTIONS = 'directions'
    STEP_TICKETS = 'ticket_open'

    # The order is the funnel, and the summary reads it in this order. A step
    # added later goes where it belongs in the journey, not at the end.
    STEPS = (
        STEP_OPEN, STEP_TICKETS, STEP_BUY, STEP_CHECKOUT,
        STEP_VENDORS, STEP_STALL, STEP_SHARE, STEP_DIRECTIONS,
    )

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='funnel_days')
    day = models.DateField(db_index=True)
    step = models.CharField(max_length=32)
    ref = models.CharField(max_length=80, blank=True, default='')
    count = models.PositiveIntegerField(default=0)
    people = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('event', 'day', 'step', 'ref')
        ordering = ['day', 'step']
        indexes = [models.Index(fields=['event', 'day'])]

    def __str__(self):
        return '%s %s %s x%d' % (self.event_id, self.day, self.step, self.count)


class EventSettlement(models.Model):
    """One pass that paid everybody owed anything on one event.

    A RUN, not a queue. A queue of individual payouts worked through twice pays
    twice; a run stamps every line it paid, in the same transaction that moved
    the coins, so a second run pays nothing. That property is the whole reason
    this row exists rather than a boolean on each line.
    """
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='settlements')
    run_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                               null=True, blank=True,
                               related_name='settlements_run')
    amount_vc = models.IntegerField(default=0)
    lines_paid = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '%s settlement %s VC' % (self.event_id, self.amount_vc)


class EventLedgerEntry(models.Model):
    """One party, owed one amount, out of one purchase.

    A ledger rather than a balance. A running total incremented at the till
    drifts the first time a refund lands or an issue runs twice, and once it
    has drifted there is no way to find out by how much - so a balance here is
    always the SUM of unsettled lines, never a stored number.

    `fee_pct` and `fee_bearer` are stamped on the line at the sale. The
    platform rate can change; what a ticket sold under cannot, and a rate
    change next month must never rewrite what an event earned last month.

    A refund writes a REVERSAL line with the opposite sign rather than editing
    the original. Editing a settled line would rewrite a payment already made,
    and editing an unsettled one would erase the fact that a sale happened.
    """
    KIND_ORGANISER = 'organiser'
    KIND_PLATFORM = 'platform'
    KIND_AFFILIATE = 'affiliate'
    KIND_REVERSAL = 'reversal'
    KIND_CHOICES = [
        (KIND_ORGANISER, 'Organiser'),
        (KIND_PLATFORM, 'Platform fee'),
        (KIND_AFFILIATE, 'Affiliate commission'),
        (KIND_REVERSAL, 'Reversal'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='ledger')
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    # Null for the platform's own lines, which are not paid into a wallet, and
    # for an affiliate link whose owner has not claimed an account yet.
    user = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='event_ledger')
    referral = models.ForeignKey('EventReferral', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='ledger')
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='ledger')

    amount_vc = models.IntegerField(default=0)
    gross_vc = models.IntegerField(default=0)
    fee_vc = models.IntegerField(default=0)
    fee_pct = models.FloatField(default=0)
    fee_bearer = models.CharField(max_length=16, default='organiser')
    quantity = models.PositiveIntegerField(default=1)
    note = models.CharField(max_length=200, blank=True, default='')

    reverses = models.ForeignKey('self', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='reversal_of')
    reversed_by = models.ForeignKey('self', on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name='reverses_line')

    settlement = models.ForeignKey(EventSettlement, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='lines')
    settled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['event', 'kind', 'settled_at'])]

    def __str__(self):
        return '%s %s %s VC' % (self.event_id, self.kind, self.amount_vc)


class TicketTransfer(models.Model):
    """One ticket changing hands, and the trail it leaves.

    CEO, 7 September 2026, from the ticketing research: give a ticket to
    somebody else, the code reissues, the door sees the new holder.

    A row rather than an edit to the ticket, because the question that gets
    asked at a door is "whose ticket was this", and a ticket that has simply
    been overwritten cannot answer it. Somebody arrives with a screenshot of
    the OLD code, and the steward has to know that code was real, who it
    belonged to, and who holds it now.

    The old code is dead the moment the transfer completes. That is the point
    of reissuing rather than renaming: a code that still admits somebody after
    the ticket was given away means two people at one gate with one seat, and
    the second one is turned away having done nothing wrong.
    """
    id = models.AutoField(primary_key=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE,
                               related_name='transfers')
    from_user = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                  null=True, blank=True,
                                  related_name='tickets_given')
    to_user = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                null=True, blank=True,
                                related_name='tickets_received')
    from_email = models.EmailField(blank=True, default='')
    to_email = models.EmailField(blank=True, default='')
    from_name = models.CharField(max_length=120, blank=True, default='')
    to_name = models.CharField(max_length=120, blank=True, default='')
    # Both codes, so a steward handed the old one can see it was real and say
    # what happened to it rather than "no such ticket".
    old_code = models.CharField(max_length=18, db_index=True)
    new_code = models.CharField(max_length=18, db_index=True)
    note = models.CharField(max_length=200, blank=True, default='')
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-at']

    def __str__(self):
        return '%s -> %s' % (self.old_code, self.new_code)


class GeocodedAddress(models.Model):
    """One address, looked up once, ever.

    CEO, 8 September 2026: an organiser who types an address should get a pin
    from it. `vent_event/geo.geocode` does the asking; this is what stops it
    asking twice.

    A MISS is cached as deliberately as a hit, with both columns null. An
    address nobody can find will not become findable on the next save, and
    re-asking every time an organiser edits their event is how a free service's
    rate limit is reached and then withdrawn.
    """
    id = models.AutoField(primary_key=True)
    # Normalised by the caller: trimmed, single-spaced. Unique because the
    # whole point is one lookup per address.
    address = models.CharField(max_length=300, unique=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6,
                                   null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6,
                                    null=True, blank=True)
    # WHICH form answered. A pin found from "Landmark Centre Lagos" is a
    # building; one found from "Lagos" is a city. Keeping this means a screen
    # can eventually say which, rather than drawing both the same way and
    # looking precise while being wrong.
    matched = models.CharField(max_length=300, blank=True, default='')
    looked_up_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['address']

    def __str__(self):
        if self.latitude is None:
            return '%s (not found)' % self.address
        return '%s (%s, %s)' % (self.address, self.latitude, self.longitude)


class AbandonedCheckout(models.Model):
    """Somebody who reached the payment page for a paid ticket and never paid.

    The last GAP on the tix and selar research (inbox row 148, "abandoned
    checkout recovery"). Everything else on that list is built.

    ## Why a row exists here when `guest_buy` deliberately writes none

    `guest_buy` holds the whole order in the Paystack metadata rather than in a
    pending row, with the reason written beside it: "a payment nobody completes
    should leave nothing behind to clean up". That is still right for the
    ORDER. It is wrong for the fact that somebody tried, which is the only
    thing an organiser can act on and which the metadata takes to the grave.

    So this stores the smallest thing that answers "who nearly bought": an
    address, an event, how many, and when. Not the answers they typed, not the
    attendee names, not the card. If it is never converted and never reminded,
    it is swept.

    ## One reminder, ever, and a person presses it

    `reminded_at` is set once and checked before every send. There is no
    scheduler here on purpose. An automatic sequence to somebody who did not
    buy is a marketing list built out of a checkout, and the address was given
    to pay for a ticket, not to be marketed at. One "you did not finish"
    message about that same purchase, sent because the organiser chose to, is
    the thing a buyer expects and the most that address was given for.

    ## It resolves itself

    `converted_at` is stamped when a ticket for that reference is issued, so
    the list only ever shows people who really did not come back. A row that
    is converted is kept briefly for the funnel and then swept like the rest:
    see `sweep()`, which is what stops this table becoming an address book.
    """

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='abandoned_checkouts')
    tier = models.ForeignKey(
        'TicketTier', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='abandoned_checkouts')

    email = models.EmailField()
    quantity = models.PositiveIntegerField(default=1)
    # The Paystack reference this attempt was started under. It is how the
    # verification that DOES arrive finds the row to close, and it is unique
    # so a retried request cannot write the attempt twice.
    reference = models.CharField(max_length=64, unique=True)
    total_ngn = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    started_at = models.DateTimeField(auto_now_add=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['event', 'converted_at'])]

    def __str__(self):
        return '%s x%s %s' % (self.email, self.quantity, self.reference)

    @property
    def open(self):
        return self.converted_at is None

    @classmethod
    def sweep(cls, days=30, now=None):
        """Delete anything older than `days`, converted or not.

        A row here is an email address somebody gave in order to pay. Keeping
        it indefinitely because it might one day be useful is how a checkout
        becomes a mailing list. Thirty days is longer than any reminder is
        worth sending and shorter than anybody would call a record.
        """
        from django.utils import timezone as _tz
        cutoff = (now or _tz.now()) - timedelta(days=days)
        deleted, _ = cls.objects.filter(started_at__lt=cutoff).delete()
        return deleted

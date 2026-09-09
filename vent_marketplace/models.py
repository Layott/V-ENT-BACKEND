"""Vermillion City: what somebody is offering, and what happened to it.

Six tables, and the shape of them is the whole design decision.

**One `Listing`, not three.** The spec describes a service, a swap and a sale
with different fields under each, and three models is the obvious reading of it.
It is the wrong one: all three share an author, an address, a price, media,
messages, reports, reviews, bids, a lifecycle and an expiry, so three models
means writing all of that three times and discovering in six weeks that only one
of them learned about wishlists. `catalogue.py` holds what differs.

**Nothing here duplicates a table that exists.** Messaging a seller uses
`Conversation` and `DirectMessage`. Reporting a listing uses `UserReport` with a
marketplace `context`. Money moves through `UserWallet` and `Transaction`.
Premium is `vent_auth.premium`. What is genuinely new is a listing, a bid, a
purchase, a review and a wishlist entry.

**A purchase is a state machine with money in it.** `Purchase` holds the
buyer's coins from the moment they commit until the seller delivers, which is
what "the platform facilitates secure and reliable transactions" has to mean if
it means anything. Every move is a row: who moved it, when, and from what. See
`holds.py` for the only writer.
"""
from django.db import models
from django.utils import timezone

from vent_auth.models import Games, Organization, Users
from vent_auth.slugs import ensure_token, sync_slug

from . import catalogue


class Listing(models.Model):
    """One thing somebody is offering, of one of three kinds."""

    KIND_CHOICES = [(k, v) for k, v in catalogue.KINDS.items()]
    CATEGORY_CHOICES = [(k, v) for k, v in catalogue.CATEGORIES.items()]

    STATUSES = (
        ('draft', 'Not published'),
        ('active', 'Live'),
        ('paused', 'Paused by the seller'),
        ('sold', 'Sold or completed'),
        ('expired', 'Past its date'),
        ('removed', 'Taken down'),
    )

    listing_id = models.AutoField(primary_key=True)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True,
                            db_index=True)

    seller = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='listings')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)

    title = models.CharField(max_length=140)
    description = models.TextField(blank=True, default='')

    # --- money, and what it means depends on the kind -----------------------
    # A sale's price is per item; a service's is per hour, per package or flat,
    # which `price_kind` says. VENT COINS, whole numbers, like every other
    # price on the platform.
    price = models.IntegerField(default=0)
    price_kind = models.CharField(max_length=10, blank=True, default='')
    quantity = models.PositiveIntegerField(default=1)

    # --- a service ---------------------------------------------------------
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    available_from = models.DateTimeField(null=True, blank=True)
    available_to = models.DateTimeField(null=True, blank=True)
    experience = models.TextField(blank=True, default='')
    delivery = models.CharField(max_length=12, blank=True, default='')
    location = models.CharField(max_length=255, blank=True, default='')

    # --- a swap ------------------------------------------------------------
    offered = models.CharField(max_length=200, blank=True, default='')
    wanted = models.CharField(max_length=200, blank=True, default='')
    trade_value = models.IntegerField(null=True, blank=True)

    # --- a sale ------------------------------------------------------------
    condition = models.CharField(max_length=10, blank=True, default='')
    payment_methods = models.CharField(max_length=200, blank=True, default='')

    # --- discoverability ---------------------------------------------------
    # Tags as a list rather than a join table: they are free text a seller
    # types ("League of Legends coaching"), never a controlled vocabulary, and
    # a join table for free text is a table of typos.
    tags = models.JSONField(default=list, blank=True)
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name='listings')
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='listings')
    tournament = models.ForeignKey(
        'vent_tournament.Tournament', on_delete=models.SET_NULL, null=True,
        blank=True, related_name='listings')

    # --- premium -----------------------------------------------------------
    # Set only when the seller had premium at the time. Kept as fields rather
    # than checked live on every read, because a seller whose premium lapses
    # should not have their existing listing silently rewritten.
    discount_code = models.CharField(max_length=40, blank=True, default='')
    discount_percent = models.PositiveIntegerField(default=0)
    hoisted_until = models.DateTimeField(null=True, blank=True)

    # --- lifecycle ---------------------------------------------------------
    status = models.CharField(max_length=10, choices=STATUSES, default='draft')
    bidding = models.BooleanField(default=False)
    bids_close_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    response_time_hours = models.PositiveIntegerField(null=True, blank=True)

    views = models.PositiveIntegerField(default=0)
    inquiries = models.PositiveIntegerField(default=0)
    completed = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['kind', 'category']),
            models.Index(fields=['seller', 'status']),
        ]

    def save(self, *args, **kwargs):
        # The address follows the title, and every address it has ever had
        # keeps working. `update_fields` has to learn about the slug or a
        # rename computes the new one and drops it, which IS the rename path.
        changed = sync_slug(self, self.title, entity_type='listing',
                            id_attr='listing_id')
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)

    def is_live(self):
        if self.status != 'active':
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True

    def __str__(self):
        return '%s (%s)' % (self.title, self.kind)


class ListingMedia(models.Model):
    """A picture or a video on a listing.

    `portfolio` marks the premium half: the spec puts "samples of previous
    work" behind premium and ordinary product images in front of it, and they
    are the same upload with a different meaning, so they are one table with a
    flag rather than two.
    """

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE,
                                related_name='media')
    file = models.FileField(upload_to='marketplace/')
    caption = models.CharField(max_length=140, blank=True, default='')
    portfolio = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'created_at']


class Bid(models.Model):
    """An offer on a listing that accepts them.

    The spec: "a single bidding mechanism, with bids limited to 3 days", and a
    premium line for longer. The limit is on the LISTING's window rather than
    on each bid, because "this closes on Friday" is what a bidder needs to know.
    """

    STATUSES = (
        ('open', 'Standing'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('withdrawn', 'Withdrawn'),
        ('expired', 'The window closed'),
    )

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE,
                                related_name='bids')
    bidder = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='bids')
    amount = models.IntegerField()
    message = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUSES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-amount', 'created_at']
        indexes = [models.Index(fields=['listing', 'status'])]


class Purchase(models.Model):
    """Money held, then released or returned.

    The states, and there are only five:

        held      the buyer's coins have left their balance and are with the
                  platform. Nobody can spend them.
        released  the seller has been paid, less the commission.
        refunded  the buyer has them back.
        disputed  an admin has to decide. Nothing moves while it is here.
        cancelled the buyer changed their mind before delivery started, and the
                  hold went straight back.

    `holds.py` is the only writer. A second place that moves this row is a
    second answer to who has the money.
    """

    STATUSES = (
        ('held', 'Held by V-ENT'),
        ('released', 'Paid to the seller'),
        ('refunded', 'Returned to the buyer'),
        ('disputed', 'With an admin'),
        ('cancelled', 'Called off before delivery'),
    )

    slug = models.SlugField(max_length=40, unique=True, null=True, blank=True,
                            db_index=True)
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT,
                                related_name='purchases')
    buyer = models.ForeignKey(Users, on_delete=models.PROTECT,
                              related_name='purchases')
    seller = models.ForeignKey(Users, on_delete=models.PROTECT,
                               related_name='sales')

    # What the buyer paid, what V-ENT keeps, what the seller gets. All three
    # are stored rather than two stored and one computed, because the rate can
    # change and a purchase must always be able to say what it actually was.
    amount = models.IntegerField()
    commission = models.IntegerField(default=0)
    seller_amount = models.IntegerField(default=0)
    quantity = models.PositiveIntegerField(default=1)

    status = models.CharField(max_length=10, choices=STATUSES, default='held')
    note = models.CharField(max_length=300, blank=True, default='')

    hold_transaction = models.ForeignKey(
        'vent_auth.Transaction', on_delete=models.SET_NULL, null=True,
        blank=True, related_name='+')
    release_transaction = models.ForeignKey(
        'vent_auth.Transaction', on_delete=models.SET_NULL, null=True,
        blank=True, related_name='+')

    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='+')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['buyer', '-created_at']),
            models.Index(fields=['seller', '-created_at']),
        ]

    def save(self, *args, **kwargs):
        # An opaque token, never the primary key. A purchase cannot be named,
        # and a sequential id in an address lets anybody count their way
        # through everybody else's orders.
        if ensure_token(self, 'm') and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)


class Review(models.Model):
    """What a buyer thought, written only by somebody who actually bought.

    `purchase` is a OneToOne and it is required. A rating anybody can write is
    a rating nobody trusts, and the marketplace's whole trust story is the
    seller's record.
    """

    purchase = models.OneToOneField(Purchase, on_delete=models.CASCADE,
                                    related_name='review')
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE,
                                related_name='reviews')
    reviewer = models.ForeignKey(Users, on_delete=models.CASCADE,
                                 related_name='reviews_written')
    seller = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='reviews_received')
    rating = models.PositiveSmallIntegerField()      # 1 to 5
    body = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['seller', '-created_at'])]


class Wishlist(models.Model):
    """What somebody is looking for, so they can be told when it appears.

    A saved SEARCH rather than a saved listing: the spec says "be told when a
    wanted item appears", and a listing you have already found does not need
    watching.
    """

    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='wishlist')
    text = models.CharField(max_length=140, blank=True, default='')
    category = models.CharField(max_length=20, blank=True, default='')
    kind = models.CharField(max_length=10, blank=True, default='')
    max_price = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

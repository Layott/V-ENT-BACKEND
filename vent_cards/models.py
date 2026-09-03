# -*- coding: utf-8 -*-
"""EAFC cards, and the lineups players build out of them.

CEO, 3 September 2026: "the feature was a way to get the eafc cards that the
players wanted to use as their lineup for the next matches and then what they
picked and formation they selected was shown inside the player squad depth
overlay design, updated automatically for each player. then we had to constantly
scrape futbin for updates."

Three things, and they are deliberately separate:

  `GameCard`     the catalogue, scraped from Futbin. Shared by every
                 tournament, because a card is a fact about the game and not
                 about any one event.
  `LineupRules`  when an organiser lets lineups be submitted and when they
                 close. Per tournament, because it is theirs to set.
  `Lineup`       one player's chosen side for one tournament, and
  `LineupSlot`   the cards in it.

A card is NOT a V-ENT player. `Users` is a person with an account; a `GameCard`
is a picture of Kylian Mbappé with a rating on it. Nothing about the two should
ever be merged, and this is written down because the names invite it.
"""

from django.db import models
from django.utils import timezone

from vent_auth.models import Users
from vent_tournament.models import Tournament

from . import formations as formation_catalogue


class GameCard(models.Model):
    """One EAFC card, as Futbin describes it.

    Everything here comes from Futbin: the numbers, the club and nation, and
    BOTH images. The portrait is the player's face and the frame is the card
    around it, and they are separate files on Futbin's CDN, so they are two
    columns rather than one.

    Identity is `(source, source_id)`, which is Futbin's own resource id. A
    name is not identity: there are three Mbappés and two of them are the same
    person in different cards.
    """

    SOURCE_FUTBIN = 'futbin'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_FUTBIN, 'Futbin'),
        (SOURCE_MANUAL, 'Entered by hand'),
    ]

    ITEM_TYPES = [
        ('gold', 'Gold'), ('silver', 'Silver'), ('bronze', 'Bronze'),
        ('icon', 'Icon'), ('hero', 'Hero'), ('special', 'Special'),
        ('other', 'Other'),
    ]

    id = models.AutoField(primary_key=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES,
                              default=SOURCE_FUTBIN)
    #: Futbin's own id for the card. A string because a hand-entered card has
    #: no number and gets a slug instead.
    source_id = models.CharField(max_length=64)

    name = models.CharField(max_length=120)
    #: The name with its accents stripped and lowercased, so a search for
    #: "Mbappe" finds "Mbappé". Two cards sharing a slug are the same PERSON in
    #: different variants, which is what stops both being picked at once.
    slug = models.CharField(max_length=140, db_index=True)

    rating = models.PositiveSmallIntegerField()
    position = models.CharField(max_length=8)
    alt_positions = models.JSONField(default=list, blank=True)

    club = models.CharField(max_length=120, blank=True, default='')
    league = models.CharField(max_length=120, blank=True, default='')
    nation = models.CharField(max_length=120, blank=True, default='')
    #: Futbin's internal integer for a nation. Kept because Futbin's rows often
    #: carry no ISO code and a non-English nation name, and an id compares
    #: cleanly where a string does not.
    nation_id = models.IntegerField(null=True, blank=True)

    item_type = models.CharField(max_length=16, choices=ITEM_TYPES,
                                 default='gold')
    #: The card's own variant name from its frame file: `toty`, `if`, `gold`.
    variant = models.CharField(max_length=64, blank=True, default='')

    #: Pace, shooting, passing, dribbling, defending, physical. A dict rather
    #: than six columns because a goalkeeper's six are different numbers with
    #: different names, and the card only ever shows what it was given.
    stats = models.JSONField(default=dict, blank=True)
    weak_foot = models.PositiveSmallIntegerField(null=True, blank=True)
    skill_moves = models.PositiveSmallIntegerField(null=True, blank=True)

    price_coins = models.BigIntegerField(null=True, blank=True)

    #: The two images, both from Futbin. `image_url` is the player's portrait,
    #: `frame_url` the card art behind it. Stored as URLs rather than files:
    #: they are Futbin's CDN and re-hosting tens of thousands of images to save
    #: a hotlink is a cost with no reader.
    image_url = models.URLField(max_length=500, blank=True, default='')
    frame_url = models.URLField(max_length=500, blank=True, default='')

    #: When a scrape last SAW this card, which is different from when it last
    #: changed. A card missing for weeks has probably been delisted.
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('source', 'source_id')]
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['rating']),
            models.Index(fields=['position']),
        ]
        ordering = ['-rating', 'name']

    def __str__(self):
        return '%s %s (%s)' % (self.name, self.rating, self.item_type)


class LineupRules(models.Model):
    """When an organiser lets lineups be submitted, and when they close.

    CEO, 3 September 2026: "The submission time should be a feature and
    something the tournament organizers should be able to set."

    ESOCCER hardcoded Thursday 10:00 WAT with a Friday 21:00 to 22:00 change
    window. That is CADE's rule, not a law, so both are settings here and a
    tournament that sets neither simply never locks.

    Two shapes, because leagues and one-off tournaments are different:

      a single moment       `closes_at`, for a tournament played once
      the same time weekly  `weekly_day` and `weekly_time`, for a league

    The optional second window is for the change a lot of leagues allow after
    the deadline: one swap, a formation change, nothing more.
    """

    WEEKDAYS = [(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
                (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'),
                (6, 'Sunday')]

    id = models.AutoField(primary_key=True)
    tournament = models.OneToOneField(
        Tournament, on_delete=models.CASCADE, related_name='lineup_rules')

    #: Lineups are a feature an organiser turns on. Off, the tab is not there.
    enabled = models.BooleanField(default=True)

    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)

    weekly_day = models.PositiveSmallIntegerField(
        choices=WEEKDAYS, null=True, blank=True)
    weekly_time = models.TimeField(null=True, blank=True)

    #: The second window, after the deadline, for a limited change.
    changes_open_at = models.DateTimeField(null=True, blank=True)
    changes_close_at = models.DateTimeField(null=True, blank=True)
    #: How many cards may be swapped in that window. 0 means the window only
    #: allows a formation change or a rearrangement.
    changes_allowed = models.PositiveSmallIntegerField(default=1)

    #: The organiser's hand on the switch, which beats the clock either way.
    #: A deadline that cannot be overridden is a deadline that ruins an event
    #: when somebody's power goes out.
    locked_by_hand = models.BooleanField(default=False)
    reopened_by_hand = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return 'lineup rules for tournament %s' % self.tournament_id


class Lineup(models.Model):
    """One player's side for one tournament.

    One live lineup per player per tournament. Editing replaces its slots
    rather than making a second row, so "their lineup" is always one thing to
    look up, which is what the overlay needs.
    """

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='lineups')
    user = models.ForeignKey(
        Users, on_delete=models.CASCADE, related_name='lineups')

    formation = models.CharField(
        max_length=16, default=formation_catalogue.DEFAULT_FORMATION)

    #: Set the first time it is saved with a full eleven. A lineup with nine
    #: cards in it is a draft, not a submission, and an organiser reading the
    #: list needs to know which is which.
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('tournament', 'user')]
        ordering = ['user__username']

    def __str__(self):
        return '%s in tournament %s' % (self.user.username, self.tournament_id)

    @property
    def is_complete(self):
        """A full eleven. The bench is optional and always was."""
        return self.slots.filter(slot_index__lt=formation_catalogue.FIRST_SUB
                                 ).count() == formation_catalogue.FIRST_SUB


class LineupSlot(models.Model):
    """One card in one slot of one lineup.

    `position` is stored rather than looked up, because it is the position the
    card was picked FOR. A player who changes formation afterwards should see
    what they chose, not what the new formation would have called it.
    """

    id = models.AutoField(primary_key=True)
    lineup = models.ForeignKey(
        Lineup, on_delete=models.CASCADE, related_name='slots')
    card = models.ForeignKey(
        GameCard, on_delete=models.PROTECT, related_name='in_lineups')
    slot_index = models.PositiveSmallIntegerField()
    position = models.CharField(max_length=8, blank=True, default='')

    class Meta:
        unique_together = [('lineup', 'slot_index')]
        ordering = ['slot_index']

    def __str__(self):
        return 'slot %s of lineup %s' % (self.slot_index, self.lineup_id)

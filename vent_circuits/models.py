"""Circuits (P5, design section 13).

A circuit is a season of tournaments run as one competition: "a series of
tournaments and rankings" split by season, region and tier (Toornament, T12),
with a point table per tier in start.gg's form (S7). The design's layer
diagram puts it above Event and Tournament: it owns no matches of its own, it
reads the final standings of the tournaments linked to it.

Four tables, one concept each:

- `Circuit`: the competition, its game and entrant type, season, region, the
  engine that ranks it and the table used when a tier has none.
- `CircuitTier`: a level of event inside it (Major, Open, Weekly), with its
  own point table, so a bigger event is worth more.
- `CircuitTournament`: a tournament counted in it, at a tier. One to one: a
  tournament belongs to at most one circuit (Toornament's rule), so its points
  can never be counted twice by two rankings that disagree.
- `CircuitAdjustment`: points given or taken by hand, always with a public
  reason (start.gg's point adjustments, S7).

Divisions (P5d, "promotion and relegation move entrants between division
circuits across seasons"):

- `CircuitLeague`: a ladder of circuits, one per division, season after
  season, with how many go up and down at the end of each.
- A circuit in a league names its division (1 is the top), its season number
  in the league, and the circuit of the season before it.
- `CircuitMember`: who is in a division. A division's ranking is its members,
  listed from the first day; ending a season moves them.
"""
from django.db import models

from vent_auth.models import Games, Organization, Teams, Users


#: Addresses the circuit doors use themselves; a circuit never takes one.
RESERVED_SLUGS = ('leagues',)


class CircuitLeague(models.Model):
    """Division circuits ranked as one ladder across seasons (P5d)."""
    ENTRANTS = (('player', 'Players'), ('team', 'Teams'))

    slug = models.SlugField(max_length=160, unique=True, blank=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='circuit_leagues')
    entrant = models.CharField(max_length=10, choices=ENTRANTS, default='player')
    region = models.CharField(max_length=40, blank=True, default='')
    #: How many of each division go up (none from the top one) and down (none
    #: from the bottom one) when a season ends.
    up = models.PositiveSmallIntegerField(default=2)
    down = models.PositiveSmallIntegerField(default=2)
    owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='circuit_leagues')
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='circuit_leagues')
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from vent_auth.slugs import sync_slug
        changed = sync_slug(self, self.name, entity_type='circuit_league', id_attr='pk')
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)


class Circuit(models.Model):
    ENTRANTS = (('player', 'Players'), ('team', 'Teams'))
    ENGINES = (('points', 'Points from final standings'), ('elo', 'Elo from match results'))

    slug = models.SlugField(max_length=160, unique=True, blank=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='circuits')
    entrant = models.CharField(max_length=10, choices=ENTRANTS, default='player')
    #: "2026 Season 1": words, because organisers name seasons their own way.
    season = models.CharField(max_length=60, blank=True, default='')
    #: One of vent_auth.regions.ORDER, or blank for everywhere.
    region = models.CharField(max_length=40, blank=True, default='')
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    engine = models.CharField(max_length=10, choices=ENGINES, default='points')
    #: Engine settings (Elo's K, P5b). Read through standings.py, never raw.
    settings = models.JSONField(default=dict, blank=True)
    #: The table a tournament with no tier, or a tier with no table, scores by.
    default_table = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='circuits')
    #: Who runs it, shown on its page. Not a permission: the owner and an
    #: admin manage it, as with a tournament.
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='circuits')
    #: A draft is its manager's alone; published, anybody can read it.
    is_published = models.BooleanField(default=False)
    #: A division of a league (P5d): which one (1 is the top), in which of the
    #: league's seasons, and the circuit it carries on from.
    league = models.ForeignKey(CircuitLeague, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='divisions')
    division = models.PositiveSmallIntegerField(null=True, blank=True)
    league_season = models.PositiveSmallIntegerField(null=True, blank=True)
    previous = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='following')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # The slug follows the name; the one it replaces stays in SlugHistory,
        # so a renamed circuit keeps every link ever shared (the slug rule). A
        # division says its season too, because every season of a league has
        # a "Division 1".
        from vent_auth.slugs import sync_slug
        from django.utils.text import slugify
        source = '%s %s' % (self.name, self.season) if self.league_id and self.season else self.name
        if slugify(source) in RESERVED_SLUGS:
            source = '%s circuit' % source
        changed = sync_slug(self, source, entity_type='circuit', id_attr='pk')
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)


class CircuitTier(models.Model):
    circuit = models.ForeignKey(Circuit, on_delete=models.CASCADE, related_name='tiers')
    name = models.CharField(max_length=60)
    order = models.PositiveSmallIntegerField(default=0)
    #: tables.parse() output; empty means the circuit's default table.
    table = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['circuit', 'name'], name='circuit_tier_name_unique')]

    def __str__(self):
        return '%s: %s' % (self.circuit_id, self.name)


class CircuitTournament(models.Model):
    circuit = models.ForeignKey(Circuit, on_delete=models.CASCADE, related_name='links')
    tournament = models.OneToOneField('vent_tournament.Tournament', on_delete=models.CASCADE,
                                      related_name='circuit_link')
    tier = models.ForeignKey(CircuitTier, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='links')
    #: Who played for each team entry, frozen the first time the circuit
    #: counts the tournament: {registration id: [user ids]}. Somebody leaving
    #: the club later keeps what they earned; somebody joining later gets none.
    roster = models.JSONField(default=dict, blank=True)
    added_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['added_at', 'id']


class CircuitMember(models.Model):
    """Somebody in a division (P5d): a person or a club, as the league ranks."""
    CAME = (('start', 'Put in by the organiser'), ('up', 'Came up'), ('down', 'Came down'),
            ('stayed', 'Stayed'))

    circuit = models.ForeignKey(Circuit, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='circuit_memberships')
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='circuit_memberships')
    came = models.CharField(max_length=10, choices=CAME, default='start')
    #: The division they were in the season before, when a season end put them here.
    came_from = models.ForeignKey(Circuit, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['added_at', 'id']
        constraints = [
            models.CheckConstraint(
                check=(models.Q(user__isnull=False, team__isnull=True)
                           | models.Q(user__isnull=True, team__isnull=False)),
                name='circuit_member_one_entrant'),
            models.UniqueConstraint(fields=['circuit', 'user'], condition=models.Q(user__isnull=False),
                                    name='circuit_member_user_unique'),
            models.UniqueConstraint(fields=['circuit', 'team'], condition=models.Q(team__isnull=False),
                                    name='circuit_member_team_unique'),
        ]

    @property
    def key(self):
        return ('team', self.team_id) if self.team_id else ('user', self.user_id)


class CircuitAdjustment(models.Model):
    circuit = models.ForeignKey(Circuit, on_delete=models.CASCADE, related_name='adjustments')
    #: Exactly one of user and team, matching the circuit's entrant type.
    user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='circuit_adjustments')
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='circuit_adjustments')
    points = models.DecimalField(max_digits=10, decimal_places=2)
    #: Shown to everybody beside the points: an adjustment nobody can explain
    #: is a ranking nobody can trust.
    reason = models.CharField(max_length=240)
    tournament = models.ForeignKey('vent_tournament.Tournament', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='circuit_adjustments')
    by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at', '-id']

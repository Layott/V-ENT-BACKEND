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
"""
from django.db import models

from vent_auth.models import Games, Organization, Teams, Users


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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # The slug follows the name; the one it replaces stays in SlugHistory,
        # so a renamed circuit keeps every link ever shared (the slug rule).
        from vent_auth.slugs import sync_slug
        changed = sync_slug(self, self.name, entity_type='circuit', id_attr='pk')
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

from django.db import models
from vent_auth.models import Users, Games, Teams, Organization
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class Tournament(models.Model):
    TOURNAMENT_VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('private', 'Private'),
        ('protected', 'Protected'),
    ]

    TOURNAMENT_ACCESS_CHOICES = [
        ('team', 'Team'),
        ('individual', 'Individual'),
        ('team_and_individual', 'Team and Individual'),
    ]

    TOURNAMENT_TYPE_CHOICES = [
        ('online', 'Online'),
        ('physical', 'Physical'),
        ('hybrid', 'Hybrid'),
    ]

    ENTRY_FEE_CHOICES = [
        ('Paid', 'Paid'),
        ('Free', 'Free'),
    ]

    PRIZE_TYPE_CHOICES = [
        ('distributed', 'Distributed'),
        ('winner_takes_all', 'Winner Takes All'),
        ('no_prize', 'No Prize'),
    ]


    tournament_id = models.AutoField(primary_key=True)
    tournament_title = models.CharField(max_length=148, null=False)
    tournament_game = models.ForeignKey(Games, on_delete=models.CASCADE)
    game_mode = models.CharField(max_length=50, null=True, blank=True)  # Game Mode
    tournament_logo = models.ImageField(upload_to='tournament_logos/', null=False, blank=False)
    tournament_banner = models.ImageField(upload_to='tournament_banners/', null=False, blank=False)
    tournament_description = models.TextField(null=True)
    tournament_rules = models.TextField(null=True, blank=True)
    bracket_type = models.CharField(max_length=50, default='Single Elimination')
    tournament_creator = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='tournament_creator')
    tournament_organization = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, blank=True)

    start_date_and_time = models.DateTimeField()
    end_date_and_time = models.DateTimeField()
    tournament_visibility = models.CharField(max_length=9, choices=TOURNAMENT_VISIBILITY_CHOICES, default='public')
    tournament_type = models.CharField(max_length=8, choices=TOURNAMENT_TYPE_CHOICES)
    tournament_location = models.CharField(max_length=255, null=True, blank=True)
    virtual_link = models.URLField(null=True, blank=True)  # Virtual Link
    team_size = models.PositiveIntegerField(default=1)  # Default to 1 for individuals

    player_size = models.IntegerField(null=True, blank=True)
    min_number_of_teams = models.IntegerField(null=True, blank=True)
    max_number_of_teams = models.IntegerField(null=True, blank=True)

    prize_type = models.CharField(max_length=20, choices=PRIZE_TYPE_CHOICES, default='no_prize')


    tournament_access = models.CharField(max_length=20, choices=TOURNAMENT_ACCESS_CHOICES)
    entry_fee = models.CharField(max_length=5, choices=ENTRY_FEE_CHOICES)
    entry_fee_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Social Links
    facebook_link = models.URLField(null=True, blank=True)
    twitter_link = models.URLField(null=True, blank=True)
    instagram_link = models.URLField(null=True, blank=True)
    youtube_link = models.URLField(null=True, blank=True)
    twitch_link = models.URLField(null=True, blank=True)
    kick_link = models.URLField(null=True, blank=True)
    tiktok_link = models.URLField(null=True, blank=True)
    bigolive_link = models.URLField(null=True, blank=True)


    # Sponsors
    sponsors = models.ManyToManyField('Sponsors', blank=True)

    # Interaction Count
    interaction_count = models.PositiveIntegerField(default=0)  # To track user interactions

    # Check if its a draft
    is_draft = models.BooleanField(default=True)

    def __str__(self):
        return self.tournament_title


class TournamentPrizeDistribution(models.Model):
    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='prize_distributions')
    position = models.IntegerField(null=False)
    prize = models.DecimalField(max_digits=10, decimal_places=2, null=False)
    extras = models.CharField(max_length=40, blank=True)  # Optional field for additional prize details

    def __str__(self):
        return f"{self.tournament.tournament_title} - Position {self.position}"


class Sponsors(models.Model):
    sponsor_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    
    # Generic relation to support multiple models
    sponsor_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True)
    sponsor_id_object = models.PositiveIntegerField(null=True)
    sponsor = GenericForeignKey('sponsor_type', 'sponsor_id_object')

    logo = models.ImageField(upload_to='sponsor_logos/', null=True, blank=True)
    website = models.URLField(null=True, blank=True)

    def __str__(self):
        return self.name

class RegisteredTeams(models.Model):
    tournament_id = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='registered_teams')
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)


class Match(models.Model):
    match_id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='matches')
    match_check_in_time = models.TimeField()
    match_check_in_date = models.DateField()
    match_check_in_started = models.BooleanField(default=False)
    match_check_in_ended = models.BooleanField(default=False)


class UnconfirmedTeams(models.Model):
    id = models.AutoField(primary_key=True)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)

    def __str__(self):
        return f"UnconfirmedTeam {self.id} - {self.team_id.team_name}"


class TournamentRegistration(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('disqualified', 'Disqualified'),
        ('withdrawn', 'Withdrawn'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='registrations')
    # Either team or individual — one will be null
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, null=True, blank=True, related_name='tournament_registrations')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True, blank=True, related_name='tournament_registrations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    registered_at = models.DateTimeField(auto_now_add=True)
    entry_fee_paid = models.BooleanField(default=False)
    payment_reference = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [
            ('tournament', 'team'),
            ('tournament', 'user'),
        ]

    def __str__(self):
        participant = self.team.team_name if self.team else self.user.username
        return f"{participant} @ {self.tournament.tournament_title}"


class BracketMatch(models.Model):
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('bye', 'Bye'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='bracket_matches')
    round_number = models.PositiveIntegerField()
    match_number = models.PositiveIntegerField()
    participant_1 = models.ForeignKey(
        TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches_as_p1'
    )
    participant_2 = models.ForeignKey(
        TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches_as_p2'
    )
    winner = models.ForeignKey(
        TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches_won'
    )
    score_p1 = models.IntegerField(default=0)
    score_p2 = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['round_number', 'match_number']

    def __str__(self):
        return f"{self.tournament.tournament_title} R{self.round_number} M{self.match_number}"


class TournamentDispute(models.Model):
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('under_review', 'Under Review'),
        ('resolved', 'Resolved'),
        ('dismissed', 'Dismissed'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='disputes')
    match = models.ForeignKey(BracketMatch, on_delete=models.SET_NULL, null=True, blank=True, related_name='disputes')
    raised_by = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='disputes_raised')
    description = models.TextField()
    evidence = models.JSONField(default=list, blank=True)  # list of image URLs / notes
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Dispute by {self.raised_by.username} on {self.tournament.tournament_title}"


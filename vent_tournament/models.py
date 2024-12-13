from django.db import models
from vent_auth.models import Users, Games, Teams

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

    tournament_id = models.AutoField(primary_key=True)
    tournament_title = models.CharField(max_length=148, null=False)
    game = models.CharField(max_length=100, null=False)  # Game Name
    game_mode = models.CharField(max_length=50, null=True, blank=True)  # Game Mode
    tournament_logo = models.ImageField(upload_to='tournament_logos/', null=True, blank=True)
    tournament_banner = models.ImageField(upload_to='tournament_banners/', null=True, blank=True)
    tournament_description = models.TextField(null=True)
    tournament_rules = models.TextField(null=True, blank=True)
    bracket_type = models.CharField(max_length=50, default='Single Elimination')

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

    # Sponsors
    sponsors = models.ManyToManyField('Sponsor', blank=True)

    # Interaction Count
    interaction_count = models.PositiveIntegerField(default=0)  # To track user interactions

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


class Sponsor(models.Model):
    sponsor_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to='sponsor_logos/', null=True, blank=True)
    website = models.URLField(null=True, blank=True)

    def __str__(self):
        return self.name

class RegisteredTeams(models.Model):
    tournament_id = models.ForeignKey(Tournament, on_delete=models.CASCADE)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)    

class Match(models.Model):
    match_id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='matches')
    match_check_in_time = models.TimeField()
    match_check_in_date = models.DateField()
    match_check_in_started = models.BooleanField(default=False)
    match_check_in_ended = models.BooleanField(default=False)


class UnconfirmedTeams(models.Model):
    match_id = models.AutoField(primary_key=True)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)    
    


    def __str__(self):
        return f"Match {self.match_id} - {self.tournament.tournament_name}"

    class Meta:
        verbose_name = "Match"
        verbose_name_plural = "Matches"


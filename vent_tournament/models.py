from django.db import models
from vent_auth.models import Users, Games, Teams  # Ensure you import relevant models

class Tournament(models.Model):
    tournament_id = models.AutoField(primary_key=True, null=False)
    tournament_name = models.CharField(max_length=148, null=False)
    tournament_desc = models.TextField()
    tournament_creator = models.ForeignKey(Users, on_delete=models.CASCADE, null=False, related_name='created_tournaments')
    tournament_creation_date = models.DateField()
    tournament_game_type = models.ForeignKey(Games, on_delete=models.CASCADE, null=False, related_name='tournaments')
    tournament_type = models.CharField(max_length=5, null=False)
    tournament_registration_date = models.DateField()
    tournament_registration_end_date = models.DateField()

    def __str__(self):
        return self.tournament_name

    class Meta:
        verbose_name = "Tournament"
        verbose_name_plural = "Tournaments"

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


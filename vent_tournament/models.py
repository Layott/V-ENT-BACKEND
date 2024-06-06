from django.db import models
from vent_auth.models import *

# Create your models here.


class Tournaments(models.Model):
    tournament_id = models.AutoField(primary_key=True, null=False)
    tournament_name = models.CharField(max_length=148, null=False)
    tournament_desc = models.TextField()
    tournament_creator_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    tournament_creation_date = models.DateField()
    tournament_game_type = models.ForeignKey(Games, on_delete=models.CASCADE, null=False)
    tournament_type = models.CharField(max_length=5, null=False)
    tournament_registration_date = models.DateField()
    tournament_checkin_date = models.DateField()


class RegisteredTeams(models.Model):
    tournament_id = models.ForeignKey(Tournaments, on_delete=models.CASCADE, null=False)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE, null=False)
    registration_date = models.DateField()


class CheckInTeams(models.Model):
    tournament_id = models.ForeignKey(Tournaments, on_delete=models.CASCADE, null=False)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE, null=False)
    check_in_date = models.DateField()

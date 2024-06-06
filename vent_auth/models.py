from django.db import models
from django.contrib.auth.models import AbstractUser
import datetime
import django
# Create your models here.


class Users(AbstractUser):
    user_id = models.AutoField(primary_key=True, null=False)
    first_name = models.CharField(max_length=148, null=False)
    last_name = models.CharField(max_length=148, null=False)
    user_email = models.EmailField(unique=True, null=False)
    user_password = models.CharField(max_length=256, null=False)


class Games(models.Model):
    game_id = models.AutoField(primary_key=True, null=False)
    game_name = models.CharField(max_length=40, unique=True, null=False)


class Teams(models.Model):
    team_id = models.IntegerField(primary_key=True)
    team_name = models.CharField(unique=True, max_length=60, null=False)
    creation_date = models.DateField(default=django.utils.timezone.now, null=False)
    team_owner_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    game_id = models.ForeignKey(Games, on_delete=models.CASCADE, null=False)
    team_privacy = models.CharField(max_length=7, default="public", null=False)
    matches = models.IntegerField(default=0, null=False)


class TeamMembers(models.Model):
    id = models.IntegerField(primary_key=True)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)
    user_id = models.ForeignKey(Users, on_delete=models.CASCADE)
    is_captain = models.BooleanField(default=False)
    join_date = models.DateField(default=django.utils.timezone.now)


    
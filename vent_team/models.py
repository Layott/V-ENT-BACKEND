from django.db import models
from vent_auth.models import Teams, GameAccount


class TeamInterests(models.Model):
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, related_name='vent_team_interests')
    interests = models.CharField(max_length=40)


class TeamMembers(models.Model):
    ROLE_CHOICES = [
        ('captain', 'Captain'),
        ('vice_captain', 'Vice Captain'),
        ('member', 'Member'),
        ('coach', 'Coach'),
        ('manager', 'Manager'),
        ('analyst', 'Analyst'),
    ]
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, related_name='vent_team_members')
    member = models.ForeignKey(GameAccount, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')

from django.db import models
from vent_auth.models import Teams


class TeamInterests(models.Model):
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, related_name='vent_team_interests')
    interests = models.CharField(max_length=40)

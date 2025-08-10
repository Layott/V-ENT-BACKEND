from django.db import models
from vent_auth.models import *


# Create your models here.


# class Teams(models.Model):
#     team_id = models.AutoField(primary_key=True)
#     team_name = models.CharField(unique=True, max_length=60)
#     team_logo = models.ImageField(upload_to='teams_logos/', null=True, blank=True)
#     team_banner = models.ImageField(upload_to='teams_banners/', null=True, blank=True)
#     game = models.ForeignKey(Games, on_delete=models.CASCADE)
#     description = models.TextField(default="Passionate gamer with a sharp eye for detail, always on the lookout for the next big win. Whether it's dominating in-game or leveling up your project with killer design, I'm here to make it happen. Let's team up and create something epic!")
#     allow_membership_requests = models.BooleanField(default=True)
#     creation_date = models.DateField(default=timezone.now)
#     team_creator = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='created_teams')
#     team_owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='owned_teams')
#     penalty_points = models.IntegerField()
#     number_of_members = models.IntegerField()
    
#     def __str__(self):
#         return self.team_name


class Teams(models.Model):
    team_id = models.AutoField(primary_key=True)
    team_name = models.CharField(unique=True, max_length=60)
    team_logo = models.ImageField(upload_to='teams_logos/', null=True, blank=True)
    team_banner = models.ImageField(upload_to='teams_banners/', null=True, blank=True)
    
    game = models.ForeignKey(
        Games,
        on_delete=models.CASCADE,
        related_name='vent_team_teams'
    )

    description = models.TextField(default="Passionate gamer with a sharp eye for detail...")
    allow_membership_requests = models.BooleanField(default=True)
    creation_date = models.DateField(default=timezone.now)

    team_creator = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='vent_team_created_teams'
    )
    team_owner = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='vent_team_owned_teams'
    )

    penalty_points = models.IntegerField()
    number_of_members = models.IntegerField()

    def __str__(self):
        return self.team_name


class TeamProfile(models.Model):
    team_profile_id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Teams, on_delete=models.CASCADE)
    country = models.CharField(max_length=40)

    # Social Links
    facebook_link = models.URLField(null=True, blank=True)
    twitter_link = models.URLField(null=True, blank=True)
    instagram_link = models.URLField(null=True, blank=True)
    youtube_link = models.URLField(null=True, blank=True)
    twitch_link = models.URLField(null=True, blank=True)
    kick_link = models.URLField(null=True, blank=True)


class TeamInterests(models.Model):
    team = models.ForeignKey(Teams, on_delete=models.CASCADE)
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
    team = models.ForeignKey(Teams, on_delete=models.CASCADE)
    member = models.ForeignKey(GameAccount, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')

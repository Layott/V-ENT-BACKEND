from django.db import models
from django.contrib.auth.models import AbstractUser
import datetime
import django
from django.utils import timezone
# Create your models here.


class Users(AbstractUser):
    user_id = models.AutoField(primary_key=True, null=False)
    full_name = models.CharField(max_length=148, null=False)
    username = models.CharField(max_length=128, unique=True, null=False)
    email = models.EmailField(unique=True, null=False)
    password = models.CharField(max_length=256, null=False)

    USERNAME_FIELD = 'email'  # Use email for authentication
    REQUIRED_FIELDS = ['username', 'full_name']  # Required fields for creating a superuser

    def __str__(self):
        return self.email
    

class UserProfile(models.Model):
    profile_id = models.AutoField(primary_key=True, null=False)
    user_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True)


class VerificationToken(models.Model):
    user_email = models.EmailField(unique=True)
    token = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        now = timezone.now()
        return now - self.created_at < datetime.timedelta(minutes=10)


class UserCommunity(models.Model):
    user_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    is_gamer = models.BooleanField(default=False)
    is_anime_enth = models.BooleanField(default=False)


class Genres(models.Model):
    genre_id = models.AutoField(primary_key=True, null=False)
    genre_name = models.CharField(max_length=40, null=False)


class Games(models.Model):
    game_id = models.AutoField(primary_key=True, null=False)
    game_name = models.CharField(max_length=40, unique=True, null=False)


class UserGenre(models.Model):
    user_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    genre_id = models.ForeignKey(Genres, on_delete=models.CASCADE, null=False)


class UserGames(models.Model):
    user_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    game_id = models.ForeignKey(Games, on_delete=models.CASCADE, null=False)


class Teams(models.Model):
    team_id = models.AutoField(primary_key=True)
    team_name = models.CharField(unique=True, max_length=60, null=False)
    creation_date = models.DateField(default=django.utils.timezone.now, null=False)
    team_owner_id = models.ForeignKey(Users, on_delete=models.CASCADE, null=False)
    game_id = models.ForeignKey(Games, on_delete=models.CASCADE, null=False)
    team_privacy = models.CharField(max_length=7, default="public", null=False)
    matches = models.IntegerField(default=0, null=False)


class TeamMembers(models.Model):
    team_member_id = models.AutoField(primary_key=True)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)
    user_id = models.ForeignKey(Users, on_delete=models.CASCADE)
    is_captain = models.BooleanField(default=False)
    join_date = models.DateField(default=django.utils.timezone.now)


    
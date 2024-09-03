from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
import datetime

class Users(AbstractUser):
    user_id = models.AutoField(primary_key=True)
    full_name = models.CharField(max_length=148, null=True)
    username = models.CharField(max_length=128, unique=True)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=256, null=True)

    USERNAME_FIELD = 'email'  # Use email for authentication
    REQUIRED_FIELDS = ['username', 'full_name']  # Required fields for creating a superuser

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    profile_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True)
    date_of_birth = models.DateField(null=True)


class VerificationToken(models.Model):
    user_email = models.EmailField(unique=True)
    token = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        now = timezone.now()
        return now - self.created_at < datetime.timedelta(minutes=10)


class VerificationTokenMain(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    token = models.CharField(max_length=64)  # Increased length to accommodate URL-safe tokens
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        now = timezone.now()
        return now - self.created_at < datetime.timedelta(minutes=10)

class UserCommunity(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    is_gamer = models.BooleanField(default=False)
    is_anime_enth = models.BooleanField(default=False)


class Genres(models.Model):
    genre_id = models.AutoField(primary_key=True)
    genre_name = models.CharField(max_length=40)


class Games(models.Model):
    game_id = models.AutoField(primary_key=True)
    game_name = models.CharField(max_length=40, unique=True)


class UserGenre(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    genre = models.ForeignKey(Genres, on_delete=models.CASCADE)


class UserGames(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    game = models.ForeignKey(Games, on_delete=models.CASCADE)


class Teams(models.Model):
    team_id = models.AutoField(primary_key=True)
    team_name = models.CharField(unique=True, max_length=60)
    creation_date = models.DateField(default=timezone.now)
    team_creator = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='created_teams')
    team_owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='owned_teams')
    game = models.ForeignKey(Games, on_delete=models.CASCADE)
    team_privacy = models.CharField(max_length=7, default="public")

    def __str__(self):
        return self.team_name


class TeamProfile(models.Model):
    team_profile_id = models.AutoField(primary_key=True)
    team = models.OneToOneField(Teams, on_delete=models.CASCADE)
    matches = models.IntegerField(default=0)
    tournament_played = models.IntegerField(default=0)

    def __str__(self):
        return f"Profile of {self.team.team_name}"


class TeamMembers(models.Model):
    team_member_id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Teams, on_delete=models.CASCADE)
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    is_captain = models.BooleanField(default=False)
    join_date = models.DateField(default=timezone.now)


class GameAccount(models.Model):
    game_account_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    game = models.ForeignKey(Games, on_delete=models.CASCADE)
    game_username = models.CharField(max_length=20)


class Organization(models.Model):
    org_id = models.AutoField(primary_key=True)
    org_name = models.CharField(max_length=148, unique=True)
    org_creator = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='created_organizations')
    org_owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='owned_organizations')


class UserWallet(models.Model):
    user_wallet_id = models.CharField(primary_key=True, max_length=10)
    user = models.OneToOneField(Users, on_delete=models.CASCADE, related_name='wallet')
    wallet_balance = models.IntegerField(default=0)
    user_wallet_pin = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username}'s Wallet"


class TeamWallet(models.Model):
    team_wallet_id = models.CharField(primary_key=True, max_length=10)
    team = models.OneToOneField(Teams, on_delete=models.CASCADE, related_name='wallet')
    wallet_balance = models.IntegerField(default=0)
    team_wallet_pin = models.IntegerField(null=True, blank=True)


class OrgWallet(models.Model):
    org_wallet_id = models.CharField(primary_key=True, max_length=10)
    org = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='wallet')
    wallet_balance = models.IntegerField(default=0)
    org_wallet_pin = models.IntegerField(null=True, blank=True)

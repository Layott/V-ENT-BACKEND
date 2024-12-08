from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
import datetime

class Users(AbstractUser):
    user_id = models.AutoField(primary_key=True)
    full_name = models.CharField(max_length=148, null=True)
    username = models.CharField(max_length=128, unique=True)
    email = models.EmailField()
    password = models.CharField(max_length=256, null=True)
    country = models.CharField(max_length=256, null=True)
    login_session_token = models.CharField(max_length=16, null=True)
    signup_type = models.CharField(max_length=32, default='normal', null=True)  # normal, google, facebook
    provider_id = models.CharField(max_length=256, null=True, blank=True)  # Social provider ID

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'full_name']

    def __str__(self):
        return f"{self.email} ({self.signup_type})"


# class SocialAccount(models.Model):
#     user = models.ForeignKey(Users, related_name="social_accounts", on_delete=models.CASCADE)
#     provider = models.CharField(max_length=32)  # google, facebook
#     provider_id = models.CharField(max_length=256)

#     class Meta:
#         unique_together = ('provider', 'provider_id')  # Prevent duplicate provider entries

#     def __str__(self):
#         return f"{self.provider} - {self.user.email}"


class UserProfile(models.Model):
    profile_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True)
    date_of_birth = models.DateField(null=True)
    banner = models.ImageField(upload_to='banners/', null=True)
    description = models.CharField(max_length=140, null=True)
    penalty_point = models.IntegerField(default=0, null=True)


class UserInterests(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    interests = models.CharField(max_length=30)


class VerificationToken(models.Model):
    user_email = models.EmailField(unique=True)
    token = models.CharField(max_length=64)
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
    game_title = models.CharField(max_length=40, unique=True)
    description = models.TextField(null=True)
    logo = models.ImageField(upload_to='game_logos/', null=True, blank=True)  # Add the logo field

    def __str__(self):
        return self.game_title


class Achievement(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(null=True)
    logo = models.ImageField(upload_to='achievements/', blank=True, null=True)  # Updated folder name
    awarded_to = models.ManyToManyField(Users, related_name="achievements", blank=True, null=True)

    def __str__(self):
        return self.name


class UserGameStats(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)  # Changed to custom Users model
    game = models.ForeignKey(Games, on_delete=models.CASCADE)  # Fixed Games reference
    kills = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.user.username} - {self.game.game_title} ({self.kills} kills)"

    def add_kills(self, kill_count):
        self.kills += kill_count
        self.save()
        self.check_for_achievement()  # Renamed method

    def check_for_achievement(self):
        if self.kills >= 100:
            achievement, created = Achievement.objects.get_or_create(
                name="100 Kills", 
                description="Achieved 100 kills in total",
                defaults={'logo': 'path/to/logo.png'}
            )
            self.user.achievements.add(achievement)


class UserGenre(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    genre = models.ForeignKey(Genres, on_delete=models.CASCADE)


class FavoriteGames(models.Model):
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


class SocialLink(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='social_links')
    title = models.CharField(max_length=100)  # e.g., "Facebook", "Instagram"
    url = models.URLField(max_length=200)

    def __str__(self):
        return f"{self.title}: {self.url}"
    

class Waitlist(models.Model):
    email = models.EmailField(unique=True)
    is_notified = models.BooleanField(default=False)
    join_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

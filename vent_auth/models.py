from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.utils import timezone
import datetime
import uuid

from .storages import private_storage


class Users(AbstractUser):
    ADMIN_ROLE_CHOICES = [
        ('super_admin', 'Super Admin'),
        ('finance_admin', 'Finance Admin'),
        ('mod_admin', 'Moderator Admin'),
        ('support_admin', 'Support Admin'),
    ]

    user_id = models.AutoField(primary_key=True)
    full_name = models.CharField(max_length=148, null=True)
    username = models.CharField(max_length=128, unique=True)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=256, null=True)
    country = models.CharField(max_length=256, null=True)
    state = models.CharField(max_length=256, null=True)

    # Where the account signed in from, refreshed on the first login of each
    # day rather than on every request: the lookup is a local database read, but
    # a write on every call would be a write on every call.
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    # Whether the country on this account was worked out from an address rather
    # than chosen by the person. A guess and an answer look identical once
    # stored, and a screen that cannot tell them apart presents the guess as a
    # fact. It is cleared the moment somebody sets their own country.
    country_is_guess = models.BooleanField(default=False)

    # Deactivation hides an account and is undone by signing in. A scheduled
    # deletion is the same thing with a date attached - nothing is destroyed
    # while it runs, because other people's tournament results, disputes and
    # wallet history point at this row.
    is_deactivated = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    login_session_token = models.CharField(max_length=16, null=True)
    # When this session was last USED, not when it was created. Moved forward
    # by SessionActivityMiddleware while somebody is working, so the timeout
    # measures inactivity. The name is historical; renaming it would have meant
    # touching 67 comparison sites at once for no behavioural gain.
    login_session_created_at = models.DateTimeField(null=True, blank=True)
    signup_type = models.CharField(max_length=32, default='normal', null=True)  # normal, google, facebook
    provider_id = models.CharField(max_length=256, null=True, blank=True)  # Social provider ID
    tst = models.CharField(max_length=44, null=True)
    social_id = models.CharField(max_length=100, blank=True, null=True)
    role = models.CharField(
        max_length=20,
        choices=[('user', 'User'), ('organizer', 'Organizer'), ('admin', 'Admin')],
        default='user',
    )
    # RBAC sub-role. NULL for non-admins; required when role == 'admin'.
    admin_role = models.CharField(
        max_length=32, null=True, blank=True, choices=ADMIN_ROLE_CHOICES
    )

    # Set when an account is created by claiming a pre-launch waitlist
    # reservation. Kept on the user rather than read through the reservation so
    # a profile can show it without a join, and so it survives if the
    # reservation row is ever cleaned up. `founding_position` is the queue
    # number they earned on the waitlist, referral boosts included.
    is_founding_member = models.BooleanField(default=False)
    founding_position = models.IntegerField(null=True, blank=True)

    # A founder can carry a badge beside their name, and can switch it off.
    # Being a founder is a fact; wearing it is a choice, and somebody who does
    # not want a mark on every post they make should not have to have one.
    # Retired 2026-08-27. The console used to hold its own grant, because the
    # admin signed in a second time at its own door. It no longer has a door:
    # an admin proves the second factor at the ordinary sign-in, and the
    # console reads the same session as the rest of the site. Kept as columns
    # so the change is a code change rather than a data migration; nothing
    # reads them.
    admin_session_token = models.CharField(max_length=256, null=True, blank=True,
                                           db_index=True)
    admin_session_created_at = models.DateTimeField(null=True, blank=True)

    # When this session last passed a second-factor challenge.
    #
    # This is what the console checks. An admin's session is only an admin
    # session if the person holding it typed a code from their authenticator to
    # get it, so the second factor is not weaker for having moved to the front
    # door - it is now unavoidable rather than being asked for once the site
    # session already existed.
    #
    # Cleared on logout, and never set by a sign-in that skipped the challenge.
    login_session_2fa_at = models.DateTimeField(null=True, blank=True)

    is_founder = models.BooleanField(default=False)
    show_founder_badge = models.BooleanField(default=True)

    USERNAME_FIELD = 'username'  # Use 'username' for authentication
    REQUIRED_FIELDS = ['full_name']  # Exclude 'username'

    def clean(self):
        # Advisory: enforced at the API layer (admin_set_user_role). Only fires
        # when full_clean() is called (e.g. Django admin forms).
        super().clean()
        if self.role == 'admin' and not self.admin_role:
            raise ValidationError({'admin_role': 'admin_role is required when role is admin.'})

    def __str__(self):
        return f"{self.username} ({self.signup_type})"


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
    date_of_birth = models.DateField(null=True, blank=True)
    banner = models.ImageField(upload_to='banners/', null=True)
    description = models.CharField(max_length=140, null=True)
    penalty_point = models.IntegerField(default=0, null=True)


class UserInterests(models.Model):
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    interests = models.CharField(max_length=30)


class UserGallery(models.Model):
    """A picture somebody put on their profile, and what may be done with it.

    CEO, 31 August 2026: "there should be another type of upload for those who
    want to upload their Esports pictures, let them know that the Esports
    images will be used publicly and inside events or tournaments. that they
    grant use of it to organizers for those events."

    So a picture is one of two things:

    - **personal**  it sits on the profile and goes no further. Whether a
                    stranger sees it at all follows the profile's own privacy
                    setting.
    - **esports**   the person has released it for organisers to use on event
                    and tournament pages. That is a licence somebody grants,
                    and a licence is worthless unless it is recorded, so the
                    moment and the wording they agreed to are stored ON THE ROW.
                    An image with no `released_at` was never released, whatever
                    its kind says.

    `RELEASE_TERMS_VERSION` moves whenever the wording changes. Rows keep the
    version they were released under, because consent is to a specific sentence
    and not to a policy that can be edited afterwards.
    """

    KIND_PERSONAL = 'personal'
    KIND_ESPORTS = 'esports'
    KIND_CHOICES = [
        (KIND_PERSONAL, 'Personal'),
        (KIND_ESPORTS, 'Esports'),
    ]

    # Bump this when the release wording changes. The string is stored, not the
    # sentence, so the wording lives in one place and every row says which one.
    RELEASE_TERMS_VERSION = '2026-08-31'

    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='gallery/', null=True, blank=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_PERSONAL)
    caption = models.CharField(max_length=140, blank=True, default='')
    # When the person granted organisers use of this picture, and under which
    # wording. Null on a personal picture, and null is the only honest answer
    # for an esports one that somehow lost its consent.
    released_at = models.DateTimeField(null=True, blank=True)
    release_terms_version = models.CharField(max_length=32, blank=True, default='')
    date_added = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date_added']

    def __str__(self):
        return f"Gallery of {self.user.username}"

    @property
    def is_released(self):
        """Whether an organiser may use this. Both halves, always together."""
        return self.kind == self.KIND_ESPORTS and self.released_at is not None


class VerificationToken(models.Model):
    user_email = models.EmailField(unique=True)
    token = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    # Proof that whoever is asking to set a new password actually read the code
    # we mailed. Minted when the code checks out, spent when the password
    # changes. Before this existed, /forgot-password/change-password/ took an
    # email address and a new password and nothing else, so anyone could take
    # over any account by naming it.
    reset_ticket = models.CharField(max_length=64, blank=True, default='')
    ticket_created_at = models.DateTimeField(null=True, blank=True)

    # Six digits is 1,000,000 guesses, and nginx rate limiting alone would let a
    # patient attacker walk it. Five wrong codes burns the token.
    attempts = models.PositiveSmallIntegerField(default=0)

    RESET_CODE_MINUTES = 15
    RESET_TICKET_MINUTES = 15
    MAX_ATTEMPTS = 5

    def is_valid(self, window_minutes=None):
        now = timezone.now()
        if window_minutes is None:
            # Read the setting directly - importing views_helpers here would be
            # a circular import, since that module imports this one.
            from django.conf import settings
            window_minutes = int(getattr(settings, 'SESSION_TOKEN_TIMEOUT_MINUTES', 60 * 24 * 14))
        return now - self.created_at < datetime.timedelta(minutes=window_minutes)

    def ticket_is_valid(self, ticket):
        """True only for the exact unspent ticket, inside its window."""
        import hmac
        if not ticket or not self.reset_ticket or not self.ticket_created_at:
            return False
        if not hmac.compare_digest(str(ticket), self.reset_ticket):
            return False
        return timezone.now() - self.ticket_created_at < datetime.timedelta(minutes=self.RESET_TICKET_MINUTES)


class LoginEvent(models.Model):
    """One row per successful sign-in.

    The Security page showed a fixed list of invented sign-ins - a MacBook, an
    iPad, addresses in Lagos and Abuja - on every account, which is worse than
    showing nothing: the whole point of that table is to let somebody spot a
    sign-in that was not theirs.

    Kept short on purpose. The last twenty per account is plenty for "does
    anything here look unfamiliar", and it keeps a table of addresses from
    growing forever.
    """

    KEEP_PER_USER = 20

    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='login_events')
    ip = models.GenericIPAddressField(null=True, blank=True)
    city = models.CharField(max_length=120, blank=True, default='')
    country = models.CharField(max_length=120, blank=True, default='')
    user_agent = models.CharField(max_length=400, blank=True, default='')
    method = models.CharField(max_length=20, default='password')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.user_id} @ {self.created_at:%Y-%m-%d %H:%M}'


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
    # A game that is no longer run leaves the pickers without being deleted.
    # Deleting is not an option: tournaments point at it, and some of those FKs
    # cascade.
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'game_title']

    def __str__(self):
        return self.game_title


class Currency(models.Model):
    """A currency people read prices in, and what it is worth against the naira.

    V-ENT prices in naira because that is what Paystack settles and what a VENT
    COIN is worth. Somebody in Accra still thinks in cedis, and should not have
    to do arithmetic to find out what a ticket costs.

    The rate lives here rather than being fetched per page load: the platform
    runs itself, and a price that changes between the page and the checkout
    because a third party moved is worse than one a day stale.

    **Display only.** Money moves in naira. A converted figure tells somebody
    roughly what they are paying; it never bills them in another currency.
    """
    code = models.CharField(max_length=3, primary_key=True)  # ISO 4217
    name = models.CharField(max_length=60)
    symbol = models.CharField(max_length=8)
    rate_from_ngn = models.DecimalField(
        max_digits=18, decimal_places=8, default=1,
        help_text='How many of this currency one naira buys.')
    rate_updated = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = 'Currencies'
        ordering = ['sort_order', 'code']

    def from_ngn(self, amount):
        """A naira amount in this currency. Rounded for reading, not for billing."""
        from decimal import Decimal, ROUND_HALF_UP

        if amount is None:
            return None
        converted = Decimal(amount) * Decimal(self.rate_from_ngn)
        return converted.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def __str__(self):
        return self.code


class GameSeries(models.Model):
    """One edition of a game: EA FC 25, CODM Season 4, Street Fighter 6.

    Annual titles were being added as whole new games, so nothing tied this
    year's EA FC to last year's. An organiser picks the game, then the edition.
    """
    series_id = models.AutoField(primary_key=True)
    game = models.ForeignKey(Games, on_delete=models.CASCADE, related_name='series')
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    release_year = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = 'Game series'
        ordering = ['sort_order', '-release_year', 'name']
        # Two editions of the SAME game cannot share a name. Two different games
        # can both have a "2025", which is why this is not unique on its own.
        unique_together = ('game', 'name')

    def save(self, *args, **kwargs):
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, '%s %s' % (self.game.game_title if self.game_id else '', self.name),
            entity_type='game_series', id_attr='series_id',
        )
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)

    def __str__(self):
        return '%s %s' % (self.game.game_title, self.name)


class GameMode(models.Model):
    """A way a game is played: Battle Royale, Clash Squad, 5v5 Bomb, Ultimate Team.

    The wizard had a Game Mode select fed by a fixed list, so it offered Free
    Fire's modes to somebody running EA FC. A mode belongs to a game, and to an
    edition where the edition changed it - Clash Squad is Free Fire's, Ultimate
    Team is EA FC's, and neither should appear under the other.

    `default_format` and `default_placement_table` are what this mode is
    normally run as, so picking Battle Royale pre-selects points scoring with
    the right placement table instead of leaving an organiser to work it out.
    They are defaults, not constraints: an organiser can still choose otherwise.
    """

    mode_id = models.AutoField(primary_key=True)
    game = models.ForeignKey(Games, on_delete=models.CASCADE, related_name='modes')
    # Null means it applies to every edition of the game, which is the usual case.
    series = models.ForeignKey(
        GameSeries, on_delete=models.CASCADE, null=True, blank=True,
        related_name='modes',
    )
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True, default='')

    # How many a side, when the mode fixes it. 0 means the organiser decides.
    team_size = models.PositiveIntegerField(default=0)

    default_format = models.CharField(max_length=40, blank=True, default='')
    default_placement_table = models.CharField(max_length=40, blank=True, default='')

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        # The same game cannot have two modes with one name IN THE SAME EDITION.
        # The edition is part of the key because the class above says a mode
        # belongs to a game and to an edition where the edition changed it, and
        # without it that is not expressible: a global "Battle Royale" and a
        # 2026-specific one with a different team size could not both exist.
        # Two games can both have a "Ranked", which is why game is in the key
        # and name is not unique on its own.
        unique_together = ('game', 'series', 'name')

    def __str__(self):
        return f'{self.game.game_title}: {self.name}'


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

    # The editor has asked for both of these since it was built, and the model
    # held neither, so every save dropped them and the panel came back blank.
    gamertag = models.CharField(max_length=64, blank=True, default='')
    is_main = models.BooleanField(default=False)


class PlatformAccount(models.Model):
    """A player's handle on an external platform: PSN, Steam, Discord, others.

    Distinct from GameAccount, which is a handle for one game. The Gaming
    Accounts panel posted to /auth/update-gaming-accounts/, an endpoint that did
    not exist, so nothing a person typed there had ever been stored.

    `verified` stays False for anything typed by hand. It is reserved for a
    handle confirmed by the platform itself - Discord and Steam can do that, and
    most of the others have no public way to.
    """

    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='platform_accounts')
    platform = models.CharField(max_length=32)
    display_name = models.CharField(max_length=64, blank=True, default='')
    gamertag = models.CharField(max_length=64, blank=True, default='')
    connected = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'platform')

    def __str__(self):
        return f'{self.user_id}:{self.platform}' 


class Teams(models.Model):
    team_id = models.AutoField(primary_key=True)
    team_name = models.CharField(unique=True, max_length=60)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    # A team may be fielded by an organization (org profiles list their rosters).
    organization = models.ForeignKey(
        'Organization', on_delete=models.SET_NULL, null=True, blank=True, related_name='teams',
    )
    team_logo = models.ImageField(upload_to='teams_logos/', null=True, blank=True)
    team_banner = models.ImageField(upload_to='teams_banners/', null=True, blank=True)
    
    game = models.ForeignKey(
        Games,
        on_delete=models.CASCADE,
        related_name='vent_auth_teams'
    )

    # Blank, not a sentence about a person. This defaulted to "Passionate gamer
    # with a sharp eye for detail...", so every team created without a
    # description introduced itself with somebody's profile bio.
    description = models.TextField(blank=True, default='')
    allow_membership_requests = models.BooleanField(default=True)
    creation_date = models.DateField(default=timezone.now)

    team_creator = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='vent_auth_created_teams'
    )
    team_owner = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='vent_auth_owned_teams'
    )

    penalty_points = models.IntegerField(default=0)
    number_of_members = models.IntegerField(default=0)

    # Membership-settings columns (teams-consolidation-contract Part A).
    max_members = models.PositiveIntegerField(null=True, blank=True)
    password_protected = models.BooleanField(default=False)
    join_password = models.CharField(max_length=128, null=True, blank=True)  # hashed via make_password

    def __str__(self):
        return self.team_name

    def save(self, *args, **kwargs):
        # The slug follows the name. Whatever it replaces is kept in SlugHistory
        # and redirects here, so a renamed team keeps every link ever shared.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, self.team_name, entity_type='team', id_attr='team_id',
        )
        # A caller that named its fields (edit_team does) would otherwise
        # compute the new slug and never write it, which is the whole rename path.
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)


class TeamProfile(models.Model):
    team_profile_id = models.AutoField(primary_key=True)
    team = models.OneToOneField(Teams, on_delete=models.CASCADE)
    matches = models.IntegerField(default=0)
    tournament_played = models.IntegerField(default=0)
    country = models.CharField(max_length=40, null=True, blank=True)
    facebook_link = models.URLField(null=True, blank=True)
    twitter_link = models.URLField(null=True, blank=True)
    instagram_link = models.URLField(null=True, blank=True)
    youtube_link = models.URLField(null=True, blank=True)
    twitch_link = models.URLField(null=True, blank=True)
    kick_link = models.URLField(null=True, blank=True)

    def __str__(self):
        return f"Profile of {self.team.team_name}"


class TeamMembers(models.Model):
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('captain', 'Captain'),
        ('vice_captain', 'Vice Captain'),
        ('member', 'Member'),
        ('coach', 'Coach'),
        ('manager', 'Manager'),
        ('analyst', 'Analyst'),
    ]
    team_member_id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Teams, on_delete=models.CASCADE)
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    is_captain = models.BooleanField(default=False)
    join_date = models.DateField(default=timezone.now)


class TeamJoinRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ]
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, related_name='join_requests')
    applicant = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='team_join_requests')
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"JoinRequest {self.applicant_id}→{self.team_id} ({self.status})"


class GameAccount(models.Model):
    game_account_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    game = models.ForeignKey(Games, on_delete=models.CASCADE)
    game_username = models.CharField(max_length=20)


class Organization(models.Model):
    """An esports organization: a brand that fields teams and runs tournaments.

    The model was four columns (id, name, creator, owner) while the UI expected a
    full profile - identity, stats, verification. The rest of it lives here now.
    """

    org_id = models.AutoField(primary_key=True)
    org_name = models.CharField(max_length=148, unique=True)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    org_creator = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='created_organizations')
    org_owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='owned_organizations')

    # Identity
    tag = models.CharField(max_length=12, blank=True, default='')        # e.g. VEC
    bio = models.CharField(max_length=280, blank=True, default='')
    mission = models.TextField(blank=True, default='')
    focus = models.CharField(max_length=120, blank=True, default='')     # e.g. "Free Fire · FIFA"
    location = models.CharField(max_length=120, blank=True, default='')
    region = models.CharField(max_length=60, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    founded = models.DateField(null=True, blank=True)
    logo = models.ImageField(upload_to='org_logos/', null=True, blank=True)
    banner = models.ImageField(upload_to='org_banners/', null=True, blank=True)
    social_links = models.JSONField(default=dict, blank=True)

    # Trust
    verified = models.BooleanField(default=False)
    verification_requested = models.BooleanField(default=False)
    verification_note = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['org_name']

    def __str__(self):
        return self.org_name

    def save(self, *args, **kwargs):
        # Addresses carry the name, and every name it has had keeps working.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, self.org_name, entity_type='organization', id_attr='org_id',
        )
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)
class OrgMember(models.Model):
    """Somebody in an organisation, and what they are allowed to run in it.

    CEO, 31 August 2026: "I should be able to invite people and give them
    different roles to manage different things."

    So role decides how far somebody reaches, and for a manager, `scopes`
    decides which parts of the organisation they reach into:

    - **owner**    the organisation is theirs. Exactly one. Appoints admins,
                   cannot be removed, and holds every scope.
    - **admin**    everything except appointing another admin: edits the
                   profile, invites and removes people below them, runs every
                   area.
    - **manager**  runs only the areas named in `scopes` - any of teams,
                   events, tournaments, clubs. A tournament manager who cannot
                   touch the shop is the whole point of the role.
    - **member**   represents the organisation and runs nothing.

    Scopes are stored rather than derived, because "which areas" is a decision
    the owner makes per person and there is no rule that recovers it.
    """

    ROLE_OWNER = 'owner'
    ROLE_ADMIN = 'admin'
    ROLE_MANAGER = 'manager'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_OWNER, 'Owner'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MANAGER, 'Manager'),
        (ROLE_MEMBER, 'Member'),
    ]
    RANK = {ROLE_MEMBER: 0, ROLE_MANAGER: 1, ROLE_ADMIN: 2, ROLE_OWNER: 3}

    # The four things an organisation holds. Adding a fifth means adding it
    # here and nowhere else: every check reads this list.
    SCOPE_TEAMS = 'teams'
    SCOPE_EVENTS = 'events'
    SCOPE_TOURNAMENTS = 'tournaments'
    SCOPE_CLUBS = 'clubs'
    ALL_SCOPES = [SCOPE_TEAMS, SCOPE_EVENTS, SCOPE_TOURNAMENTS, SCOPE_CLUBS]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='org_memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    scopes = models.JSONField(default=list, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('org', 'user')
        ordering = ['role', 'joined_at']

    def __str__(self):
        return f"{self.user_id}@{self.org_id} ({self.role})"

    @property
    def rank(self):
        return self.RANK.get(self.role, 0)

    def outranks(self, other):
        """Whether this member may act on `other`. Equal rank is not enough:
        two admins that can demote each other leaves an organisation with no
        management, decided by whoever pressed first."""
        return other is not None and self.rank > other.rank

    @property
    def areas(self):
        """Which parts of the organisation this person may run."""
        if self.rank >= self.RANK[self.ROLE_ADMIN]:
            return list(self.ALL_SCOPES)
        if self.role == self.ROLE_MANAGER:
            return [s for s in (self.scopes or []) if s in self.ALL_SCOPES]
        return []

    def may_run(self, area):
        return area in self.areas


class OrgInvite(models.Model):
    """An invitation to join an organisation with a role already chosen.

    An invite names the role and the areas up front, so accepting is one press
    rather than a request that somebody then has to grade. It is addressed to a
    V-ENT account: an organisation invites a player it can already see, and an
    invite to an email address nobody has claimed is a signup funnel rather
    than a membership.

    It carries an opaque token rather than its primary key, because an
    invitation identifier that can be guessed by counting is an invitation
    anybody can accept.
    """

    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='invites')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='org_invites')
    invited_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='org_invites_sent')
    role = models.CharField(max_length=20, choices=OrgMember.ROLE_CHOICES, default='member')
    scopes = models.JSONField(default=list, blank=True)
    message = models.CharField(max_length=280, blank=True, default='')
    token = models.CharField(max_length=32, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"invite {self.token} -> {self.user_id}@{self.org_id}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = 'oi_' + uuid.uuid4().hex[:20]
        super().save(*args, **kwargs)


class OrgJoinRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='join_requests')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='org_join_requests')
    message = models.CharField(max_length=280, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class OrgFollower(models.Model):
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='followers')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='followed_orgs')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('org', 'user')


class UserWallet(models.Model):
    user_wallet_id = models.CharField(primary_key=True, max_length=10)
    user = models.OneToOneField(Users, on_delete=models.CASCADE, related_name='wallet')
    wallet_balance = models.IntegerField(default=0)
    pin_hash = models.CharField(max_length=128, blank=True, null=True)  # hashed 4-digit PIN via make_password
    kyc_verified = models.BooleanField(default=False)

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


class WaitlistReservation(models.Model):
    """Somebody who signed up before launch, imported from the waitlist site.

    The waitlist ran on its own site and its own database, and it never captured
    a password - the join endpoint passed `password_hash: null` for every one of
    the 102 rows. So there are no credentials to migrate and nobody can "log in
    with what they saved". What they actually reserved is a **username**.

    Claiming converts that reservation into a real account. The token mailed to
    them proves they control the address, so no separate verification step is
    needed; they pick a password and the handle they chose is waiting.

    The row is kept here rather than read from the waitlist database at request
    time, so the platform never depends on the marketing site being up to let
    somebody sign in.
    """
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=128, null=True, blank=True)
    display_name = models.CharField(max_length=148, blank=True)
    game = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    position = models.IntegerField(default=0)
    referral_code = models.CharField(max_length=32, blank=True)
    boost_count = models.IntegerField(default=0)
    email_verified = models.BooleanField(default=False)
    # waitlist_entries.id on the waitlist site, so a re-import updates rather
    # than duplicates and the two sides stay traceable to each other.
    source_id = models.BigIntegerField(null=True, blank=True)

    claim_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    claim_sent_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_user = models.ForeignKey(
        'Users', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='waitlist_reservation')
    # After this, the reserved username goes back into the open pool. Without a
    # deadline a name someone reserved and abandoned is burned forever.
    hold_expires_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']

    def __str__(self):
        return f"{self.username or self.email} (#{self.position})"

    @property
    def is_claimed(self):
        return self.claimed_at is not None

    def holds_username(self):
        """True while this reservation still blocks the handle from open signup."""
        if self.is_claimed or not self.username:
            return False
        if self.hold_expires_at is None:
            return True
        return timezone.now() < self.hold_expires_at


# ---------------------------------------------------------------------------
# Wallet - extended models
# ---------------------------------------------------------------------------

class Transaction(models.Model):
    TYPE_CHOICES = [
        ('top_up', 'Top Up'),
        ('deduction', 'Deduction'),
        ('prize', 'Prize'),
        ('send', 'Send'),
        ('receive', 'Receive'),
        ('withdrawal', 'Withdrawal'),
        ('refund', 'Refund'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    wallet = models.ForeignKey(
        UserWallet, related_name='transactions', on_delete=models.CASCADE
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    # Positive for credits (top_up, prize, receive, refund), negative for debits
    amount = models.IntegerField()
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    # Paystack reference. Unique so a single payment reference can credit the
    # wallet at most once (idempotency backstop for topup verify + webhook).
    # NULL (not '') for transactions with no gateway reference - MySQL permits
    # many NULLs under a unique index but not many empty strings.
    reference = models.CharField(
        max_length=255, blank=True, null=True, default=None, unique=True
    )  # Paystack reference
    # Lazy string reference avoids circular import with vent_tournament
    tournament = models.ForeignKey(
        'vent_tournament.Tournament',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='transactions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.type} {self.amount} - {self.wallet.user.username}"


class WithdrawalRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
    ]

    wallet = models.ForeignKey(
        UserWallet, on_delete=models.CASCADE, related_name='withdrawals'
    )
    amount = models.IntegerField()  # in VENT COINS
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Withdrawal {self.amount} COINS - {self.wallet.user.username} ({self.status})"


class KYCDocument(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    DOCUMENT_TYPE_CHOICES = [
        ('national_id', 'National ID'),
        ('passport', 'Passport'),
        ('drivers_license', "Driver's License"),
    ]

    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='kyc_documents')
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPE_CHOICES)
    # Identity documents live outside MEDIA_ROOT (see vent_auth/storages.py).
    # nginx serves MEDIA_ROOT directly, so a file written there is public to
    # anyone who guesses the name. Read these through GET /auth/kyc/document/<id>/.
    document_image = models.ImageField(upload_to='kyc/', storage=private_storage)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    rejection_reason = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"KYC {self.document_type} - {self.user.username} ({self.status})"


# ---------------------------------------------------------------------------
# Admin audit log
# ---------------------------------------------------------------------------

class AdminAction(models.Model):
    admin = models.ForeignKey(
        Users, related_name='admin_actions', on_delete=models.CASCADE
    )
    action_type = models.CharField(max_length=50)  # 'ban_user', 'approve_payout', etc.
    target_model = models.CharField(max_length=50)  # 'User', 'Tournament', etc.
    target_id = models.CharField(max_length=100)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    performed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action_type} by {self.admin.username} @ {self.performed_at}"


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

class UserSetting(models.Model):
    """Per-user preferences for the /settings page.

    Stored as a single JSON blob keyed by section (notifications, privacy,
    security, payments, language) so new toggles don't need a migration. The
    view merges DEFAULT_SETTINGS on read, so a missing key is never a crash.
    """
    user = models.OneToOneField(Users, related_name='settings', on_delete=models.CASCADE)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Settings - {self.user.username}"


# ---------------------------------------------------------------------------
# Platform-wide admin settings  (admin dashboard §21)
# ---------------------------------------------------------------------------

# Sensible defaults so a fresh install returns a complete settings object with
# no migration required for new toggles. The view deep-merges the stored blob
# over this so a missing key is never a crash.
DEFAULT_ADMIN_SETTINGS = {
    'platform_fees': {
        'tournament_fee_pct': 0,
        'withdrawal_fee_pct': 0,
        'listing_fee_pct': 0,
        'payout_min_vc': 0,
        'topup_max_ngn_per_day': 0,
    },
    'feature_flags': {
        'tournaments_enabled': True,
        'events_enabled': True,
        'wallet_enabled': True,
        'marketplace_enabled': False,
        'shop_enabled': False,
        'anime_enabled': False,
        'wager_enabled': False,
        'referral_program_enabled': False,
    },
    'banner': {
        'enabled': False,
        'title': '',
        'message': '',
        'type': 'info',   # info | warn | error | success
    },
    'maintenance': {
        'enabled': False,
        'message': '',
    },
}


def _deep_merge_settings(defaults, stored):
    """Two-level deep-merge of `stored` over `defaults`.

    Section dicts (platform_fees, feature_flags, banner, maintenance) merge
    key-by-key; scalars fall back to the default when absent. Extra top-level
    keys the caller saved are carried through untouched.
    """
    stored = stored or {}
    out = {}
    for key, default_val in defaults.items():
        if isinstance(default_val, dict):
            out[key] = {**default_val, **(stored.get(key) or {})}
        else:
            out[key] = stored.get(key, default_val)
    for key, val in stored.items():
        if key not in out:
            out[key] = val
    return out


class AdminSetting(models.Model):
    """Singleton row holding the platform-wide admin config as a JSON blob.

    Mirrors UserSetting but scoped to the whole platform (one row, pk=1). Read
    via `AdminSetting.load()`; `merged()` returns the blob deep-merged over
    DEFAULT_ADMIN_SETTINGS so every documented key is always present.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.id = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={'data': {}})
        return obj

    def merged(self):
        return _deep_merge_settings(DEFAULT_ADMIN_SETTINGS, self.data or {})

    def __str__(self):
        return "Admin platform settings"


class Notification(models.Model):
    """A single in-app notification for a user. Written fire-and-forget from the
    platform's real event sites (wallet receive, tournament registration, team
    join request, dispute raised/resolved, KYC + payout decisions) via
    `views_notifications.create_notification`. Powers the /notifications inbox
    and the header bell unread badge."""

    CATEGORY_CHOICES = [
        ('tournament', 'Tournament'),
        ('event', 'Event'),
        ('wallet', 'Wallet'),
        ('dispute', 'Dispute'),
        ('team', 'Team'),
        ('kyc', 'KYC'),
        ('payout', 'Payout'),
        ('system', 'System'),
        ('mention', 'Mention'),
        ('follower', 'Follower'),
    ]

    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='notifications')
    category = models.CharField(max_length=40, default='system')
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=500, blank=True, default='')
    link = models.CharField(max_length=300, blank=True, default='')
    is_read = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.category}] {self.title} -> {self.user_id}"


class AdminTOTP(models.Model):
    """Time-based one-time password enrolment for an admin account.

    The admin portal used to accept any six digits - the "2FA" step was
    frontend theatre with no server involvement. This stores the shared secret
    so codes are verified against RFC 6238 for real.

    `last_used_step` blocks replay: a code is valid for its 30-second step, and
    each step can only be spent once.
    """

    user = models.OneToOneField(Users, on_delete=models.CASCADE, related_name='admin_totp')
    secret = models.CharField(max_length=64)              # base32, no padding
    confirmed = models.BooleanField(default=False)        # set once a code verifies
    last_used_step = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"TOTP<{self.user_id} confirmed={self.confirmed}>"


class SavedCard(models.Model):
    """A card that can be charged again, described by what Paystack told us.

    There is no card number here and there never will be. Paystack returns an
    authorization code plus the brand, the last four digits, the expiry and the
    issuing bank; that is enough to recognise a card and to charge it, and it is
    the only version of "saving a card" that does not put this platform inside
    PCI DSS scope.
    """

    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='saved_cards')
    authorization_code = models.CharField(max_length=128, blank=True, default='')
    # Paystack's signature identifies the same physical card across
    # authorizations, so re-adding a card updates it instead of duplicating it.
    signature = models.CharField(max_length=128, blank=True, default='', db_index=True)

    brand = models.CharField(max_length=32, blank=True, default='')
    last4 = models.CharField(max_length=4, blank=True, default='')
    exp_month = models.CharField(max_length=2, blank=True, default='')
    exp_year = models.CharField(max_length=4, blank=True, default='')
    bank = models.CharField(max_length=120, blank=True, default='')
    channel = models.CharField(max_length=32, blank=True, default='card')
    country = models.CharField(max_length=8, blank=True, default='')

    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-is_default', '-created_at')

    def __str__(self):
        return f'{self.brand} ****{self.last4}'


class UserTOTP(models.Model):
    """An ordinary account's authenticator.

    Separate from AdminTOTP on purpose: an admin's second factor protects the
    dashboard, a member's protects their own account, and revoking one should
    never touch the other.
    """

    user = models.OneToOneField(Users, on_delete=models.CASCADE, related_name='user_totp')
    secret = models.CharField(max_length=64)
    confirmed = models.BooleanField(default=False)
    last_used_step = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'UserTOTP<{self.user_id} confirmed={self.confirmed}>'


# ---------------------------------------------------------------------------
# Community - feed posts, clubs, discussion threads, scrims, direct messages
# ---------------------------------------------------------------------------

class Post(models.Model):
    """A feed post. Text plus an optional image, attributed to a real user."""

    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    author = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='posts')
    body = models.TextField()
    image = models.ImageField(upload_to='post_images/', null=True, blank=True)
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    club = models.ForeignKey('Club', on_delete=models.CASCADE, null=True, blank=True, related_name='posts')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"post {self.pk} by {self.author_id}"

    def save(self, *args, **kwargs):
        # No name to build an address from, so it carries an opaque token
        # instead of the primary key - a sequential id in a URL lets anybody
        # walk the whole table by counting.
        from vent_auth.slugs import ensure_token

        if ensure_token(self, 'p') and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)
class PostLike(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='post_likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('post', 'user')


class PostComment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='post_comments')
    body = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class Club(models.Model):
    """A community group - usually built around a game."""

    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, default='')
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name='clubs')
    logo = models.ImageField(upload_to='club_logos/', null=True, blank=True)
    banner = models.ImageField(upload_to='club_banners/', null=True, blank=True)
    owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='owned_clubs')
    # A club can belong to an organisation, which is how an org holds its
    # community alongside its teams, events and tournaments.
    organization = models.ForeignKey(
        'Organization', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='clubs')
    is_private = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Addresses carry the name, and every name it has had keeps working.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, self.name, entity_type='club', id_attr='id',
        )
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)
class ClubMember(models.Model):
    """Somebody in a club, and what they are allowed to do in it.

    CEO, 31 August 2026: "Clubs are meant to be like group chats, that people
    can join and stay an read and send messages around particular set topics,
    then you have people who manage the group chat and manage it, they also can
    add also admins too with varying levels of control to their clubs."

    So four levels, and they are a ladder rather than a set of switches:

    - **owner**     the person whose club it is. Exactly one. Can do everything,
                    including appointing admins, and cannot be removed.
    - **admin**     can appoint and demote moderators, manage topics, and remove
                    or mute anybody below them. Cannot touch another admin or the
                    owner - otherwise two admins can remove each other and the
                    club has no management left.
    - **moderator** looks after the conversation: delete a message, mute a member
                    for a while. Cannot change anybody's role.
    - **member**    reads and writes.

    `muted_until` is a time rather than a flag, so a mute expires by itself
    instead of relying on somebody remembering to lift it.
    """

    ROLE_OWNER = 'owner'
    ROLE_ADMIN = 'admin'
    ROLE_MODERATOR = 'moderator'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_OWNER, 'Owner'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MODERATOR, 'Moderator'),
        (ROLE_MEMBER, 'Member'),
    ]
    # Higher outranks lower. Every "may I act on this person" question is this
    # comparison, in one place, so the rule cannot be written differently in two
    # endpoints.
    RANK = {ROLE_MEMBER: 0, ROLE_MODERATOR: 1, ROLE_ADMIN: 2, ROLE_OWNER: 3}

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='club_memberships')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    muted_until = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('club', 'user')

    def __str__(self):
        return f'{self.user_id} in {self.club_id} as {self.role}'

    @property
    def rank(self):
        return self.RANK.get(self.role, 0)

    @property
    def is_muted(self):
        return bool(self.muted_until and self.muted_until > timezone.now())

    def outranks(self, other):
        """Whether this member may act on `other`. Equal rank is not enough."""
        return other is not None and self.rank > other.rank


class ClubTopic(models.Model):
    """One conversation inside a club.

    The CEO asked for messages "around particular set topics", so a club is not
    one undifferentiated wall: it holds named topics and every message belongs to
    one. A club gets a "General" topic when it is created, because a club with no
    topic has nowhere to say anything.
    """

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='topics')
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True, default='')
    position = models.PositiveIntegerField(default=0)
    # A locked topic still reads. It is how an announcement channel is made, and
    # how a thread that has run its course is closed without deleting what was
    # said in it.
    is_locked = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_club_topics')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'id']
        unique_together = ('club', 'name')

    def __str__(self):
        return f'{self.club_id}/{self.name}'


class ClubMessage(models.Model):
    """Something somebody said in a topic.

    Deleting is soft. A moderator removing a message should not also remove the
    evidence of what was moderated, and a thread with holes punched through it
    cannot be read back later to settle an argument about what happened.
    """

    topic = models.ForeignKey(ClubTopic, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, related_name='club_messages')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='deleted_club_messages')

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['topic', 'id'])]

    def __str__(self):
        return f'msg {self.id} in {self.topic_id}'

    @property
    def is_deleted(self):
        return self.deleted_at is not None


class Thread(models.Model):
    """A discussion thread - longer-form than a feed post."""

    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    CATEGORY_CHOICES = [
        ('general', 'General'),
        ('lfg', 'Looking for group'),
        ('strategy', 'Strategy'),
        ('support', 'Support'),
        ('marketplace', 'Marketplace'),
    ]

    title = models.CharField(max_length=180)
    body = models.TextField()
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='general')
    author = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='threads')
    club = models.ForeignKey(Club, on_delete=models.CASCADE, null=True, blank=True, related_name='threads')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_activity_at = models.DateTimeField(auto_now_add=True)
    view_count = models.PositiveIntegerField(default=0)
    is_pinned = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_pinned', '-last_activity_at']

    def save(self, *args, **kwargs):
        # Addresses carry the name, and every name it has had keeps working.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, self.title, entity_type='thread', id_attr='id',
        )
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)
class ThreadReply(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='replies')
    author = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='thread_replies')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class ThreadUpvote(models.Model):
    """One upvote per user per thread."""

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='upvotes')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='thread_upvotes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('thread', 'user')


class ThreadReplyUpvote(models.Model):
    """One upvote per user per reply."""

    reply = models.ForeignKey(ThreadReply, on_delete=models.CASCADE, related_name='upvotes')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='thread_reply_upvotes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('reply', 'user')


class Scrim(models.Model):
    """A practice-match request posted by one team, accepted by another."""

    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('accepted', 'Accepted'),
        ('cancelled', 'Cancelled'),
        ('played', 'Played'),
    ]

    # A scrim is posted by a team OR by one player.
    #
    # CEO, 29 August 2026: "should be able to create solo challenges also."
    # Most of what is played on this platform is 1v1 - EA FC, Lone Wolf, a
    # Clash Squad duel - and requiring a team to post one meant a player had to
    # invent a team of themselves first. `team` is nullable now and `player`
    # carries the other case; exactly one of them is set, which `clean()`
    # enforces.
    team = models.ForeignKey(
        Teams, on_delete=models.CASCADE, null=True, blank=True, related_name='scrims_posted',
    )
    player = models.ForeignKey(
        Users, on_delete=models.CASCADE, null=True, blank=True, related_name='scrims_as_player',
    )
    opponent = models.ForeignKey(
        Teams, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims_accepted',
    )
    opponent_player = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims_accepted_solo',
    )
    # Set when the post is a direct challenge: only this team may accept.
    challenged = models.ForeignKey(
        Teams, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims_challenged',
    )
    challenged_player = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims_challenged_solo',
    )
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims')
    created_by = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='scrims_created')
    scheduled_for = models.DateTimeField(null=True, blank=True)

    # Which way the game is being played. Free Fire alone is four different
    # games depending on this answer, and the format that makes sense follows
    # from it: a Battle Royale is points across N matches and Clash Squad is
    # first to N rounds, so "Bo3" is meaningless in one and wrong in the other.
    # See vent_auth/game_modes.py.
    mode = models.CharField(max_length=40, blank=True, default='')
    # How many a side. 1 for a solo challenge.
    team_size = models.PositiveSmallIntegerField(default=1)
    match_format = models.CharField(max_length=40, blank=True, default='')
    # Free Fire Craftland is played on a map somebody built, shared as a code.
    map_code = models.CharField(max_length=40, blank=True, default='')

    # Where the players are, as a country.
    #
    # This was `region`, and its list was 'NG-West', 'NG-East', 'ZA', 'KE',
    # 'EU-West', 'NA-East', 'SA', 'AS-East' - Nigerian zones, ISO country
    # codes and continental shards in one picker, so it could not be compared
    # with anything. The rest of the platform stores a country as a full name
    # from src/constants/countries.js, and a scrim is now the same, which means
    # "scrims near me" can actually be a query instead of a guess.
    country = models.CharField(max_length=60, blank=True, default='')

    # Who may answer this, by where they are.
    #
    # CEO, 30 August 2026: "for country should be able to open it to all, or
    # select a group of countries they want also."
    #
    # One country was the only option, which is wrong at both ends: somebody
    # happy to play anybody had to pick a country and turn everyone else away,
    # and somebody running a West African scrim had to post four separate
    # challenges. `anywhere` and a list cover both without making the simple
    # case harder.
    OPEN_TO_CHOICES = [
        ('anywhere', 'Anyone, anywhere'),
        ('countries', 'A chosen group of countries'),
        ('country', 'One country'),
    ]
    open_to = models.CharField(max_length=20, choices=OPEN_TO_CHOICES, default='country')
    # Used only when open_to is `countries`. Full names, from the one list the
    # rest of the platform compares against.
    countries = models.JSONField(default=list, blank=True)

    notes = models.CharField(max_length=280, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_solo(self):
        return self.player_id is not None

    def open_to_country(self, country):
        """Whether somebody from this country may answer this challenge.

        Enforced on accept as well as drawn on the page: a filter that only
        hides things is a suggestion, and the person who edits the request gets
        in anyway.
        """
        if self.open_to == 'anywhere':
            return True
        wanted = (country or '').strip().lower()
        if self.open_to == 'countries':
            return wanted in {str(c).strip().lower() for c in (self.countries or [])}
        # A single country, and a challenge with no country set turns nobody away.
        return not self.country or wanted == self.country.strip().lower()

    @property
    def open_to_label(self):
        if self.open_to == 'anywhere':
            return 'Anyone, anywhere'
        if self.open_to == 'countries':
            return ', '.join(self.countries or []) or 'A chosen group'
        return self.country or 'Anywhere'

    @property
    def poster_name(self):
        """Who posted it, whichever kind it is."""
        if self.player_id:
            return self.player.username
        return self.team.team_name if self.team_id else ''

    def clean(self):
        """Exactly one side, and it is either a team or a player.

        A row with both set would render twice and be acceptable by two
        different people; a row with neither has nobody to play it.
        """
        from django.core.exceptions import ValidationError

        if bool(self.team_id) == bool(self.player_id):
            raise ValidationError(
                'A scrim is posted either by a team or by one player, not both '
                'and not neither.'
            )

    def save(self, *args, **kwargs):
        # No name to build an address from, so it carries an opaque token
        # instead of the primary key - a sequential id in a URL lets anybody
        # walk the whole table by counting.
        from vent_auth.slugs import ensure_token

        if ensure_token(self, 's') and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)
class Conversation(models.Model):
    """A direct-message thread between exactly two people."""

    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    user_a = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='conversations_a')
    user_b = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='conversations_b')
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('user_a', 'user_b')
        ordering = ['-last_message_at']

    def save(self, *args, **kwargs):
        # There is nothing here to name, and the first line of somebody's
        # message has no business being in a URL. So it carries an opaque
        # token rather than the primary key: a sequential id in an address
        # lets anybody walk the table by counting, and a private conversation
        # is the last thing that should be enumerable.
        from vent_auth.slugs import ensure_token

        if ensure_token(self, 'd') and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)


class DirectMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='sent_messages')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']


# Every address a thing has ever had, so a rename never kills a shared link.
# Defined in its own module and re-exported here so Django picks it up as a
# model of this app without models.py growing another table nobody reads.
from .models_slughistory import SlugHistory  # noqa: E402,F401


class TeamInvite(models.Model):
    """An invitation to join a team: to one named player, or as a link.

    CEO, 29 August 2026: "There is no way for me to add players to my teams or
    invite people, or get a link players can use to join directly."

    Both are the same act - somebody with authority saying "you may join" - and
    they differ only in who it is addressed to, so they are one model with a
    `kind` rather than two tables that would need the same accept path written
    twice.

    A DIRECT invite names a person. It is spent when they answer, and only they
    can answer it.

    A LINK invite names nobody and carries a token. An owner posts it in a
    group chat and whoever follows it joins. That is the whole point and also
    the whole risk, so a link has limits a direct invite does not need: it can
    expire, it can be capped at a number of uses, and it can be revoked
    outright. Without those, one leaked link is a permanent open door into the
    roster.
    """

    KIND_CHOICES = [
        ('direct', 'To one player'),
        ('link', 'A link anyone may use'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Waiting for an answer'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('revoked', 'Withdrawn'),
    ]

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, related_name='invites')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='direct')
    invited_by = models.ForeignKey(Users, on_delete=models.CASCADE,
                                   related_name='team_invites_sent')
    # Null on a link invite: it is addressed to nobody in particular.
    user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='team_invites_received')
    # The role they arrive as. An owner inviting a coach should not have to
    # invite them and then change their role in a second step.
    role = models.CharField(max_length=20, default='member')
    message = models.CharField(max_length=280, blank=True, default='')

    # Link invites only. Opaque and not enumerable: a sequential id here would
    # let anybody walk every team's open link by counting.
    token = models.CharField(max_length=40, blank=True, default='', db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # 0 means no limit.
    max_uses = models.PositiveIntegerField(default=0)
    uses = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One live invitation per person per team. Asking twice is a
            # reminder, not a second row, so their list never shows the same
            # team twice and accepting cannot happen twice.
            models.UniqueConstraint(
                fields=['team', 'user'],
                condition=models.Q(status='pending', kind='direct'),
                name='one_pending_direct_team_invite',
            ),
        ]

    def __str__(self):
        who = self.user.username if self.user_id else 'link'
        return f'{self.team.team_name} -> {who}'

    def save(self, *args, **kwargs):
        if self.kind == 'link' and not self.token:
            import secrets
            # `t_` so a token is recognisable in a log or a support message.
            self.token = 't_' + secrets.token_urlsafe(16)
        super().save(*args, **kwargs)

    @property
    def is_spent(self):
        """Whether this link can still be used.

        A direct invite is spent by being answered. A link is spent by expiry,
        by its cap, or by being revoked.
        """
        from django.utils import timezone as _tz

        if self.status != 'pending':
            return True
        if self.kind == 'link':
            if self.expires_at and self.expires_at <= _tz.now():
                return True
            if self.max_uses and self.uses >= self.max_uses:
                return True
        return False


class ScrimResult(models.Model):
    """How a challenge actually finished, agreed by both sides.

    CEO, 30 August 2026: "record results also, the results should also show on
    their profiles as history".

    One side reports and the other confirms. That is the whole design, and the
    reason for it is that a scrim has no referee: there is no bracket, no
    organiser and no admin watching, so whatever one player types is the only
    account of what happened. If reporting were enough, the ledger would record
    whatever the faster typist claimed, and a history built on that is worse
    than no history because people would rely on it.

    So a result is `reported` until the other side agrees. A disagreement is
    recorded as `disputed` and kept - both numbers, both names - rather than
    letting the second report overwrite the first. Nobody is adjudicated here;
    the point is that the disagreement is visible instead of silently resolved
    in favour of whoever went last.
    """

    STATUS_CHOICES = [
        ('reported', 'Waiting to be confirmed'),
        ('confirmed', 'Agreed by both sides'),
        ('disputed', 'The two sides disagree'),
    ]

    id = models.AutoField(primary_key=True)
    scrim = models.OneToOneField(Scrim, on_delete=models.CASCADE, related_name='result')

    # `a` is whoever posted the challenge, `b` is whoever accepted it, and that
    # never changes, so a score always means the same thing however it is read.
    score_a = models.IntegerField(default=0)
    score_b = models.IntegerField(default=0)

    reported_by = models.ForeignKey(Users, on_delete=models.CASCADE,
                                    related_name='scrim_results_reported')
    reported_at = models.DateTimeField(auto_now_add=True)

    confirmed_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='scrim_results_confirmed')
    confirmed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='reported')

    # What the other side said it was, when they disagree. Kept beside the
    # original rather than replacing it: a dispute with only one set of numbers
    # in it is not a dispute, it is a rewrite.
    disputed_score_a = models.IntegerField(null=True, blank=True)
    disputed_score_b = models.IntegerField(null=True, blank=True)
    disputed_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='scrim_results_disputed')
    disputed_at = models.DateTimeField(null=True, blank=True)

    note = models.CharField(max_length=280, blank=True, default='')

    class Meta:
        ordering = ['-reported_at']

    def __str__(self):
        return f'{self.scrim_id}: {self.score_a}-{self.score_b} ({self.status})'

    @property
    def winner(self):
        """'a', 'b' or 'draw'. Only meaningful once it is confirmed."""
        if self.score_a > self.score_b:
            return 'a'
        if self.score_b > self.score_a:
            return 'b'
        return 'draw'

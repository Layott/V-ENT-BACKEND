from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.utils import timezone
import datetime

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

    # Deactivation hides an account and is undone by signing in. A scheduled
    # deletion is the same thing with a date attached - nothing is destroyed
    # while it runs, because other people's tournament results, disputes and
    # wallet history point at this row.
    is_deactivated = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    login_session_token = models.CharField(max_length=16, null=True)
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
    user = models.ForeignKey(Users, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='gallery/', null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Gallery of {self.user.username}"


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
        if not self.slug:
            from vent_auth.slugs import build_slug
            self.slug = build_slug(
                self.team_name, model=type(self), instance_pk=self.pk, pk_field='pk',
            )
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


class OrgMember(models.Model):
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('member', 'Member'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='org_memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('org', 'user')
        ordering = ['role', 'joined_at']

    def __str__(self):
        return f"{self.user_id}@{self.org_id} ({self.role})"


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

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, default='')
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name='clubs')
    logo = models.ImageField(upload_to='club_logos/', null=True, blank=True)
    banner = models.ImageField(upload_to='club_banners/', null=True, blank=True)
    owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='owned_clubs')
    is_private = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ClubMember(models.Model):
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='club_memberships')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('club', 'user')


class Thread(models.Model):
    """A discussion thread - longer-form than a feed post."""

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

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('accepted', 'Accepted'),
        ('cancelled', 'Cancelled'),
        ('played', 'Played'),
    ]

    team = models.ForeignKey(Teams, on_delete=models.CASCADE, related_name='scrims_posted')
    opponent = models.ForeignKey(
        Teams, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims_accepted',
    )
    # Set when the post is a direct challenge: only this team may accept.
    challenged = models.ForeignKey(
        Teams, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims_challenged',
    )
    game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True, related_name='scrims')
    created_by = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='scrims_created')
    scheduled_for = models.DateTimeField(null=True, blank=True)
    match_format = models.CharField(max_length=20, blank=True, default='')
    region = models.CharField(max_length=40, blank=True, default='')
    notes = models.CharField(max_length=280, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Conversation(models.Model):
    """A direct-message thread between exactly two people."""

    user_a = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='conversations_a')
    user_b = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='conversations_b')
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('user_a', 'user_b')
        ordering = ['-last_message_at']


class DirectMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='sent_messages')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

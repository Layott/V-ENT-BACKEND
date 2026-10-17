"""The partner programme: API access, and V-ENT as a sign-in provider.

Three things live here.

1. A **Partner** is a person or an organisation that has asked for access. It is
   nothing until an admin approves it, and being approved for the API is not the
   same as being approved for SSO - the second asks for more, because a partner
   putting "sign in with V-ENT" on their own site is handling other people's
   identities.

2. A **PartnerApiKey** carries scopes. There is no all-access key: a key that can
   read tournaments cannot read player profiles unless somebody said so. Only a
   hash of the secret is stored, so a leaked database does not leak working keys,
   and the plaintext is shown exactly once at issue.

3. **OAuthAuthorizationCode / OAuthAccessToken** are the V-ENT-as-provider side
   of SSO: a partner site sends a person here, they approve, and the partner gets
   a code it can trade for a token that reads a deliberately small profile.
"""
import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from vent_auth.models import Users, Organization


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------
# Deliberately granular, because "give them API access" is not a decision anyone
# should have to make in one lump. Each scope names exactly what it opens.

SCOPES = {
    'events:read': 'Read events, their schedule and their venues',
    'events:tickets:read': 'Read ticket types and remaining capacity for events',
    'tournaments:read': 'Read tournaments, formats, prize pools and schedules',
    'tournaments:participants:read': 'Read who is registered for a tournament',
    'tournaments:brackets:read': 'Read brackets, matches and results',
    'teams:read': 'Read team profiles and rosters',
    'players:read': 'Read public player profiles',
    'players:stats:read': 'Read player win and loss records',
    'rankings:read': 'Read platform rankings',
    # Game studios (design section 14). Reviewed for live keys, because they
    # put results into real matches and tie game accounts to real people.
    'studio:accounts': 'Link player accounts in your game to V-ENT accounts',
    'studio:sessions': 'Run game sessions for V-ENT matches and report their results',
}

SCOPE_CHOICES = [(key, label) for key, label in SCOPES.items()]

# What a sandbox key may carry with nobody reviewing it. A sandbox game reaches
# only its own studio's test tournaments and the testers it names, so a test
# key with these scopes can touch nothing real. Going live is the review.
SANDBOX_SCOPES = ['studio:accounts', 'studio:sessions']

# The tier anybody gets by asking.
#
# Everything in it is already readable by anybody with a browser: the tournament
# list, the event list, team profiles, public player profiles, rankings. Asking
# for a company registration number before handing over a listing protects
# nothing and costs every integration a week of waiting.
#
# What is NOT in it is the part where the answer differs per partner:
# `tournaments:participants:read` and `tournaments:brackets:read` are about
# identifiable people rather than listings, and SSO hands over identity.
SELF_SERVE_SCOPES = [
    'events:read',
    'events:tickets:read',
    'tournaments:read',
    'teams:read',
    'players:read',
    'rankings:read',
]

REVIEWED_SCOPES = [s for s in SCOPES if s not in SELF_SERVE_SCOPES]


def self_serve(values):
    """The subset of a request that grants itself."""
    wanted = {str(v).strip() for v in (values or [])}
    return [s for s in SELF_SERVE_SCOPES if s in wanted]


def needs_review(values):
    """The subset that a person still has to look at."""
    wanted = {str(v).strip() for v in (values or [])}
    return [s for s in REVIEWED_SCOPES if s in wanted]


def valid_scopes(values):
    """Keep only scopes that exist, in a stable order."""
    wanted = {str(v).strip() for v in (values or [])}
    return [scope for scope in SCOPES if scope in wanted]


class Partner(models.Model):
    """Somebody outside V-ENT who has been given, or has asked for, access."""

    STATUS_CHOICES = [
        ('pending', 'Pending review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('suspended', 'Suspended'),
    ]

    SSO_CHOICES = [
        ('none', 'Not requested'),
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    partner_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True)

    # Who is accountable for this partner on our side of the line.
    owner = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='partners')
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name='partners',
    )

    contact_name = models.CharField(max_length=140)
    contact_email = models.EmailField()
    website = models.URLField(blank=True, default='')
    description = models.TextField(blank=True, default='')
    intended_use = models.TextField(blank=True, default='')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requested_scopes = models.JSONField(default=list, blank=True)
    approved_scopes = models.JSONField(default=list, blank=True)

    # SSO is a second, stricter approval. None of these are asked for unless a
    # partner wants to sign V-ENT members in on their own site.
    sso_status = models.CharField(max_length=20, choices=SSO_CHOICES, default='none')
    # Where we ask this partner to confirm one of their own usernames, for a
    # tournament requirement that names them. Empty means "we do not call this
    # partner", and every such requirement falls back to a person reading it.
    verification_url = models.URLField(max_length=300, blank=True, default='')
    # What we send them so they can tell it is us asking. Their secret, not
    # ours: they issue it, we store it, and rotating it is their side's job.
    verification_secret = models.CharField(max_length=120, blank=True, default='')

    legal_name = models.CharField(max_length=200, blank=True, default='')
    registration_number = models.CharField(max_length=80, blank=True, default='')
    privacy_policy_url = models.URLField(blank=True, default='')
    terms_url = models.URLField(blank=True, default='')
    data_protection_contact = models.EmailField(blank=True, default='')
    redirect_uris = models.JSONField(default=list, blank=True)
    sso_client_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    sso_client_secret_hash = models.CharField(max_length=128, blank=True, default='')

    review_note = models.TextField(blank=True, default='')
    reviewed_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='partner_reviews',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.name} ({self.status})'

    @property
    def is_active(self):
        return self.status == 'approved'

    @property
    def sso_enabled(self):
        return self.status == 'approved' and self.sso_status == 'approved'

    def allows(self, scope):
        return scope in (self.approved_scopes or [])

    def issue_sso_credentials(self):
        """Mint a client id and secret. The secret is returned once, never stored."""
        self.sso_client_id = f'vent_sso_{secrets.token_hex(12)}'
        secret = secrets.token_urlsafe(32)
        self.sso_client_secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        self.save(update_fields=['sso_client_id', 'sso_client_secret_hash', 'updated_at'])
        return secret

    def sso_secret_matches(self, secret):
        import hmac
        if not (secret and self.sso_client_secret_hash):
            return False
        return hmac.compare_digest(
            hashlib.sha256(str(secret).encode()).hexdigest(), self.sso_client_secret_hash,
        )


class PartnerApiKey(models.Model):
    """One key, one set of scopes, one hash.

    The plaintext is shown at issue and never again, which is the only way the
    promise "a leaked database does not leak working keys" can be true.
    """

    PREFIX = 'vent_pk_'
    # A test key's id starts with this, so the key itself reads
    # `vent_pk_test_...` and nobody pastes one into production by accident.
    SANDBOX_ID_PREFIX = 'test_'

    ENVIRONMENT_CHOICES = [('live', 'Live'), ('sandbox', 'Sandbox')]

    id = models.AutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=120, default='Default key')
    key_id = models.CharField(max_length=32, unique=True, db_index=True)
    secret_hash = models.CharField(max_length=128)
    scopes = models.JSONField(default=list, blank=True)
    environment = models.CharField(max_length=10, choices=ENVIRONMENT_CHOICES, default='live')

    # A partner that starts hammering the API should slow down, not take the
    # platform with it.
    rate_limit_per_minute = models.PositiveIntegerField(default=60)

    created_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='issued_partner_keys',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.partner_id}:{self.key_id}'

    @property
    def is_active(self):
        return self.revoked_at is None

    @classmethod
    def issue(cls, partner, *, name='Default key', scopes=None, created_by=None,
              rate_limit_per_minute=60, environment='live'):
        """Create a key and hand back the one plaintext copy of it."""
        sandbox = environment == 'sandbox'
        key_id = (cls.SANDBOX_ID_PREFIX if sandbox else '') + secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        obj = cls.objects.create(
            partner=partner,
            name=name or 'Default key',
            key_id=key_id,
            secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
            # A key can never carry a scope the partner was not approved for,
            # whatever the caller asked for. A sandbox key may also carry the
            # studio scopes, which reach nothing real.
            scopes=[s for s in valid_scopes(scopes)
                    if partner.allows(s) or (sandbox and s in SANDBOX_SCOPES)],
            created_by=created_by,
            rate_limit_per_minute=rate_limit_per_minute or 60,
            environment='sandbox' if sandbox else 'live',
        )
        return obj, f'{cls.PREFIX}{key_id}.{secret}'

    def matches(self, secret):
        import hmac
        return hmac.compare_digest(
            hashlib.sha256(str(secret).encode()).hexdigest(), self.secret_hash,
        )

    def allows(self, scope):
        """A key grants a scope only while its partner still does.

        The one exception is a sandbox key holding a studio scope: that needs
        the partner to be active, not to hold the scope, because a sandbox
        game reaches nothing real (see SANDBOX_SCOPES).
        """
        if not (self.is_active and self.partner.is_active and scope in (self.scopes or [])):
            return False
        if self.environment == 'sandbox' and scope in SANDBOX_SCOPES:
            return True
        return self.partner.allows(scope)


class OAuthAuthorizationCode(models.Model):
    """A one-shot code handed to a partner site after somebody approves it."""

    LIFETIME = timedelta(minutes=10)

    id = models.AutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='auth_codes')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='partner_auth_codes')
    code_hash = models.CharField(max_length=128, db_index=True)
    redirect_uri = models.URLField()
    scopes = models.JSONField(default=list, blank=True)
    # PKCE, so a partner with a public client is not relying on a secret it
    # cannot keep.
    code_challenge = models.CharField(max_length=128, blank=True, default='')
    code_challenge_method = models.CharField(max_length=10, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def is_valid(self):
        return self.used_at is None and timezone.now() - self.created_at < self.LIFETIME


class OAuthAccessToken(models.Model):
    """What the partner site actually calls the profile endpoint with."""

    LIFETIME = timedelta(hours=1)

    id = models.AutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='access_tokens')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='partner_tokens')
    token_hash = models.CharField(max_length=128, db_index=True)
    scopes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def is_valid(self):
        return self.revoked_at is None and timezone.now() - self.created_at < self.LIFETIME


class ExternalIdentity(models.Model):
    """A V-ENT account linked to an account somewhere else.

    Built for signing in with an African Free Fire Community account, and shaped
    so a second provider is configuration rather than code.
    """

    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='external_identities')
    provider = models.CharField(max_length=40)
    external_id = models.CharField(max_length=190)
    external_username = models.CharField(max_length=190, blank=True, default='')
    external_email = models.EmailField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('provider', 'external_id')

    def __str__(self):
        return f'{self.provider}:{self.external_id}'


class InboundLogin(models.Model):
    """One in-flight "sign in with AFC" attempt, and its PKCE verifier.

    AFC sets `PKCE_REQUIRED: True`, and their guide is blunt about it: it is not
    advisory and not only for public clients, and omitting it is "the single
    most common cause of a first integration attempt failing". So the authorize
    request carries a `code_challenge` and the token exchange has to carry the
    matching `code_verifier`.

    The verifier cannot travel in `state`. State goes out through the player's
    browser and comes back the same way, and Django's `signing.dumps` is signed
    but not encrypted, so anybody who can read the address bar could read the
    verifier out of it. That is precisely the interception PKCE exists to stop,
    so putting the secret there would leave the ceremony in place and the
    protection gone.

    It cannot live in the cache either. No CACHES backend is configured, so
    Django falls back to per-process local memory: under more than one gunicorn
    worker the request that starts the login and the request that finishes it
    are usually different processes, and the second one would find nothing.
    That failure is invisible in development, where there is one process.

    So it is a row. Short-lived, single use, and deleted the moment it is spent.
    """

    id = models.AutoField(primary_key=True)
    provider = models.CharField(max_length=40)
    # The opaque value handed to the provider and returned on the callback.
    state = models.CharField(max_length=190, unique=True, db_index=True)
    code_verifier = models.CharField(max_length=128)
    # Set when somebody already signed in started this from their settings page
    # to add the provider to their linked accounts. Null means the ordinary
    # case: a sign-in, where whoever comes back is whoever the provider says.
    #
    # It is held here rather than inferred on the way back, because the callback
    # arrives from the provider with no V-ENT session on it at all: a linking
    # flow and a sign-in flow look identical at that point unless the intent was
    # written down when the flow started.
    link_user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True,
                                  blank=True, related_name='inbound_link_attempts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.provider}:{self.state[:12]}'

    @classmethod
    def sweep(cls, older_than_seconds=900):
        """Drop attempts nobody finished.

        An abandoned sign-in leaves a row behind, and without this the table
        grows forever with dead secrets in it. Called on the way past rather
        than from a cron, because it is two orders of magnitude cheaper than
        the HTTP round trip it sits next to.
        """
        from django.utils import timezone as _tz
        from datetime import timedelta
        cutoff = _tz.now() - timedelta(seconds=older_than_seconds)
        return cls.objects.filter(created_at__lt=cutoff).delete()


# ---------------------------------------------------------------------------
# Game studios (bracket structure design, section 14; P6)
# ---------------------------------------------------------------------------

#: How many games one partner may register.
STUDIO_GAME_LIMIT = 10

#: How long the secret before a rotation still signs and verifies, so a studio
#: can swap its secret without dropping an event in between.
SECRET_OVERLAP = timedelta(hours=24)

#: Usernames a sandbox game may name as its testers.
STUDIO_TESTER_LIMIT = 20

#: Words a game's address may not be, because a route beside it already is.
STUDIO_RESERVED_SLUGS = {'register', 'sandbox-key', 'link', 'my-links', 'tournament'}


class StudioGame(models.Model):
    """A game a studio has connected to V-ENT.

    CEO, 16 October 2026 (inbox 482): "then even build the section and part for
    integration with game developers." Design section 14, item 1: webhook
    addresses, a signing secret encrypted at rest, a result schema, a scoring
    expression, sandbox or live.

    A game starts in the sandbox. There it reaches only tournaments its own
    studio runs and the testers it names, with test keys nobody reviews. Live
    is a second approval by a super admin, the same shape as Sign in with V-ENT
    on a partner: asking does not grant it.
    """

    MODE_CHOICES = [('sandbox', 'Sandbox'), ('live', 'Live')]
    LIVE_CHOICES = [
        ('none', 'Not requested'),
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.AutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='studio_games')
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True)
    # The V-ENT game this serves: tournaments of that game may run through it.
    game = models.ForeignKey('vent_auth.Games', on_delete=models.SET_NULL, null=True,
                             blank=True, related_name='studio_games')

    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='sandbox')
    live_status = models.CharField(max_length=12, choices=LIVE_CHOICES, default='none')

    session_webhook_url = models.URLField(max_length=300, blank=True, default='')
    events_webhook_url = models.URLField(max_length=300, blank=True, default='')

    # Encrypted, never hashed: every webhook is signed with it, so we need it
    # back (studio_secrets). The hint is its last four characters, so a studio
    # can tell which secret it is holding without us showing it again.
    signing_secret_encrypted = models.TextField(blank=True, default='')
    previous_secret_encrypted = models.TextField(blank=True, default='')
    previous_secret_expires_at = models.DateTimeField(null=True, blank=True)
    secret_hint = models.CharField(max_length=8, blank=True, default='')

    result_schema = models.JSONField(default=dict, blank=True)
    scoring_expression = models.CharField(max_length=500, default='team.score')
    settings_schema = models.JSONField(default=dict, blank=True)
    # Empty means every event in the catalogue.
    subscribed_events = models.JSONField(default=list, blank=True)
    testers = models.JSONField(default=list, blank=True)

    review_note = models.TextField(blank=True, default='')
    reviewed_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='studio_game_reviews')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='studio_games_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return f'{self.name} ({self.mode})'

    def save(self, *args, **kwargs):
        from django.utils.text import slugify
        from vent_auth.slugs import sync_slug

        # A game called "Register" must not take the address of the register
        # route beside it, so a name that would shadow one gets a suffix.
        basis = self.name
        if slugify(basis or '') in STUDIO_RESERVED_SLUGS:
            basis = '%s game' % basis
        changed = sync_slug(self, basis, entity_type='studio_game', id_attr='id')
        fields = kwargs.get('update_fields')
        if changed and fields is not None:
            kwargs['update_fields'] = list(set(fields) | {'slug'})
        super().save(*args, **kwargs)

    # -- the secret ---------------------------------------------------------

    def set_secret(self, plain=None):
        """Lock a secret onto this game and return its one readable copy."""
        from . import studio_secrets

        plain = plain or studio_secrets.new_signing_secret()
        self.signing_secret_encrypted = studio_secrets.encrypt(plain)
        self.secret_hint = plain[-4:]
        return plain

    def rotate_secret(self):
        """A new secret now; the one it replaces still works for a day."""
        self.previous_secret_encrypted = self.signing_secret_encrypted
        self.previous_secret_expires_at = timezone.now() + SECRET_OVERLAP
        plain = self.set_secret()
        self.save(update_fields=['signing_secret_encrypted', 'previous_secret_encrypted',
                                 'previous_secret_expires_at', 'secret_hint', 'updated_at'])
        return plain

    def signing_secrets(self, now=None):
        """The secrets that sign and verify right now: current first.

        An unreadable secret (the server lost its key) signs nothing rather
        than raising, so one broken game never stops anybody else's delivery.
        """
        from . import studio_secrets

        now = now or timezone.now()
        out = []
        try:
            if self.signing_secret_encrypted:
                out.append(studio_secrets.decrypt(self.signing_secret_encrypted))
        except studio_secrets.SecretUnreadable:
            return []
        if (self.previous_secret_encrypted and self.previous_secret_expires_at
                and now < self.previous_secret_expires_at):
            try:
                out.append(studio_secrets.decrypt(self.previous_secret_encrypted))
            except studio_secrets.SecretUnreadable:
                pass
        return out

    # -- where it may reach ----------------------------------------------------

    @property
    def is_live(self):
        return self.mode == 'live' and self.live_status == 'approved'

    @property
    def environment(self):
        return 'live' if self.is_live else 'sandbox'

    def may_test(self, user):
        """The studio's own account, or a tester it named."""
        if user is None:
            return False
        if self.partner.owner_id == user.user_id:
            return True
        names = {str(n).lower() for n in (self.testers or [])}
        return (user.username or '').lower() in names


class StudioTournamentLink(models.Model):
    """A tournament whose matches run through a studio's game.

    One per tournament: a match has one lobby, in one game. The organiser
    connects it (P6d); events about the tournament go to the game from then on
    (P6b), and its matches get game sessions (P6d).
    """

    id = models.AutoField(primary_key=True)
    tournament = models.OneToOneField('vent_tournament.Tournament', on_delete=models.CASCADE,
                                      related_name='studio_link')
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='tournament_links')
    # What the organiser chose for the game, checked against its settings schema.
    settings = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    enabled_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='studio_links_enabled')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.tournament_id} -> {self.game_id}'


def _delivery_ref():
    return 'dl_' + secrets.token_hex(8)


class StudioDelivery(models.Model):
    """One signed request V-ENT owes a studio, and what became of it.

    Design section 14, item 5: "retries with backoff; a delivery log the studio
    can read". Events out and the game session calls share this one engine
    (`studio_delivery`), so a session call is retried, signed and logged
    exactly as an event is. The row IS the log: a studio reads what we sent,
    when, how often, and what their server answered.
    """

    KIND_CHOICES = [
        ('event', 'Event'),
        ('create_game_session', 'Create a game session'),
        ('cancel_game_session', 'Cancel a game session'),
        ('get_game_session', 'Ask for a game session'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Waiting to be delivered'),
        ('delivered', 'Delivered'),
        ('failed', 'Gave up'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.AutoField(primary_key=True)
    # The id the studio sees, also sent as V-Ent-Delivery for their replay cache.
    ref = models.CharField(max_length=24, unique=True, default=_delivery_ref)
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='deliveries')
    kind = models.CharField(max_length=24, choices=KIND_CHOICES, default='event')
    event = models.CharField(max_length=40, blank=True, default='')
    url = models.URLField(max_length=300)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    # A code, never a sentence or a traceback: TIMEOUT, HTTP_STATUS, GONE...
    last_error = models.CharField(max_length=40, blank=True, default='')
    # The start of what the studio's server answered, for their own debugging.
    response_excerpt = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # The game session a session call is about (P6d); empty for events.
    session = models.ForeignKey('StudioSession', on_delete=models.SET_NULL, null=True,
                                blank=True, related_name='deliveries')

    class Meta:
        ordering = ('-created_at', '-id')

    def __str__(self):
        return f'{self.ref} {self.kind} {self.status}'


def _link_ref():
    return 'ln_' + secrets.token_hex(6)


def _code_ref():
    return 'lc_' + secrets.token_hex(6)


class StudioAccountLink(models.Model):
    """A V-ENT account and the account it plays as in a studio's game (P6c).

    Design section 14, item 2. One account per person per game, and one person
    per account: `studio_links` refuses anything else with a code rather than
    moving a link from one person to another without anybody saying so.
    """

    METHOD_CHOICES = [('code', 'A code shown in the game'), ('oauth', 'Sign in with V-ENT')]

    id = models.AutoField(primary_key=True)
    ref = models.CharField(max_length=24, unique=True, default=_link_ref)
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='account_links')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='studio_account_links')
    # The studio's own id for the account, and the name the game shows.
    external_id = models.CharField(max_length=190)
    external_name = models.CharField(max_length=120, blank=True, default='')
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='code')
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-linked_at',)
        constraints = [
            models.UniqueConstraint(fields=['game', 'external_id'], name='studio_link_one_person_per_account'),
            models.UniqueConstraint(fields=['game', 'user'], name='studio_link_one_account_per_person'),
        ]

    def __str__(self):
        return f'{self.game_id}:{self.external_id} -> {self.user_id}'


class StudioLinkCode(models.Model):
    """A code a game shows a player so they can link their account (P6c).

    Only a hash is kept: the code itself is shown once, in the game. Ten
    minutes, one use, and a newer code for the same account ends the older one.
    """

    id = models.AutoField(primary_key=True)
    # What the studio polls by. Never the code.
    ref = models.CharField(max_length=24, unique=True, default=_code_ref)
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='link_codes')
    code_hash = models.CharField(max_length=64, db_index=True)
    external_id = models.CharField(max_length=190)
    external_name = models.CharField(max_length=120, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    claimed_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='studio_link_codes_claimed')
    claimed_at = models.DateTimeField(null=True, blank=True)
    link = models.ForeignKey(StudioAccountLink, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='codes')

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.ref} for {self.game_id}:{self.external_id}'


def _session_ref():
    return 'gs_' + secrets.token_hex(8)


class StudioSession(models.Model):
    """One game of a match, played in a studio's lobby (P6d).

    Design section 14, item 3. A best of three is up to three sessions, one
    per game; a game that crashes is replayed as the next attempt of the same
    game number. The studio sees `ref` and the idempotency key, never our ids.
    """

    STATE_CHOICES = [
        ('creating', 'Asked for'),
        ('created', 'Lobby made'),
        ('starting', 'Starting'),
        ('started', 'Being played'),
        ('finished', 'Finished'),
        ('cancelling', 'Being cancelled'),
        ('cancelled', 'Cancelled'),
    ]
    OUTCOME_CHOICES = [
        ('', 'None yet'),
        ('applied', 'Recorded on the match'),
        ('superseded', 'The match was settled another way'),
        ('needs_organiser', 'Needs the organiser'),
    ]

    id = models.AutoField(primary_key=True)
    ref = models.CharField(max_length=24, unique=True, default=_session_ref)
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='sessions')
    link = models.ForeignKey(StudioTournamentLink, on_delete=models.CASCADE, related_name='sessions')
    match = models.ForeignKey('vent_tournament.BracketMatch', on_delete=models.CASCADE,
                              related_name='studio_sessions')
    game_number = models.PositiveSmallIntegerField(default=1)
    attempt = models.PositiveSmallIntegerField(default=1)
    idempotency_key = models.CharField(max_length=40, unique=True)
    state = models.CharField(max_length=12, choices=STATE_CHOICES, default='creating')
    # The studio's own id for its lobby, and how a player gets into it.
    studio_session_id = models.CharField(max_length=190, blank=True, default='')
    join = models.JSONField(default=dict, blank=True)
    result = models.JSONField(null=True, blank=True)
    scores = models.JSONField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES, blank=True, default='')
    error_code = models.CharField(max_length=40, blank=True, default='')
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('match_id', 'game_number', 'attempt')

    def __str__(self):
        return f'{self.ref} {self.state}'

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = 'vent-%s' % self.ref
        super().save(*args, **kwargs)


class StudioNonce(models.Model):
    """A request id a studio already used, so the same signed request cannot
    be played twice inside its five minutes (owner rule R72). In the database
    rather than the cache, because the cache here is per process."""

    id = models.AutoField(primary_key=True)
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='nonces')
    nonce = models.CharField(max_length=64)
    seen_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['game', 'nonce'], name='studio_nonce_once')]


class StudioMonthlyPlayer(models.Model):
    """A linked player who played a game's sessions in a month. Counted from
    the first session, so either pricing model can be chosen later (design
    section 14, item 8, and decision 9)."""

    id = models.AutoField(primary_key=True)
    game = models.ForeignKey(StudioGame, on_delete=models.CASCADE, related_name='monthly_players')
    month = models.DateField()
    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='studio_months')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['game', 'month', 'user'],
                                               name='studio_player_once_a_month')]

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
}

SCOPE_CHOICES = [(key, label) for key, label in SCOPES.items()]

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

    id = models.AutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=120, default='Default key')
    key_id = models.CharField(max_length=32, unique=True, db_index=True)
    secret_hash = models.CharField(max_length=128)
    scopes = models.JSONField(default=list, blank=True)

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
              rate_limit_per_minute=60):
        """Create a key and hand back the one plaintext copy of it."""
        key_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        obj = cls.objects.create(
            partner=partner,
            name=name or 'Default key',
            key_id=key_id,
            secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
            # A key can never carry a scope the partner was not approved for,
            # whatever the caller asked for.
            scopes=[s for s in valid_scopes(scopes) if partner.allows(s)],
            created_by=created_by,
            rate_limit_per_minute=rate_limit_per_minute or 60,
        )
        return obj, f'{cls.PREFIX}{key_id}.{secret}'

    def matches(self, secret):
        import hmac
        return hmac.compare_digest(
            hashlib.sha256(str(secret).encode()).hexdigest(), self.secret_hash,
        )

    def allows(self, scope):
        """A key grants a scope only while its partner still does."""
        return (
            self.is_active
            and self.partner.is_active
            and scope in (self.scopes or [])
            and self.partner.allows(scope)
        )


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

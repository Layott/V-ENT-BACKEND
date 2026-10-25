"""An organiser's own keys to paid services (inbox 551, 25 October 2026).

The one place that stores, finds and checks a connected key. The secret is
encrypted with the same keyring as a studio's signing secret
(vent_partners.studio_secrets, STUDIO_SECRETS_KEY, rotation included), because
it has the same need: we must use the plaintext again, so it cannot be hashed.

Whose key a tournament uses: its organisation's when it runs for one that has
connected a key, else its creator's. Nobody else's, and never a platform key:
the CEO's rule is that the organiser connects their own.
"""
import re

from vent_partners import studio_secrets

from .models import ConnectedKey

PROVIDERS = dict(ConnectedKey.PROVIDERS)
# Google's API keys: "AIza" and 35 more characters of [A-Za-z0-9_-].
GEMINI_SHAPE = re.compile(r'^AIza[0-9A-Za-z_\-]{35}$')


class ConnectedKeyError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def check_shape(provider, key):
    if provider not in PROVIDERS:
        raise ConnectedKeyError('KEY_PROVIDER_UNKNOWN')
    if provider == 'gemini' and not GEMINI_SHAPE.match(key or ''):
        raise ConnectedKeyError('KEY_SHAPE_WRONG')


def verify_with_provider(provider, key):
    """Ask the provider whether the key works, with a free call. Raises
    KEY_REJECTED when it says no and KEY_CHECK_UNAVAILABLE when it cannot be
    reached; a key is never stored without an answer."""
    import requests

    if provider == 'gemini':
        try:
            resp = requests.get('https://generativelanguage.googleapis.com/v1beta/models',
                                params={'key': key, 'pageSize': 1}, timeout=8)
        except requests.RequestException:
            raise ConnectedKeyError('KEY_CHECK_UNAVAILABLE')
        if resp.status_code in (400, 401, 403):
            raise ConnectedKeyError('KEY_REJECTED')
        if resp.status_code >= 500 or resp.status_code == 429:
            raise ConnectedKeyError('KEY_CHECK_UNAVAILABLE')
        return True
    raise ConnectedKeyError('KEY_PROVIDER_UNKNOWN')


def store(*, provider, key, user=None, organization=None, added_by=None):
    """Encrypt and keep `key`, replacing any key the same holder had for the provider."""
    holder = user if organization is None else None
    row = ConnectedKey.objects.filter(provider=provider, organization=organization,
                                      user=holder).first()
    if row is None:
        row = ConnectedKey(provider=provider, organization=organization, user=holder)
    row.secret = studio_secrets.encrypt(key)
    row.last4 = key[-4:]
    row.added_by = added_by
    row.save()
    return row


def plaintext(row):
    try:
        return studio_secrets.decrypt(row.secret)
    except studio_secrets.SecretUnreadable:
        return None


def key_for_tournament(tournament, provider):
    """The plaintext key a tournament reads with, or None."""
    org = getattr(tournament, 'tournament_organization', None)
    if org is not None:
        row = ConnectedKey.objects.filter(organization=org, provider=provider).first()
        if row:
            return plaintext(row)
    row = ConnectedKey.objects.filter(user_id=tournament.tournament_creator_id, provider=provider,
                                      organization__isnull=True).first()
    return plaintext(row) if row else None


def as_row(row):
    """What a screen may see: never the key."""
    org = row.organization
    return {'provider': row.provider, 'provider_label': PROVIDERS.get(row.provider, row.provider),
            'last4': row.last4,
            'organization': ({'slug': getattr(org, 'slug', '') or '', 'name': org.org_name}
                             if org is not None else None),
            'connected_at': row.updated_at}

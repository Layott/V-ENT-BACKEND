"""An organiser's own keys to AI services (inbox 551 and 555, 25 October 2026).

The one place that stores, finds and checks a connected key. The secret is
encrypted with the same keyring as a studio's signing secret
(vent_partners.studio_secrets, STUDIO_SECRETS_KEY, rotation included), because
it has the same need: we must use the plaintext again, so it cannot be hashed.

Which providers, how their keys are checked and how they are asked to read,
lives in `ai_providers` (inbox 555: "any api key from any ai").

Whose key a tournament uses: its organisation's when it runs for one that has
connected a key, else its creator's. Nobody else's, and never a platform key:
the CEO's rule is that the organiser connects their own. When a holder has
connected more than one, the one connected most recently is used.
"""
from vent_partners import studio_secrets

from . import ai_providers
from .models import ConnectedKey

PROVIDERS = dict(ai_providers.choices())


class ConnectedKeyError(Exception):
    def __init__(self, code, **extra):
        super().__init__(code)
        self.code = code
        self.extra = extra


def check_shape(provider, key, model=''):
    try:
        ai_providers.check_shape(provider, key)
        ai_providers.check_model(model)
    except ai_providers.KeyCheck as exc:
        raise ConnectedKeyError(exc.code, **exc.extra)


def verify_with_provider(provider, key):
    """Ask the provider whether the key works, with a free call. Raises
    KEY_REJECTED when it says no and KEY_CHECK_UNAVAILABLE when it cannot be
    reached; a key is never stored without an answer."""
    try:
        return ai_providers.verify(provider, key)
    except ai_providers.KeyCheck as exc:
        raise ConnectedKeyError(exc.code, **exc.extra)


def store(*, provider, key, model='', user=None, organization=None, added_by=None):
    """Encrypt and keep `key`, replacing any key the same holder had for the provider."""
    holder = user if organization is None else None
    row = ConnectedKey.objects.filter(provider=provider, organization=organization,
                                      user=holder).first()
    if row is None:
        row = ConnectedKey(provider=provider, organization=organization, user=holder)
    row.secret = studio_secrets.encrypt(key)
    row.last4 = key[-4:]
    row.model = model or ''
    row.added_by = added_by
    row.save()
    return row


def plaintext(row):
    try:
        return studio_secrets.decrypt(row.secret)
    except studio_secrets.SecretUnreadable:
        return None


class Chosen:
    """The key a tournament reads with: provider, plaintext and model."""

    def __init__(self, row, key):
        self.provider = row.provider
        self.key = key
        self.model = row.model or ai_providers.PROVIDERS[row.provider]['default_model']
        self.label = PROVIDERS.get(row.provider, row.provider)


def _first_readable(rows):
    for row in rows:
        if row.provider not in PROVIDERS:
            continue
        key = plaintext(row)
        if key:
            return Chosen(row, key)
    return None


def chosen_for_tournament(tournament, provider=None):
    """The key a tournament reads with, or None: the organisation's most recent,
    else the creator's most recent. `provider` limits it to one service."""
    org = getattr(tournament, 'tournament_organization', None)
    if org is not None:
        rows = ConnectedKey.objects.filter(organization=org)
        if provider:
            rows = rows.filter(provider=provider)
        found = _first_readable(rows.order_by('-updated_at'))
        if found:
            return found
    rows = ConnectedKey.objects.filter(user_id=tournament.tournament_creator_id,
                                       organization__isnull=True)
    if provider:
        rows = rows.filter(provider=provider)
    return _first_readable(rows.order_by('-updated_at'))


def key_for_tournament(tournament, provider):
    """The plaintext key a tournament reads with for one provider, or None."""
    found = chosen_for_tournament(tournament, provider)
    return found.key if found else None


def as_row(row):
    """What a screen may see: never the key."""
    org = row.organization
    return {'provider': row.provider, 'provider_label': PROVIDERS.get(row.provider, row.provider),
            'model': row.model or None,
            'default_model': ai_providers.PROVIDERS.get(row.provider, {}).get('default_model'),
            'last4': row.last4,
            'organization': ({'slug': getattr(org, 'slug', '') or '', 'name': org.org_name}
                             if org is not None else None),
            'connected_at': row.updated_at}

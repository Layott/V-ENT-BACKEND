"""A studio's signing secret, kept so we can sign with it and nobody can read it.

Design section 14, item 1: "a signing secret (encrypted at rest, R63)". An API
key can be stored as a hash because we only ever compare against it. A signing
secret cannot: every webhook we send is signed with it, so we need the secret
itself back. It is therefore encrypted, never hashed, and the plaintext is
shown to the studio exactly twice in its life: when it is issued and when it is
rotated.

The key comes from `STUDIO_SECRETS_KEY`, a comma separated list of Fernet keys.
The first locks everything new; every key in the list opens, so a key can be
replaced without a moment where old secrets cannot be read:

    STUDIO_SECRETS_KEY=<new key>,<old key>      until every secret is rotated
    STUDIO_SECRETS_KEY=<new key>                after

With no key set, one is derived from `SECRET_KEY` by HKDF, under its own label
so it is never the site secret itself. That keeps a developer machine working
with nothing to configure. The cost is written here so it is not discovered: a
server that changes `SECRET_KEY` with no `STUDIO_SECRETS_KEY` set can no longer
read its studios' secrets, and every studio then rotates.
"""
import base64
import os
import secrets as _random

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = 'vent_whsec_'


class SecretUnreadable(Exception):
    """The ciphertext does not open with any key this server holds."""

    code = 'STUDIO_SECRET_UNREADABLE'


def new_signing_secret():
    """A fresh shared secret, named so a developer can tell what it is."""
    return PREFIX + _random.token_urlsafe(32)


def _derived_key():
    from django.conf import settings

    material = (settings.SECRET_KEY or '').encode()
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'vent-studio-secrets').derive(material)
    return base64.urlsafe_b64encode(raw)


def _fernet():
    configured = [k.strip() for k in os.environ.get('STUDIO_SECRETS_KEY', '').split(',') if k.strip()]
    keys = [Fernet(k.encode() if isinstance(k, str) else k) for k in configured]
    if not keys:
        keys = [Fernet(_derived_key())]
    return MultiFernet(keys)


def encrypt(text):
    return _fernet().encrypt(str(text).encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(str(token or '').encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        raise SecretUnreadable() from None

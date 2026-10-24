"""Sealed ticket codes (inbox 525, released 24 October 2026).

CEO, 18 October: "Each ticket is cryptographically unique. Our system
instantly flags duplicates or tampered codes at check-in."

A ticket's code is already random (`views_tickets._new_code`). What a code
alone cannot prove is that V-ENT issued it: offline, the door's copy of the
list cannot tell a forged code from a ticket bought after the copy was made.
So the QR carries the code and a signature over it, `VT-XXXXXXXX.<seal>`:

  * the seal is ECDSA P-256 over `vent-ticket:<code>`, in the raw r||s form
    WebCrypto verifies, base64url without padding;
  * the private key is the server's alone (`TICKET_SEAL_KEY`, a PEM in the
    environment, never in source); the door keeps the public key
    (`public_jwk`) and checks a seal on the phone with no signal;
  * without a key, sealing is off and the QR is the bare code, which is what
    every ticket printed before this carries and what the door still accepts.
"""
import base64
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature, encode_dss_signature,
)

PREFIX = 'vent-ticket:'


class SealError(ValueError):
    def __init__(self, code='TICKET_TAMPERED'):
        super().__init__(code)
        self.code = code


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _private_key():
    pem = os.environ.get('TICKET_SEAL_KEY', '').strip()
    if not pem:
        return None
    # An .env line keeps a PEM on one line with literal \n between its lines.
    return serialization.load_pem_private_key(pem.replace('\\n', '\n').encode('ascii'), password=None)


def enabled():
    return _private_key() is not None


def seal(code):
    """The QR payload for `code`: sealed when a key is set, else the code."""
    key = _private_key()
    if key is None:
        return code
    der = key.sign((PREFIX + code).encode('ascii'), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return '%s.%s' % (code, _b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big')))


def read(scanned):
    """The ticket code inside what the door scanned. A bare code is returned
    as it is; a sealed one only when its seal is V-ENT's, else SealError."""
    text = str(scanned or '').strip()
    if '.' not in text:
        return text.upper()
    code, _dot, mark = text.partition('.')
    key = _private_key()
    if key is None:
        # Sealing is off on this server: nothing to check the seal against,
        # so the code is read and judged as a bare code.
        return code.upper()
    try:
        raw = _unb64(mark)
        if len(raw) != 64:
            raise SealError()
        der = encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
        key.public_key().verify(der, (PREFIX + code).encode('ascii'), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise SealError()
    return code.upper()


def public_jwk():
    """The public half as a JWK, for the door to verify on the phone. None
    when sealing is off."""
    key = _private_key()
    if key is None:
        return None
    numbers = key.public_key().public_numbers()
    return {'kty': 'EC', 'crv': 'P-256',
            'x': _b64(numbers.x.to_bytes(32, 'big')),
            'y': _b64(numbers.y.to_bytes(32, 'big')),
            'alg': 'ES256', 'use': 'sig'}

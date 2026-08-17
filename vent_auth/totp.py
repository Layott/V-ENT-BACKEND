"""RFC 6238 time-based one-time passwords, standard library only.

The admin portal's previous "2FA" accepted any six digits and never told the
server, so it added nothing. This module is the real thing: HMAC-SHA1 over the
30-second time step, 6 digits, with a +/- 1 step window for clock drift.

No third-party dependency: `pyotp` would do the same in fewer lines, but this
avoids adding a package to a deployment that is about to move to a VPS.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1  # accept the previous and next step


def generate_secret(length_bytes=20):
    """A fresh base32 secret (20 bytes = 160 bits, the RFC 4226 recommendation)."""
    return base64.b32encode(secrets.token_bytes(length_bytes)).decode('utf-8').rstrip('=')


def _code_for_step(secret_b32, step):
    # base32 decoding requires padding to a multiple of 8 characters.
    padded = secret_b32 + '=' * (-len(secret_b32) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10 ** DIGITS)).zfill(DIGITS)


def current_step(at=None):
    return int((at if at is not None else time.time()) // STEP_SECONDS)


def verify(secret_b32, code, last_used_step=None, at=None):
    """Check a submitted code.

    Returns the step it matched, or None. Callers must persist the returned step
    as `last_used_step` so the same code cannot be replayed inside its window.
    """
    if not secret_b32 or not code:
        return None
    code = str(code).strip().replace(' ', '')
    if len(code) != DIGITS or not code.isdigit():
        return None

    now_step = current_step(at)
    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        step = now_step + offset
        if last_used_step is not None and step <= last_used_step:
            continue  # already spent
        if hmac.compare_digest(_code_for_step(secret_b32, step), code):
            return step
    return None


def provisioning_uri(secret_b32, account_name, issuer='V-ENT Admin'):
    """otpauth:// URI for Google Authenticator / Authy / 1Password."""
    label = quote(f'{issuer}:{account_name}')
    return (
        f'otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}'
        f'&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}'
    )

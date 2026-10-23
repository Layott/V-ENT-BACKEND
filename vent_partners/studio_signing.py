"""How a webhook between V-ENT and a studio proves who sent it, and when.

Design section 14, item 5: "Signed with HMAC SHA-256 over method, path,
timestamp, host and body hash; five minute replay window." Challengermode's
form (research file, section 7.5), because Toornament's sha256(body + secret)
signs the body alone: a captured request replays for ever and can be pointed at
another address.

The string to sign, one line each for method and path, then the rest:

    POST
    /vent/hook?x=1
    1760659200;studio.example;<base64 SHA-256 of the body>

and the signature is the base64 HMAC SHA-256 of that string under the shared
secret. Every request carries:

    V-Ent-Timestamp        unix seconds
    V-Ent-Content-SHA256   base64 SHA-256 of the body
    V-Ent-Signature        v1=<signature>, one per live secret, comma separated
    V-Ent-Delivery         the delivery id, unique, for the receiver's replay cache

Two signatures appear only during a rotation, for the 24 hours the old secret
still works, so a studio can swap its secret without dropping a single event.

The same scheme guards the other direction (owner rule R72): a studio posting a
result to us signs it the same way, and `verify` reads it over the raw body.
"""
import base64
import hashlib
import hmac
import time
from urllib.parse import urlsplit

#: Owner's words in the design: a five minute replay window.
WINDOW_SECONDS = 300

HEADER_TIMESTAMP = 'V-Ent-Timestamp'
HEADER_CONTENT = 'V-Ent-Content-SHA256'
HEADER_SIGNATURE = 'V-Ent-Signature'
HEADER_DELIVERY = 'V-Ent-Delivery'


class SignatureError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def body_hash(body):
    return base64.b64encode(hashlib.sha256(body or b'').digest()).decode()


def string_to_sign(method, path_qs, timestamp, host, content_hash):
    return '%s\n%s\n%s;%s;%s' % (str(method).upper(), path_qs, int(timestamp),
                                 str(host).lower(), content_hash)


def _mac(secret, text):
    return base64.b64encode(
        hmac.new(str(secret).encode(), text.encode(), hashlib.sha256).digest()).decode()


def sign(secret, method, path_qs, timestamp, host, body):
    return _mac(secret, string_to_sign(method, path_qs, timestamp, host, body_hash(body)))


def _path_and_query(url):
    parts = urlsplit(url)
    return (parts.path or '/') + (('?' + parts.query) if parts.query else '')


def outbound_headers(secrets, method, url, body, *, delivery_ref, now=None):
    """Everything a request we send carries, signed under each live secret."""
    timestamp = int(now if now is not None else time.time())
    parts = urlsplit(url)
    content = body_hash(body)
    text = string_to_sign(method, _path_and_query(url), timestamp, parts.netloc, content)
    return {
        'Content-Type': 'application/json',
        HEADER_TIMESTAMP: str(timestamp),
        HEADER_CONTENT: content,
        HEADER_SIGNATURE: ','.join('v1=%s' % _mac(s, text) for s in secrets),
        HEADER_DELIVERY: delivery_ref,
    }


def _header(headers, name):
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def verify(secrets, method, path_qs, host, headers, body, *, now=None, window=WINDOW_SECONDS):
    """Return quietly when the request is signed and fresh; raise SignatureError otherwise.

    Checked in the order that costs least and says most: are the headers there,
    is it inside the window, does the body match the hash it claims, and only
    then the signature itself, compared in constant time against each secret.
    """
    stamp = _header(headers, HEADER_TIMESTAMP)
    claimed = _header(headers, HEADER_CONTENT)
    given = _header(headers, HEADER_SIGNATURE)
    if not (stamp and claimed and given):
        raise SignatureError('SIGNATURE_MISSING')
    try:
        timestamp = int(str(stamp).strip())
    except ValueError:
        raise SignatureError('SIGNATURE_MISSING') from None

    current = int(now if now is not None else time.time())
    if abs(current - timestamp) > window:
        raise SignatureError('SIGNATURE_STALE')

    content = body_hash(body)
    if not hmac.compare_digest(content, str(claimed).strip()):
        raise SignatureError('CONTENT_HASH_MISMATCH')

    text = string_to_sign(method, path_qs, timestamp, host, content)
    offered = [part.strip()[3:] for part in str(given).split(',')
               if part.strip().startswith('v1=')]
    for secret in secrets or []:
        expected = _mac(secret, text)
        for candidate in offered:
            if hmac.compare_digest(expected, candidate):
                return
    raise SignatureError('SIGNATURE_INVALID')

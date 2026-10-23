"""The addresses V-ENT is willing to call on a studio's behalf.

A studio gives us two webhook addresses, and from then on our server makes
requests to wherever they point. Left unchecked that is a way to make V-ENT
call itself, the cloud metadata service at 169.254.169.254, or anything else on
the private network behind it (server-side request forgery). So:

  * at registration, the address must be https, carry no username or password,
    and name no private, loopback or link-local host;
  * at send time, the name is resolved again and every address it resolves to
    must be public, because a name that was public on Monday can point inside
    on Tuesday (DNS rebinding), and the registration check cannot see that.

`http://localhost` is allowed while DEBUG is on, so a studio's developer can
build against a server on their own machine; never in production.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_LENGTH = 300


def _is_private_literal(host):
    if host in ('localhost', 'localhost.localdomain') or host.endswith('.localhost'):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def webhook_url_problem(url):
    """None when we may call this address, otherwise the code saying why not."""
    from django.conf import settings

    text = str(url or '').strip()
    if len(text) > MAX_LENGTH:
        return 'URL_TOO_LONG'
    parts = urlsplit(text)
    host = (parts.hostname or '').lower()

    if getattr(settings, 'DEBUG', False) and parts.scheme == 'http' \
            and host in ('localhost', '127.0.0.1'):
        return None
    if parts.scheme != 'https' or not host:
        return 'URL_NOT_HTTPS'
    if parts.username or parts.password:
        return 'URL_HAS_CREDENTIALS'
    if _is_private_literal(host):
        return 'URL_PRIVATE_HOST'
    return None


def resolves(url):
    """True when the host has at least one address right now, public or not.

    Tells "points at a private network" apart from "the name does not
    resolve", so a studio is told the fault it actually has.
    """
    parts = urlsplit(str(url or ''))
    try:
        return bool(socket.getaddrinfo(parts.hostname or '', parts.port or 443,
                                       proto=socket.IPPROTO_TCP))
    except (socket.gaierror, UnicodeError, OSError):
        return False


def resolves_public(url):
    """True when every address the host resolves to right now is public."""
    from django.conf import settings

    parts = urlsplit(str(url or ''))
    host = parts.hostname or ''
    if getattr(settings, 'DEBUG', False) and host in ('localhost', '127.0.0.1'):
        return True
    try:
        found = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not found:
        return False
    for _family, _type, _proto, _name, address in found:
        try:
            if not ipaddress.ip_address(address[0]).is_global:
                return False
        except ValueError:
            return False
    return True

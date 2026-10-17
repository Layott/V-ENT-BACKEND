"""Proving that somebody controls a site (P8, level 2).

Two ways, the two every search console offers:

  a DNS TXT record   `_vent-verify.<host>` holding `vent-verify=<token>`. It
                     proves control of the name, so it covers the host and
                     every name under it. A dedicated name rather than the host
                     itself, because a host that is a CNAME cannot carry a TXT
                     record beside it.
  a meta tag         `<meta name="vent-verify" content="<token>">` on
                     `https://<host>/`. It proves control of one page.

The meta tag check makes this server fetch a page somebody else named, which
is the shape of a server-side request forgery. So before any request the name
is resolved and every address it gives must be public (no loopback, private,
link-local, reserved or multicast range, including IPv4 inside IPv6); only
https; a redirect only between the host and its `www.` twin, re-resolved and
re-checked each hop, three at most; five seconds; the first 200 KB read; the
body is searched for the tag and never returned to anybody. The remaining gap
is a name that changes its address between the check and the connection; the
answer that could leak through it is "the tag is there" or "it is not".
"""
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import requests

TXT_NAME = '_vent-verify.%s'
TXT_PREFIX = 'vent-verify='
META_NAME = 'vent-verify'
TIMEOUT = 5
MAX_BYTES = 200_000
MOST_REDIRECTS = 3
USER_AGENT = 'V-ENT site check (+https://v-ent.co)'


class Unreachable(Exception):
    """The name or the page did not answer."""


class Refused(Exception):
    """The check would not be safe or would prove the wrong thing. `code` says which."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def txt_records(name):
    """Every TXT string at `name`, or [] when there is none."""
    import dns.exception
    import dns.resolver

    try:
        answer = dns.resolver.resolve(name, 'TXT', lifetime=TIMEOUT)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise Unreachable() from exc
    except dns.exception.DNSException as exc:
        raise Unreachable() from exc
    return [b''.join(record.strings).decode('utf-8', 'replace') for record in answer]


def addresses(host):
    """Every address `host` resolves to."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise Unreachable() from exc
    return sorted({info[4][0] for info in infos})


def public(address):
    """Whether an address is somewhere on the public internet."""
    try:
        ip = ipaddress.ip_address(str(address).split('%')[0])
    except ValueError:
        return False
    mapped = getattr(ip, 'ipv4_mapped', None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


def _get(url):
    return requests.get(url, timeout=(3, TIMEOUT), allow_redirects=False, stream=True,
                        headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'})


def _twins(host, other):
    """The host and its www. twin, either way round, and nothing else."""
    return other == host or other == 'www.' + host or 'www.' + other == host


def fetch_home(host):
    """The start of `https://<host>/`, safely. Raises Refused or Unreachable."""
    url = 'https://%s/' % host
    for _hop in range(MOST_REDIRECTS + 1):
        name = urlsplit(url).hostname or ''
        found = addresses(name)
        if not found:
            raise Unreachable()
        if not all(public(a) for a in found):
            raise Refused('SITE_ADDRESS_PRIVATE')
        try:
            response = _get(url)
        except requests.RequestException as exc:
            raise Unreachable() from exc
        try:
            location = response.headers.get('Location')
            if response.status_code in (301, 302, 303, 307, 308) and location:
                nxt = urljoin(url, location)
                parts = urlsplit(nxt)
                if parts.scheme != 'https' or parts.port not in (None, 443) \
                        or not _twins(host, (parts.hostname or '').lower()):
                    raise Refused('SITE_REDIRECT_AWAY')
                url = nxt
                continue
            if response.status_code != 200:
                raise Unreachable()
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) >= MAX_BYTES:
                    break
            return bytes(body[:MAX_BYTES]).decode(response.encoding or 'utf-8', 'replace')
        finally:
            response.close()
    raise Refused('SITE_REDIRECT_AWAY')


_META = re.compile(r'<meta\b([^>]*)>', re.IGNORECASE)
_ATTR = re.compile(r'''([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`/]+))''')
_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)


def find_meta(html, token):
    """Whether the page carries `<meta name="vent-verify" content="<token>">`."""
    for tag in _META.finditer(_COMMENT.sub('', html or '')):
        attrs = {}
        for match in _ATTR.finditer(tag.group(1)):
            value = next(v for v in match.groups()[1:] if v is not None)
            attrs[match.group(1).lower()] = value
        if attrs.get('name', '').strip().lower() == META_NAME \
                and attrs.get('content', '').strip() == token:
            return True
    return False


def check(site, method=None):
    """(True, 'dns' | 'meta') when the site is proven, else (False, code).

    `method` None tries the DNS record, then the meta tag. When neither is
    there the answer is SITE_NOT_PROVEN; a page that could not be checked
    safely or at all says that instead, because it is the thing to fix.
    """
    dns_code = None
    if method in (None, 'dns'):
        try:
            records = txt_records(TXT_NAME % site.host)
        except Unreachable:
            dns_code = 'SITE_UNREACHABLE'
        else:
            if any(r.strip() == TXT_PREFIX + site.token for r in records):
                return True, 'dns'
            dns_code = 'SITE_DNS_MISSING'
        if method == 'dns':
            return False, dns_code
    try:
        html = fetch_home(site.host)
    except Refused as exc:
        return False, exc.code
    except Unreachable:
        return False, 'SITE_UNREACHABLE'
    if find_meta(html, site.token):
        return True, 'meta'
    return False, ('SITE_META_MISSING' if method == 'meta' else 'SITE_NOT_PROVEN')

"""What a site's name may be, and what proving one allows (P8, level 2).

A host ends up inside a Content-Security-Policy header (frame-ancestors) and in
a request the server makes (the meta tag check). So the rule is narrow on
purpose: a public DNS name, lowercase, IDNA encoded, no scheme, path, port,
credentials or IP address, and never one of V-ENT's own names. Anything else is
refused with a code before it is stored.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from django.conf import settings


class BadHost(ValueError):
    """A name that cannot be a site. `code` is what the screen translates."""

    def __init__(self, code='SITE_HOST_INVALID'):
        super().__init__(code)
        self.code = code


_LABEL = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)$')

#: Names that only ever mean somebody's own machine or network, or are
#: reserved for testing (RFC 6761, RFC 8375).
_LOCAL = ('localhost', 'local', 'internal', 'localdomain', 'lan', 'home.arpa',
          'test', 'invalid', 'example')


def _ours():
    hosts = {'v-ent.co'}
    frontend = urlsplit(getattr(settings, 'FRONTEND_URL', '') or '').hostname
    if frontend:
        hosts.add(frontend.lower())
        # app.v-ent.co means v-ent.co and all of it.
        parts = frontend.lower().split('.')
        if len(parts) > 2:
            hosts.add('.'.join(parts[-2:]))
    return hosts


def normalize(raw):
    """The host in `raw` (a name or a pasted address), or BadHost."""
    text = str(raw or '').strip().lower()
    if not text or len(text) > 300 or any(c.isspace() for c in text):
        raise BadHost()
    if '://' in text:
        parts = urlsplit(text)
        if parts.scheme not in ('http', 'https'):
            raise BadHost()
        netloc = parts.netloc
    elif ':' in text.split('/')[0] and not text.startswith('['):
        # "theirsite.com:8443", or a scheme with no slashes ("javascript:").
        raise BadHost()
    else:
        netloc = re.split(r'[/?#]', text, maxsplit=1)[0]
    if not netloc or any(c in netloc for c in '@:[]'):
        raise BadHost()
    host = netloc.rstrip('.')
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        raise BadHost()
    # BadHost is a ValueError, so it is raised outside the try.
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        raise BadHost()
    labels = host.split('.')
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(l) for l in labels):
        raise BadHost()
    tld = labels[-1]
    if not (tld.isalpha() or tld.startswith('xn--')):
        raise BadHost()
    if any(host == name or host.endswith('.' + name) for name in _LOCAL):
        raise BadHost()
    if any(host == name or host.endswith('.' + name) for name in _ours()):
        raise BadHost('SITE_HOST_OURS')
    return host


def ancestors(sites):
    """The frame-ancestors sources for these sites, proven ones only, each once.

    A DNS record at `_vent-verify.<host>` shows control of the name itself, so
    it covers the host and everything under it. A meta tag shows control of one
    page on one host, so it covers that host.
    """
    out = []
    for site in sites:
        if not getattr(site, 'verified_at', None):
            continue
        sources = ['https://%s' % site.host]
        if site.method == 'dns':
            sources.append('https://*.%s' % site.host)
        out.extend(s for s in sources if s not in out)
    return out

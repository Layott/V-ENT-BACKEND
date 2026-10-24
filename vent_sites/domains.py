"""A record on the organiser's own address (P8c, level 3).

The organiser proves a site by DNS (P8a), then points a name under it at V-ENT
with `CNAME <name> -> sites.v-ent.co`. A domain is active only while that CNAME
answers. Nothing here issues a certificate: the certificate tool asks
/sites/tls-ask/ first, and that answers 200 only for an active domain, so the
box can never be made to request a certificate for a name nobody set up.
"""
from django.core.cache import cache
from django.utils import timezone

from . import verify

#: What the CNAME must resolve to. The name every custom address points at.
TARGET = 'sites.v-ent.co'
#: Domains one record may hold.
MOST_DOMAINS = 3
#: How long the frontend's host lookup may be answered from memory.
HOST_CACHE_SECONDS = 60


def host_key(hostname):
    return 'sites-host:%s' % hostname


def covering_site(sites, hostname):
    """The most specific DNS-proven site whose name is `hostname` or sits above it.

    A DNS record proves the name and everything under it; a meta tag proves
    one page, which is not the right to point a name at V-ENT.
    """
    best = None
    for site in sites:
        if site.verified_at is None or site.method != 'dns':
            continue
        if hostname == site.host or hostname.endswith('.' + site.host):
            if best is None or len(site.host) > len(best.host):
                best = site
    return best


def check_cname(hostname):
    """(True, '') when the name's CNAME is the V-ENT target, else (False, code)."""
    try:
        targets = verify.cname_records(hostname)
    except verify.Unreachable:
        return False, 'CUSTOM_DOMAIN_UNREACHABLE'
    if not targets:
        return False, 'CUSTOM_DOMAIN_CNAME_MISSING'
    if TARGET in targets:
        return True, ''
    return False, 'CUSTOM_DOMAIN_CNAME_WRONG'


def run_check(domain):
    """Check the CNAME now and record how it went. Returns the domain."""
    ok, code = check_cname(domain.hostname)
    domain.state = 'active' if ok else 'failed'
    domain.last_code = code
    domain.checked_at = timezone.now()
    domain.save(update_fields=['state', 'last_code', 'checked_at'])
    forget(domain.hostname)
    return domain


def forget(hostname):
    """Drop the remembered host answer, so a change shows at once."""
    cache.delete(host_key(hostname))

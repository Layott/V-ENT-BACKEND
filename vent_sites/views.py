"""Approved sites (P8, level 2): the doors under /sites/.

CEO, inbox 480: "we want people to be able to whitelist v-ent on their site or
put tournament registrations and brackets system under their own website from
v-ent." Design 14c: "The organiser adds a domain and proves it with a DNS TXT
record or a meta tag; V-ENT then sends frame-ancestors for that tournament
listing only approved domains, so nobody else can frame it. Views counted per
domain."

  GET  POST  mine/                     your sites; add one
  POST       mine/<host>/check/        prove it (DNS TXT, then the meta tag)
  DELETE     mine/<host>/              remove it, and every lock it held
  GET        <kind>/<ref>/             a record's locks and views (its managers)
  PUT        <kind>/<ref>/locks/       which proven sites may frame it
  GET        frame/<kind>/<ref>/       who may frame it (public; the frontend
                                       middleware writes it into the header)
  POST       view/                     one view on a site (public; the frame)
  GET  POST  <kind>/<ref>/domains/     a record's own addresses (P8c); add one (Premium)
  POST       <kind>/<ref>/domains/<h>/check/   look for the CNAME now
  DELETE     <kind>/<ref>/domains/<h>/         remove it
  GET        tls-ask/?domain=          200 only for an active address (certificate tool)
  GET        host/?host=               the record behind an active address (middleware)

Identity from the session token on every door (R64); a site is always looked
up as `owner=viewer`, so another person's host answers 404, never theirs
(R58, R88). Refusals carry a code the screen translates (R79).
"""
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import inputs, premium
from vent_auth.actors import actor_from_request
from vent_auth.throttle import limited

from . import domains, hosts, records, verify
from .models import CustomDomain, EmbedLock, EmbedView, Site

#: Sites one person may hold, and sites one record may be locked to. The
#: second also bounds the length of the header every frame carries.
MOST_SITES = 20
MOST_LOCKS = 10
#: How far back the views on the console go.
VIEW_DAYS = 30

_MESSAGES = {
    'SITE_HOST_INVALID': 'That is not a website address we can use. Type the name, like theirsite.com.',
    'SITE_HOST_OURS': 'That is V-ENT itself. Add the address of your own website.',
    'SITE_LIMIT': 'You already have the most sites one account can add. Remove one first.',
    'SITE_NOT_FOUND': 'That site is not one of yours.',
    'SITE_DNS_MISSING': 'The DNS record was not found yet. It can take a while to appear after you add it.',
    'SITE_META_MISSING': 'The meta tag was not found on the home page.',
    'SITE_NOT_PROVEN': 'Neither the DNS record nor the meta tag was found yet.',
    'SITE_UNREACHABLE': 'The site did not answer. Check the address and try again.',
    'SITE_ADDRESS_PRIVATE': 'That name points at a private address, so it cannot be checked.',
    'SITE_REDIRECT_AWAY': 'The home page sends visitors to another site, so the tag cannot be read there.',
    'SITE_NOT_VERIFIED': 'Prove the site first, then lock it.',
    'SITE_NOT_YOURS': 'Only a site you added and proved can be locked here.',
    'SITE_LOCKS_LIMIT': 'One record can be locked to ten sites at most.',
    'SITE_NOT_MANAGER': 'Only the people who run this can choose where it is shown.',
    'SITE_RECORD_NOT_FOUND': 'Not found.',
    'CUSTOM_DOMAIN_NOT_PROVEN': 'Prove the website that owns this name by DNS first.',
    'CUSTOM_DOMAIN_TAKEN': 'That address is already in use.',
    'CUSTOM_DOMAIN_NOT_PREMIUM': 'Your own address is part of Premium.',
    'CUSTOM_DOMAIN_BAD_HOST': 'That is not an address we can use. Type the name, like tournaments.theirsite.com.',
    'CUSTOM_DOMAIN_NOT_YOURS': "That address is not one of this record's.",
    'CUSTOM_DOMAIN_LIMIT': 'One record can have three addresses at most.',
    'CUSTOM_DOMAIN_NOT_FOUND': 'Not found.',
    'CUSTOM_DOMAIN_CNAME_MISSING': 'No CNAME record was found for that address yet. It can take a while to appear.',
    'CUSTOM_DOMAIN_CNAME_WRONG': 'The CNAME record points somewhere else. It must point at %s.' % domains.TARGET,
    'CUSTOM_DOMAIN_UNREACHABLE': 'The address did not answer. Try again in a minute.',
}


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(code, http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'code': code, 'message': _MESSAGES.get(code, 'Refused.'),
                     'data': data or {}}, status=http_status)


def _site_row(site):
    """A site as its owner sees it: what to publish, and how the last check went."""
    return {
        'host': site.host,
        'verified': site.verified_at is not None,
        'method': site.method,
        'verified_at': site.verified_at,
        'checked_at': site.checked_at,
        'last_code': site.last_code,
        'dns': {'name': verify.TXT_NAME % site.host, 'value': verify.TXT_PREFIX + site.token},
        'meta': '<meta name="%s" content="%s">' % (verify.META_NAME, site.token),
        'locks': site.locks.count(),
    }


def _mine(viewer, host):
    """The viewer's own site with this name, else None (R58: owner=viewer)."""
    try:
        name = hosts.normalize(host)
    except hosts.BadHost:
        return None
    return Site.objects.filter(owner=viewer, host=name).first()


@api_view(['GET', 'POST'])
def my_sites(request):
    viewer, err = actor_from_request(request)
    if err:
        return err
    if request.method == 'GET':
        rows = Site.objects.filter(owner=viewer).order_by('host')
        return _ok({'sites': [_site_row(s) for s in rows], 'most': MOST_SITES})

    raw = inputs.read_text(request.data, 'host', max_length=300, required=True)
    try:
        host = hosts.normalize(raw)
    except hosts.BadHost as exc:
        return _err(exc.code)
    existing = Site.objects.filter(owner=viewer, host=host).first()
    if existing is not None:
        return _ok(_site_row(existing), 'You have already added this site.')
    if Site.objects.filter(owner=viewer).count() >= MOST_SITES:
        return _err('SITE_LIMIT')
    try:
        with transaction.atomic():
            site = Site.objects.create(owner=viewer, host=host)
    except IntegrityError:
        # The same name added twice at once: one row, both answers.
        site = Site.objects.get(owner=viewer, host=host)
    return _ok(_site_row(site), 'Site added. Publish the record or the tag, then check it.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
@limited('site_check', 10)
def check_site(request, host):
    viewer, err = actor_from_request(request)
    if err:
        return err
    site = _mine(viewer, host)
    if site is None:
        return _err('SITE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    method = inputs.read_choice(request.data, 'method', ('dns', 'meta'), default=None)
    proven, result = verify.check(site, method)
    site.checked_at = timezone.now()
    if proven:
        site.method = result
        site.verified_at = site.verified_at or site.checked_at
        site.last_code = ''
    else:
        site.last_code = result
    site.save(update_fields=['checked_at', 'method', 'verified_at', 'last_code'])
    if proven:
        return _ok(_site_row(site), 'Site proven.')
    return _err(result, data=_site_row(site))


@api_view(['DELETE'])
def remove_site(request, host):
    viewer, err = actor_from_request(request)
    if err:
        return err
    site = _mine(viewer, host)
    if site is None:
        return _err('SITE_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    released = site.locks.count()
    site.delete()
    return _ok({'host': site.host, 'released': released}, 'Site removed.')


def _record_payload(kind, record, viewer):
    rows = EmbedLock.objects.filter(**records.target(kind, record)).select_related('site').order_by('site__host')
    since = timezone.localdate() - timedelta(days=VIEW_DAYS - 1)
    views = list(EmbedView.objects.filter(**records.target(kind, record), day__gte=since)
                 .values('host').annotate(total=Sum('views')).order_by('-total', 'host')[:50])
    ancestors = records.frame_ancestors(kind, record)
    return {
        'kind': kind,
        'slug': record.slug,
        'anywhere': not ancestors,
        'ancestors': ancestors,
        'locks': [{'host': lock.site.host, 'method': lock.site.method,
                   'verified': lock.site.verified_at is not None,
                   'yours': lock.site.owner_id == viewer.user_id} for lock in rows],
        'views': [{'host': v['host'], 'views': v['total']} for v in views],
        'views_total': sum(v['total'] for v in views),
        'days': VIEW_DAYS,
        'most_locks': MOST_LOCKS,
    }


def _managed(request, kind, ref):
    """(viewer, record, None) for somebody who runs the record, else (None, None, refusal)."""
    viewer, err = actor_from_request(request)
    if err:
        return None, None, err
    record = records.find(kind, ref, public=False) if kind in records.KINDS else None
    if record is None:
        return None, None, _err('SITE_RECORD_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not records.may_manage(viewer, kind, record):
        return None, None, _err('SITE_NOT_MANAGER', status.HTTP_403_FORBIDDEN)
    return viewer, record, None


@api_view(['GET'])
def record_sites(request, kind, ref):
    viewer, record, err = _managed(request, kind, ref)
    if err:
        return err
    return _ok(_record_payload(kind, record, viewer))


@api_view(['PUT'])
def set_locks(request, kind, ref):
    viewer, record, err = _managed(request, kind, ref)
    if err:
        return err
    wanted = request.data.get('hosts')
    if not isinstance(wanted, list) or not all(isinstance(h, str) for h in wanted):
        return inputs.refusal(inputs.BadInput('hosts', 'not a list of sites'))
    if len(wanted) > MOST_LOCKS:
        return _err('SITE_LOCKS_LIMIT')
    names = []
    for raw in wanted:
        try:
            name = hosts.normalize(raw)
        except hosts.BadHost as exc:
            return _err(exc.code)
        if name not in names:
            names.append(name)

    here = records.target(kind, record)
    current = list(EmbedLock.objects.filter(**here).select_related('site'))
    held = {lock.site.host for lock in current}
    # Every new name checked before anything is written: a list that names
    # one site it may not lock changes nothing at all.
    to_add = []
    for name in names:
        if name in held:
            continue
        site = Site.objects.filter(owner=viewer, host=name).first()
        if site is None:
            return _err('SITE_NOT_YOURS')
        if site.verified_at is None:
            return _err('SITE_NOT_VERIFIED')
        to_add.append(site)
    with transaction.atomic():
        for lock in current:
            if lock.site.host not in names:
                lock.delete()
        for site in to_add:
            EmbedLock.objects.get_or_create(site=site, defaults={'added_by': viewer}, **here)
    return _ok(_record_payload(kind, record, viewer), 'Saved.')


@api_view(['GET'])
@limited('embed_frame', 600)
def frame_policy(request, kind, ref):
    record = records.find(kind, ref) if kind in records.KINDS else None
    if record is None:
        return _err('SITE_RECORD_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({'ancestors': records.frame_ancestors(kind, record)})


@api_view(['POST'])
@limited('embed_view', 60)
def count_view(request):
    kind = inputs.read_choice(request.data, 'kind', records.KINDS, required=True)
    ref = inputs.read_text(request.data, 'ref', max_length=200, required=True)
    raw = inputs.read_text(request.data, 'host', max_length=300)
    record = records.find(kind, ref)
    if record is None:
        return _err('SITE_RECORD_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        host = hosts.normalize(raw) if raw else ''
    except hosts.BadHost as exc:
        if exc.code == 'SITE_HOST_OURS':
            # V-ENT showing its own embed (the console preview) is not a view.
            return _ok({'counted': False})
        # A frame that could not see where it is: counted, under "unknown".
        host = ''
    here = records.target(kind, record)
    day = timezone.localdate()
    try:
        with transaction.atomic():
            row, _created = EmbedView.objects.get_or_create(host=host, day=day, **here)
    except IntegrityError:
        # The day's first two views at once: the other one made the row.
        row = EmbedView.objects.get(host=host, day=day, **here)
    EmbedView.objects.filter(pk=row.pk).update(views=F('views') + 1)
    return _ok({'counted': True})


def _domain_row(domain):
    return {
        'hostname': domain.hostname,
        'state': domain.state,
        'site': domain.site.host,
        'checked_at': domain.checked_at,
        'last_code': domain.last_code,
    }


def _domains_payload(kind, record):
    rows = (CustomDomain.objects.filter(**records.target(kind, record))
            .select_related('site').order_by('hostname'))
    return {
        'kind': kind,
        'slug': record.slug,
        'premium': premium.has_premium(record),
        'target': domains.TARGET,
        'most': domains.MOST_DOMAINS,
        'domains': [_domain_row(d) for d in rows],
    }


def _own_domain(kind, record, hostname):
    """The domain with this name on this record, else None (a name that
    belongs to another record answers the same as one nobody added)."""
    try:
        name = hosts.normalize(hostname)
    except hosts.BadHost:
        return None
    return (CustomDomain.objects.filter(hostname=name, **records.target(kind, record))
            .select_related('site').first())


@api_view(['GET', 'POST'])
def record_domains(request, kind, ref):
    viewer, record, err = _managed(request, kind, ref)
    if err:
        return err
    if request.method == 'GET':
        return _ok(_domains_payload(kind, record))

    if not premium.has_premium(record):
        return _err('CUSTOM_DOMAIN_NOT_PREMIUM', status.HTTP_402_PAYMENT_REQUIRED)
    raw = inputs.read_text(request.data, 'hostname', max_length=300, required=True)
    try:
        name = hosts.normalize(raw)
    except hosts.BadHost:
        return _err('CUSTOM_DOMAIN_BAD_HOST')
    here = records.target(kind, record)
    held = CustomDomain.objects.filter(hostname=name).first()
    if held is not None:
        if CustomDomain.objects.filter(pk=held.pk, **here).exists():
            return _ok(_domains_payload(kind, record), 'You have already added this address.')
        return _err('CUSTOM_DOMAIN_TAKEN', status.HTTP_409_CONFLICT)
    site = domains.covering_site(Site.objects.filter(owner=viewer), name)
    if site is None:
        return _err('CUSTOM_DOMAIN_NOT_PROVEN')
    if CustomDomain.objects.filter(**here).count() >= domains.MOST_DOMAINS:
        return _err('CUSTOM_DOMAIN_LIMIT')
    try:
        with transaction.atomic():
            CustomDomain.objects.create(site=site, hostname=name, created_by=viewer, **here)
    except IntegrityError:
        # The same name added twice at once: the other request made the row.
        return _err('CUSTOM_DOMAIN_TAKEN', status.HTTP_409_CONFLICT)
    return _ok(_domains_payload(kind, record),
               'Address added. Point it at V-ENT, then check it.', status.HTTP_201_CREATED)


@api_view(['POST'])
@limited('domain_check', 10)
def check_domain(request, kind, ref, hostname):
    viewer, record, err = _managed(request, kind, ref)
    if err:
        return err
    domain = _own_domain(kind, record, hostname)
    if domain is None:
        return _err('CUSTOM_DOMAIN_NOT_YOURS', status.HTTP_404_NOT_FOUND)
    domains.run_check(domain)
    payload = _domains_payload(kind, record)
    if domain.state == 'active':
        return _ok(payload, 'Address is live.')
    return _err(domain.last_code, data=payload)


@api_view(['DELETE'])
def remove_domain(request, kind, ref, hostname):
    viewer, record, err = _managed(request, kind, ref)
    if err:
        return err
    domain = _own_domain(kind, record, hostname)
    if domain is None:
        return _err('CUSTOM_DOMAIN_NOT_YOURS', status.HTTP_404_NOT_FOUND)
    name = domain.hostname
    domain.delete()
    domains.forget(name)
    return _ok(_domains_payload(kind, record), 'Address removed.')


def _active_domain(raw):
    """The active domain for a typed name, or None."""
    try:
        name = hosts.normalize(raw)
    except hosts.BadHost:
        return None
    return CustomDomain.objects.filter(hostname=name, state='active').first()


@api_view(['GET'])
@limited('tls_ask', 600)
def tls_ask(request):
    """200 only for an active domain: what the certificate tool asks before issuing."""
    if _active_domain(request.query_params.get('domain', '')) is None:
        return _err('CUSTOM_DOMAIN_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({'issue': True})


@api_view(['GET'])
@limited('sites_host', 600)
def host_record(request):
    """The record behind an active custom address, for the frontend middleware."""
    raw = request.query_params.get('host', '')
    try:
        name = hosts.normalize(raw)
    except hosts.BadHost:
        return _err('CUSTOM_DOMAIN_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    key = domains.host_key(name)
    found = cache.get(key)
    if found is None:
        found = False
        domain = _active_domain(name)
        if domain is not None:
            kind = 'tournament' if domain.tournament_id else 'event'
            record = domain.tournament or domain.event
            if record is not None and not (kind == 'tournament' and record.is_draft):
                found = {'kind': kind, 'slug': record.slug}
        cache.set(key, found, domains.HOST_CACHE_SECONDS)
    if not found:
        return _err('CUSTOM_DOMAIN_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok(found)

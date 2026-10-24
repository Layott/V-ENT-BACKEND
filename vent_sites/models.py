"""Approved sites: embeds that work only on the organiser's own domains (P8, level 2).

CEO, inbox 480: "we want people to be able to whitelist v-ent on their site or
put tournament registrations and brackets system under their own website from
v-ent." Design 14c: "The organiser adds a domain and proves it with a DNS TXT
record or a meta tag; V-ENT then sends frame-ancestors for that tournament
listing only approved domains, so nobody else can frame it. Views counted per
domain."

Three rows, one job each:

  Site        a host somebody proved they control, once, for every record
              they run (Search Console's model: proof belongs to a person)
  EmbedLock   a tournament or an event that frames only on these sites; no
              lock at all means framable anywhere, as before
  EmbedView   how often a record was shown on a host, one row per day

Events and tournaments share them because the embed code is shared: one
frame policy, one count, one console panel (one model per thing).
"""
import secrets

from django.db import models
from django.db.models import Q


def new_token():
    """What the organiser publishes. Not a secret (it sits in public DNS), only unguessable."""
    return secrets.token_urlsafe(18)


class Site(models.Model):
    METHODS = (('dns', 'DNS TXT record'), ('meta', 'Meta tag'))

    owner = models.ForeignKey('vent_auth.Users', on_delete=models.CASCADE, related_name='embed_sites')
    #: Lowercase, no scheme, port or path, IDNA encoded (hosts.normalize).
    host = models.CharField(max_length=253)
    token = models.CharField(max_length=40, default=new_token)
    #: How it was proven, once it was: a DNS record covers the host and
    #: everything under it, a meta tag the one host whose page carried it.
    method = models.CharField(max_length=8, choices=METHODS, blank=True, default='')
    verified_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    #: Why the last check failed, as the code the screen translates.
    last_code = models.CharField(max_length=40, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['host']
        constraints = [
            models.UniqueConstraint(fields=['owner', 'host'], name='vent_sites_one_host_per_owner'),
        ]

    def __str__(self):
        return '%s (%s)' % (self.host, 'proven' if self.verified_at else 'not proven')


def _one_target():
    return (Q(tournament__isnull=False, event__isnull=True)
            | Q(tournament__isnull=True, event__isnull=False))


class EmbedLock(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='locks')
    tournament = models.ForeignKey('vent_tournament.Tournament', on_delete=models.CASCADE,
                                   null=True, blank=True, related_name='embed_locks')
    event = models.ForeignKey('vent_event.Event', on_delete=models.CASCADE,
                              null=True, blank=True, related_name='embed_locks')
    added_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=_one_target(), name='vent_sites_lock_one_target'),
            models.UniqueConstraint(fields=['site', 'tournament'], name='vent_sites_lock_once_tournament'),
            models.UniqueConstraint(fields=['site', 'event'], name='vent_sites_lock_once_event'),
        ]


class CustomDomain(models.Model):
    """A record served on the organiser's own address (P8c, level 3).

    `hostname` is a name under a site the organiser proved by DNS (a meta tag
    proves a page, not the right to point a name at V-ENT). It is active once
    its CNAME answers with the V-ENT target; the certificate tool asks
    /sites/tls-ask/ before issuing for it, and the frontend middleware asks
    /sites/host/ to find the record behind it.
    """
    STATES = (('pending', 'Pending'), ('active', 'Active'), ('failed', 'Failed'))

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='domains')
    tournament = models.ForeignKey('vent_tournament.Tournament', on_delete=models.CASCADE,
                                   null=True, blank=True, related_name='custom_domains')
    event = models.ForeignKey('vent_event.Event', on_delete=models.CASCADE,
                              null=True, blank=True, related_name='custom_domains')
    #: Lowercase, IDNA encoded (hosts.normalize); one record per name, anywhere.
    hostname = models.CharField(max_length=253, unique=True)
    state = models.CharField(max_length=8, choices=STATES, default='pending')
    checked_at = models.DateTimeField(null=True, blank=True)
    #: Why the last check failed, as the code the screen translates.
    last_code = models.CharField(max_length=40, blank=True, default='')
    created_by = models.ForeignKey('vent_auth.Users', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['hostname']
        constraints = [
            models.CheckConstraint(check=_one_target(), name='vent_sites_domain_one_target'),
        ]

    def __str__(self):
        return '%s (%s)' % (self.hostname, self.state)


class EmbedView(models.Model):
    tournament = models.ForeignKey('vent_tournament.Tournament', on_delete=models.CASCADE,
                                   null=True, blank=True, related_name='embed_views')
    event = models.ForeignKey('vent_event.Event', on_delete=models.CASCADE,
                              null=True, blank=True, related_name='embed_views')
    #: '' when the frame could not see its host (a page sending no referrer).
    host = models.CharField(max_length=253, blank=True, default='')
    day = models.DateField()
    views = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(check=_one_target(), name='vent_sites_view_one_target'),
            models.UniqueConstraint(fields=['tournament', 'host', 'day'], name='vent_sites_view_tournament_day'),
            models.UniqueConstraint(fields=['event', 'host', 'day'], name='vent_sites_view_event_day'),
        ]
        indexes = [models.Index(fields=['day'], name='vent_sites_view_day')]

"""A Discord channel a tournament or an event announces into.

CEO, 7 September 2026, asked for both delivery routes. This is the broadcast
one: a webhook URL points at one channel in somebody's Discord server, and
V-ENT posts there when something happens that the people in that channel care
about.

## Why a webhook rather than the bot

A webhook needs no bot, no gateway intents, no install and no permissions
review. The URL **is** the permission: whoever holds it can post into that
channel, which is why it is treated as a secret everywhere below and is never
returned to a browser in full.

That also means the organiser grants access without V-ENT asking for anything
in their server, which is the difference between "paste this URL" and "approve
this application".

## One model for both, deliberately

A tournament and an event are the two things V-ENT runs, and building this for
one of them is the fault with its own hard rule. Rather than two tables that
drift, there is one with two nullable owners and a constraint that exactly one
is set.
"""
from django.core.exceptions import ValidationError
from django.db import models

from .models import Users


class DiscordWebhook(models.Model):
    """One channel, belonging to one tournament or one event."""

    #: What V-ENT will post. An organiser choosing none of them has a webhook
    #: that never fires, which is a legitimate way to pause it without
    #: deleting the URL and having to fetch it again.
    EVENT_CHOICES = [
        ('starting', 'Starting soon'),
        ('bracket', 'Bracket updated'),
        ('tickets', 'Tickets live'),
        ('result', 'Result recorded'),
        ('announcement', 'Organiser announcement'),
    ]
    DEFAULT_EVENTS = ['starting', 'bracket', 'tickets', 'announcement']

    id = models.AutoField(primary_key=True)

    tournament = models.ForeignKey(
        'vent_tournament.Tournament', on_delete=models.CASCADE,
        null=True, blank=True, related_name='discord_webhooks')
    event = models.ForeignKey(
        'vent_event.Event', on_delete=models.CASCADE,
        null=True, blank=True, related_name='discord_webhooks')

    #: The secret. Long because Discord's URLs are, and never rendered: the
    #: API returns `hint` instead, which is the channel name plus the last few
    #: characters, enough to tell two apart and useless to anybody who steals
    #: the response.
    url = models.CharField(max_length=300)

    #: What the organiser called it, or what Discord reported. For telling two
    #: webhooks apart on a screen without showing either URL.
    label = models.CharField(max_length=80, blank=True, default='')

    events = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    added_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='discord_webhooks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: The last outcome, so a dead webhook says so on the screen instead of
    #: failing in silence for a month. Discord answers 404 for a webhook
    #: somebody deleted, and that one will never work again.
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        indexes = [models.Index(fields=['tournament', 'active']),
                   models.Index(fields=['event', 'active'])]

    def clean(self):
        if bool(self.tournament_id) == bool(self.event_id):
            raise ValidationError(
                'A webhook belongs to exactly one tournament or one event.')

    def save(self, *args, **kwargs):
        # `auto_now` is skipped when save() is called with update_fields that
        # do not name it, which silently freezes updated_at. Same override as
        # Ticket and BroadcastSlot carry, for the same reason.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            fields = set(update_fields)
            fields.add('updated_at')
            kwargs['update_fields'] = tuple(fields)
        return super().save(*args, **kwargs)

    @property
    def hint(self):
        """What a screen may show. Never the URL itself."""
        tail = (self.url or '')[-4:]
        return f'{self.label or "Discord channel"} ...{tail}' if tail else ''

    def wants(self, kind):
        return self.active and kind in (self.events or self.DEFAULT_EVENTS)

    def __str__(self):
        owner = f'tournament {self.tournament_id}' if self.tournament_id \
            else f'event {self.event_id}'
        return f'discord webhook for {owner}'

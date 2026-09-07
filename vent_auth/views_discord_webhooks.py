"""Managing the Discord channels a tournament or an event announces into.

CEO, 7 September 2026, on webhook announcements: "Simplest by a distance, and
it covers tournament starting, bracket updated, tickets live."

Those three are the shipped triggers, plus results and organiser
announcements, and `announce()` at the bottom is the single call every one of
them makes.

## The secret

A webhook URL is not a name, it is a capability: anybody holding it can post
into that channel as V-ENT. So it goes in and never comes back out. The list
endpoint returns `hint` - a label and the last four characters - which is
enough to tell two apart on a screen and useless to anybody who intercepts the
response.

The same reasoning is why replacing a webhook is a fresh POST rather than a
PATCH of the URL: there is no case where somebody needs to edit part of a
secret they cannot see.

## One route shape, two owners

`/auth/discord/webhooks/tournament/<ref>/` and
`/auth/discord/webhooks/event/<ref>/`, same view, same payload, same
permission question asked of a different object. Building it for tournaments
and leaving events for later is the fault with its own rule.
"""
import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from . import discord
from .models_discord import DiscordWebhook
from .views_profile import _user_from_bearer

logger = logging.getLogger(__name__)


def _owner(kind, ref):
    """The tournament or the event, by slug or by id, and who may run it."""
    if kind == 'tournament':
        from vent_tournament.access import may_manage
        from vent_tournament.models import Tournament
        obj = (Tournament.objects.filter(tournament_id=int(ref)).first()
               if str(ref).isdigit()
               else Tournament.objects.filter(slug=str(ref)).first())
        return obj, may_manage
    if kind == 'event':
        from vent_event.models import Event
        from vent_event.permissions import may_run_event
        obj = (Event.objects.filter(event_id=int(ref)).first()
               if str(ref).isdigit()
               else Event.objects.filter(slug=str(ref)).first())
        return obj, may_run_event
    return None, None


def _row(hook):
    """What a screen may see. Never the URL."""
    return {
        'id': hook.id,
        'hint': hook.hint,
        'label': hook.label,
        'events': hook.events or DiscordWebhook.DEFAULT_EVENTS,
        'active': hook.active,
        'last_ok_at': hook.last_ok_at,
        'last_error': hook.last_error,
        'added_by': hook.added_by.username if hook.added_by_id else '',
        'created_at': hook.created_at,
    }


def _guard(request, kind, ref):
    user, err = _user_from_bearer(request)
    if err:
        return None, None, err

    obj, may = _owner(kind, ref)
    if obj is None:
        return None, None, Response(
            {'status': 'error', 'code': 'NOT_FOUND',
             'message': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if not may(user, obj):
        return None, None, Response(
            {'status': 'error', 'code': 'NOT_ORGANIZER',
             'message': 'Only the organiser can manage Discord announcements.'},
            status=status.HTTP_403_FORBIDDEN)
    return user, obj, None


@api_view(['GET', 'POST'])
def webhooks(request, kind, ref):
    """List the channels, or add one."""
    user, obj, err = _guard(request, kind, ref)
    if err:
        return err

    filt = {'tournament': obj} if kind == 'tournament' else {'event': obj}

    if request.method == 'GET':
        rows = DiscordWebhook.objects.filter(**filt).order_by('id')
        return Response({
            'status': 'success',
            'data': {'webhooks': [_row(h) for h in rows],
                     'choices': DiscordWebhook.EVENT_CHOICES},
            'message': 'Discord channels.',
        })

    url = str(request.data.get('url') or '').strip()
    if not url.startswith('https://discord.com/api/webhooks/') \
            and not url.startswith('https://discordapp.com/api/webhooks/'):
        return Response(
            {'status': 'error', 'code': 'VALIDATION_ERROR', 'field': 'url',
             'message': 'That is not a Discord webhook URL. In Discord: '
                        'Server Settings, Integrations, Webhooks, New Webhook, '
                        'then Copy Webhook URL.'},
            status=status.HTTP_400_BAD_REQUEST)

    wanted = request.data.get('events')
    if not isinstance(wanted, list):
        wanted = DiscordWebhook.DEFAULT_EVENTS
    valid = {c[0] for c in DiscordWebhook.EVENT_CHOICES}
    wanted = [w for w in wanted if w in valid]

    hook = DiscordWebhook(
        url=url,
        label=str(request.data.get('label') or '')[:80],
        events=wanted,
        added_by=user,
        **filt,
    )
    hook.save()

    # Post once, straight away, and BLOCKING. An organiser who pastes a URL
    # should find out now whether it works, not discover at the start of a
    # tournament that it never did. This is the only blocking send in the
    # system, and it is worth the second it costs.
    name = getattr(obj, 'tournament_title', None) or getattr(obj, 'name', '')
    ok, error = discord.post_webhook(
        url,
        embed=discord.embed(
            'Connected to V-ENT',
            f'This channel will now get announcements about {name}.',
        ),
        blocking=True,
    )
    if ok:
        hook.last_ok_at = timezone.now()
        hook.last_error = ''
    else:
        hook.last_error = error
    hook.save(update_fields=['last_ok_at', 'last_error'])

    return Response({
        'status': 'success',
        'data': {'webhook': _row(hook), 'delivered': ok},
        'message': ('Connected. A test message was posted in that channel.'
                    if ok else
                    'Saved, but the test message did not arrive: ' + error),
    }, status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def webhook_detail(request, kind, ref, hook_id):
    """Change which announcements go there, pause it, or remove it."""
    user, obj, err = _guard(request, kind, ref)
    if err:
        return err

    filt = {'tournament': obj} if kind == 'tournament' else {'event': obj}
    hook = DiscordWebhook.objects.filter(id=hook_id, **filt).first()
    if hook is None:
        return Response({'status': 'error', 'code': 'NOT_FOUND',
                         'message': 'Not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        hook.delete()
        return Response({'status': 'success', 'data': {},
                         'message': 'Removed.'})

    fields = []
    if 'active' in request.data:
        hook.active = bool(request.data.get('active'))
        fields.append('active')
    if 'label' in request.data:
        hook.label = str(request.data.get('label') or '')[:80]
        fields.append('label')
    if isinstance(request.data.get('events'), list):
        valid = {c[0] for c in DiscordWebhook.EVENT_CHOICES}
        hook.events = [w for w in request.data['events'] if w in valid]
        fields.append('events')
    if fields:
        hook.save(update_fields=fields)

    return Response({'status': 'success', 'data': {'webhook': _row(hook)},
                     'message': 'Saved.'})


# ---------------------------------------------------------------- the one call

def announce(obj, kind, title, description='', path='', fields=None):
    """Tell every Discord channel watching this thing.

    `kind` is one of DiscordWebhook.EVENT_CHOICES. Every trigger in the
    platform calls THIS rather than posting itself, so a new trigger cannot
    forget the active check, the per-channel event filter, or recording why a
    delivery failed.

    Never raises. The transport is off-thread, so this returns immediately even
    when Discord is unreachable.
    """
    try:
        is_tournament = hasattr(obj, 'tournament_id') and not hasattr(obj, 'event_id')
        filt = ({'tournament': obj} if is_tournament else {'event': obj})
        rows = DiscordWebhook.objects.filter(active=True, **filt)

        url = f'{FRONTEND_URL}{path}' if path else ''
        card = discord.embed(title, description, url, fields)

        for hook in rows:
            if not hook.wants(kind):
                continue
            discord.post_webhook(hook.url, embed=card)
    except Exception:                                           # noqa: BLE001
        logger.exception('discord announce failed (%s)', kind)

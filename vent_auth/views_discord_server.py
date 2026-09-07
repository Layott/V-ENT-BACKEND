"""An organisation driving the V-ENT bot in its own Discord server.

CEO, 7 September 2026, on how much power the bot should ask for:
**"let each organiser grant only the parts they want."**

## The two checks, on every action

1. **Did the organisation grant it?** `DiscordServer.granted` is what they
   chose, and it decides what this API offers.
2. **Does Discord still allow it?** Read live, because a server owner can
   change the bot's role an hour later and our record would still say yes.

`DiscordServer.may()` asks both and returns the sentence to show when either
says no. Nothing here acts without going through it.

## Why the invite URL is built per organisation

The permissions integer in the bot invite is computed from the capabilities
that organisation ticked, so an organiser who wants only announcements
authorises a bot that **literally cannot delete a message in their server**.
The restriction is enforced by Discord, not by V-ENT remembering to check.

That is the whole difference between granular and all-or-nothing: with one
invite for everybody, "I only wanted announcements" would be a preference we
promise to honour rather than a permission they never gave.

## Everything is recorded

Roles given, channels made, messages deleted: all of it happens in a community
that is not ours, at the instruction of somebody who is not its owner. Every
call writes a `DiscordAction`, including the ones that FAIL, because a run of
refusals is the shape of somebody testing what they can get away with.
"""
import logging

from django.core import signing
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent.settings import FRONTEND_URL
from . import discord, org_link
from .discord_server import CAPABILITIES, catalogue, normalise, permission_bits
from .models import Organization
from .models_discord import DiscordAction, DiscordServer
from .views_linking import API_BASE, _discord_credentials
from .views_profile import _user_from_bearer

logger = logging.getLogger(__name__)

STATE_SALT = 'vent.discord-guild-install'
STATE_MAX_AGE = 60 * 15


def _org_and_role(request, ref):
    """The organisation, and whether this person may speak for it."""
    user, err = _user_from_bearer(request)
    if err:
        return None, None, err

    org = (Organization.objects.filter(org_id=int(ref)).first()
           if str(ref).isdigit()
           else Organization.objects.filter(slug=str(ref)).first())
    if org is None:
        return None, None, Response(
            {'status': 'error', 'code': 'NOT_FOUND',
             'message': 'Organisation not found.'},
            status=status.HTTP_404_NOT_FOUND)

    if org_link.role_of(org, user) not in org_link.MAY_LINK:
        return None, None, Response(
            {'status': 'error', 'code': 'NOT_ORGANIZER',
             'message': 'Only an owner, admin or manager can connect a '
                        'Discord server.'},
            status=status.HTTP_403_FORBIDDEN)
    return user, org, None


def _row(server, live=None):
    """One server, with what it may do and what it actually can."""
    granted = normalise(server.granted)
    permissions = server.live_permissions if live is None else live
    can = {}
    for name in CAPABILITIES:
        ok, why = server.may(name) if live is None else (None, '')
        if live is not None:
            from .discord_server import missing_for
            ok = name in granted and not missing_for(name, permissions)
            why = ''
        can[name] = bool(ok)
    return {
        'id': server.id,
        'guild_id': server.guild_id,
        'guild_name': server.guild_name,
        'granted': granted,
        'can': can,
        'active': server.active,
        'checked_at': server.checked_at,
        'last_error': server.last_error,
        'connected_by': (server.connected_by.username
                         if server.connected_by_id else ''),
        'connected_at': server.connected_at,
    }


@api_view(['GET'])
def install_url(request, ref):
    """GET /auth/discord/guild/<org>/install/?caps=announce,roles

    The bot invite for THIS organisation, carrying exactly the permissions it
    is granting and no more.
    """
    user, org, err = _org_and_role(request, ref)
    if err:
        return err

    client_id, secret = _discord_credentials()
    if not (client_id and secret):
        return Response({'status': 'error', 'code': 'DISCORD_NOT_SET',
                         'configured': False,
                         'message': 'Discord is not set up on this server yet.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)

    raw = str(request.query_params.get('caps') or '')
    wanted = normalise([c.strip() for c in raw.split(',') if c.strip()])
    bits = permission_bits(wanted)

    # The state carries which organisation asked and what it granted, signed,
    # so neither can be swapped on the way back from Discord.
    state = signing.dumps({'org': org.org_id, 'caps': wanted},
                          salt=STATE_SALT)

    from urllib.parse import urlencode
    params = {
        'client_id': client_id,
        'scope': 'bot applications.commands',
        'permissions': bits,
        'redirect_uri': f'{API_BASE}/auth/discord/guild/callback/',
        'response_type': 'code',
        'state': state,
    }
    return Response({
        'status': 'success',
        'data': {
            'url': f'https://discord.com/oauth2/authorize?{urlencode(params)}',
            'permissions': bits,
            'granting': wanted,
        },
        'message': 'Continue at Discord.',
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def install_callback(request):
    """Where Discord sends the browser back after the bot is added."""
    guild_id = request.query_params.get('guild_id')
    try:
        payload = signing.loads(request.query_params.get('state') or '',
                                salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.BadSignature:
        return redirect(f'{FRONTEND_URL}/organizations?discord=failed')

    org = Organization.objects.filter(org_id=payload.get('org')).first()
    if org is None or not guild_id:
        return redirect(f'{FRONTEND_URL}/organizations?discord=failed')

    # Somebody else's organisation may already have this server. Refused
    # rather than reassigned: two organisations both able to delete messages
    # in one server is a question with no good answer.
    taken = DiscordServer.objects.filter(guild_id=str(guild_id)).exclude(
        org=org).exists()
    if taken:
        return redirect(f'{FRONTEND_URL}/organizations/{org.slug or org.org_id}'
                        f'?discord=taken')

    ok, info = discord.guild(guild_id)
    live, _err = discord.bot_permissions(guild_id)

    server, _made = DiscordServer.objects.update_or_create(
        guild_id=str(guild_id),
        defaults={
            'org': org,
            'guild_name': (info or {}).get('name', '') if ok else '',
            'granted': normalise(payload.get('caps')),
            'live_permissions': live,
            'checked_at': timezone.now(),
            'last_error': '' if ok else str(info)[:200],
            'active': True,
        },
    )
    return redirect(f'{FRONTEND_URL}/organizations/'
                    f'{org.slug or org.org_id}?discord=connected'
                    f'&server={server.id}')


@api_view(['GET'])
def servers(request, ref):
    """The organisation's connected servers, with permissions read live."""
    user, org, err = _org_and_role(request, ref)
    if err:
        return err

    rows = []
    for server in DiscordServer.objects.filter(org=org).order_by('id'):
        live, problem = discord.bot_permissions(server.guild_id)
        server.live_permissions = live
        server.checked_at = timezone.now()
        server.last_error = problem or ''
        server.save(update_fields=['live_permissions', 'checked_at',
                                   'last_error'])
        rows.append(_row(server, live=live))

    return Response({
        'status': 'success',
        'data': {'servers': rows, 'capabilities': catalogue()},
        'message': 'Discord servers.',
    })


@api_view(['PATCH', 'DELETE'])
def server_detail(request, ref, server_id):
    """Change what is granted, or disconnect."""
    user, org, err = _org_and_role(request, ref)
    if err:
        return err

    server = DiscordServer.objects.filter(id=server_id, org=org).first()
    if server is None:
        return Response({'status': 'error', 'code': 'NOT_FOUND',
                         'message': 'Not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        # The row goes, and the organiser is told the other half: removing our
        # record does not remove the bot from their server, and pretending
        # otherwise would leave a bot sitting in there that we no longer list.
        server.delete()
        return Response({
            'status': 'success', 'data': {},
            'message': 'Disconnected here. Remove the V-ENT bot in your '
                       'Discord server settings to revoke it completely.',
        })

    if isinstance(request.data.get('granted'), list):
        server.granted = normalise(request.data['granted'])
        server.save(update_fields=['granted'])

    # Widening a grant needs a fresh authorisation, because the permissions
    # live on the bot's role in Discord and cannot be raised from here.
    needed = permission_bits(server.granted)
    live = server.live_permissions or 0
    short = (live & needed) != needed and not (live & (1 << 3))

    return Response({
        'status': 'success',
        'data': {'server': _row(server), 'needs_reauthorise': short},
        'message': ('Saved. Add the bot again to give it the new permissions '
                    'in Discord.' if short else 'Saved.'),
    })


# ------------------------------------------------------------------- actions

def _acting(request, ref, server_id, capability):
    """Everything an action needs, or the response to refuse with."""
    user, org, err = _org_and_role(request, ref)
    if err:
        return None, None, err

    server = DiscordServer.objects.filter(id=server_id, org=org,
                                          active=True).first()
    if server is None:
        return None, None, Response(
            {'status': 'error', 'code': 'NOT_FOUND',
             'message': 'That Discord server is not connected.'},
            status=status.HTTP_404_NOT_FOUND)

    allowed, why = server.may(capability)
    if not allowed:
        DiscordAction.objects.create(server=server, actor=user,
                                     kind=capability, ok=False, error=why[:200])
        return None, None, Response(
            {'status': 'error', 'code': 'NOT_GRANTED', 'message': why},
            status=status.HTTP_403_FORBIDDEN)
    return user, server, None


def _record(server, user, kind, ok, error='', target='', detail=None):
    DiscordAction.objects.create(
        server=server, actor=user, kind=kind, target=str(target)[:120],
        detail=detail or {}, ok=bool(ok), error=str(error)[:200])


@api_view(['GET'])
def server_targets(request, ref, server_id):
    """The channels and roles this server has, so a screen can offer them."""
    user, server, err = _acting(request, ref, server_id, 'announce')
    if err:
        return err

    ok_c, channels = discord.guild_channels(server.guild_id)
    ok_r, roles = discord.guild_roles(server.guild_id)

    return Response({
        'status': 'success',
        'data': {
            'channels': [
                {'id': c['id'], 'name': c.get('name', ''),
                 'category': c.get('type') == 4}
                for c in (channels if ok_c else [])
                if c.get('type') in (0, 4)
            ],
            # @everyone is excluded: it is every member of the server, and
            # offering it in a picker beside three real roles is how somebody
            # pings a whole community by mis-clicking.
            'roles': [
                {'id': r['id'], 'name': r.get('name', '')}
                for r in (roles if ok_r else [])
                if r.get('id') != server.guild_id and not r.get('managed')
            ],
        },
        'message': 'Channels and roles.',
    })


@api_view(['POST'])
def server_post(request, ref, server_id):
    """Say something in a channel, optionally tagging people or roles."""
    user, server, err = _acting(request, ref, server_id, 'announce')
    if err:
        return err

    channel = str(request.data.get('channel_id') or '')
    text = str(request.data.get('content') or '').strip()
    if not channel or not text:
        return Response({'status': 'error', 'code': 'VALIDATION_ERROR',
                         'message': 'Pick a channel and write something.'},
                        status=status.HTTP_400_BAD_REQUEST)

    roles = request.data.get('mention_roles') or []
    users = request.data.get('mention_users') or []
    if (roles or users):
        # Tagging is its own grant. An organisation that only wanted the bot
        # to post quietly did not agree to it notifying everybody.
        allowed, why = server.may('mention')
        if not allowed:
            _record(server, user, 'post', False, why, channel)
            return Response({'status': 'error', 'code': 'NOT_GRANTED',
                             'message': why},
                            status=status.HTTP_403_FORBIDDEN)
        text = ' '.join([f'<@&{r}>' for r in roles[:20]]
                        + [f'<@{u}>' for u in users[:20]]) + '\n' + text

    ok, result = discord.post_message(
        channel, content=text,
        mentions={'roles': roles, 'users': users})
    _record(server, user, 'post', ok, '' if ok else result, channel,
            {'mentioned_roles': len(roles), 'mentioned_users': len(users)})

    if not ok:
        return Response({'status': 'error', 'code': 'DISCORD_REFUSED',
                         'message': result},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'status': 'success', 'data': {}, 'message': 'Posted.'})


@api_view(['POST'])
def server_role(request, ref, server_id):
    """Give somebody a role, or take it back."""
    user, server, err = _acting(request, ref, server_id, 'roles')
    if err:
        return err

    member = str(request.data.get('discord_user_id') or '')
    role = str(request.data.get('role_id') or '')
    remove = bool(request.data.get('remove'))
    if not member or not role:
        return Response({'status': 'error', 'code': 'VALIDATION_ERROR',
                         'message': 'Pick a person and a role.'},
                        status=status.HTTP_400_BAD_REQUEST)

    fn = discord.remove_role if remove else discord.add_role
    ok, result = fn(server.guild_id, member, role)
    kind = 'role_remove' if remove else 'role_add'
    _record(server, user, kind, ok, '' if ok else result, member,
            {'role_id': role})

    if not ok:
        return Response({'status': 'error', 'code': 'DISCORD_REFUSED',
                         'message': result},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'status': 'success', 'data': {},
                     'message': 'Role removed.' if remove else 'Role given.'})


@api_view(['POST'])
def server_channel(request, ref, server_id):
    """Create a channel, or a category to hold channels."""
    user, server, err = _acting(request, ref, server_id, 'channels')
    if err:
        return err

    name = str(request.data.get('name') or '').strip()
    kind = 'category' if request.data.get('kind') == 'category' else 'text'
    if not name:
        return Response({'status': 'error', 'code': 'VALIDATION_ERROR',
                         'message': 'Give it a name.'},
                        status=status.HTTP_400_BAD_REQUEST)

    ok, result = discord.create_channel(
        server.guild_id, name, kind,
        parent_id=request.data.get('parent_id'))
    _record(server, user, 'channel_create', ok, '' if ok else result,
            (result or {}).get('id', '') if ok else '', {'name': name,
                                                         'kind': kind})

    if not ok:
        return Response({'status': 'error', 'code': 'DISCORD_REFUSED',
                         'message': result},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'status': 'success',
                     'data': {'channel': {'id': result.get('id'),
                                          'name': result.get('name')}},
                     'message': 'Created.'})


@api_view(['POST'])
def server_purge(request, ref, server_id):
    """Delete recent messages, optionally only one person's.

    The strongest thing here, and the one most worth being careful about: it
    removes other people's words from a community that is not ours. So it is
    its own grant, it is capped by Discord at 100 and at two weeks, and the
    reply reports how many were ACTUALLY removed rather than how many were
    asked for.
    """
    user, server, err = _acting(request, ref, server_id, 'moderate')
    if err:
        return err

    channel = str(request.data.get('channel_id') or '')
    try:
        limit = int(request.data.get('limit') or 0)
    except (TypeError, ValueError):
        limit = 0
    if not channel or limit < 1:
        return Response({'status': 'error', 'code': 'VALIDATION_ERROR',
                         'message': 'Pick a channel and how many to remove.'},
                        status=status.HTTP_400_BAD_REQUEST)

    from_user = str(request.data.get('from_discord_user_id') or '') or None

    ok, result = discord.purge_messages(channel, limit, from_user)
    _record(server, user, 'purge', ok, '' if ok else result, channel,
            {'asked': limit, 'from': from_user or 'anybody',
             'deleted': (result or {}).get('deleted') if ok else 0})

    if not ok:
        return Response({'status': 'error', 'code': 'DISCORD_REFUSED',
                         'message': result},
                        status=status.HTTP_400_BAD_REQUEST)

    deleted = result.get('deleted', 0)
    return Response({
        'status': 'success',
        'data': result,
        'message': (result.get('note') or
                    ('Removed %d message%s.' % (deleted,
                                                '' if deleted == 1 else 's'))),
    })


@api_view(['GET'])
def server_log(request, ref, server_id):
    """Everything V-ENT has done in this server, newest first."""
    user, org, err = _org_and_role(request, ref)
    if err:
        return err

    server = DiscordServer.objects.filter(id=server_id, org=org).first()
    if server is None:
        return Response({'status': 'error', 'code': 'NOT_FOUND',
                         'message': 'Not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    rows = (DiscordAction.objects.filter(server=server)
            .select_related('actor')[:100])
    return Response({
        'status': 'success',
        'data': {'actions': [
            {'kind': a.kind, 'target': a.target, 'detail': a.detail,
             'ok': a.ok, 'error': a.error, 'at': a.at,
             'actor': a.actor.username if a.actor_id else ''}
            for a in rows
        ]},
        'message': 'What V-ENT has done in this server.',
    })

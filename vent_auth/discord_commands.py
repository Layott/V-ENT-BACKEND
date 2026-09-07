"""Slash commands, answered over HTTP rather than a gateway connection.

CEO, 7 September 2026: "also let the bot have commands like / commands it can
use inisde the server."

## Why HTTP interactions and not a gateway bot

The usual way to run a Discord bot is a persistent WebSocket to Discord's
gateway, which means a process that runs for ever and reconnects. V-ENT runs
gunicorn behind nginx with no worker of its own, and adding a long-lived
daemon means adding something to supervise, restart and deploy.

Discord's other option fits what already exists: set an **Interactions Endpoint
URL** and Discord POSTs each command to it like a webhook. No connection to
hold open, no reconnect logic, nothing new to keep alive, and it scales with
the web tier because it IS the web tier.

The cost is a three second budget: Discord wants a reply within three seconds
or it shows the person an error. Everything here answers from our own database,
so that is comfortable, and anything slower would defer instead.

## The signature is the whole security model

This endpoint is public and unauthenticated, because Discord calls it. What
stops anybody else calling it is that **every request is signed**, with
Ed25519, using the application's public key. An unsigned or badly signed
request is rejected with 401 before anything is parsed.

Discord actively tests this: it sends deliberately invalid signatures when you
save the endpoint URL, and refuses to accept the URL unless they are rejected.
So getting this wrong fails loudly at setup rather than quietly in production.

Verified with `cryptography`, which is already a dependency, rather than
adding PyNaCl for one function.
"""
import json
import logging
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

#: Discord's interaction types.
PING = 1
APPLICATION_COMMAND = 2

#: And the response types we use.
PONG = 1
CHANNEL_MESSAGE = 4

#: 64 means EPHEMERAL: only the person who typed the command sees the reply.
#: The default for everything here, because a slash command answer is a
#: conversation with one person and filling somebody's channel with bot replies
#: is how a bot gets removed.
EPHEMERAL = 64


#: The application's public key, from the Discord developer portal. It is
#: PUBLIC by design: it verifies Discord's signature and cannot sign anything,
#: so unlike the client secret and the bot token it is safe as a default here
#: and safe in the repository.
DEFAULT_PUBLIC_KEY = '3dedac2e6ef705fb9875314d85f3efa0b6b0df55f07b85b93012008c4d94f1f3'


def public_key():
    return os.environ.get('DISCORD_PUBLIC_KEY', DEFAULT_PUBLIC_KEY)


def verify(signature, timestamp, body):
    """Did Discord really send this?

    `signature` and `timestamp` come from the X-Signature-Ed25519 and
    X-Signature-Timestamp headers, and the signed message is timestamp + body
    exactly as received. Any change to the body invalidates it, which is why
    the RAW body is used rather than a re-serialised dict.
    """
    key = public_key()
    if not (key and signature and timestamp):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
            bytes.fromhex(signature), timestamp.encode() + body)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    except Exception:                                           # noqa: BLE001
        logger.exception('discord signature check blew up')
        return False


def reply(content, ephemeral=True):
    """A plain answer."""
    data = {'content': content[:2000]}
    if ephemeral:
        data['flags'] = EPHEMERAL
    return {'type': CHANNEL_MESSAGE, 'data': data}


def reply_embed(embed, ephemeral=True):
    data = {'embeds': [embed]}
    if ephemeral:
        data['flags'] = EPHEMERAL
    return {'type': CHANNEL_MESSAGE, 'data': data}


# --------------------------------------------------------------- the commands
#
# Deliberately all READ-ONLY. A slash command is typed by anybody in a server,
# and V-ENT cannot tell from the interaction alone whether that person is an
# organiser. Anything that changes something belongs on the V-ENT console where
# the permission question has a real answer, which is the same reasoning that
# put the destructive controls behind an organisation role.

COMMANDS = [
    {
        'name': 'vent',
        'description': 'Look something up on V-ENT',
        'options': [
            {
                'type': 1,                      # SUB_COMMAND
                'name': 'tournament',
                'description': 'Find a tournament and see where it is up to',
                'options': [{
                    'type': 3,                  # STRING
                    'name': 'name',
                    'description': 'Part of the tournament name',
                    'required': True,
                }],
            },
            {
                'type': 1,
                'name': 'event',
                'description': 'Find an event and see whether tickets are open',
                'options': [{
                    'type': 3,
                    'name': 'name',
                    'description': 'Part of the event name',
                    'required': True,
                }],
            },
            {
                'type': 1,
                'name': 'me',
                'description': 'What V-ENT knows about your linked account',
            },
            {
                'type': 1,
                'name': 'link',
                'description': 'How to connect this Discord to your V-ENT account',
            },
        ],
    },
]


def _sub(data):
    """The subcommand and its arguments, flattened."""
    options = data.get('options') or []
    if options and options[0].get('type') == 1:
        sub = options[0]
        args = {o['name']: o.get('value')
                for o in (sub.get('options') or [])}
        return sub.get('name', ''), args
    return data.get('name', ''), {o['name']: o.get('value') for o in options}


def _who(interaction):
    """The Discord user id of whoever typed it.

    In a server it arrives under `member`; in a DM it arrives under `user`.
    Reading only one of the two is a bug that shows up the first time somebody
    uses the command outside a server.
    """
    member = interaction.get('member') or {}
    user = member.get('user') or interaction.get('user') or {}
    return str(user.get('id') or '')


def handle(interaction):
    """Answer one command. Never raises: a traceback here is a Discord error
    message in front of a user."""
    try:
        name, args = _sub(interaction.get('data') or {})
        if name == 'tournament':
            return _tournament(args.get('name') or '')
        if name == 'event':
            return _event(args.get('name') or '')
        if name == 'me':
            return _me(_who(interaction))
        if name == 'link':
            return _link()
        return reply('I do not know that one yet.')
    except Exception:                                           # noqa: BLE001
        logger.exception('discord command failed')
        return reply('Something went wrong looking that up. Try again in a '
                     'moment.')


def _front(path=''):
    from vent.settings import FRONTEND_URL
    return f'{FRONTEND_URL}{path}'


def _tournament(term):
    from vent_tournament.models import Tournament
    from .discord import embed

    term = term.strip()
    if len(term) < 2:
        return reply('Give me at least two characters to search for.')

    rows = (Tournament.objects.filter(tournament_title__icontains=term,
                                      is_draft=False)
            .order_by('-start_date_and_time')[:5])
    if not rows:
        return reply(f'Nothing called "{term}". It may be a draft, or spelled '
                     'differently.')

    if len(rows) > 1:
        listing = '\n'.join(
            f'- [{t.tournament_title}]({_front("/tournaments/" + (t.slug or str(t.tournament_id)))})'
            for t in rows)
        return reply_embed(embed('Which one?', listing))

    t = rows[0]
    registered = t.registrations.filter(status='confirmed').count() \
        if hasattr(t, 'registrations') else 0
    fields = [
        ('Game', t.tournament_game.game_title if t.tournament_game_id else 'TBC', True),
        ('Starts', t.start_date_and_time.strftime('%d %b %Y, %H:%M UTC')
         if t.start_date_and_time else 'TBC', True),
        ('Entered', str(registered), True),
    ]
    if t.entry_fee == 'Paid':
        fields.append(('Entry', f'{t.entry_fee_price} VENT COINS', True))
    return reply_embed(embed(
        t.tournament_title,
        (t.tournament_description or '')[:300],
        _front('/tournaments/' + (t.slug or str(t.tournament_id))),
        fields))


def _event(term):
    from vent_event.models import Event
    from .discord import embed

    term = term.strip()
    if len(term) < 2:
        return reply('Give me at least two characters to search for.')

    rows = Event.objects.filter(name__icontains=term,
                                is_active=True).order_by('-event_date')[:5]
    if not rows:
        return reply(f'Nothing called "{term}".')

    if len(rows) > 1:
        listing = '\n'.join(
            f'- [{e.name}]({_front("/events/" + (e.slug or str(e.event_id)))})'
            for e in rows)
        return reply_embed(embed('Which one?', listing))

    e = rows[0]
    fields = [
        ('When', e.event_date.strftime('%d %b %Y') if e.event_date else 'TBC', True),
        ('Where', (e.location or 'Online')[:60], True),
    ]
    return reply_embed(embed(e.name, (e.desc or '')[:300],
                             _front('/events/' + (e.slug or str(e.event_id))),
                             fields))


def _me(discord_id):
    from .discord import embed
    from .models import PlatformAccount

    if not discord_id:
        return reply('I could not tell who you are.')

    row = (PlatformAccount.objects
           .filter(platform='discord', provider_user_id=discord_id,
                   connected=True)
           .select_related('user').first())
    if row is None:
        return reply('This Discord is not connected to a V-ENT account yet. '
                     f'Connect it at {_front("/settings?panel=linked")}')

    user = row.user
    fields = [('Username', user.username, True)]
    wallet = getattr(user, 'wallet', None)
    if wallet is not None:
        fields.append(('Wallet', f'{wallet.wallet_balance} VENT COINS', True))
    fields.append(('Direct messages',
                   'on' if row.dm_enabled else 'off', True))

    return reply_embed(embed(
        user.full_name or user.username,
        'Your V-ENT account, as this Discord sees it.',
        _front('/u/' + user.username), fields))


def _link():
    return reply(
        'Sign in at V-ENT, open Settings, then Linked accounts, and press '
        f'Connect on Discord: {_front("/settings?panel=linked")}\n'
        'Once it is connected you can turn on direct messages there, and '
        '/vent me will know who you are.')

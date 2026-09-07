"""Everything V-ENT sends TO Discord, in one place.

CEO, 7 September 2026, asked for both delivery routes: "i WANT THE ABOVE BOTH"
on announcements into a server channel by webhook, and direct messages to a
person through the bot.

They are different products - one is a broadcast into somebody's server, the
other is addressed to one person - but they are the same HTTP call to the same
API with the same failure modes, so they share a transport. Two copies of "post
to Discord" is how one of them ends up without the rate-limit handling.

## The rule this file exists to enforce

**Discord being down must never break a V-ENT request.** A tournament starting,
a bracket moving, a ticket selling: all of those are the real work, and telling
Discord about it is a courtesy. So every function here:

- runs OFF the request thread, so a slow Discord cannot slow a page;
- has a short timeout, because a hung socket is worse than a failed post;
- swallows every exception and logs it, and never propagates;
- respects a 429 by waiting the `retry_after` Discord names, once, then gives
  up rather than joining in on a rate limit.

That last one matters more than it looks. Discord's limits are per-webhook and
per-bot, and a platform that retries hard on 429 gets its bot disabled. The
overlay feed on 3 September asked the API 25 times a second for exactly this
class of reason, and that was our own API being kind about it.

## Why threads rather than Celery

Celery and Redis are installed and two worker processes are alive on the box,
but no `tasks.py` exists in any app and nothing is registered, so scheduling
onto it would mean standing that up first. A short-lived daemon thread with a
hard timeout is the honest shape for a fire-and-forget HTTP call of a few
hundred milliseconds, and it matches how `create_notification` already
behaves. If the volume ever justifies a queue, `_dispatch` is the one function
that changes.
"""
import logging
import os
import threading
import time

import requests as http

logger = logging.getLogger(__name__)

API = 'https://discord.com/api/v10'

#: Long enough for a normal round trip on a bad connection, short enough that a
#: hung Discord cannot pin a thread open.
TIMEOUT = 10

#: One retry on a rate limit, and only when Discord tells us how long to wait.
#: Never a loop.
MAX_RETRY_WAIT = 5


#: V-ENT's own Discord server, and the invite anybody may use to join it.
#:
#: This is not decoration. **Discord will not let a bot send a direct message
#: to somebody it shares no server with**, so the invite is the thing that
#: makes the direct-message switch work at all. Offering the switch without
#: offering the invite is offering a control that cannot succeed, which is the
#: exact shape this codebase has a hard rule about.
#:
#: Both invites the platform has used resolve to the same server, checked on
#: 7 September: discord.gg/z7MNM9pmYr and discord.com/invite/mxevc5aQG3 both
#: answer with guild 1046108379598291036, "V-ENT", 478 members.
GUILD_ID = os.environ.get('DISCORD_GUILD_ID', '1046108379598291036')
INVITE = os.environ.get('DISCORD_INVITE', 'https://discord.gg/z7MNM9pmYr')


def bot_token():
    return os.environ.get('DISCORD_BOT_TOKEN', '')


def dm_configured():
    """Whether direct messages can work at all right now.

    Reported rather than assumed, so a screen can say "not set up yet" instead
    of offering a switch that silently does nothing. Same shape as
    `provider_status` for linking.
    """
    return bool(bot_token())


def _dispatch(fn, *args, **kwargs):
    """Run something off the request thread, and never let it raise.

    The daemon flag matters: a deploy restarting gunicorn should not wait on an
    in-flight announcement, and losing one is the correct trade against holding
    a worker open.
    """
    def run():
        try:
            fn(*args, **kwargs)
        except Exception:                                       # noqa: BLE001
            logger.exception('discord: dispatch failed')
    threading.Thread(target=run, daemon=True).start()


def _post(url, payload, headers=None, what='post'):
    """One POST, with the 429 handling both callers need.

    Returns (ok, error) where error is a short sentence fit to store on a row
    and show to an organiser. Never raises.
    """
    try:
        res = http.post(url, json=payload, headers=headers or {}, timeout=TIMEOUT)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('discord %s: %s', what, exc)
        return False, 'Could not reach Discord.'

    if res.status_code == 429:
        # Discord says how long to wait. Waiting that long once is co-operating;
        # retrying on a timer is how a bot gets disabled.
        try:
            wait = float(res.json().get('retry_after') or 0)
        except Exception:                                       # noqa: BLE001
            wait = 0
        if 0 < wait <= MAX_RETRY_WAIT:
            time.sleep(wait)
            try:
                res = http.post(url, json=payload, headers=headers or {},
                                timeout=TIMEOUT)
            except Exception as exc:                            # noqa: BLE001
                logger.warning('discord %s retry: %s', what, exc)
                return False, 'Could not reach Discord.'
        else:
            return False, 'Discord is rate limiting this channel.'

    if res.status_code in (200, 201, 204):
        return True, ''

    # 404 on a webhook means somebody deleted it in Discord, which is the one
    # an organiser most needs told: it will never work again and the row should
    # be turned off rather than retried.
    if res.status_code == 404:
        return False, 'That webhook no longer exists in Discord.'
    if res.status_code in (401, 403):
        return False, 'Discord refused. Check the webhook or the bot has access.'

    logger.warning('discord %s: %s %s', what, res.status_code, res.text[:200])
    return False, 'Discord rejected the message.'


# --------------------------------------------------------------- announcements

def post_webhook(url, content='', embed=None, blocking=False):
    """Announce into whatever channel this webhook points at.

    No bot, no intents, no install: a webhook URL IS the permission, which is
    also why it is treated as a secret everywhere it is stored.
    """
    if not url:
        return False, 'No webhook set.'
    payload = {}
    if content:
        payload['content'] = content[:2000]
    if embed:
        payload['embeds'] = [embed]
    if not payload:
        return False, 'Nothing to send.'

    if blocking:
        return _post(url, payload, what='webhook')
    _dispatch(_post, url, payload, None, 'webhook')
    return True, ''


# ------------------------------------------------------------------------ DMs

def _open_dm(user_id):
    """Discord makes you open a channel with somebody before writing to them."""
    token = bot_token()
    if not token:
        return None, 'Direct messages are not set up on this server yet.'
    try:
        res = http.post(f'{API}/users/@me/channels',
                        json={'recipient_id': str(user_id)},
                        headers={'Authorization': f'Bot {token}'},
                        timeout=TIMEOUT)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('discord open dm: %s', exc)
        return None, 'Could not reach Discord.'

    if res.status_code in (200, 201):
        return res.json().get('id'), ''
    if res.status_code in (400, 403):
        # The two ordinary refusals, and neither is worth retrying: Discord
        # will not let a bot message somebody who shares no server with it, or
        # who has direct messages from server members turned off.
        return None, ('Discord would not open a message with that account. '
                      'They may need to join the V-ENT server, or allow direct '
                      'messages from server members.')
    logger.warning('discord open dm: %s %s', res.status_code, res.text[:200])
    return None, 'Discord refused to open a message.'


def send_dm(user_id, content='', embed=None, blocking=False):
    """Message one person, addressed by their Discord id.

    By ID, never by handle: a handle is renameable and reusable, so a message
    addressed to a name is a message that eventually reaches a stranger.
    """
    if not user_id:
        return False, 'No Discord account on file.'

    def run():
        channel, err = _open_dm(user_id)
        if not channel:
            return False, err
        payload = {}
        if content:
            payload['content'] = content[:2000]
        if embed:
            payload['embeds'] = [embed]
        return _post(f'{API}/channels/{channel}/messages', payload,
                     {'Authorization': f'Bot {bot_token()}'}, 'dm')

    if blocking:
        return run()
    _dispatch(run)
    return True, ''


# --------------------------------------------------------------------- shaping

#: V-ENT red, as a Discord embed integer. The brand colour rather than a
#: decoration: an embed with no colour reads as a system message.
BRAND = 0xED1C24


def embed(title, description='', url='', fields=None, image=''):
    """One embed builder, so every message from V-ENT looks like V-ENT.

    Discord truncates silently past its limits and an embed that vanishes is
    worse than a short one, so everything is cut here to the documented
    maximums rather than hoped about.
    """
    out = {'color': BRAND, 'title': (title or '')[:256]}
    if description:
        out['description'] = description[:4096]
    if url:
        out['url'] = url
    if image:
        out['image'] = {'url': image}
    if fields:
        out['fields'] = [
            {'name': str(n)[:256], 'value': str(v)[:1024], 'inline': bool(i)}
            for n, v, i in fields[:25]
        ]
    return out

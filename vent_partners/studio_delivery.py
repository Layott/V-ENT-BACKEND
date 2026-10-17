"""Every request V-ENT sends a studio: signed, retried, and written down.

Design section 14, item 5: "Signed with HMAC SHA-256 over method, path,
timestamp, host and body hash; five minute replay window; retries with
backoff; a delivery log the studio can read." Item 3: the create game session
webhook is called until the studio answers, which is the same machinery, so
events and session calls go through this one engine rather than two.

How a delivery moves:

    queue()      a row is written, in the caller's transaction, so a result
                 that is rolled back never tells a studio anything
    kick()       after the commit, the first try is made off the request
                 thread (a slow studio never slows a page)
    attempt()    claims the row with a lease, signs, sends, records the answer
    run_due()    the cron pass (`manage.py studio_run`) picks up every retry

The body every studio receives, whatever the kind:

    {"id": "dl_...", "type": "event", "event": "result_confirmed",
     "created_at": "...", "game": "<slug>", "environment": "live",
     "data": {...}}

A 2xx is delivered. A 410 means the studio has taken the address down, and we
stop at once. Anything else, a timeout, a refused connection or an address that
now resolves inside a private network, is tried again on the kind's schedule,
then given up on. Every outcome is a code on the row, never a traceback.

Why threads and cron rather than Celery: the same reasons `vent_auth/discord.py`
and `send_due_reminders` give. Nothing is registered with Celery on this box,
a management command under cron is less machinery, and every step here is an
ordinary function a test can drive with a moved clock.
"""
import json
import logging
import sys
import threading
from datetime import timedelta

import requests as http
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import studio_signing, studio_urls

logger = logging.getLogger(__name__)

#: Long enough for a studio on a bad connection, short enough that a hung one
#: cannot hold a cron pass for long.
TIMEOUT = 10

#: How long a claimed delivery belongs to the run that claimed it. Longer than
#: TIMEOUT, so an overlapping cron run never sends the same row twice.
LEASE = timedelta(seconds=120)

#: Seconds to wait after each failed try, by kind; past the end, give up.
#: Events back off over about two days, as a webhook provider does. A session
#: call is somebody waiting for a lobby, so it retries fast and stops sooner.
BACKOFF = {
    'event': [30, 120, 600, 1800, 3600, 10800, 21600, 43200, 86400],
    'create_game_session': [10, 30, 60, 120, 120, 300, 300, 600],
    'cancel_game_session': [30, 120, 600, 1800, 3600],
    'get_game_session': [60],
}

#: How much of a studio's answer is kept for them to read.
EXCERPT = 500

#: Finished deliveries are cleared after this long (`studio_run`).
KEEP = timedelta(days=30)


def _inline():
    """Send in the caller's thread: under tests, or when settings say so."""
    return getattr(settings, 'STUDIO_DELIVERY_INLINE', 'test' in sys.argv)


def address_for(game, kind):
    """Where a kind is sent: events to the events address, falling back to the
    session address; session calls to the session address."""
    if kind == 'event':
        return game.events_webhook_url or game.session_webhook_url
    return game.session_webhook_url


def queue(game, kind, data, *, event='', session=None):
    """Write a delivery to be sent. None when the game gave no address for it."""
    from .models import StudioDelivery

    url = address_for(game, kind)
    if not url:
        return None
    delivery = StudioDelivery(game=game, kind=kind, event=event or '', url=url,
                              payload=data or {}, status='pending',
                              next_attempt_at=timezone.now())
    if session is not None:
        delivery.session = session
    delivery.save()
    return delivery


def body_of(delivery):
    """The exact bytes signed and sent."""
    game = delivery.game
    return json.dumps({
        'id': delivery.ref,
        'type': delivery.kind,
        'event': delivery.event or None,
        'created_at': delivery.created_at.isoformat() if delivery.created_at else None,
        'game': game.slug,
        'environment': game.environment,
        'data': delivery.payload or {},
    }, separators=(',', ':'), default=str).encode()


def kick(refs):
    """After the caller's transaction commits, try each delivery once now."""
    refs = [r for r in (refs or []) if r]
    if not refs:
        return

    def run():
        for ref in refs:
            try:
                attempt_ref(ref)
            except Exception:
                logger.warning('studio delivery %s failed to send', ref, exc_info=True)

    def start():
        if _inline():
            run()
        else:
            threading.Thread(target=run, daemon=True, name='studio-delivery').start()

    transaction.on_commit(start)


def _claim(ref, now):
    from .models import StudioDelivery

    with transaction.atomic():
        delivery = (StudioDelivery.objects.select_for_update()
                    .select_related('game', 'game__partner')
                    .filter(ref=ref, status='pending', next_attempt_at__lte=now).first())
        if delivery is None:
            return None
        delivery.next_attempt_at = now + LEASE
        delivery.save(update_fields=['next_attempt_at'])
        return delivery


def _readable(text):
    cleaned = ''.join(c if (c >= ' ' or c in '\t\n') else ' ' for c in str(text or ''))
    return cleaned[:EXCERPT]


def _record(delivery, now, *, status_code=None, error='', excerpt='', delivered=False,
            final=False):
    delivery.attempts += 1
    delivery.last_status_code = status_code
    delivery.last_error = error
    delivery.response_excerpt = _readable(excerpt)
    if delivered:
        delivery.status = 'delivered'
        delivery.delivered_at = now
        delivery.next_attempt_at = None
    elif final:
        delivery.status = 'failed'
        delivery.next_attempt_at = None
    else:
        schedule = BACKOFF.get(delivery.kind, BACKOFF['event'])
        if delivery.attempts > len(schedule):
            delivery.status = 'failed'
            delivery.last_error = 'GAVE_UP'
            delivery.next_attempt_at = None
        else:
            delivery.next_attempt_at = now + timedelta(seconds=schedule[delivery.attempts - 1])
    delivery.save(update_fields=['attempts', 'last_status_code', 'last_error',
                                 'response_excerpt', 'status', 'delivered_at',
                                 'next_attempt_at'])
    return delivery


def attempt_ref(ref, now=None):
    """Send one due delivery now. None when it is not due or not ours to send."""
    now = now or timezone.now()
    delivery = _claim(ref, now)
    if delivery is None:
        return None
    return _send(delivery, now)


def _send(delivery, now):
    game = delivery.game
    secrets = game.signing_secrets(now=now)
    if not secrets:
        return _record(delivery, now, error='STUDIO_SECRET_UNREADABLE')
    if not studio_urls.resolves_public(delivery.url):
        # Nothing is sent either way; the error says which fault it is.
        error = 'URL_PRIVATE_HOST' if studio_urls.resolves(delivery.url) else 'UNREACHABLE'
        return _record(delivery, now, error=error)

    body = body_of(delivery)
    headers = studio_signing.outbound_headers(secrets, 'POST', delivery.url, body,
                                              delivery_ref=delivery.ref, now=now.timestamp())
    try:
        res = http.post(delivery.url, data=body, headers=headers, timeout=TIMEOUT,
                        allow_redirects=False)
    except http.Timeout:
        return _record(delivery, now, error='TIMEOUT')
    except http.RequestException:
        return _record(delivery, now, error='UNREACHABLE')

    code = res.status_code
    if 200 <= code < 300:
        _record(delivery, now, status_code=code, excerpt=res.text, delivered=True)
        if delivery.kind != 'event':
            # A session call's answer moves the session (studio_sessions).
            from . import studio_sessions
            try:
                answer = res.json()
            except ValueError:
                answer = None
            try:
                studio_sessions.read_answer(delivery, answer)
            except Exception:
                logger.warning('studio answer for %s could not be read', delivery.ref,
                               exc_info=True)
        return delivery
    if code == 410:
        return _record(delivery, now, status_code=code, error='GONE', excerpt=res.text,
                       final=True)
    return _record(delivery, now, status_code=code, error='HTTP_STATUS', excerpt=res.text)


def run_due(now=None, limit=200):
    """The cron pass: send every delivery that is due. Returns how many arrived."""
    from .models import StudioDelivery

    now = now or timezone.now()
    refs = list(StudioDelivery.objects
                .filter(status='pending', next_attempt_at__lte=now)
                .order_by('next_attempt_at', 'id')
                .values_list('ref', flat=True)[:limit])
    delivered = 0
    for ref in refs:
        try:
            done = attempt_ref(ref, now=now)
        except Exception:
            logger.warning('studio delivery %s failed to send', ref, exc_info=True)
            continue
        if done is not None and done.status == 'delivered':
            delivered += 1
    return delivered


def resend(delivery):
    """The same body again, as a new delivery to the game's address today."""
    from .models import StudioDelivery

    again = StudioDelivery(game=delivery.game, kind=delivery.kind, event=delivery.event,
                           url=address_for(delivery.game, delivery.kind) or delivery.url,
                           payload=delivery.payload, status='pending',
                           next_attempt_at=timezone.now())
    if getattr(delivery, 'session_id', None):
        again.session_id = delivery.session_id
    again.save()
    return again


def clear_old(now=None):
    """Drop finished deliveries older than KEEP. Pending ones are never dropped."""
    from .models import StudioDelivery

    cutoff = (now or timezone.now()) - KEEP
    deleted, _ = (StudioDelivery.objects
                  .filter(created_at__lt=cutoff, status__in=('delivered', 'failed', 'cancelled'))
                  .delete())
    return deleted

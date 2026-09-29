"""Browser push notifications (Web Push with VAPID keys).

Push was a column on Settings > Notifications with nothing behind it (CEO, 30
September 2026: "If they put on or off something for discord or push or email
or in app, it must work as each user set it").

A browser subscribes through the site's service worker (`door-sw.js`, the one
worker the site registers) and hands over an endpoint and two keys, stored as
`PushSubscription`. A notification the person allowed on push is sent to every
browser they subscribed, on a background thread so a slow push service never
holds up the request that caused it.

Keys, in the backend `.env`:
    VAPID_PUBLIC_KEY    base64url, given to the browser to subscribe with
    VAPID_PRIVATE_KEY   base64url, never leaves the server
    VAPID_SUBJECT       mailto: address the push services can write to
Generate a pair with `python manage.py vapid_keys`.
"""
import json
import logging
import os
import threading

from django.utils import timezone

logger = logging.getLogger(__name__)


def public_key():
    return (os.environ.get('VAPID_PUBLIC_KEY') or '').strip()


def _private_key():
    return (os.environ.get('VAPID_PRIVATE_KEY') or '').strip()


def configured():
    return bool(public_key() and _private_key())


def _subject():
    return (os.environ.get('VAPID_SUBJECT') or 'mailto:support@v-ent.co').strip()


def payload_for(notification, frontend_url):
    link = notification.link or '/notifications'
    url = link if link.startswith('http') else '%s%s' % (frontend_url, link)
    return json.dumps({
        'title': notification.title[:120],
        'body': (notification.body or '')[:240],
        'url': url,
        'tag': 'vent-%s' % (notification.pk or notification.category),
    })


def send_to_user(user_id, data):
    """Send `data` to every browser this person subscribed. Returns how many took it."""
    if not configured():
        return 0
    from pywebpush import WebPushException, webpush
    from .models import PushSubscription

    sent = 0
    for sub in PushSubscription.objects.filter(user_id=user_id):
        try:
            webpush(
                subscription_info={'endpoint': sub.endpoint,
                                   'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}},
                data=data,
                vapid_private_key=_private_key(),
                vapid_claims={'sub': _subject()},
                timeout=10,
            )
            sent += 1
            PushSubscription.objects.filter(pk=sub.pk).update(last_sent_at=timezone.now())
        except WebPushException as exc:
            code = getattr(getattr(exc, 'response', None), 'status_code', None)
            if code in (404, 410):
                # The browser unsubscribed or the push service forgot it.
                PushSubscription.objects.filter(pk=sub.pk).delete()
            else:
                logger.warning('push to subscription %s failed: %s', sub.pk, exc)
        except Exception:                                      # noqa: BLE001
            logger.exception('push to subscription %s failed', sub.pk)
    return sent


def deliver(notification, frontend_url):
    """Fire and forget: never raises, never blocks the caller."""
    if not configured():
        return
    data = payload_for(notification, frontend_url)
    user_id = notification.user_id

    def run():
        try:
            send_to_user(user_id, data)
        except Exception:                                      # noqa: BLE001
            logger.exception('push delivery failed')

    from django.conf import settings
    if getattr(settings, 'NOTIFY_IN_BACKGROUND', True):
        threading.Thread(target=run, daemon=True).start()
    else:
        run()

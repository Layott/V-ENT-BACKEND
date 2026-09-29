"""In-app notifications - the fire-and-forget writer + the /auth/notifications/
inbox endpoints (contract §1.2-1.3).

`create_notification` is called from the platform's real event sites (wallet
receive, tournament registration, team join request/accept, dispute
raised/resolved, KYC + payout decisions). It MUST never raise into the caller -
a notification insert failing must not break the host wallet/tournament/team
flow - so the whole body is wrapped in try/except + logger.exception.

Read endpoints authenticate with the standard Bearer helper
`views_profile._user_from_bearer` and speak the {status, data, message}
envelope like the rest of vent_auth.
"""
import logging

from vent.settings import FRONTEND_URL
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, Notification
from .views_profile import _user_from_bearer

logger = logging.getLogger(__name__)

PER_PAGE = 20

# Field limits mirrored from the Notification model so create_notification can
# truncate defensively (a long username/title can never blow up the insert).
_TITLE_MAX = 160
_BODY_MAX = 500
_LINK_MAX = 300
_CATEGORY_MAX = 40


# ---------------------------------------------------------------------------
# §1.2 - fire-and-forget writer
# ---------------------------------------------------------------------------

def create_notification(user, category, title, body='', link='', metadata=None, email=True):
    """Create a single notification. Fire-and-forget.

    `user` may be a Users instance OR an int user_id. Never raises - on any
    failure it logs and returns None. Returns the created Notification on
    success. Title/body/link/category are truncated to their field limits.

    `email=False` is for a site that sends its own, richer email for the same
    thing (a ticket with its QR code, a registration receipt, a payout
    decision): the generic copy would be a second email about one event.
    """
    try:
        if not isinstance(user, Users):
            user = Users.objects.filter(pk=user).first()
            if user is None:
                logger.warning('create_notification: no user for id %r', user)
                return None

        # Every channel is a switch the person set (CEO, 30 September 2026:
        # "If they put on or off something for discord or push or email or in
        # app, it must work as each user set it"). One read of their settings,
        # then each channel asks the same question of it.
        from . import notify_prefs
        category = (category or 'system')[:_CATEGORY_MAX]
        stored = notify_prefs._stored(user)
        wants = {c: notify_prefs.wants(user, category, c, stored) for c in notify_prefs.CHANNELS}

        # The row is always written: the other channels send from it, and a
        # caller can tell "delivered elsewhere" from "failed". Switched off for
        # the inbox, it is kept out of the inbox and the bell.
        row = Notification.objects.create(
            user=user,
            category=category,
            title=(title or '')[:_TITLE_MAX],
            body=(body or '')[:_BODY_MAX],
            link=(link or '')[:_LINK_MAX],
            metadata=metadata if isinstance(metadata, dict) else {},
            in_inbox=wants['in_app'],
        )

        # Delivery channels on this one notification, never second systems:
        # every site that calls create_notification gets all of them, and a new
        # site cannot support the inbox and forget the rest.
        if wants['discord']:
            _deliver_to_discord(row)
        if wants['email'] and email:
            _deliver_by_email(row, notify_prefs.row_for(category))
        if wants['push']:
            _deliver_by_push(row)
        return row
    except Exception:
        logger.exception('create_notification failed (category=%s)', category)
        return None


def _deliver_by_email(row, row_id):
    """Email this notification, on its own thread. Never raises, never blocks."""
    import threading

    def run():
        try:
            from .emails import send_notification
            send_notification(row, row_id)
        except Exception:                                       # noqa: BLE001
            logger.exception('email delivery failed for notification %s', row.pk)

    from django.conf import settings
    if getattr(settings, 'NOTIFY_IN_BACKGROUND', True):
        threading.Thread(target=run, daemon=True).start()
    else:
        run()


def _deliver_by_push(row):
    """Browser push to every device this person subscribed. Never raises."""
    try:
        from . import push
        push.deliver(row, FRONTEND_URL)
    except Exception:                                           # noqa: BLE001
        logger.exception('push delivery failed for notification %s', row.pk)


def _deliver_to_discord(row):
    """Send this notification on to Discord, if the person asked for that.

    Never raises and never blocks: the transport puts it on its own thread and
    swallows everything. An unreachable Discord must not stop a notification
    being written, which is the part that actually matters.
    """
    try:
        from .discord import dm_configured, embed, send_dm
        from .models import PlatformAccount

        if not dm_configured():
            return

        link = row.link or ''
        account = (PlatformAccount.objects
                   .filter(user_id=row.user_id, platform='discord',
                           connected=True, dm_enabled=True)
                   .exclude(provider_user_id='')
                   .first())
        if account is None:
            return

        url = link if link.startswith('http') else (
            f'{FRONTEND_URL}{link}' if link else '')
        send_dm(account.provider_user_id,
                embed=embed(row.title, row.body, url))
    except Exception:                                           # noqa: BLE001
        logger.exception('discord delivery failed for notification')


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _row(n):
    return {
        'id': n.id,
        'category': n.category,
        'title': n.title,
        'body': n.body,
        'link': n.link,
        'is_read': n.is_read,
        'metadata': n.metadata or {},
        'created_at': n.created_at,
    }


def _unread_count(user):
    return Notification.objects.filter(user=user, is_read=False, in_inbox=True).count()


# ---------------------------------------------------------------------------
# §1.3 - GET /auth/notifications/?page=1&filter=all|unread
# ---------------------------------------------------------------------------

@api_view(['GET'])
def list_notifications(request):
    user, err = _user_from_bearer(request)
    if err:
        return err

    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    filter_arg = (request.GET.get('filter') or 'all').lower()

    qs = Notification.objects.filter(user=user, in_inbox=True)
    if filter_arg == 'unread':
        qs = qs.filter(is_read=False)

    total = qs.count()
    offset = (page - 1) * PER_PAGE
    rows = [_row(n) for n in qs.order_by('-created_at')[offset:offset + PER_PAGE]]

    return Response({
        'status': 'success',
        'data': {
            'notifications': rows,
            'unread_count': _unread_count(user),
            'total': total,
            'page': page,
            'per_page': PER_PAGE,
        },
        'message': 'Notifications retrieved',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §1.3 - GET /auth/notifications/unread-count/  (cheap bell poll)
# ---------------------------------------------------------------------------

@api_view(['GET'])
def notifications_unread_count(request):
    user, err = _user_from_bearer(request)
    if err:
        return err

    return Response({
        'status': 'success',
        'data': {'unread_count': _unread_count(user)},
        'message': 'Unread count',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §1.3 - POST /auth/notifications/<id>/read/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def mark_notification_read(request, notification_id):
    user, err = _user_from_bearer(request)
    if err:
        return err

    notification = Notification.objects.filter(id=notification_id, user=user).first()
    if notification is None:
        return Response(
            { 'code': 'NOTIFICATION_NOT_FOUND','status': 'error', 'message': 'Notification not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=['is_read', 'read_at'])

    return Response({
        'status': 'success',
        'data': {'unread_count': _unread_count(user)},
        'message': 'Notification marked as read',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §1.3 - POST /auth/notifications/read-all/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def mark_all_notifications_read(request):
    user, err = _user_from_bearer(request)
    if err:
        return err

    updated = Notification.objects.filter(user=user, is_read=False, in_inbox=True).update(
        is_read=True, read_at=timezone.now(),
    )

    return Response({
        'status': 'success',
        'data': {'unread_count': 0, 'updated': updated},
        'message': 'All notifications marked as read',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §1.3 - POST /auth/notifications/<id>/delete/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def delete_notification(request, notification_id):
    user, err = _user_from_bearer(request)
    if err:
        return err

    notification = Notification.objects.filter(id=notification_id, user=user).first()
    if notification is None:
        return Response(
            { 'code': 'NOTIFICATION_NOT_FOUND','status': 'error', 'message': 'Notification not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    notification.delete()

    return Response({
        'status': 'success',
        'data': {'unread_count': _unread_count(user)},
        'message': 'Notification deleted',
    }, status=status.HTTP_200_OK)

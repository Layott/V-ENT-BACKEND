"""Admin dashboard - extension endpoints (contract §4, §5, §10, §14, §17, §21).

Split out of views_admin.py to keep the file that already shipped focused. All
endpoints reuse the same RBAC decorator, Response envelope, AdminAction audit
log, and _log_action helper as views_admin.py.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, Transaction, AdminAction
from .decorators import ADMIN_ROLES, admin_role_required
from .views_admin import _log_action, _approve_payout_core, _paginate


# ---------------------------------------------------------------------------
# §4 - GET /auth/admin/charts/   (all admins)
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_charts(request):
    """Last-14-days timeline, oldest→newest, zero-filled (contract §4)."""
    from vent_tournament.models import TournamentRegistration

    today = timezone.now().date()
    start = today - timedelta(days=13)
    days = [start + timedelta(days=i) for i in range(14)]

    signup_map = dict(
        Users.objects.filter(date_joined__date__gte=start)
        .annotate(d=TruncDate('date_joined')).values('d')
        .annotate(c=Count('user_id')).values_list('d', 'c')
    )
    vc_map = dict(
        Transaction.objects.filter(
            created_at__date__gte=start, status='completed',
            type__in=['top_up', 'prize', 'receive'], amount__gt=0,
        ).annotate(d=TruncDate('created_at')).values('d')
        .annotate(s=Sum('amount')).values_list('d', 's')
    )
    join_map = dict(
        TournamentRegistration.objects.filter(registered_at__date__gte=start)
        .annotate(d=TruncDate('registered_at')).values('d')
        .annotate(c=Count('id')).values_list('d', 'c')
    )

    timeline = [
        {
            'label': f"{day.strftime('%b')} {day.day}",
            'signups': signup_map.get(day, 0),
            'vc_issued': int(vc_map.get(day, 0) or 0),
            'tournament_joins': join_map.get(day, 0),
        }
        for day in days
    ]

    return Response({
        'status': 'success',
        'data': {'timeline': timeline},
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §5 - GET /auth/admin/recent-activity/   (all admins, role-scoped)
# ---------------------------------------------------------------------------

def _activity_description(a):
    """Human string for an AdminAction (mirrors the audit-log description)."""
    if a.reason:
        return a.reason
    target = a.target_model or 'record'
    return f"{a.action_type} on {target} #{a.target_id}"


@api_view(['GET'])
@admin_role_required(ADMIN_ROLES)
def admin_recent_activity(request):
    """Latest 15 AdminAction rows (contract §5). super_admin sees all; every
    other admin sees only their own actions (mirrors audit-log scoping)."""
    qs = AdminAction.objects.select_related('admin').order_by('-performed_at')
    if request.admin_role != 'super_admin':
        qs = qs.filter(admin=request.admin_user)

    activity = [
        {
            'id': a.id,
            'action': a.action_type,
            'description': _activity_description(a),
            'created_at': a.performed_at,
            'admin_username': a.admin.username if a.admin else None,
            'target_type': a.target_model,
        }
        for a in qs[:15]
    ]

    return Response({
        'status': 'success',
        'data': {'activity': activity},
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §10 - POST /auth/admin/users/bulk/   (super_admin, mod_admin)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@admin_role_required(['super_admin', 'mod_admin'])
def admin_bulk_user_action(request):
    """Bulk ban/unban (contract §10). Body {action:"ban"|"unban", ids:[...]}."""
    admin = request.admin_user
    action = request.data.get('action')
    ids = request.data.get('ids') or []

    if action not in ('ban', 'unban'):
        return Response(
            {'status': 'error', 'message': 'action must be "ban" or "unban"'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(ids, list) or not ids:
        return Response(
            {'status': 'error', 'message': 'ids must be a non-empty list'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    new_active = (action == 'unban')
    count = 0
    for uid in ids:
        try:
            user = Users.objects.get(user_id=uid)
        except Users.DoesNotExist:
            continue
        if user.user_id == admin.user_id:
            continue  # never ban yourself
        if user.is_active == new_active:
            # still count as applied? no - only count actual changes
            continue
        user.is_active = new_active
        user.save(update_fields=['is_active'])
        _log_action(admin, 'ban_user' if action == 'ban' else 'unban_user',
                    'User', uid, reason='bulk action')
        count += 1

    return Response({
        'status': 'success',
        'message': f'{count} user(s) {action}ned',
        'data': {'count': count},
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §14 - POST /auth/admin/tournaments/<id>/disqualify/   (super_admin, mod_admin)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@admin_role_required(['super_admin', 'mod_admin'])
def admin_disqualify_registration(request, tournament_id):
    """Disqualify a registration (contract §14). Body {team_name} OR
    {registration_id}."""
    from vent_tournament.models import Tournament, TournamentRegistration

    tournament = get_object_or_404(Tournament, tournament_id=tournament_id)
    registration_id = request.data.get('registration_id')
    team_name = request.data.get('team_name')

    reg = None
    if registration_id:
        reg = TournamentRegistration.objects.filter(
            id=registration_id, tournament=tournament).first()
    elif team_name:
        reg = TournamentRegistration.objects.filter(
            tournament=tournament, team__team_name=team_name).first()
    else:
        return Response(
            {'status': 'error', 'message': 'registration_id or team_name is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if reg is None:
        return Response(
            {'status': 'error', 'message': 'Registration not found for this tournament'},
            status=status.HTTP_404_NOT_FOUND,
        )

    reg.status = 'disqualified'
    reg.save(update_fields=['status'])

    _log_action(admin=request.admin_user, action_type='disqualify',
                target_model='TournamentRegistration', target_id=reg.id,
                reason=request.data.get('reason', ''),
                metadata={'tournament_id': tournament_id, 'team_name': team_name})

    return Response({'status': 'success', 'message': 'Registration disqualified'},
                    status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §17 - POST /auth/admin/payouts/bulk-approve/   (super_admin, finance_admin)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@admin_role_required(['super_admin', 'finance_admin'])
def admin_bulk_approve_payouts(request):
    """Bulk-approve payouts (contract §17). Body {ids:[...]}. Reuses the single
    approve core incl. the KYC gate; skips + doesn't count any that fail."""
    admin = request.admin_user
    ids = request.data.get('ids') or []

    if not isinstance(ids, list) or not ids:
        return Response(
            {'status': 'error', 'message': 'ids must be a non-empty list'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    note = request.data.get('note', 'bulk approve')
    count = 0
    for wid in ids:
        ok, _reason = _approve_payout_core(admin, wid, note)
        if ok:
            count += 1

    return Response({
        'status': 'success',
        'message': f'{count} payout(s) approved',
        'data': {'count': count},
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §21 - GET/POST /auth/admin/settings/   (super_admin)
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@admin_role_required(['super_admin'])
def admin_settings(request):
    """Platform-wide admin settings (contract §21). GET returns the merged blob;
    POST deep-merges the request body into the stored blob."""
    from .models import AdminSetting, _deep_merge_settings

    obj = AdminSetting.load()

    if request.method == 'GET':
        return Response({
            'status': 'success',
            'data': obj.merged(),
        }, status=status.HTTP_200_OK)

    # POST - deep-merge incoming body over the stored blob, then persist.
    body = request.data if isinstance(request.data, dict) else {}
    obj.data = _deep_merge_settings(obj.data or {}, body)
    obj.save()

    _log_action(request.admin_user, 'update_settings', 'AdminSetting', 1,
                reason='platform settings updated')

    return Response({
        'status': 'success',
        'message': 'Settings updated',
    }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# §2.3 - Dispute center (admin queue across all tournaments)
# ---------------------------------------------------------------------------

_DISPUTE_STATUSES = ('open', 'under_review', 'resolved', 'dismissed')


def _dispute_row(d):
    return {
        'id': d.id,
        'tournament_id': d.tournament_id,
        'tournament_title': d.tournament.tournament_title if d.tournament else None,
        'match_id': d.match_id,
        'round_number': d.match.round_number if d.match else None,
        'match_number': d.match.match_number if d.match else None,
        'raised_by': d.raised_by.username if d.raised_by else None,
        'raised_by_id': d.raised_by_id,
        'description': d.description,
        'evidence': d.evidence,
        'status': d.status,
        'resolution_note': d.resolution_note,
        'created_at': d.created_at,
        'resolved_at': d.resolved_at,
    }


@api_view(['GET'])
@admin_role_required(['super_admin', 'mod_admin'])
def admin_disputes_list(request):
    """GET /auth/admin/disputes/ - every dispute across tournaments (contract §2.3).

    Params: status (default open; open|under_review|resolved|dismissed|all),
    page, page_size (20). Response data = {results:[DROW], count, page, page_size}.
    """
    from vent_tournament.models import TournamentDispute

    qs = (
        TournamentDispute.objects
        .select_related('tournament', 'match', 'raised_by')
        .order_by('-created_at')
    )

    status_filter = request.GET.get('status', 'open')
    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)

    page, page_size, offset = _paginate(request, default_size=20)
    total = qs.count()
    rows = [_dispute_row(d) for d in qs[offset: offset + page_size]]

    return Response({
        'status': 'success',
        'data': {'results': rows, 'count': total, 'page': page, 'page_size': page_size},
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@admin_role_required(['super_admin', 'mod_admin'])
def admin_resolve_dispute_by_id(request, dispute_id):
    """POST /auth/admin/disputes/{id}/resolve/ - resolve/dismiss any dispute by id
    (contract §2.3). Logs the action and notifies the user who raised it."""
    from vent_tournament.models import TournamentDispute

    resolution = request.data.get('resolution')  # 'resolved' or 'dismissed'
    note = request.data.get('note', '')

    if resolution not in ('resolved', 'dismissed'):
        return Response(
            {'status': 'error', 'message': 'resolution must be "resolved" or "dismissed"'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    dispute = get_object_or_404(TournamentDispute, id=dispute_id)
    dispute.status = resolution
    dispute.resolution_note = note
    dispute.resolved_at = timezone.now()
    dispute.save(update_fields=['status', 'resolution_note', 'resolved_at'])

    _log_action(request.admin_user, 'resolve_dispute', 'TournamentDispute', dispute_id,
                reason=note, metadata={'resolution': resolution})

    # Notify the dispute author of the outcome - fire-and-forget.
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            dispute.raised_by_id, 'dispute', f'Your dispute was {resolution}',
            link='/disputes', metadata={'dispute_id': dispute.id, 'resolution': resolution},
        )
    except Exception:
        pass

    return Response({
        'status': 'success',
        'data': {'dispute_id': dispute.id, 'status': dispute.status},
        'message': f'Dispute {resolution}',
    }, status=status.HTTP_200_OK)

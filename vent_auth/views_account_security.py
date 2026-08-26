"""Two-factor authentication for ordinary accounts, and the danger zone.

The Security page offered an authenticator toggle that wrote a boolean into a
settings blob and nothing else - no secret, no verification, no effect on
signing in. It is the real thing here, reusing the RFC 6238 implementation the
admin portal already uses.

The danger zone offered three buttons. Exporting produced nothing, deactivating
produced nothing, and deleting produced nothing. All three do what they say now,
and deleting is a soft delete with the grace period the screen promises.
"""
import json
import logging
from datetime import timedelta
from io import BytesIO

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import totp as totp_lib
from .models import UserTOTP, Users
from .views_profile import _user_from_bearer

logger = logging.getLogger(__name__)

DELETE_GRACE_DAYS = 30


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(message, code='ERROR', http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': None},
                    status=http_status)


# ---------------------------------------------------------------------------
# Two-factor authentication
# ---------------------------------------------------------------------------

@api_view(['GET'])
def twofactor_status(request):
    """Whether this account has an authenticator, and whether it is confirmed."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    enrolment = UserTOTP.objects.filter(user=user).first()
    return _ok({
        'enabled': bool(enrolment and enrolment.confirmed),
        'pending': bool(enrolment and not enrolment.confirmed),
    }, 'Two-factor status')


@api_view(['POST'])
def twofactor_begin(request):
    """Start enrolment: mint a secret and hand back what an app can scan.

    The secret is re-shown until it is confirmed, so somebody who closes the
    screen halfway is not locked out of finishing.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    enrolment = UserTOTP.objects.filter(user=user).first()
    if enrolment and enrolment.confirmed:
        return _err('Two-factor is already on for this account.', 'ALREADY_ENABLED')

    if enrolment is None:
        enrolment = UserTOTP.objects.create(user=user, secret=totp_lib.generate_secret())

    return _ok({
        'secret': enrolment.secret,
        'otpauth_url': totp_lib.provisioning_uri(
            enrolment.secret, user.email or user.username, issuer='V-ENT',
        ),
    }, 'Scan this in your authenticator app, then enter a code to confirm.')


@api_view(['POST'])
def twofactor_confirm(request):
    """Prove the app is set up before switching it on.

    Turning 2FA on without a verified code is how people lock themselves out.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    enrolment = UserTOTP.objects.filter(user=user).first()
    if enrolment is None:
        return _err('Start the setup first.', 'NOT_STARTED')

    matched = totp_lib.verify(enrolment.secret, request.data.get('code'), enrolment.last_used_step)
    if matched is None:
        return _err('That code did not match. Check your app and try again.', 'BAD_CODE')

    enrolment.last_used_step = matched
    enrolment.confirmed = True
    enrolment.confirmed_at = timezone.now()
    enrolment.save(update_fields=['last_used_step', 'confirmed', 'confirmed_at'])
    return _ok({'enabled': True}, 'Two-factor authentication is on.')


@api_view(['POST'])
def twofactor_disable(request):
    """Switching it off needs a current code, not just a session.

    A stolen session should not be able to remove the thing protecting the
    account from a stolen session.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    enrolment = UserTOTP.objects.filter(user=user).first()
    if enrolment is None:
        return _ok({'enabled': False}, 'Two-factor was not on.')

    if enrolment.confirmed:
        matched = totp_lib.verify(enrolment.secret, request.data.get('code'),
                                  enrolment.last_used_step)
        if matched is None:
            return _err('Enter a current code from your authenticator to turn this off.',
                        'BAD_CODE')

    enrolment.delete()
    return _ok({'enabled': False}, 'Two-factor authentication is off.')


# ---------------------------------------------------------------------------
# Danger zone
# ---------------------------------------------------------------------------

def _export_payload(user):
    """Everything this account holds, in the shape a person can read."""
    from .models import (
        FavoriteGames, SocialLink, TeamMembers, Transaction, UserGallery,
        UserInterests, UserProfile, UserSetting,
    )
    from vent_tournament.models import TournamentRegistration

    profile = UserProfile.objects.filter(user=user).first()
    wallet = getattr(user, 'wallet', None)
    setting = UserSetting.objects.filter(user=user).first()

    return {
        'exported_at': timezone.now().isoformat(),
        'account': {
            'user_id': user.user_id,
            'username': user.username,
            'full_name': user.full_name,
            'email': user.email,
            'country': user.country,
            'city': user.state,
            'joined': user.date_joined.isoformat() if user.date_joined else None,
            'is_founding_member': user.is_founding_member,
            'founding_position': user.founding_position,
        },
        'profile': {
            'description': profile.description if profile else None,
            'date_of_birth': str(profile.date_of_birth) if profile and profile.date_of_birth else None,
            'penalty_points': profile.penalty_point if profile else 0,
        },
        'settings': (setting.data if setting else {}),
        'interests': list(UserInterests.objects.filter(user=user).values_list('interests', flat=True)),
        'favourite_games': [
            {'game': row.game.game_title, 'gamertag': row.gamertag, 'is_main': row.is_main}
            for row in FavoriteGames.objects.filter(user=user).select_related('game')
        ],
        'teams': [
            {'team': row.team.team_name, 'joined': str(row.join_date), 'captain': row.is_captain}
            for row in TeamMembers.objects.filter(user=user).select_related('team')
        ],
        'tournaments': [
            {
                'tournament': row.tournament.tournament_title,
                'status': row.status,
                'registered_at': row.registered_at.isoformat() if row.registered_at else None,
                'entry_fee_paid': row.entry_fee_paid,
            }
            for row in TournamentRegistration.objects.filter(user=user).select_related('tournament')
        ],
        'wallet': {
            'balance_vc': wallet.wallet_balance if wallet else 0,
            'transactions': [
                {
                    'type': t.type, 'amount': t.amount, 'description': t.description,
                    'status': t.status, 'at': t.created_at.isoformat(),
                }
                for t in Transaction.objects.filter(wallet=wallet).order_by('-created_at')
            ] if wallet else [],
        },
        'social_links': list(SocialLink.objects.filter(user=user).values('title', 'url')),
        'gallery': [
            {'image': img.image.name, 'added': img.date_added.isoformat()}
            for img in UserGallery.objects.filter(user=user)
        ],
        'sign_ins': [
            {
                'at': e.created_at.isoformat(), 'ip': e.ip,
                'where': ', '.join(p for p in [e.city, e.country] if p),
                'method': e.method,
            }
            for e in user.login_events.all()
        ] if hasattr(user, 'login_events') else [],
    }


@api_view(['GET'])
def export_data(request):
    """GET /setting/export/?format=json|xlsx - download everything, now.

    The screen used to promise delivery by email within 24 hours, which meant
    nothing was built. This answers with the file.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    payload = _export_payload(user)
    wanted = (request.GET.get('format') or 'json').lower()
    stamp = timezone.now().strftime('%Y-%m-%d')

    if wanted in ('xlsx', 'excel'):
        workbook = _as_xlsx(payload)
        if workbook is None:
            return _err(
                'A spreadsheet export needs openpyxl on the server. JSON is available now.',
                'XLSX_UNAVAILABLE', status.HTTP_501_NOT_IMPLEMENTED,
            )
        response = HttpResponse(
            workbook,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="v-ent-{user.username}-{stamp}.xlsx"'
        return response

    response = HttpResponse(
        json.dumps(payload, indent=2, default=str), content_type='application/json',
    )
    response['Content-Disposition'] = f'attachment; filename="v-ent-{user.username}-{stamp}.json"'
    return response


def _as_xlsx(payload):
    """One sheet per section. Returns None when openpyxl is not installed."""
    try:
        from openpyxl import Workbook
    except ImportError:
        return None

    workbook = Workbook()
    workbook.remove(workbook.active)

    def sheet_for(name, rows):
        sheet = workbook.create_sheet(title=name[:31])
        if not rows:
            sheet.append(['Nothing recorded'])
            return
        headers = list(rows[0].keys())
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(h) for h in headers])

    sheet_for('Account', [payload['account']])
    sheet_for('Profile', [payload['profile']])
    sheet_for('Favourite games', payload['favourite_games'])
    sheet_for('Teams', payload['teams'])
    sheet_for('Tournaments', payload['tournaments'])
    sheet_for('Transactions', payload['wallet']['transactions'])
    sheet_for('Sign-ins', payload['sign_ins'])
    sheet_for('Social links', payload['social_links'])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@api_view(['POST'])
def deactivate_account(request):
    """Hide the account until the person signs in again.

    Deactivating is reversible by design, so nothing is deleted: the account is
    marked hidden, the session ends, and signing back in undoes it.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    user.is_deactivated = True
    user.deactivated_at = timezone.now()
    user.login_session_token = None
    user.login_session_created_at = None
    user.save(update_fields=[
        'is_deactivated', 'deactivated_at', 'login_session_token', 'login_session_created_at',
    ])
    return _ok({'deactivated': True},
               'Your account is hidden. Sign in again at any time to bring it back.')


@api_view(['POST'])
def delete_account(request):
    """Start the grace period the screen promises.

    A soft delete: the account is scheduled, hidden immediately, and signing
    back in during the grace period cancels it. Nothing is destroyed here -
    there are tournament results, disputes and wallet history attached to this
    person that other people's records depend on.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    if not request.data.get('confirm'):
        return _err('Send confirm to start the deletion.', 'CONFIRM_REQUIRED')

    with transaction.atomic():
        user.is_deactivated = True
        user.deactivated_at = timezone.now()
        user.deletion_requested_at = timezone.now()
        user.login_session_token = None
        user.login_session_created_at = None
        user.save(update_fields=[
            'is_deactivated', 'deactivated_at', 'deletion_requested_at',
            'login_session_token', 'login_session_created_at',
        ])

    ends = user.deletion_requested_at + timedelta(days=DELETE_GRACE_DAYS)
    return _ok(
        {'deletion_scheduled_for': ends.isoformat(), 'grace_days': DELETE_GRACE_DAYS},
        f'Your account will be deleted on {ends:%d %B %Y}. Sign in before then to cancel it.',
    )


@api_view(['POST'])
def cancel_deletion(request):
    """Undo a scheduled deletion, which signing in also does."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    user.is_deactivated = False
    user.deactivated_at = None
    user.deletion_requested_at = None
    user.save(update_fields=['is_deactivated', 'deactivated_at', 'deletion_requested_at'])
    return _ok({'deletion_scheduled_for': None}, 'Your account is active again.')


# ---------------------------------------------------------------------------
# The founder badge
# ---------------------------------------------------------------------------

@api_view(['POST'])
def founder_badge(request):
    """POST /setting/founder-badge/ {show: bool} - wear it or do not.

    Only a founder can switch this; for anybody else it is not a setting, it is
    a claim, and the endpoint says so rather than storing something that will
    never render.
    """
    user, err = _user_from_bearer(request)
    if err:
        return err

    if not getattr(user, 'is_founder', False):
        return _err('This is only available to founding members of V-ENT.', 'NOT_A_FOUNDER',
                    status.HTTP_403_FORBIDDEN)

    user.show_founder_badge = bool(request.data.get('show', True))
    user.save(update_fields=['show_founder_badge'])
    return _ok(
        {'is_founder': True, 'show_founder_badge': user.show_founder_badge},
        'Badge on.' if user.show_founder_badge else 'Badge off.',
    )

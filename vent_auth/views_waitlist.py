"""Claiming a pre-launch waitlist reservation.

The waitlist never stored a password - the site's join endpoint passed
`password_hash: null` for all 102 rows - so there is nothing to migrate and no
way for a reserver to "log in with what they saved". What they reserved was a
username.

So claiming is not a login, it is a first-time account setup with two things
already settled: the address is proven by the token in the email, and the
username is held for them. They choose a password and they are in.

Two endpoints:

  GET  /auth/waitlist/claim/<token>/   what this token is worth, for the page
  POST /auth/waitlist/claim/          set a password, get an account

The GET is deliberately readable without auth - the token *is* the credential -
but it returns only what the claim page has to render, never the whole row.
"""
import logging

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, UserProfile, UserWallet, WaitlistReservation, Transaction
from . import emails
from .views_helpers import (
    generate_session_token,
    create_default_profile_picture,
    create_user_wallet,
)

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8


def _reservation_payload(reservation):
    """Only what the claim page draws. Not the whole row."""
    return {
        "email": reservation.email,
        "username": reservation.username,
        "display_name": reservation.display_name or reservation.username or "",
        "position": reservation.position,
        "game": reservation.game,
        # A reservation with no username (4 of the 102) means the page has to
        # ask for one instead of showing it as already settled.
        "username_reserved": bool(reservation.username),
    }


@api_view(['GET'])
def waitlist_claim_preview(request, token):
    """What this claim link is worth. Drives the page before anything is typed."""
    reservation = WaitlistReservation.objects.filter(claim_token=token).first()

    if reservation is None:
        # Covers both a made-up token and one already spent, and says so without
        # letting the caller tell those apart by probing.
        return Response({
            "status": "error",
            "message": "This claim link is not valid. It may already have been used.",
            "code": "CLAIM_TOKEN_INVALID",
        }, status=status.HTTP_404_NOT_FOUND)

    if reservation.is_claimed:
        return Response({
            "status": "error",
            "message": "This reservation has already been claimed. Please log in instead.",
            "code": "ALREADY_CLAIMED",
        }, status=status.HTTP_409_CONFLICT)

    return Response({
        "status": "success",
        "data": _reservation_payload(reservation),
        "message": "Reservation found",
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def waitlist_claim(request):
    """Turn a reservation into a real, active, signed-in account."""
    token = (request.data.get('token') or '').strip()
    password = request.data.get('password') or ''
    # Only read for the handful of reservations that never picked a username.
    requested_username = (request.data.get('username') or '').strip().lower()

    if not token or not password:
        return Response({
            "status": "error",
            "message": "Claim token and password are required",
        }, status=status.HTTP_400_BAD_REQUEST)

    if len(password) < MIN_PASSWORD_LENGTH:
        return Response({
            "status": "error",
            "message": f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
            "code": "PASSWORD_TOO_SHORT",
        }, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # Locked for the whole claim: the token is single use, and two taps on a
        # slow phone must not create two accounts or spend the reservation twice.
        reservation = (WaitlistReservation.objects
                       .select_for_update()
                       .filter(claim_token=token)
                       .first())

        if reservation is None:
            return Response({
                "status": "error",
                "message": "This claim link is not valid. It may already have been used.",
                "code": "CLAIM_TOKEN_INVALID",
            }, status=status.HTTP_404_NOT_FOUND)

        if reservation.is_claimed:
            return Response({
                "status": "error",
                "message": "This reservation has already been claimed. Please log in instead.",
                "code": "ALREADY_CLAIMED",
            }, status=status.HTTP_409_CONFLICT)

        username = reservation.username or requested_username
        if not username:
            return Response({
                "status": "error",
                "message": "Choose a username to finish claiming your account",
                "code": "USERNAME_REQUIRED",
            }, status=status.HTTP_400_BAD_REQUEST)

        # An account can already exist on this address. The common case is
        # somebody who was on the waitlist, then tried to sign up while the
        # verification link was broken and is now sitting inactive. Claiming
        # should rescue that account, not collide with it.
        user = Users.objects.filter(email__iexact=reservation.email).first()

        taken_by_somebody_else = (Users.objects
                                  .filter(username__iexact=username)
                                  .exclude(pk=user.pk if user else None)
                                  .exists())
        if taken_by_somebody_else:
            return Response({
                "status": "error",
                "message": "That username has been taken. Please choose another.",
                "code": "USERNAME_TAKEN",
            }, status=status.HTTP_409_CONFLICT)

        if user is None:
            user = Users(
                email=reservation.email,
                username=username,
                full_name=reservation.display_name or username,
                country=reservation.country or None,
                signup_type='normal',
            )

        user.username = username
        user.password = make_password(password)
        # The token arrived in their inbox, which is the same proof the
        # verification link provides. Asking them to verify again would be
        # asking them to prove the thing they just proved.
        user.is_active = True
        user.is_founding_member = True
        user.founding_position = reservation.position
        if not user.full_name:
            user.full_name = reservation.display_name or username
        if reservation.country and not user.country:
            user.country = reservation.country
        user.login_session_token = generate_session_token()
        user.login_session_created_at = timezone.now()
        user.save()

        # Same tail as verify_token_3: an account without a profile picture or a
        # wallet breaks /home, /user-profile and every paid flow.
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.profile_picture:
            picture = create_default_profile_picture(user.full_name)
            profile.profile_picture.save(f"{user.username}_profile.png", File(picture))
            profile.save()

        wallet = UserWallet.objects.filter(user=user).first()
        if wallet is None:
            create_user_wallet(user=user)
            wallet = UserWallet.objects.filter(user=user).first()

        # 0 today, by decision - see WAITLIST_CLAIM_BONUS_VC in settings. The
        # branch is here so turning it on later is a config change, not a code
        # change, and so nothing is credited that the email did not promise.
        bonus = int(getattr(settings, 'WAITLIST_CLAIM_BONUS_VC', 0) or 0)
        if bonus > 0 and wallet is not None:
            locked = UserWallet.objects.select_for_update().get(pk=wallet.pk)
            locked.wallet_balance += bonus
            locked.save(update_fields=['wallet_balance'])
            Transaction.objects.create(
                wallet=locked,
                type='top_up',
                amount=bonus,
                description='Founding member bonus',
                status='completed',
            )

        reservation.claimed_at = timezone.now()
        reservation.claimed_user = user
        reservation.claim_token = None  # single use
        reservation.save(update_fields=['claimed_at', 'claimed_user', 'claim_token'])

    # Outside the transaction: a mail relay hiccup must not undo a claim.
    try:
        emails.send_welcome(user.email, name=user.full_name or user.username)
    except Exception:
        logger.exception('welcome email failed for claimed reservation %s', reservation.pk)

    logger.info('waitlist reservation %s claimed by user %s', reservation.pk, user.pk)

    return Response({
        "status": "success",
        "data": {
            "username": user.username,
            "email": user.email,
            "session_token": user.login_session_token,
            "founding_position": user.founding_position,
        },
        "message": "Account claimed successfully",
    }, status=status.HTTP_201_CREATED)

import pathlib
"""Tournament lifecycle endpoints: bracket generation, participant-driven match
scoring (report/confirm), disputes, prize distribution, and organizer cancel.

Auth: Bearer `login_session_token` (16-char), 120-minute expiry - same pattern as
the rest of vent_tournament. Envelope: {status, data, message[, code]}.
"""
from datetime import timedelta
from vent_auth.views_helpers import session_timeout_minutes

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    Tournament, TournamentRegistration, BracketMatch, TournamentDispute, MatchScore,
)
from .services import bracket as bracket_service
from .services import prizes as prize_service
from .services import wallet as wallet_service
from vent_auth.models import Users

from . import lookup

SESSION_TIMEOUT = timedelta(minutes=session_timeout_minutes())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(data=None, message='', code=None, http_status=http.HTTP_200_OK):
    body = {'status': 'success', 'data': data if data is not None else {}, 'message': message}
    return Response(body, status=http_status)


def _err(message, code, http_status, field_errors=None):
    data = {}
    if field_errors:
        data['field_errors'] = field_errors
    return Response(
        {'status': 'error', 'data': data, 'message': message, 'code': code},
        status=http_status,
    )


def _authenticate(request):
    """Return (user, None) or (None, error_response)."""
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _err('Authorization header is required', 'UNAUTHORIZED', http.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    if not token:
        return None, _err('Authorization header is required', 'UNAUTHORIZED', http.HTTP_401_UNAUTHORIZED)
    user = Users.objects.filter(login_session_token=token).first()
    if user is None:
        return None, _err('Invalid session token', 'UNAUTHORIZED', http.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > SESSION_TIMEOUT:
        return None, _err('Session token has expired', 'SESSION_EXPIRED', http.HTTP_401_UNAUTHORIZED)
    return user, None


def _match_number_label(match):
    return {'round_number': match.round_number, 'match_number': match.match_number,
            'bracket_side': match.bracket_side}


def _participant_brief(reg):
    if reg is None:
        return None
    if reg.team_id:
        return {'registration_id': reg.id, 'type': 'team', 'id': reg.team_id, 'name': reg.team.team_name}
    if reg.user_id:
        return {'registration_id': reg.id, 'type': 'user', 'id': reg.user_id, 'name': reg.user.username}
    return {'registration_id': reg.id, 'type': 'unknown', 'name': None}


def _notify_dispute_raised(tournament):
    """Fire-and-forget: tell the tournament organizer a dispute was opened.
    Never breaks the dispute flow if the notification insert fails."""
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(
            tournament.tournament_creator_id, 'dispute',
            f'New dispute on {tournament.tournament_title}',
            link='/admin/disputes',
            metadata={'tournament_id': tournament.tournament_id},
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# N1 - Generate bracket
# ---------------------------------------------------------------------------

# Kept in step with the frontend's uploadSpecs: PNG, JPG or WebP, up to 5 MB.
EVIDENCE_MAX_BYTES = 5 * 1024 * 1024
EVIDENCE_TYPES = {'image/png', 'image/jpeg', 'image/webp'}


def _check_evidence_file(upload):
    """None when the file is fine, otherwise the sentence to send back."""
    if upload.content_type and upload.content_type not in EVIDENCE_TYPES:
        return 'The screenshot must be a PNG, JPG or WebP image.'
    if upload.size > EVIDENCE_MAX_BYTES:
        return 'The screenshot must be 5 MB or smaller.'
    return None


def _store_evidence(upload):
    """Save the screenshot and return the URL that will be stored on the score.

    Everything downstream reads `evidence_url`, so an uploaded file becomes a
    URL here and nothing else has to change.
    """
    import uuid as _uuid

    from django.conf import settings
    from django.core.files.storage import default_storage

    suffix = pathlib.Path(upload.name or '').suffix.lower() or '.png'
    name = 'match_evidence/%s%s' % (_uuid.uuid4().hex, suffix)
    saved = default_storage.save(name, upload)
    return '%s%s' % (settings.MEDIA_URL, saved)


@api_view(['POST'])
def generate_bracket(request, tournament_id):
    """POST /tournament/<id>/generate-bracket/ - organizer closes registration and
    builds the bracket tree."""
    user, err = _authenticate(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)

    if tournament.tournament_creator_id != user.user_id:
        return _err('Only the tournament organizer can generate the bracket', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)
    if tournament.is_draft:
        return _err('Publish the tournament before generating a bracket', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)
    if tournament.status in ('completed', 'cancelled'):
        return _err(f'Cannot generate a bracket for a {tournament.status} tournament', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    # A check-in window that nobody closes is a check-in window that does
    # nothing. If entrants have not checked in and the window has passed, say
    # so rather than seeding a bracket full of people who are not there.
    from . import options as tournament_options
    window = tournament_options.check_in_state(tournament, timezone.now())
    if window and window['closed'] and window['forfeit_without_check_in']:
        missing = tournament.registrations.filter(
            status__in=('pending', 'confirmed'), checked_in_at__isnull=True,
        ).count()
        if missing and not request.data.get('ignore_check_in'):
            return _err(
                f'{missing} entrants never checked in. Close check-in first so they are '
                'forfeited, or send ignore_check_in to seed them anyway.',
                'CHECK_IN_OPEN', http.HTTP_409_CONFLICT,
            )

    # The organiser already chose a seeding method when they built the
    # tournament. Honour it, and let an explicit request override it.
    stored = tournament_options.clean(tournament.options)['seeding_method']
    seed_strategy = request.data.get('seed_strategy') or request.data.get('seeding') or stored
    if seed_strategy == 'seed_field':
        seed_strategy = 'ranked'
    manual_order = request.data.get('manual_order')

    try:
        with transaction.atomic():
            locked = Tournament.objects.select_for_update().get(pk=tournament.pk)
            # Closing registration is implicit: the bracket freezes the field.
            locked.status = 'registration_closed'
            locked.save(update_fields=['status'])
            summary = bracket_service.generate(
                locked, generated_by=user,
                seed_strategy=seed_strategy, manual_order=manual_order,
            )
    except bracket_service.BracketError as e:
        code_map = {
            'bracket_already_generated': http.HTTP_409_CONFLICT,
            'not_enough_participants': http.HTTP_422_UNPROCESSABLE_ENTITY,
        }
        return _err(e.message, e.code.upper(), code_map.get(e.code, http.HTTP_400_BAD_REQUEST))

    return _ok(summary, 'Bracket generated.', http_status=http.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# N6 - Report score (participant)
# ---------------------------------------------------------------------------

@api_view(['POST'])
def report_match_score(request, match_id):
    """POST /tournament/match/<id>/report-score/ - a participant reports the result."""
    user, err = _authenticate(request)
    if err:
        return err

    match = get_object_or_404(BracketMatch.objects.select_related('tournament', 'participant_1', 'participant_2'), id=match_id)
    tournament = match.tournament

    if tournament.score_confirmation_mode == 'organizer_only':
        return _err('This tournament records results via the organizer', 'ORGANIZER_ONLY_MODE', http.HTTP_409_CONFLICT)

    slot = match.participant_owned_by(user)
    if slot is None:
        return _err('You are not a participant in this match', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)

    if match.status in ('completed', 'bye'):
        return _err('This match is already finished', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)
    if match.status == 'disputed':
        return _err('This match is under dispute', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    score_p1 = request.data.get('score_p1')
    score_p2 = request.data.get('score_p2')
    evidence_url = (request.data.get('screenshot_url') or request.data.get('evidence_url') or '').strip()

    # An uploaded screenshot beats a pasted link: the player has the picture on
    # the device they just played on, and a link they host themselves is the one
    # that stops resolving before the dispute it was meant to settle is read.
    upload = request.FILES.get('screenshot') or request.FILES.get('evidence')
    if upload is not None:
        problem = _check_evidence_file(upload)
        if problem:
            return _err(problem, 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST,
                        field_errors={'screenshot': [problem]})
        evidence_url = _store_evidence(upload)

    if score_p1 is None or score_p2 is None:
        return _err('score_p1 and score_p2 are required', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST)
    try:
        score_p1, score_p2 = int(score_p1), int(score_p2)
    except (TypeError, ValueError):
        return _err('Scores must be integers', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST)
    if score_p1 < 0 or score_p2 < 0:
        return _err('Scores cannot be negative', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST)
    if tournament.score_confirmation_mode == 'screenshot_required' and not evidence_url:
        return _err('A screenshot URL is required for this tournament', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST,
                    field_errors={'screenshot_url': ['required']})

    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        # Supersede any of this reporter's earlier unconfirmed submissions.
        submission = MatchScore.objects.create(
            match=locked, submitted_by=user,
            score_p1=score_p1, score_p2=score_p2, evidence_url=evidence_url,
        )
        (MatchScore.objects
            .filter(match=locked, confirmed=False, superseded_by__isnull=True)
            .exclude(pk=submission.pk)
            .update(superseded_by=submission))

        locked.score_p1 = score_p1
        locked.score_p2 = score_p2
        locked.status = 'pending_opponent_confirm'
        locked.save(update_fields=['score_p1', 'score_p2', 'status'])

    return _ok({
        'match_id': locked.id,
        'score_submission_id': submission.id,
        'status': 'pending_opponent_confirm',
        'awaiting_confirmation': True,
    }, 'Score reported. Awaiting opponent confirmation.')


# ---------------------------------------------------------------------------
# N7 - Confirm score (opponent)
# ---------------------------------------------------------------------------

@api_view(['POST'])
def confirm_match_score(request, match_id):
    """POST /tournament/match/<id>/confirm-score/ - opponent agrees or rejects."""
    user, err = _authenticate(request)
    if err:
        return err

    match = get_object_or_404(BracketMatch.objects.select_related('tournament', 'participant_1', 'participant_2'), id=match_id)
    tournament = match.tournament

    if tournament.score_confirmation_mode == 'organizer_only':
        return _err('This tournament records results via the organizer', 'ORGANIZER_ONLY_MODE', http.HTTP_409_CONFLICT)
    if match.status != 'pending_opponent_confirm':
        return _err('No score is awaiting confirmation on this match', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    slot = match.participant_owned_by(user)
    if slot is None:
        return _err('You are not a participant in this match', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)

    submission = (
        MatchScore.objects.filter(match=match, confirmed=False, superseded_by__isnull=True)
        .order_by('-submitted_at').first()
    )
    if submission is None:
        return _err('No pending score submission found', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)
    if submission.submitted_by_id == user.user_id:
        return _err('The opponent must confirm the score you reported', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)

    agree = request.data.get('agree')
    if isinstance(agree, str):
        agree = agree.lower() in ('1', 'true', 'yes')

    if not agree:
        description = (request.data.get('dispute_description') or request.data.get('description') or '').strip()
        if not description:
            return _err('A reason is required when rejecting the score', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST,
                        field_errors={'dispute_description': ['required']})
        with transaction.atomic():
            locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
            dispute = TournamentDispute.objects.create(
                tournament=tournament, match=locked, raised_by=user,
                description=description[:500],
                evidence=[submission.evidence_url] if submission.evidence_url else [],
                status='open',
            )
            locked.status = 'disputed'
            locked.save(update_fields=['status'])
        _notify_dispute_raised(tournament)
        return _ok({'match_id': match.id, 'status': 'disputed', 'dispute_id': dispute.id},
                   'Score rejected - dispute opened.')

    # Agree: decide the winner from the reported score.
    if submission.score_p1 == submission.score_p2:
        return _err('A tie cannot be confirmed for a bracket match', 'VALIDATION_FAILED', http.HTTP_422_UNPROCESSABLE_ENTITY)
    winner = match.participant_1 if submission.score_p1 > submission.score_p2 else match.participant_2
    if winner is None:
        return _err('Match participants are not fully set', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        locked.score_p1 = submission.score_p1
        locked.score_p2 = submission.score_p2
        locked.winner = winner
        locked.status = 'completed'
        locked.completed_at = timezone.now()
        # Saving to 'completed' fires the post_save signal -> auto-advance cascade.
        locked.save(update_fields=['score_p1', 'score_p2', 'winner', 'status', 'completed_at'])

        submission.confirmed = True
        submission.confirmed_by = user
        submission.confirmed_at = timezone.now()
        submission.save(update_fields=['confirmed', 'confirmed_by', 'confirmed_at'])

    advanced_to = None
    if locked.winner_to_match_id:
        nxt = BracketMatch.objects.filter(pk=locked.winner_to_match_id).first()
        if nxt:
            advanced_to = _match_number_label(nxt)

    tournament.refresh_from_db(fields=['status', 'completed_at'])
    return _ok({
        'match_id': locked.id,
        'status': 'completed',
        'winner_registration_id': winner.id,
        'advanced_to': advanced_to,
        'tournament_completed': tournament.completed_at is not None,
    }, 'Score confirmed.')


# ---------------------------------------------------------------------------
# N3 - Raise dispute (participant)
# ---------------------------------------------------------------------------

@api_view(['POST'])
def raise_dispute(request, match_id):
    """POST /tournament/match/<id>/raise-dispute/ - a participant files a dispute."""
    user, err = _authenticate(request)
    if err:
        return err

    match = get_object_or_404(BracketMatch.objects.select_related('tournament'), id=match_id)
    tournament = match.tournament

    slot = match.participant_owned_by(user)
    if slot is None:
        return _err('You are not a participant in this match', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)

    if match.status == 'bye':
        return _err('A walkover match cannot be disputed', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)
    if match.status == 'completed' and match.completed_at and timezone.now() - match.completed_at > timedelta(hours=24):
        return _err('The dispute window (24h) has closed for this match', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    description = (request.data.get('description') or '').strip()
    if not description:
        return _err('A description is required', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST,
                    field_errors={'description': ['required']})
    if len(description) > 500:
        return _err('Description must be 500 characters or fewer', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST,
                    field_errors={'description': ['max 500 characters']})

    evidence_urls = request.data.get('evidence_urls') or []
    if not isinstance(evidence_urls, list):
        return _err('evidence_urls must be a list', 'VALIDATION_FAILED', http.HTTP_400_BAD_REQUEST)
    evidence_urls = [str(u) for u in evidence_urls[:5]]

    if TournamentDispute.objects.filter(match=match, raised_by=user, status__in=('open', 'under_review')).exists():
        return _err('You already have an open dispute on this match', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        dispute = TournamentDispute.objects.create(
            tournament=tournament, match=locked, raised_by=user,
            description=description, evidence=evidence_urls, status='open',
        )
        # Pause advancement for this match until an admin resolves it.
        if locked.status not in ('completed', 'bye'):
            locked.status = 'disputed'
            locked.save(update_fields=['status'])

    _notify_dispute_raised(tournament)

    return _ok({'dispute_id': dispute.id, 'status': 'open', 'created_at': dispute.created_at},
               'Dispute filed.', http_status=http.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# N5 - Match detail
# ---------------------------------------------------------------------------

@api_view(['GET'])
def match_detail(request, match_id):
    """GET /tournament/match/<id>/ - match state + score submission history."""
    user, err = _authenticate(request)
    if err:
        return err

    match = get_object_or_404(
        BracketMatch.objects.select_related(
            'tournament', 'participant_1__user', 'participant_1__team',
            'participant_2__user', 'participant_2__team', 'winner',
        ),
        id=match_id,
    )
    is_creator = match.tournament.tournament_creator_id == user.user_id
    if match.participant_owned_by(user) is None and not is_creator:
        return _err('You cannot view this match', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)

    submissions = [
        {
            'id': s.id,
            'submitted_by': s.submitted_by_id,
            'score_p1': s.score_p1,
            'score_p2': s.score_p2,
            'evidence_url': s.evidence_url,
            'confirmed': s.confirmed,
            'submitted_at': s.submitted_at,
        }
        for s in match.score_submissions.all()
    ]

    return _ok({
        'match_id': match.id,
        'round_number': match.round_number,
        'match_number': match.match_number,
        'bracket_side': match.bracket_side,
        'status': match.status,
        'score_p1': match.score_p1,
        'score_p2': match.score_p2,
        'participant_1': _participant_brief(match.participant_1),
        'participant_2': _participant_brief(match.participant_2),
        'winner_registration_id': match.winner_id,
        'scheduled_at': match.scheduled_at,
        'completed_at': match.completed_at,
        'score_submissions': submissions,
    })


# ---------------------------------------------------------------------------
# N8 - Distribute prizes
# ---------------------------------------------------------------------------

@api_view(['POST'])
def distribute_prizes(request, tournament_id):
    """POST /tournament/<id>/distribute-prizes/ - organizer or staff pays winners."""
    user, err = _authenticate(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    is_creator = tournament.tournament_creator_id == user.user_id
    if not is_creator and not user.is_staff:
        return _err('Only the organizer or an admin can distribute prizes', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)

    force_recompute = bool(request.data.get('force_recompute')) and user.is_staff

    try:
        distributions = prize_service.distribute(
            tournament, triggered_by=user, auto=False, force_recompute=force_recompute,
        )
    except prize_service.PrizeError as e:
        code_map = {
            'tournament_not_completed': http.HTTP_409_CONFLICT,
            'no_prize_configured': http.HTTP_409_CONFLICT,
            'prize_distribution_missing': http.HTTP_409_CONFLICT,
            'already_distributed': http.HTTP_409_CONFLICT,
            'winner_wallet_missing': http.HTTP_422_UNPROCESSABLE_ENTITY,
        }
        return _err(e.message, e.code.upper(), code_map.get(e.code, http.HTTP_400_BAD_REQUEST))

    return _ok(
        {'tournament_id': tournament.tournament_id, 'distributions': distributions},
        f'{len(distributions)} prize position(s) processed.',
    )


# ---------------------------------------------------------------------------
# Organizer cancel + refund
# ---------------------------------------------------------------------------

@api_view(['POST'])
def cancel_tournament(request, tournament_id):
    """POST /tournament/<id>/cancel/ - organizer cancels + refunds entry fees."""
    user, err = _authenticate(request)
    if err:
        return err

    tournament = lookup.find(tournament_id)
    if tournament.tournament_creator_id != user.user_id:
        return _err('Only the organizer can cancel this tournament', 'FORBIDDEN', http.HTTP_403_FORBIDDEN)
    if tournament.status in ('completed', 'cancelled'):
        return _err(f'Tournament is already {tournament.status}', 'STATE_CONFLICT', http.HTTP_409_CONFLICT)
    if tournament.bracket_matches.filter(status='completed').exists():
        return _err('Matches have already been played - an admin must cancel this tournament',
                    'STATE_CONFLICT', http.HTTP_409_CONFLICT)

    reason = (request.data.get('reason') or '').strip()[:500]
    entry_fee_coins = int(tournament.entry_fee_price) if tournament.entry_fee == 'Paid' else 0

    refunded_count = 0
    total_refunded = 0
    with transaction.atomic():
        locked = Tournament.objects.select_for_update().get(pk=tournament.pk)
        if entry_fee_coins > 0:
            regs = list(locked.registrations
                        .filter(status='confirmed', entry_fee_paid=True)
                        .select_related('user', 'team'))
            # Lock every recipient wallet up front in PK order (deadlock avoidance).
            locked_wallets = wallet_service.lock_wallets_for_registrations(regs)
            for reg in regs:
                wallet = wallet_service.wallet_for_registration(reg, locked_wallets)
                if wallet is None:
                    continue
                wallet_service.credit(
                    wallet, entry_fee_coins,
                    tx_type='refund',
                    description=f'Refund - cancelled tournament: {locked.tournament_title}',
                    tournament=locked,
                )
                reg.status = 'withdrawn'
                reg.entry_fee_paid = False
                reg.save(update_fields=['status', 'entry_fee_paid'])
                refunded_count += 1
                total_refunded += entry_fee_coins
        locked.registrations.filter(status='confirmed').update(status='withdrawn')
        locked.status = 'cancelled'
        locked.cancelled_at = timezone.now()
        locked.cancelled_reason = reason
        locked.save(update_fields=['status', 'cancelled_at', 'cancelled_reason'])

    return _ok({
        'tournament_id': tournament.tournament_id,
        'refunded_count': refunded_count,
        'total_refunded': total_refunded,
    }, f'Tournament cancelled. {refunded_count} registration(s) refunded.')


# ---------------------------------------------------------------------------
# §2.2 - My disputes (user)
# ---------------------------------------------------------------------------

@api_view(['GET'])
def my_disputes(request):
    """GET /tournament/my-disputes/ - every dispute the caller raised, newest first."""
    user, err = _authenticate(request)
    if err:
        return err

    disputes = (
        TournamentDispute.objects
        .filter(raised_by=user)
        .select_related('tournament', 'match')
        .order_by('-created_at')
    )

    rows = [
        {
            'dispute_id': d.id,
            'tournament_id': d.tournament_id,
            'tournament_title': d.tournament.tournament_title if d.tournament else None,
            'match_id': d.match_id,
            'round_number': d.match.round_number if d.match else None,
            'match_number': d.match.match_number if d.match else None,
            'description': d.description,
            'status': d.status,
            'resolution_note': d.resolution_note,
            'created_at': d.created_at,
            'resolved_at': d.resolved_at,
        }
        for d in disputes
    ]

    return _ok({'disputes': rows}, 'Disputes retrieved.')

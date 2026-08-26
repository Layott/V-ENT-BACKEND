import json
from vent_auth.views_helpers import session_timeout_minutes
from django.http import Http404
from datetime import timedelta
from django.shortcuts import render
from imports import api_view,get_object_or_404, Response, status, transaction
from .models import (
    Tournament, Users, Games, Teams,
    TournamentPrizeDistribution, TournamentRegistration, BracketMatch,
    Sponsors, Match, RegisteredTeams,
)
from django.db.models import Q
from django.db import transaction

from rest_framework.decorators import permission_classes
from rest_framework.permissions import AllowAny

from django.db import transaction as db_transaction
from .money import CURRENCIES, from_coins, rates, to_coins
from . import options as tournament_options
from vent_auth.models import Organization
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.db.models import Prefetch



# Bracket format has been written three different ways ("Single Elimination",
# "single-elimination", "single_elimination"), which broke every format filter.
# One canonical slug from here on.
BRACKET_FORMATS = {
    'single_elimination': 'single_elimination',
    'double_elimination': 'double_elimination',
    'round_robin': 'round_robin',
    'swiss': 'swiss',
    # The wizard has offered Battle Royale and Swiss System since it was built,
    # and neither slug was listed here - so normalize_bracket_type quietly
    # returned the default and a battle royale was created, saved and displayed
    # as a single elimination bracket.
    'battle_royale': 'battle_royale',
    'swiss_system': 'swiss',
    'free_for_all': 'battle_royale',
}




def normalize_bracket_type(value, default='single_elimination'):
    slug = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    return BRACKET_FORMATS.get(slug, default)


# What each format is called when a person reads it.
BRACKET_LABELS = {
    'single_elimination': 'Single Elimination',
    'double_elimination': 'Double Elimination',
    'round_robin': 'Round Robin',
    'swiss': 'Swiss System',
    'battle_royale': 'Battle Royale',
}


def bracket_label(value):
    return BRACKET_LABELS.get(normalize_bracket_type(value), 'Single Elimination')

def _card_lookups(tournaments):
    """Bulk-compute the per-tournament numbers the listing cards need.

    Done in two queries for the whole page instead of two per tournament -
    the old per-tournament serializer was already N+1 and the cards are the
    hottest read on the platform.
    """
    ids = [t.tournament_id for t in tournaments]
    if not ids:
        return {}, {}

    from django.db.models import Count, Sum

    counts = {
        row['tournament']: row['n']
        for row in TournamentRegistration.objects
        .filter(tournament_id__in=ids, status='confirmed')
        .values('tournament').annotate(n=Count('id'))
    }
    prizes = {
        row['tournament']: int(row['total'] or 0)
        for row in TournamentPrizeDistribution.objects
        .filter(tournament_id__in=ids)
        .values('tournament').annotate(total=Sum('prize'))
    }
    return counts, prizes


def serialize_tournament_card(t, confirmed_count=0, prize_pool=0):
    """The canonical card contract the frontend listing/search/home cards read.

    The FE card reads `id / name / game / status / format / prize_pool /
    start_date / end_date / current_participants / max_participants /
    participant_type / banner`. The legacy `tournament_*` keys are kept
    alongside so older consumers (and the detail page fallbacks) keep working.
    """
    game_title = t.tournament_game.game_title if t.tournament_game else None
    access = (t.tournament_access or '').lower()
    return {
        # --- card contract ---
        "id": t.tournament_id,
        "name": t.tournament_title,
        "title": t.tournament_title,
        "game": game_title,
        "game_mode": t.game_mode,
        "status": t.status,
        "is_draft": t.is_draft,
        "format": t.bracket_type,
        "format_label": bracket_label(t.bracket_type),
        "prize_type": t.prize_type,
        "prize_pool": prize_pool,
        "current_participants": confirmed_count,
        "max_participants": t.max_number_of_teams or t.player_size,
        "participant_type": "team" if access.startswith("team") else "individual",
        "start_date": t.start_date_and_time,
        "end_date": t.end_date_and_time,
        "banner": t.tournament_banner.url if t.tournament_banner else None,
        "logo": t.tournament_logo.url if t.tournament_logo else None,
        "location": t.tournament_location,
        "entry_fee_vc": int(t.entry_fee_price or 0),
        # --- legacy keys ---
        "tournament_id": t.tournament_id,
        "slug": t.slug,
        "tournament_title": t.tournament_title,
        "tournament_logo": t.tournament_logo.url if t.tournament_logo else None,
        "tournament_banner": t.tournament_banner.url if t.tournament_banner else None,
        "tournament_description": t.tournament_description,
        "bracket_type": t.bracket_type,
        "format_label": bracket_label(t.bracket_type),
        "start_date_and_time": t.start_date_and_time,
        "end_date_and_time": t.end_date_and_time,
        "tournament_visibility": t.tournament_visibility,
        "tournament_type": t.tournament_type,
        "tournament_location": t.tournament_location,
        "player_size": t.player_size,
        "max_number_of_teams": t.max_number_of_teams,
        "min_number_of_teams": t.min_number_of_teams,
        "tournament_access": t.tournament_access,
        "entry_fee": t.entry_fee,
        "entry_fee_price": str(t.entry_fee_price),
        # The organiser settings, on the card as well as the detail page.
        #
        # One model per thing means one shape per thing: a field that decides
        # whether somebody can enter, or whether they will be forfeited for not
        # checking in, belongs everywhere a tournament is shown. Putting it only
        # on the detail payload is how a card ends up quietly describing a
        # different tournament from the page it links to.
        "options": tournament_options.clean(t.options),
        "check_in": _check_in_summary(t),
    }


def _parse_sponsor_list(data, key):
    """Read a sponsor list field from create-tournament payloads.

    The create wizard sends sponsor_names / sponsor_types / sponsor_usernames as
    JSON-stringified arrays inside multipart FormData. Be tolerant of: a plain
    list (JSON body), a JSON string, repeated multipart fields, or a bare scalar.
    """
    raw = data.get(key)
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            # Not JSON - could be repeated multipart fields under the same key.
            if hasattr(data, 'getlist'):
                values = data.getlist(key)
                return list(values) if len(values) > 1 else [text]
            return [text]
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    # Missing/None - still allow repeated multipart fields.
    if hasattr(data, 'getlist'):
        values = data.getlist(key)
        if len(values) > 1:
            return list(values)
    return []


# Create your views here.

# @api_view(['POST'])
# def create_tournament(request):
#     try:
#         # Get data from request
#         tournament_name = request.data.get('tournament_name')
#         tournament_desc = request.data.get('tournament_desc')
#         creator_id = request.data.get('creator_id')  # You may want to use login session token here
#         tournament_game_id = request.data.get('tournament_game_id')
#         reg_start_date = request.data.get('reg_start_date')
#         reg_end_date = request.data.get('reg_end_date')
#         tournament_start_date = request.data.get('tournament_start_date')
#         tournament_end_date = request.data.get('tournament_end_date')
#         tournament_format = request.data.get('tournament_format')
#         tournament_status = request.data.get('tournament_status', 'upcoming')
#         tournament_location = request.data.get('tournament_location')
#         tournament_entry_fee = request.data.get('tournament_entry_fee')
#         tournament_prize = request.data.get('tournament_prize')

#         # Validate required fields
#         if not all([tournament_name, tournament_desc, creator_id, tournament_game_id, reg_start_date, reg_end_date, tournament_start_date, tournament_end_date]):
#             return Response({'status': 'error', 'message': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)

#         # Fetch the creator (user) and game object
#         creator = get_object_or_404(Users, user_id=creator_id)
#         tournament_game = get_object_or_404(Games, id=tournament_game_id)

#         # Create the Tournament instance
#         tournament = Tournament.objects.create(
#             tournament_name=tournament_name,
#             tournament_desc=tournament_desc,
#             tournament_creator=creator,
#             tournament_game=tournament_game,
#             tournament_registration_date=reg_start_date,
#             tournament_registration_end_date=reg_end_date,
#             tournament_start_date=tournament_start_date,
#             tournament_end_date=tournament_end_date,
#             tournament_format=tournament_format,
#             tournament_status=tournament_status,
#             tournament_location=tournament_location,
#             tournament_entry_fee=tournament_entry_fee,
#             tournament_prize=tournament_prize
#         )

#         return Response({'status': 'success', 'message': 'Tournament created successfully', 'tournament_id': tournament.tournament_id}, status=status.HTTP_201_CREATED)
    
#     except Exception as e:
#         return Response({'status': 'error', 'message': f'Error creating tournament: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
    

def _overlapping_registration(user, tournament):
    """A tournament this player is already in that runs at the same time.

    Overlap is the plain reading: one starts before the other ends, and ends
    after the other starts. A tournament with no end time is treated as ending
    when it starts, so a single-slot fixture does not swallow the whole day.
    """
    if not tournament.start_date_and_time:
        return None

    starts = tournament.start_date_and_time
    ends = tournament.end_date_and_time or starts

    existing = (
        TournamentRegistration.objects
        .filter(user=user, status__in=('pending', 'confirmed'))
        .exclude(tournament=tournament)
        .select_related('tournament')
    )
    for registration in existing:
        other = registration.tournament
        if other.status in ('completed', 'cancelled'):
            continue
        other_starts = other.start_date_and_time
        if not other_starts:
            continue
        other_ends = other.end_date_and_time or other_starts
        if starts <= other_ends and ends >= other_starts:
            return other
    return None

@api_view(['POST'])
def join_tournament(request):
    """Register a user or team for a tournament."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(' ', 1)[1]

    try:
        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=status.HTTP_401_UNAUTHORIZED)

        tournament_id = request.data.get('tournament_id')
        team_id = request.data.get('team_id')  # optional - required for team-access tournaments

        if not tournament_id:
            return Response({ 'code': 'TOURNAMENT_ID_REQUIRED','status': 'error', 'message': 'tournament_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        tournament = get_object_or_404(Tournament, tournament_id=tournament_id, is_draft=False)

        # Enforce access type
        # The create wizard sends access as 'teams'/'individuals'/'both';
        # accept singular spellings too so the gate can't be silently bypassed.
        _access = (tournament.tournament_access or '').lower()
        if _access in ('team', 'teams') and not team_id:
            return Response({ 'code': 'TEAM_ID_REQUIRED_TEAM','status': 'error', 'message': 'team_id is required for team-based tournaments'}, status=status.HTTP_400_BAD_REQUEST)

        if _access in ('individual', 'individuals') and team_id:
            return Response({ 'code': 'TOURNAMENT_ONLY_ACCEPTS_INDIVIDUAL','status': 'error', 'message': 'This tournament only accepts individual registrations'}, status=status.HTTP_400_BAD_REQUEST)

        # Don't accept registrations once the bracket is live / tournament is over.
        if tournament.status in ('live', 'completed', 'cancelled', 'registration_closed'):
            return Response({'status': 'error', 'code': 'STATE_CONFLICT',
                             'message': 'Registration is closed for this tournament'},
                            status=status.HTTP_409_CONFLICT)

        # A cap that nothing enforces is a number on a page. Count what is
        # already in before letting anybody else through the door.
        capacity = tournament.max_number_of_teams or tournament.player_size or 0
        if capacity:
            taken = tournament.registrations.filter(
                status__in=('pending', 'confirmed'),
            ).count()
            if taken >= capacity:
                return Response({
                    'status': 'error',
                    'code': 'TOURNAMENT_FULL',
                    'message': f'This tournament is full. All {capacity} places have been taken.',
                    'data': {'capacity': capacity, 'registered': taken},
                }, status=status.HTTP_409_CONFLICT)

        # The entry restrictions the organiser set: verified email, age, country,
        # identity. Refusing here rather than at the bracket means nobody pays an
        # entry fee for a tournament they were never eligible for.
        refusal = tournament_options.entry_refusal(tournament, user)
        if refusal:
            return Response({
                'status': 'error',
                'code': 'NOT_ELIGIBLE',
                'message': refusal,
            }, status=status.HTTP_403_FORBIDDEN)

        # Two tournaments at the same time is two matches somebody cannot play.
        # The PRD asks for a warning rather than a refusal, so this answers with
        # the clash and what it collides with, and goes ahead when the caller
        # sends acknowledge_overlap - which is what a "yes, I know" button does.
        clash = _overlapping_registration(user, tournament)
        if clash is not None and not request.data.get('acknowledge_overlap'):
            return Response({
                'status': 'error',
                'code': 'SCHEDULE_CONFLICT',
                'message': (
                    f'This runs at the same time as {clash.tournament_title}, which you are '
                    'already registered for. Register anyway?'
                ),
                'data': {
                    'conflict': {
                        'tournament_id': clash.tournament_id,
                        'title': clash.tournament_title,
                        'slug': clash.slug,
                        'starts_at': clash.start_date_and_time,
                        'ends_at': clash.end_date_and_time,
                    },
                    'acknowledge_with': 'acknowledge_overlap',
                },
            }, status=status.HTTP_409_CONFLICT)

        is_paid = tournament.entry_fee == 'Paid' and int(tournament.entry_fee_price) > 0

        # Shared ticketing: when this tournament runs inside an event and the
        # organizer switched shared ticketing on, a valid ticket for that event
        # pays the entry fee, so there is nothing to debit and no PIN to ask for.
        from vent_event.views_linking import entry_is_covered
        _covered, _event_link = entry_is_covered(user, tournament)
        covered_by_ticket = bool(is_paid and _covered)
        if covered_by_ticket:
            is_paid = False

        entry_fee_coins = int(tournament.entry_fee_price) if is_paid else 0
        # KYC gate applies to any tournament that charges entry OR awards a prize
        # (locked CEO decision 2026-05-26).
        needs_kyc = tournament.is_paid_entry
        pin = request.data.get('pin')

        from vent_auth.models import UserWallet
        from django.contrib.auth.hashers import check_password as check_pw

        user_wallet = None
        if is_paid or needs_kyc:
            user_wallet = UserWallet.objects.filter(user=user).first()
            if user_wallet is None:
                return Response({'status': 'error', 'code': 'WALLET_NOT_FOUND', 'message': 'Wallet not found'},
                                status=status.HTTP_404_NOT_FOUND)

        # KYC required at registration for paid / prize tournaments.
        if needs_kyc and not user_wallet.kyc_verified:
            return Response({'status': 'error', 'code': 'KYC_REQUIRED',
                             'message': 'KYC verification is required to join a paid tournament'},
                            status=status.HTTP_403_FORBIDDEN)

        # PIN check for the coin deduction (does not need the row lock).
        if is_paid:
            if not pin:
                return Response({'status': 'error', 'code': 'PIN_REQUIRED',
                                 'message': 'pin is required for paid tournament registration'},
                                status=status.HTTP_400_BAD_REQUEST)
            if not user_wallet.pin_hash or not check_pw(str(pin), user_wallet.pin_hash):
                return Response({'status': 'error', 'code': 'WRONG_PIN', 'message': 'Invalid PIN'},
                                status=status.HTTP_403_FORBIDDEN)

        with db_transaction.atomic():
            if team_id:
                team = get_object_or_404(Teams, team_id=team_id)
                if TournamentRegistration.objects.filter(tournament=tournament, team=team).exists():
                    return Response({'status': 'error', 'code': 'TEAM_ALREADY_REGISTERED',
                                     'message': 'This team is already registered'},
                                    status=status.HTTP_409_CONFLICT)
                registration = TournamentRegistration.objects.create(
                    tournament=tournament, team=team,
                    status='confirmed', entry_fee_paid=not is_paid,
                )
            else:
                if TournamentRegistration.objects.filter(tournament=tournament, user=user).exists():
                    return Response({'status': 'error', 'code': 'ALREADY_REGISTERED',
                                     'message': 'You are already registered for this tournament'},
                                    status=status.HTTP_409_CONFLICT)
                registration = TournamentRegistration.objects.create(
                    tournament=tournament, user=user,
                    status='confirmed', entry_fee_paid=not is_paid,
                )

            # Record what paid for the entry when an event ticket did.
            if covered_by_ticket and _event_link:
                registration.payment_reference = f'event-ticket:{_event_link.event_id}'
                registration.save(update_fields=['payment_reference'])

            # Deduct fee for paid tournaments - lock the wallet row so the balance
            # check and debit are race-safe against concurrent registrations/sends.
            if is_paid:
                from vent_auth.models import Transaction
                locked_wallet = UserWallet.objects.select_for_update().get(pk=user_wallet.pk)
                if locked_wallet.wallet_balance < entry_fee_coins:
                    return Response({'status': 'error', 'code': 'INSUFFICIENT_BALANCE',
                                     'message': 'Insufficient VENT COINS balance'},
                                    status=status.HTTP_422_UNPROCESSABLE_ENTITY)
                locked_wallet.wallet_balance -= entry_fee_coins
                locked_wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=locked_wallet,
                    type='deduction',
                    amount=-entry_fee_coins,
                    description=f'Registration fee - {tournament.tournament_title}',
                    status='completed',
                    tournament=tournament,
                )
                registration.entry_fee_paid = True
                registration.save(update_fields=['entry_fee_paid'])

        # Notify the registrant (team owner for team entries) - fire-and-forget.
        try:
            from vent_auth.views_notifications import create_notification
            recipient = registration.team.team_owner if registration.team_id else user
            create_notification(
                recipient, 'tournament',
                f"You're registered for {tournament.tournament_title}",
                link=f'/tournaments/view-tournament?id={tournament.tournament_id}',
                metadata={'tournament_id': tournament.tournament_id},
            )
            # Confirmation of the slot, with the start time and what was paid.
            # Sent to whoever holds the entry: the team owner, or the player.
            from vent_auth import emails
            emails.send_tournament_registered(
                recipient, tournament,
                entry_paid_vc=entry_fee_coins if is_paid else 0,
            )
        except Http404:
            return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            pass

        return Response({
            'status': 'success',
            'message': 'Successfully registered for the tournament',
            'data': {
                'registration_id': registration.id,
                'status': registration.status,
                'entry_fee_paid': registration.entry_fee_paid,
                'coins_deducted': entry_fee_coins if is_paid else 0,
                'covered_by_event_ticket': covered_by_ticket,
            }
        }, status=status.HTTP_201_CREATED)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['GET'])
def search_tournament(request):
    try:
        # The tournaments page sends q / game / format / entry / status / from / to.
        # `name`, `game_id`, `location`, `access` are the older param names - both work.
        name = request.GET.get('name') or request.GET.get('q')
        game_id = request.GET.get('game_id')
        game_title = request.GET.get('game')
        location = request.GET.get('location')
        access = request.GET.get('access')  # team / individual / team_and_individual
        fmt = request.GET.get('format')
        entry = (request.GET.get('entry') or '').lower()
        wanted_status = request.GET.get('status')
        date_from = request.GET.get('from')
        date_to = request.GET.get('to')

        query = Q(is_draft=False, tournament_visibility__in=['public', 'protected'])

        if name:
            query &= Q(tournament_title__icontains=name)
        if game_id and str(game_id).isdigit():
            query &= Q(tournament_game__game_id=game_id)
        if game_title and not str(game_title).isdigit() and game_title != 'All Games':
            query &= Q(tournament_game__game_title__iexact=game_title)
        if location:
            query &= Q(tournament_location__icontains=location)
        if access:
            query &= Q(tournament_access__iexact=access)
        if fmt and fmt != 'All Formats':
            query &= Q(bracket_type__iexact=normalize_bracket_type(fmt))
        if wanted_status:
            query &= Q(status__iexact=wanted_status)
        if entry == 'free':
            query &= Q(entry_fee_price__lte=0)
        elif entry == 'paid':
            query &= Q(entry_fee_price__gt=0)
        if date_from:
            query &= Q(start_date_and_time__gte=date_from)
        if date_to:
            query &= Q(start_date_and_time__lte=date_to)

        tournaments = list(
            Tournament.objects.filter(query).select_related('tournament_game').order_by('-start_date_and_time')
        )

        if not tournaments:
            return Response({'status': 'success', 'data': [], 'message': 'No tournaments found'}, status=status.HTTP_200_OK)

        confirmed_counts, prize_pools = _card_lookups(tournaments)
        tournament_list = [
            serialize_tournament_card(
                t,
                confirmed_count=confirmed_counts.get(t.tournament_id, 0),
                prize_pool=prize_pools.get(t.tournament_id, 0),
            )
            for t in tournaments
        ]
        return Response({'status': 'success', 'data': tournament_list}, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def create_tournament(request):
    try:
        with transaction.atomic():
            session_token = request.headers.get('Authorization')

            if not session_token:
                return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

            # Ensure the token is in the correct format (e.g., 'Bearer <token>')
            if not session_token.startswith("Bearer "):
                return Response({ 'code': 'INVALID_TOKEN_FORMAT','status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

            # Extract the actual token
            login_session_token = session_token.split(" ")[1]
            # Get data from the request
            tournament_title = request.data.get('tournament_title')
            game = request.data.get('game')
            game_mode = request.data.get('game_mode')
            tournament_description = request.data.get('tournament_description')
            tournament_type = request.data.get('tournament_type')
            start_date_and_time = request.data.get('start_date_and_time')
            end_date_and_time = request.data.get('end_date_and_time')
            tournament_location = request.data.get('tournament_location')
            virtual_link = request.data.get('virtual_link')
            hide_location = request.data.get('hide_location', False)
            tournament_visibility = request.data.get('tournament_visibility')
            entry_type = request.data.get('entry_type')
            entry_fee_price = 0.00 if entry_type == 'Free' else request.data.get('entry_fee_price', 0.00)
            tournament_logo = request.FILES.get('tournament_logo')
            tournament_banner = request.FILES.get('tournament_banner')
            tournament_access = request.data.get('tournament_access')
            team_size = request.data.get('team_size', 1)
            min_number_of_participants = request.data.get('min_number_of_participants', 0)
            max_number_of_participants = request.data.get('max_number_of_participants', 0)
            bracket_type = normalize_bracket_type(request.data.get('bracket_type'))
            tournament_rules = request.data.get('tournament_rules')
            # Wizard sends 'winner-takes-all' (hyphen); model choice is
            # 'winner_takes_all' - normalize so the prize branch + stored value match.
            prize_type = (request.data.get('prize_type', 'no_prize') or 'no_prize').replace('-', '_')

            # Which currency the organiser is thinking in, and the pool they
            # announced in it. Both are kept; the coins are what pay out.
            prize_currency = (request.data.get('prize_currency') or 'VC').upper()
            if prize_currency not in CURRENCIES:
                prize_currency = 'VC'
            announced_total = request.data.get('prize_pool_total')
            is_draft = request.data.get('is_draft', True)
            # Locked CEO decision 2026-05-26: organizer picks how scores get confirmed.
            score_confirmation_mode = request.data.get('score_confirmation_mode', 'both_players_confirm')
            valid_modes = {'organizer_only', 'both_players_confirm', 'screenshot_required'}
            if score_confirmation_mode not in valid_modes:
                score_confirmation_mode = 'both_players_confirm'
            is_draft_bool = str(is_draft) not in ('0', 'false', 'False')

            # The organiser settings that decide who may enter, how the draw is
            # made and whether there is a check-in window. The wizard sends them
            # as one JSON object, and clean() is what makes it safe to store:
            # unknown keys dropped, numbers clamped, every key present.
            raw_options = request.data.get('options')
            if isinstance(raw_options, str):
                try:
                    raw_options = json.loads(raw_options) if raw_options.strip() else {}
                except (json.JSONDecodeError, ValueError):
                    raw_options = {}
            cleaned_options = tournament_options.clean(raw_options)


            # Sponsor data - the create wizard sends these as JSON-stringified
            # arrays in multipart FormData; sponsor logos are not uploaded yet.
            sponsor_names = _parse_sponsor_list(request.data, 'sponsor_names')
            sponsor_types = _parse_sponsor_list(request.data, 'sponsor_types')
            sponsor_usernames = _parse_sponsor_list(request.data, 'sponsor_usernames')
            sponsor_logos = request.FILES.getlist('sponsor_logos')

            # Social Links
            social_links = {
                "facebook_link": request.data.get('facebook_link'),
                "twitter_link": request.data.get('twitter_link'),
                "instagram_link": request.data.get('instagram_link'),
                "youtube_link": request.data.get('youtube_link'),
                "twitch_link": request.data.get('twitch_link'),
                "kick_link": request.data.get('kick_link'),
                "tiktok_link": request.data.get('tiktok_link'),
                "bigolive_link": request.data.get('bigolive_link')
            }

            # Validate dates
            if start_date_and_time >= end_date_and_time:
                raise ValueError("Start date and time must be before end date and time.")

            game = Games.objects.get(game_title=game.title())

            creator = Users.objects.filter(login_session_token=login_session_token).first()
            if creator is None:
                return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
            if creator.login_session_created_at is None or timezone.now() - creator.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
                return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=401)


            # Create Tournament
            tournament = Tournament.objects.create(
                tournament_title=tournament_title,
                tournament_creator=creator,
                tournament_game=game,
                game_mode=game_mode,
                tournament_logo=tournament_logo,
                tournament_banner=tournament_banner,
                tournament_description=tournament_description,
                tournament_rules=tournament_rules,
                start_date_and_time=start_date_and_time,
                end_date_and_time=end_date_and_time,
                tournament_visibility=tournament_visibility,
                tournament_type=tournament_type,
                tournament_location=None if hide_location else tournament_location,
                virtual_link=virtual_link,
                team_size=team_size,
                player_size=max_number_of_participants,
                min_number_of_teams=min_number_of_participants,
                max_number_of_teams=max_number_of_participants,
                bracket_type=bracket_type,
                tournament_access=tournament_access,
                entry_fee=entry_type,
                entry_fee_price=entry_fee_price,
                is_draft=is_draft_bool,
                score_confirmation_mode=score_confirmation_mode,
                status='draft' if is_draft_bool else 'published',
                prize_type=prize_type,
                prize_currency=prize_currency,
                prize_pool_total=announced_total or None,
                prize_pool_total_vc=to_coins(announced_total, prize_currency) or None,
                options=cleaned_options,
                **social_links
            )

            # Create prize distributions if applicable
            if prize_type == 'distributed':
                prize_data = request.data.get('prize_data', [])
                # The create wizard sends prize_data as a JSON-stringified array
                # inside multipart FormData (same as sponsors), so decode it before
                # iterating - otherwise we iterate the string's characters.
                if isinstance(prize_data, str):
                    try:
                        prize_data = json.loads(prize_data) if prize_data.strip() else []
                    except (json.JSONDecodeError, ValueError):
                        prize_data = []
                for prize_entry in prize_data:
                    if not isinstance(prize_entry, dict):
                        continue
                    # The organiser may type in naira, dollars or coins. The
                    # conversion happens here rather than in the browser,
                    # because a figure worked out client-side is a figure
                    # somebody can edit before it is sent.
                    entry_currency = (prize_entry.get('currency') or prize_currency or 'VC').upper()
                    typed = prize_entry.get('amount', prize_entry.get('prize'))
                    coins = to_coins(typed, entry_currency)

                    extras_typed = prize_entry.get('extras_amount')
                    extras_coins = to_coins(extras_typed, entry_currency) if extras_typed else None

                    TournamentPrizeDistribution.objects.create(
                        tournament=tournament,
                        position=prize_entry['position'],
                        prize=coins,
                        amount_original=typed or None,
                        currency=entry_currency,
                        extras=(prize_entry.get('extras') or '')[:120],
                        extras_amount=extras_typed or None,
                        extras_prize=extras_coins,
                    )
            elif prize_type == 'winner_takes_all':
                typed = request.data.get('winner_prize', request.data.get('total_prize', 0))
                TournamentPrizeDistribution.objects.create(
                    tournament=tournament,
                    position=1,
                    prize=to_coins(typed, prize_currency),
                    amount_original=typed or None,
                    currency=prize_currency,
                    extras='Winner Takes All',
                )

            # Add sponsors. The wizard collects a name (required) plus an optional
            # username; type defaults to 'individual' (name-only). Only resolve a
            # linked entity when the type names one and the lookup succeeds - a
            # missing entity must not abort tournament creation. Logos are optional
            # (the wizard does not upload sponsor logo files yet).
            for index, raw_name in enumerate(sponsor_names):
                name = raw_name.strip() if isinstance(raw_name, str) else raw_name
                if not name:
                    continue
                s_type = sponsor_types[index] if index < len(sponsor_types) else ''
                s_type = (s_type or '').strip().lower() if isinstance(s_type, str) else ''
                username = sponsor_usernames[index] if index < len(sponsor_usernames) else ''
                username = username.strip() if isinstance(username, str) else ''
                logo = sponsor_logos[index] if index < len(sponsor_logos) else None

                sponsor_instance = None
                if username and s_type in ('user', 'team', 'org'):
                    try:
                        if s_type == 'user':
                            sponsor_instance = Users.objects.get(username=username)
                        elif s_type == 'team':
                            sponsor_instance = Teams.objects.get(team_name=username)
                        else:
                            sponsor_instance = Organization.objects.get(org_name=username)
                    except (Users.DoesNotExist, Teams.DoesNotExist, Organization.DoesNotExist):
                        sponsor_instance = None  # keep as a name-only sponsor

                sponsor = Sponsors.objects.create(
                    name=name,
                    sponsor=sponsor_instance,
                    logo=logo,
                )
                tournament.sponsors.add(sponsor)

            return Response({"status": "success", "message": "Tournament created successfully",
                             "data": {"tournament_id": tournament.tournament_id,
                                      "slug": tournament.slug, "is_draft": is_draft_bool}},
                            status=status.HTTP_201_CREATED)

    except ValueError as e:
        return Response({"status": "error", "message": str(e)},
                        status=status.HTTP_400_BAD_REQUEST)
    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"status": "error", "message": f"An error occurred: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)



# @api_view(["GET"])
# def get_all_tournaments(request):
#     # Featured Tournaments (most interacted)
#     featured_tournaments = Tournament.objects.order_by('-interaction_count')[:5]
#     # New Tournaments (recent ones)
#     new_tournaments = Tournament.objects.order_by('-start_date_and_time')[:5]
#     # All Tournaments grouped by game
#     all_tournaments = Tournament.objects.all()
#     tournaments_by_game = {}
#     for tournament in all_tournaments:
#         game = tournament.game if hasattr(tournament, 'game') else "Unknown Game"
#         if game not in tournaments_by_game:
#             tournaments_by_game[game] = []
#         tournaments_by_game[game].append(tournament)

#     # Sponsors Serializer
#     def get_sponsors_list(tournament):
#         return [
#             {
#                 "id": sponsor.id,
#                 "name": sponsor.name,
#                 "logo": sponsor.logo.url if sponsor.logo else None,
#                 "website": sponsor.website
#             }
#             for sponsor in tournament.sponsors.all()
#         ]

#     # Prize Distributions Serializer
#     def get_prize_list(tournament):
#         prize_distributions = TournamentPrizeDistribution.objects.filter(tournament=tournament)
#         return [
#             {
#                 "id": prize.id,
#                 "position": prize.position,
#                 "prize": str(prize.prize),
#                 "extras": prize.extras
#             }
#             for prize in prize_distributions
#         ]

#     # Matches Serializer
#     def get_match_list(tournament):
#         matches = Match.objects.filter(tournament=tournament)
#         return [
#             {
#                 "match_id": match.match_id,
#                 "match_check_in_time": str(match.match_check_in_time),
#                 "match_check_in_date": str(match.match_check_in_date),
#                 "match_check_in_started": match.match_check_in_started,
#                 "match_check_in_ended": match.match_check_in_ended
#             }
#             for match in matches
#         ]

#     # Registered Teams Serializer
#     def get_registered_teams_list(tournament):
#         registered_teams = RegisteredTeams.objects.filter(tournament_id=tournament)
#         return [
#             {
#                 "team_id": team.team_id.team_id
#             }
#             for team in registered_teams
#         ]

#     # Serialize Tournaments
#     def serialize_tournaments(tournament):
#         return {
#             "tournament_id": tournament.tournament_id,
#             "tournament_title": tournament.tournament_title,
#             "tournament_logo": tournament.tournament_logo.url if tournament.tournament_logo else None,
#             "tournament_banner": tournament.tournament_banner.url if tournament.tournament_banner else None,
#             "tournament_description": tournament.tournament_description,
#             "tournament_rules": tournament.tournament_rules,
#             "bracket_type": tournament.bracket_type,
#             "start_date_and_time": tournament.start_date_and_time,
#             "end_date_and_time": tournament.end_date_and_time,
#             "tournament_visibility": tournament.tournament_visibility,
#             "tournament_type": tournament.tournament_type,
#             "tournament_location": tournament.tournament_location,
#             "player_size": tournament.player_size,
#             "max_number_of_teams": tournament.max_number_of_teams,
#             "min_number_of_teams": tournament.min_number_of_teams,
#             "tournament_access": tournament.tournament_access,
#             "entry_fee": tournament.entry_fee,
#             "entry_fee_price": str(tournament.entry_fee_price),
#             "facebook_link": tournament.facebook_link,
#             "twitter_link": tournament.twitter_link,
#             "instagram_link": tournament.instagram_link,
#             "youtube_link": tournament.youtube_link,
#             "twitch_link": tournament.twitch_link,
#             "kick_link": tournament.kick_link,
#             "sponsors": get_sponsors_list(tournament),
#             "prize_distributions": get_prize_list(tournament),
#             "matches": get_match_list(tournament),
#             "registered_teams": get_registered_teams_list(tournament),
#         }

#     # Serialize Featured Tournaments
#     featured = [serialize_tournaments(tournament) for tournament in featured_tournaments]
#     # Serialize New Tournaments
#     new = [serialize_tournaments(tournament) for tournament in new_tournaments]
#     # Serialize Tournaments by Game
#     games = {
#         game: [serialize_tournaments(tournament) for tournament in tournaments]
#         for game, tournaments in tournaments_by_game.items()
#     }

#     return Response({
#         "status": "success",
#         "data": {
#             "featured": featured,
#             "new": new,
#             "by_game": games
#         }
#     }, status=status.HTTP_200_OK)


# A tournament set to `private` is, in the organizer's own words on the creation
# wizard, "hidden from the public and available to only users with a link". The
# listing endpoints filtered on is_draft alone, so every private tournament was
# published on the front page. Direct links still resolve - that is the point of
# the setting - but nothing here puts one in front of somebody who was not sent
# it. `protected` stays listed on purpose: it restricts registration, not
# discovery.
PUBLICLY_LISTED = {'is_draft': False, 'tournament_visibility__in': ['public', 'protected']}


def _check_in_summary(tournament):
    """The check-in window as the detail page needs it, or None when unused."""
    window = tournament_options.check_in_state(tournament, timezone.now())
    if window is None:
        return None
    return {
        'required': True,
        'opens_at': window['opens_at'],
        'closes_at': window['closes_at'],
        'open_now': window['open_now'],
        'closed': window['closed'],
        'closed_by_organiser': window['closed_by_organiser'],
        'forfeit_without_check_in': window['forfeit_without_check_in'],
        'checked_in_count': tournament.registrations.filter(
            status__in=('pending', 'confirmed'), checked_in_at__isnull=False,
        ).count(),
    }


def _is_creator(request, tournament):
    """True when the caller's Bearer token belongs to the tournament's creator."""
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return False
    token = header.split(' ', 1)[1]
    if not token or not tournament.tournament_creator_id:
        return False
    return Users.objects.filter(
        login_session_token=token, user_id=tournament.tournament_creator_id
    ).exists()


@api_view(["GET"])
def get_all_tournaments(request):
    # Featured Tournaments (most interacted)
    featured_tournaments = Tournament.objects.filter(**PUBLICLY_LISTED).order_by('-interaction_count')[:5]
    
    # New Tournaments (recent ones)
    new_tournaments = Tournament.objects.filter(**PUBLICLY_LISTED).order_by('-start_date_and_time')[:5]
    
    # All Tournaments grouped by game
    all_tournaments = Tournament.objects.filter(**PUBLICLY_LISTED).select_related('tournament_game')
    tournaments_by_game = {}

    for tournament in all_tournaments:
        game_name = tournament.tournament_game.game_title if tournament.tournament_game else "Unknown Game"
        if game_name not in tournaments_by_game:
            tournaments_by_game[game_name] = []
        tournaments_by_game[game_name].append(tournament)

    # Sponsors Serializer
    def get_sponsors_list(tournament):
        return [
            {
                "id": sponsor.sponsor_id,
                "name": sponsor.name,
                "logo": sponsor.logo.url if sponsor.logo else None,
                "website": sponsor.website
            }
            for sponsor in tournament.sponsors.all()
        ]

    # Prize Distributions Serializer
    def get_prize_list(tournament):
        prize_distributions = TournamentPrizeDistribution.objects.filter(tournament=tournament)
        return [
            {
                "id": prize.id,
                "position": prize.position,
                "prize": str(prize.prize),
                "extras": prize.extras
            }
            for prize in prize_distributions
        ]

    # Matches Serializer
    def get_match_list(tournament):
        matches = Match.objects.filter(tournament=tournament)
        return [
            {
                "match_id": match.match_id,
                "match_check_in_time": str(match.match_check_in_time),
                "match_check_in_date": str(match.match_check_in_date),
                "match_check_in_started": match.match_check_in_started,
                "match_check_in_ended": match.match_check_in_ended
            }
            for match in matches
        ]

    # Registered Teams Serializer
    def get_registered_teams_list(tournament):
        registered_teams = RegisteredTeams.objects.filter(tournament_id=tournament)
        return [
            {
                "team_id": team.team_id.team_id
            }
            for team in registered_teams
        ]

    # Counts + prize pools for every tournament on this page, in bulk.
    every = list(featured_tournaments) + list(new_tournaments) + list(all_tournaments)
    confirmed_counts, prize_pools = _card_lookups(every)

    # Serialize Tournament - card contract first (what the listing renders),
    # then the legacy detail-ish extras this endpoint has always returned.
    def serialize_tournaments(tournament):
        card = serialize_tournament_card(
            tournament,
            confirmed_count=confirmed_counts.get(tournament.tournament_id, 0),
            prize_pool=prize_pools.get(tournament.tournament_id, 0),
        )
        card.update({
            "tournament_rules": tournament.tournament_rules,
            "facebook_link": tournament.facebook_link,
            "twitter_link": tournament.twitter_link,
            "instagram_link": tournament.instagram_link,
            "youtube_link": tournament.youtube_link,
            "twitch_link": tournament.twitch_link,
            "kick_link": tournament.kick_link,
            "sponsors": get_sponsors_list(tournament),
            "prize_distributions": get_prize_list(tournament),
            "matches": get_match_list(tournament),
            "registered_teams": get_registered_teams_list(tournament),
        })
        return card

    # Serialize Featured Tournaments
    featured = [serialize_tournaments(tournament) for tournament in featured_tournaments]

    # Serialize New Tournaments
    new = [serialize_tournaments(tournament) for tournament in new_tournaments]

    # Serialize Tournaments by Game
    games = {
        game: [serialize_tournaments(tournament) for tournament in tournaments]
        for game, tournaments in tournaments_by_game.items()
    }

    return Response({
        "status": "success",
        "data": {
            "featured": featured,
            "new": new,
            "by_game": games
        }
    }, status=status.HTTP_200_OK)


@api_view(["GET"])
def view_tournament(request, tournament_id):
    try:
        # The address may be an id or a slug, so a link carrying the name and a
        # link somebody bookmarked last month both resolve.
        from vent_auth.slugs import resolve_or_redirect

        tournament, moved_to = resolve_or_redirect(
            tournament_id, entity_type='tournament',
            id_field='tournament_id', model=Tournament,
        )
        if moved_to:
            # This tournament was renamed. Say where it lives now rather than
            # 404ing a link somebody shared before the rename.
            return Response({
                'status': 'moved',
                'code': 'SLUG_CHANGED',
                'message': 'This tournament has been renamed.',
                'data': {'slug': moved_to, 'url': f'/tournaments/{moved_to}'},
            }, status=status.HTTP_200_OK)
        if tournament is None:
            raise Tournament.DoesNotExist

        # A draft is an unpublished plan: half-written rules, a prize pool the
        # organizer is still arguing about, a date that will move. It was
        # readable by anybody who tried the id. Only its creator sees it until
        # it is published.
        if tournament.is_draft and not _is_creator(request, tournament):
            return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'},
                            status=status.HTTP_404_NOT_FOUND)

        # Increase interaction count
        tournament.interaction_count += 1
        tournament.save(update_fields=['interaction_count'])

        # Sponsors
        sponsors = tournament.sponsors.all()
        sponsors_list = [
            {
                "id": sponsor.sponsor_id,
                "name": sponsor.name,
                "logo": sponsor.logo.url if sponsor.logo else None,
                "website": sponsor.website
            }
            for sponsor in sponsors
        ]

        # Prize Distributions
        prize_distributions = TournamentPrizeDistribution.objects.filter(tournament=tournament)
        prize_list = [
            {
                "id": prize.id,
                "position": prize.position,
                "prize": str(prize.prize),
                "extras": prize.extras
            }
            for prize in prize_distributions
        ]

        # Matches
        matches = Match.objects.filter(tournament=tournament)
        match_list = [
            {
                "match_id": match.match_id,
                "match_check_in_time": str(match.match_check_in_time),
                "match_check_in_date": str(match.match_check_in_date),
                "match_check_in_started": match.match_check_in_started,
                "match_check_in_ended": match.match_check_in_ended
            }
            for match in matches
        ]

        # Registered Teams
        registered_teams = RegisteredTeams.objects.filter(tournament_id=tournament)
        teams_list = [
            {
                "team_id": team.team_id.team_id
            }
            for team in registered_teams
        ]

        # Fields the detail UI reads (status / organizer / prize pool / slots / format).
        prize_pool_total = sum(int(p.prize) for p in prize_distributions) if prize_distributions else 0
        confirmed_count = TournamentRegistration.objects.filter(tournament=tournament, status='confirmed').count()
        creator = tournament.tournament_creator
        creator_obj = {
            "user_id": creator.user_id,
            "username": creator.username,
            "full_name": creator.full_name,
        } if creator else None

        # Build the response
        data = {
            # Card-contract aliases so every surface (detail, manage, organizer
            # tools) reads the same key names as the listing cards.
            "id": tournament.tournament_id,
            "name": tournament.tournament_title,
            "title": tournament.tournament_title,
            "start_date": tournament.start_date_and_time,
            "end_date": tournament.end_date_and_time,
            "banner": tournament.tournament_banner.url if tournament.tournament_banner else None,
            "logo": tournament.tournament_logo.url if tournament.tournament_logo else None,
            "participant_type": "team" if (tournament.tournament_access or '').lower().startswith("team") else "individual",
            "tournament_id": tournament.tournament_id,
            "tournament_title": tournament.tournament_title,
            "game": tournament.tournament_game.game_title if tournament.tournament_game else None,
            "game_mode": tournament.game_mode,
            "status": tournament.status,
            "is_draft": tournament.is_draft,
            "format": tournament.bracket_type,
            "slug": tournament.slug,
            "prize_type": tournament.prize_type,
            "prize_currency": tournament.prize_currency or 'VC',
            "prize_pool_total": str(tournament.prize_pool_total) if tournament.prize_pool_total else None,
            "prize_pool_total_vc": str(tournament.prize_pool_total_vc) if tournament.prize_pool_total_vc else None,
            "prize_pool": prize_pool_total,
            "max_participants": tournament.max_number_of_teams or tournament.player_size,
            "current_participants": confirmed_count,
            # What the organiser configured, so the page can show the rules it
            # will be held to rather than discovering them at registration.
            "options": tournament_options.clean(tournament.options),
            "check_in": _check_in_summary(tournament),
            "tournament_creator": creator_obj,
            "prize_distribution": prize_list,
            "tournament_logo": tournament.tournament_logo.url if tournament.tournament_logo else None,
            "tournament_banner": tournament.tournament_banner.url if tournament.tournament_banner else None,
            "tournament_description": tournament.tournament_description,
            "tournament_rules": tournament.tournament_rules,
            "bracket_type": tournament.bracket_type,
            "format_label": bracket_label(tournament.bracket_type),
            "start_date_and_time": tournament.start_date_and_time,
            "end_date_and_time": tournament.end_date_and_time,
            "tournament_visibility": tournament.tournament_visibility,
            "tournament_type": tournament.tournament_type,
            "tournament_location": tournament.tournament_location,
            "player_size": tournament.player_size,
            "max_number_of_teams": tournament.max_number_of_teams,
            "min_number_of_teams": tournament.min_number_of_teams,
            "tournament_access": tournament.tournament_access,
            "entry_fee": tournament.entry_fee,
            "entry_fee_price": str(tournament.entry_fee_price),
            "facebook_link": tournament.facebook_link,
            "twitter_link": tournament.twitter_link,
            "instagram_link": tournament.instagram_link,
            "youtube_link": tournament.youtube_link,
            "twitch_link": tournament.twitch_link,
            "kick_link": tournament.kick_link,
            "sponsors": sponsors_list,
            "prize_distributions": prize_list,
            "matches": match_list,
            "registered_teams": teams_list,
            "interaction_count": tournament.interaction_count  # Include updated interaction count
        }

        # Tournaments can run inside an event. When they do, the page carries the
        # event's branding back, and with shared ticketing the viewer's ticket
        # pays their entry fee.
        from vent_event.views_linking import entry_is_covered, event_brand, _viewer
        viewer = _viewer(request)

        # Whether this viewer already holds a place, so the page can stop
        # offering a Register button the API would refuse.
        if viewer:
            own_team_ids = list(
                Teams.objects.filter(team_owner=viewer).values_list('team_id', flat=True)
            )
            data["is_registered"] = TournamentRegistration.objects.filter(
                Q(tournament=tournament)
                & (Q(user=viewer) | Q(team_id__in=own_team_ids))
            ).exclude(status='withdrawn').exists()
        else:
            data["is_registered"] = False

        covered, link = entry_is_covered(viewer, tournament)
        if link:
            data["event"] = event_brand(request, link.event)
            data["shared_ticketing"] = link.shared_ticketing
        else:
            data["event"] = None
            data["shared_ticketing"] = False
        data["entry_covered_by_ticket"] = covered

        return Response({"status": "success", "data": data}, status=status.HTTP_200_OK)

    except Tournament.DoesNotExist:
        return Response({ 'code': 'TOURNAMENT_NOT_FOUND',"status": "error", "message": "Tournament not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
def view_user_drafted_tournaments(request):
    try:
        with transaction.atomic():
            # Step 1: Get Authorization token
            session_header = request.headers.get("Authorization")
            if not session_header:
                return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED',"status": "error", "message": "Authorization header is required"}, status=status.HTTP_400_BAD_REQUEST)
            
            if not session_header.startswith("Bearer "):
                return Response({ 'code': 'INVALID_TOKEN_FORMAT',"status": "error", "message": "Invalid token format"}, status=status.HTTP_400_BAD_REQUEST)

            login_session_token = session_header.split(" ", 1)[1]

            # Step 2: Find the user by login_session_token
            try:
                user = Users.objects.get(login_session_token=login_session_token)
            except Users.DoesNotExist:
                return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN',"status": "error", "message": "Invalid or expired session token"}, status=status.HTTP_401_UNAUTHORIZED)

            # Step 3: Fetch user's draft tournaments
            tournaments = (
                Tournament.objects
                .filter(tournament_creator=user, is_draft=True)
                .select_related("tournament_game", "tournament_organization")
                .prefetch_related("sponsors", "prize_distributions")
                .order_by("-start_date_and_time")
            )

            # Step 4: Serializers for nested objects
            def serialize_sponsors(t):
                return [
                    {
                        "id": s.sponsor_id,
                        "name": s.name,
                        "logo": s.logo.url if s.logo else None,
                        "website": s.website
                    }
                    for s in t.sponsors.all()
                ]

            def serialize_prizes(t):
                return [
                    {
                        "id": p.id,
                        "position": p.position,
                        "prize": str(p.prize),
                        "extras": p.extras
                    }
                    for p in t.prize_distributions.all()
                ]

            # Step 5: Main Tournament Serializer
            def serialize_tournament(t):
                return {
                    # Card contract first (drafts render in the same cards as
                    # published tournaments), then the draft-specific extras.
                    **serialize_tournament_card(t),
                    "tournament_id": t.tournament_id,
                    "tournament_title": t.tournament_title,
                    "tournament_game": t.tournament_game.game_title,
                    "game_mode": t.game_mode,
                    "tournament_logo": t.tournament_logo.url if t.tournament_logo else None,
                    "tournament_banner": t.tournament_banner.url if t.tournament_banner else None,
                    "tournament_description": t.tournament_description,
                    "tournament_rules": t.tournament_rules,
                    "bracket_type": t.bracket_type,
                    "format_label": bracket_label(t.bracket_type),
                    "tournament_creator_id": t.tournament_creator.user_id,
                    "tournament_organization": t.tournament_organization.name if t.tournament_organization else None,
                    "start_date_and_time": t.start_date_and_time,
                    "end_date_and_time": t.end_date_and_time,
                    "tournament_visibility": t.tournament_visibility,
                    "tournament_type": t.tournament_type,
                    "tournament_location": t.tournament_location,
                    "virtual_link": t.virtual_link,
                    "team_size": t.team_size,
                    "player_size": t.player_size,
                    "min_number_of_teams": t.min_number_of_teams,
                    "max_number_of_teams": t.max_number_of_teams,
                    "prize_type": t.prize_type,
                    "tournament_access": t.tournament_access,
                    "entry_fee": t.entry_fee,
                    "entry_fee_price": str(t.entry_fee_price),
                    "facebook_link": t.facebook_link,
                    "twitter_link": t.twitter_link,
                    "instagram_link": t.instagram_link,
                    "youtube_link": t.youtube_link,
                    "twitch_link": t.twitch_link,
                    "kick_link": t.kick_link,
                    "tiktok_link": t.tiktok_link,
                    "bigolive_link": t.bigolive_link,
                    "interaction_count": t.interaction_count,
                    "is_draft": t.is_draft,
                    "sponsors": serialize_sponsors(t),
                    "prize_distributions": serialize_prizes(t),
                }

            serialized_data = [serialize_tournament(t) for t in tournaments]

            return Response({"status": "success", "data": serialized_data}, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"status": "error", "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_tournament_participants(request, tournament_id):
    """GET /tournament/get-tournament-participants/{id}/ - registered participants tab."""
    try:
        tournament = get_object_or_404(Tournament, tournament_id=tournament_id, is_draft=False)

        registrations = (
            tournament.registrations
            .select_related('team', 'team__team_owner', 'user')
            .order_by('seed', 'registered_at')
        )

        # Played / won counts for this tournament, so the participants table can
        # show a real record instead of a column that is 0% for everybody.
        played, won = {}, {}
        finished = tournament.bracket_matches.filter(status='completed')
        for match in finished:
            for reg_id in (match.participant_1_id, match.participant_2_id):
                if reg_id:
                    played[reg_id] = played.get(reg_id, 0) + 1
            if match.winner_id:
                won[match.winner_id] = won.get(match.winner_id, 0) + 1

        def entrant(r):
            if r.team:
                owner = r.team.team_owner
                return {
                    'id': r.team.team_id,
                    'name': r.team.team_name,
                    'logo': r.team.team_logo.url if r.team.team_logo else None,
                    'captain': owner.username if owner else None,
                    'country': None,
                }
            return {
                'id': r.user.user_id,
                'name': r.user.full_name or r.user.username,
                'username': r.user.username,
                'logo': None,
                'captain': r.user.username,
                'country': r.user.country or None,
            }

        data = []
        for index, r in enumerate(registrations, start=1):
            matches = played.get(r.id, 0)
            wins = won.get(r.id, 0)
            data.append({
                'registration_id': r.id,
                'type': 'team' if r.team else 'individual',
                'seed': r.seed or index,
                'participant': entrant(r),
                'matches_played': matches,
                'wins': wins,
                'losses': matches - wins,
                'win_rate': round(wins * 100 / matches) if matches else None,
                'final_position': r.final_position,
                'status': r.status,
                'entry_fee_paid': r.entry_fee_paid,
                'registered_at': r.registered_at,
            })

        return Response({
            'status': 'success',
            'data': {
                'tournament_id': tournament.tournament_id,
                'total': len(data),
                'participants': data,
            }
        }, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def update_bracket(request, tournament_id):
    """POST /tournament/update-bracket/{id}/ - organizer updates match score / advances bracket."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(' ', 1)[1]

    try:
        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=status.HTTP_401_UNAUTHORIZED)

        tournament = get_object_or_404(Tournament, tournament_id=tournament_id, is_draft=False)

        if tournament.tournament_creator_id != user.user_id:
            return Response({ 'code': 'ONLY_ORGANIZER_CAN_UPDATE_BRACKETS','status': 'error', 'message': 'Only the tournament organizer can update brackets'}, status=status.HTTP_403_FORBIDDEN)

        match_id = request.data.get('match_id')
        score_p1 = request.data.get('score_p1')
        score_p2 = request.data.get('score_p2')
        winner_registration_id = request.data.get('winner_registration_id')

        if not match_id or score_p1 is None or score_p2 is None or not winner_registration_id:
            return Response(
                { 'code': 'MATCH_ID_SCORE_P','status': 'error', 'message': 'match_id, score_p1, score_p2, and winner_registration_id are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        match = get_object_or_404(BracketMatch, id=match_id, tournament=tournament)
        winner_reg = get_object_or_404(TournamentRegistration, id=winner_registration_id, tournament=tournament)

        match.score_p1 = int(score_p1)
        match.score_p2 = int(score_p2)
        match.winner = winner_reg
        match.status = 'completed'
        match.completed_at = timezone.now()
        match.save(update_fields=['score_p1', 'score_p2', 'winner', 'status', 'completed_at'])

        return Response({'status': 'success', 'message': 'Match updated'}, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_organizer_tournaments(request):
    """GET /tournament/get-organizer-tournaments/ - organizer's published + draft tournaments."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(' ', 1)[1]

    try:
        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=status.HTTP_401_UNAUTHORIZED)

        tournaments = (
            Tournament.objects
            .filter(tournament_creator=user)
            .select_related('tournament_game')
            .order_by('-start_date_and_time')
        )

        now = timezone.now()
        tournaments = list(tournaments)
        confirmed_counts, prize_pools = _card_lookups(tournaments)
        data = []
        for t in tournaments:
            # Same card contract as the public listing so /tournaments/my-tournaments
            # renders real names/games/prizes instead of "Untitled Tournament".
            row = serialize_tournament_card(
                t,
                confirmed_count=confirmed_counts.get(t.tournament_id, 0),
                prize_pool=prize_pools.get(t.tournament_id, 0),
            )
            row.update({
                'registrations': t.registrations.count() if not t.is_draft else 0,
                'open_disputes': t.disputes.filter(status='open').count() if not t.is_draft else 0,
                'computed_status': (
                    'draft' if t.is_draft else
                    'upcoming' if t.start_date_and_time > now else
                    'live' if t.end_date_and_time >= now else
                    'ended'
                ),
            })
            data.append(row)

        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
def delete_draft(request, tournament_id):
    """DELETE /tournament/delete-draft/{id}/ - delete a draft tournament."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(' ', 1)[1]

    try:
        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=status.HTTP_401_UNAUTHORIZED)

        tournament = get_object_or_404(Tournament, tournament_id=tournament_id, tournament_creator=user)

        if not tournament.is_draft:
            return Response(
                { 'code': 'ONLY_DRAFT_TOURNAMENTS_CAN','status': 'error', 'message': 'Only draft tournaments can be deleted this way. Use admin cancel for published tournaments.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tournament.delete()
        return Response({'status': 'success', 'message': 'Draft deleted'}, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PUT'])
def edit_tournament(request, tournament_id):
    """PUT /tournament/edit-tournament/{id}/ - edit a published or draft tournament."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({ 'code': 'AUTHORIZATION_HEADER_REQUIRED','status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(' ', 1)[1]

    try:
        user = Users.objects.filter(login_session_token=login_session_token).first()
        if user is None:
            return Response({ 'code': 'INVALID_EXPIRED_SESSION_TOKEN','status': 'error', 'message': 'Invalid or expired session token'}, status=status.HTTP_401_UNAUTHORIZED)
        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=session_timeout_minutes()):
            return Response({ 'code': 'SESSION_TOKEN_EXPIRED','status': 'error', 'message': 'Session token has expired'}, status=status.HTTP_401_UNAUTHORIZED)

        tournament = get_object_or_404(Tournament, tournament_id=tournament_id)

        if tournament.tournament_creator_id != user.user_id:
            return Response({ 'code': 'ONLY_TOURNAMENT_ORGANIZER_CAN','status': 'error', 'message': 'Only the tournament organizer can edit this tournament'}, status=status.HTTP_403_FORBIDDEN)

        # Editable fields (partial update - only update what's provided)
        editable_text = [
            'tournament_title', 'tournament_description', 'tournament_rules',
            'tournament_location', 'virtual_link', 'tournament_visibility',
            'tournament_type', 'bracket_type', 'tournament_access',
            'entry_fee', 'entry_fee_price', 'team_size', 'player_size',
            'min_number_of_teams', 'max_number_of_teams', 'prize_type',
            'game_mode', 'start_date_and_time', 'end_date_and_time',
            'facebook_link', 'twitter_link', 'instagram_link', 'youtube_link',
            'twitch_link', 'kick_link', 'tiktok_link', 'bigolive_link',
        ]

        updated_fields = []
        for field in editable_text:
            val = request.data.get(field)
            if val is not None:
                if field == 'bracket_type':
                    val = normalize_bracket_type(val, tournament.bracket_type)
                setattr(tournament, field, val)
                updated_fields.append(field)

        # File fields
        if request.FILES.get('tournament_logo'):
            tournament.tournament_logo = request.FILES['tournament_logo']
            updated_fields.append('tournament_logo')
        if request.FILES.get('tournament_banner'):
            tournament.tournament_banner = request.FILES['tournament_banner']
            updated_fields.append('tournament_banner')

        # Publish/draft toggle - keep `status` in sync with `is_draft`.
        is_draft = request.data.get('is_draft')
        if is_draft is not None:
            tournament.is_draft = str(is_draft) in ('1', 'true', 'True')
            updated_fields.append('is_draft')
            # Only touch status for pre-live tournaments (never rewind a live/completed one).
            if tournament.status in ('draft', 'published', 'registration_open'):
                tournament.status = 'draft' if tournament.is_draft else 'registration_open'
                updated_fields.append('status')

        # Organiser settings. Merged onto what is already stored rather than
        # replacing it, so an edit screen that only sends the check-in window
        # cannot silently wipe the region restriction.
        raw_options = request.data.get('options')
        if isinstance(raw_options, str):
            try:
                raw_options = json.loads(raw_options) if raw_options.strip() else None
            except (json.JSONDecodeError, ValueError):
                raw_options = None
        if isinstance(raw_options, dict):
            merged = dict(tournament_options.clean(tournament.options))
            merged.update(raw_options)
            tournament.options = tournament_options.clean(merged)
            updated_fields.append('options')

        # Validate game if provided
        game_title = request.data.get('game')
        if game_title:
            try:
                tournament.tournament_game = Games.objects.get(game_title=game_title.title())
                updated_fields.append('tournament_game')
            except Games.DoesNotExist:
                return Response({'status': 'error', 'message': f'Game "{game_title}" not found'}, status=status.HTTP_400_BAD_REQUEST)

        if updated_fields:
            tournament.save(update_fields=updated_fields)

        return Response({
            'status': 'success',
            'message': 'Tournament updated',
            'data': {'tournament_id': tournament.tournament_id, 'updated_fields': updated_fields}
        }, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_tournament_brackets(request, tournament_id):
    """Return the bracket structure for a tournament, grouped by round."""
    try:
        tournament = get_object_or_404(Tournament, tournament_id=tournament_id, is_draft=False)

        matches = (
            tournament.bracket_matches
            .select_related(
                'participant_1__team', 'participant_1__user',
                'participant_2__team', 'participant_2__user',
                'winner__team', 'winner__user',
            )
            .order_by('round_number', 'match_number')
        )

        def participant_label(reg):
            if reg is None:
                return None
            if reg.team:
                return {'type': 'team', 'id': reg.team.team_id, 'name': reg.team.team_name}
            if reg.user:
                return {'type': 'user', 'id': reg.user.user_id, 'name': reg.user.username}
            return None

        # Group by round
        rounds = {}
        for m in matches:
            r = m.round_number
            if r not in rounds:
                rounds[r] = []
            rounds[r].append({
                'match_id': m.id,
                'match_number': m.match_number,
                'participant_1': participant_label(m.participant_1),
                'participant_2': participant_label(m.participant_2),
                'score_p1': m.score_p1,
                'score_p2': m.score_p2,
                'winner': participant_label(m.winner),
                'status': m.status,
                'scheduled_at': m.scheduled_at,
                'completed_at': m.completed_at,
            })

        bracket_data = [
            {'round': r, 'matches': rounds[r]}
            for r in sorted(rounds.keys())
        ]

        return Response({
            'status': 'success',
            'data': {
                'tournament_id': tournament.tournament_id,
                'tournament_title': tournament.tournament_title,
                'bracket_type': tournament.bracket_type,
                'format_label': bracket_label(tournament.bracket_type),
                'rounds': bracket_data,
            }
        }, status=status.HTTP_200_OK)

    except Http404:
        return Response({ 'code': 'NOT_FOUND','status': 'error', 'message': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([AllowAny])
def prize_rates(request):
    """GET /tournament/prize-rates/ - the conversion the create screen shows.

    Public because the create screen needs it before anything is saved, and it
    is the same rate printed on the wallet page.
    """
    return Response({'status': 'success', 'data': rates(), 'message': 'Prize conversion rates'})

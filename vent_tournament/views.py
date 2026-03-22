from datetime import timedelta
from django.shortcuts import render
from imports import api_view,get_object_or_404, Response, status, transaction
from .models import Tournament, Users, Games, Teams, TournamentPrizeDistribution, TournamentRegistration
from django.db.models import Q
from .models import Tournament, Sponsors, TournamentPrizeDistribution, Match, RegisteredTeams, TournamentRegistration
from vent_auth.models import Organization
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.db.models import Prefetch

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
    

@api_view(['POST'])
def join_tournament(request):
    """Register a user or team for a tournament."""
    session_token = request.headers.get('Authorization')
    if not session_token or not session_token.startswith('Bearer '):
        return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

    login_session_token = session_token.split(' ', 1)[1]

    try:
        user = get_object_or_404(Users, login_session_token=login_session_token)

        if user.login_session_created_at is None or timezone.now() - user.login_session_created_at > timedelta(minutes=120):
            return Response({'status': 'error', 'message': 'Session token has expired'}, status=status.HTTP_401_UNAUTHORIZED)

        tournament_id = request.data.get('tournament_id')
        team_id = request.data.get('team_id')  # optional — required for team-access tournaments

        if not tournament_id:
            return Response({'status': 'error', 'message': 'tournament_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        tournament = get_object_or_404(Tournament, tournament_id=tournament_id, is_draft=False)

        # Enforce access type
        if tournament.tournament_access == 'team' and not team_id:
            return Response({'status': 'error', 'message': 'team_id is required for team-based tournaments'}, status=status.HTTP_400_BAD_REQUEST)

        if tournament.tournament_access == 'individual' and team_id:
            return Response({'status': 'error', 'message': 'This tournament only accepts individual registrations'}, status=status.HTTP_400_BAD_REQUEST)

        if team_id:
            team = get_object_or_404(Teams, team_id=team_id)
            if TournamentRegistration.objects.filter(tournament=tournament, team=team).exists():
                return Response({'status': 'error', 'message': 'This team is already registered'}, status=status.HTTP_400_BAD_REQUEST)
            registration = TournamentRegistration.objects.create(
                tournament=tournament,
                team=team,
                entry_fee_paid=(tournament.entry_fee == 'Free'),
            )
        else:
            if TournamentRegistration.objects.filter(tournament=tournament, user=user).exists():
                return Response({'status': 'error', 'message': 'You are already registered for this tournament'}, status=status.HTTP_400_BAD_REQUEST)
            registration = TournamentRegistration.objects.create(
                tournament=tournament,
                user=user,
                entry_fee_paid=(tournament.entry_fee == 'Free'),
            )

        return Response({
            'status': 'success',
            'message': 'Successfully registered for the tournament',
            'data': {
                'registration_id': registration.id,
                'status': registration.status,
                'entry_fee_paid': registration.entry_fee_paid,
            }
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['GET'])
def search_tournament(request):
    try:
        name = request.GET.get('name')
        game_id = request.GET.get('game_id')
        location = request.GET.get('location')
        access = request.GET.get('access')  # team / individual / team_and_individual

        query = Q(is_draft=False)

        if name:
            query &= Q(tournament_title__icontains=name)
        if game_id:
            query &= Q(tournament_game__game_id=game_id)
        if location:
            query &= Q(tournament_location__icontains=location)
        if access:
            query &= Q(tournament_access__iexact=access)

        tournaments = Tournament.objects.filter(query).select_related('tournament_game').order_by('-start_date_and_time')

        if not tournaments.exists():
            return Response({'status': 'success', 'data': [], 'message': 'No tournaments found'}, status=status.HTTP_200_OK)

        tournament_list = [
            {
                'tournament_id': t.tournament_id,
                'tournament_title': t.tournament_title,
                'tournament_logo': t.tournament_logo.url if t.tournament_logo else None,
                'game': t.tournament_game.game_title,
                'tournament_access': t.tournament_access,
                'entry_fee': t.entry_fee,
                'entry_fee_price': str(t.entry_fee_price),
                'location': t.tournament_location,
                'start_date_and_time': t.start_date_and_time,
                'end_date_and_time': t.end_date_and_time,
            }
            for t in tournaments
        ]
        return Response({'status': 'success', 'data': tournament_list}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def create_tournament(request):
    try:
        with transaction.atomic():
            session_token = request.headers.get('Authorization')

            if not session_token:
                return Response({'status': 'error', 'message': 'Authorization header is required'}, status=status.HTTP_400_BAD_REQUEST)

            # Ensure the token is in the correct format (e.g., 'Bearer <token>')
            if not session_token.startswith("Bearer "):
                return Response({'status': 'error', 'message': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

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
            bracket_type = request.data.get('bracket_type', 'Single Elimination')
            tournament_rules = request.data.get('tournament_rules')
            prize_type = request.data.get('prize_type', 'no_prize')
            is_draft = request.data.get('is_draft', True)


            # Sponsor data (Name, Type, Username, Logo)
            sponsor_names = request.data.get('sponsor_names', [])
            sponsor_types = request.data.get('sponsor_types', [])
            sponsor_usernames = request.data.get('sponsor_usernames', [])
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

            creator = get_object_or_404(Users, login_session_token=login_session_token)

            if creator.login_session_created_at is None or timezone.now() - creator.login_session_created_at > timedelta(minutes=120):
                return Response({'status': 'error', 'message': 'Session token has expired'}, status=401)


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
                is_draft=is_draft,
                **social_links
            )

            # Create prize distributions if applicable
            if prize_type == 'distributed':
                prize_data = request.data.get('prize_data', [])
                for prize_entry in prize_data:
                    TournamentPrizeDistribution.objects.create(
                        tournament=tournament,
                        position=prize_entry['position'],
                        prize=prize_entry['prize'],
                        extras=prize_entry.get('extras', '')
                    )
            elif prize_type == 'winner_takes_all':
                TournamentPrizeDistribution.objects.create(
                    tournament=tournament,
                    position=1,
                    prize=request.data.get('total_prize', 0.00),
                    extras='Winner Takes All'
                )

            # Add sponsors (loop through the provided sponsor details)
            for name, sponsor_type, username, logo in zip(sponsor_names, sponsor_types, sponsor_usernames, sponsor_logos):
                if sponsor_type.lower() == 'user':
                    sponsor_instance = Users.objects.get(username=username)
                elif sponsor_type.lower() == 'team':
                    sponsor_instance = Teams.objects.get(team_name=username)
                elif sponsor_type.lower() == 'org':
                    sponsor_instance = Organization.objects.get(org_name=username)
                else:
                    continue

                sponsor = Sponsors.objects.create(
                    name=name,
                    sponsor=sponsor_instance,
                    logo=logo
                )
                tournament.sponsors.add(sponsor)

            return Response({"status": "success", "message": "Tournament created successfully"},
                            status=status.HTTP_201_CREATED)

    except ValueError as e:
        return Response({"status": "error", "message": str(e)},
                        status=status.HTTP_400_BAD_REQUEST)
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


@api_view(["GET"])
def get_all_tournaments(request):
    # Featured Tournaments (most interacted)
    featured_tournaments = Tournament.objects.filter(is_draft=False).order_by('-interaction_count')[:5]
    
    # New Tournaments (recent ones)
    new_tournaments = Tournament.objects.filter(is_draft=False).order_by('-start_date_and_time')[:5]
    
    # All Tournaments grouped by game
    all_tournaments = Tournament.objects.filter(is_draft=False).select_related('tournament_game')
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

    # Serialize Tournament
    def serialize_tournaments(tournament):
        return {
            "tournament_id": tournament.tournament_id,
            "tournament_title": tournament.tournament_title,
            "tournament_logo": tournament.tournament_logo.url if tournament.tournament_logo else None,
            "tournament_banner": tournament.tournament_banner.url if tournament.tournament_banner else None,
            "tournament_description": tournament.tournament_description,
            "tournament_rules": tournament.tournament_rules,
            "bracket_type": tournament.bracket_type,
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
            "sponsors": get_sponsors_list(tournament),
            "prize_distributions": get_prize_list(tournament),
            "matches": get_match_list(tournament),
            "registered_teams": get_registered_teams_list(tournament),
        }

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
        # Fetch the tournament
        tournament = Tournament.objects.get(tournament_id=tournament_id)
        
        # Increase interaction count
        tournament.interaction_count += 1
        tournament.save(update_fields=['interaction_count'])

        # Sponsors
        sponsors = tournament.sponsors.all()
        sponsors_list = [
            {
                "id": sponsor.id,
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

        # Build the response
        data = {
            "tournament_id": tournament.tournament_id,
            "tournament_title": tournament.tournament_title,
            "tournament_logo": tournament.tournament_logo.url if tournament.tournament_logo else None,
            "tournament_banner": tournament.tournament_banner.url if tournament.tournament_banner else None,
            "tournament_description": tournament.tournament_description,
            "tournament_rules": tournament.tournament_rules,
            "bracket_type": tournament.bracket_type,
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

        return Response({"status": "success", "data": data}, status=status.HTTP_200_OK)

    except Tournament.DoesNotExist:
        return Response({"status": "error", "message": "Tournament not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
def view_user_drafted_tournaments(request):
    try:
        with transaction.atomic():
            # Step 1: Get Authorization token
            session_header = request.headers.get("Authorization")
            if not session_header:
                return Response({"status": "error", "message": "Authorization header is required"}, status=status.HTTP_400_BAD_REQUEST)
            
            if not session_header.startswith("Bearer "):
                return Response({"status": "error", "message": "Invalid token format"}, status=status.HTTP_400_BAD_REQUEST)

            login_session_token = session_header.split(" ", 1)[1]

            # Step 2: Find the user by login_session_token
            try:
                user = Users.objects.get(login_session_token=login_session_token)
            except Users.DoesNotExist:
                return Response({"status": "error", "message": "Invalid or expired session token"}, status=status.HTTP_401_UNAUTHORIZED)

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
                    "tournament_id": t.tournament_id,
                    "tournament_title": t.tournament_title,
                    "tournament_game": t.tournament_game.game_title,
                    "game_mode": t.game_mode,
                    "tournament_logo": t.tournament_logo.url if t.tournament_logo else None,
                    "tournament_banner": t.tournament_banner.url if t.tournament_banner else None,
                    "tournament_description": t.tournament_description,
                    "tournament_rules": t.tournament_rules,
                    "bracket_type": t.bracket_type,
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

    except Exception as e:
        return Response({"status": "error", "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
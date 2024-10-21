from django.shortcuts import render
from imports import api_view,get_object_or_404, Response, status, transaction
from .models import Tournament, Users, Games, Teams, TournamentPrizeDistribution
from django.db.models import Q

# Create your views here.

@api_view(['POST'])
def create_tournament(request):
    try:
        # Get data from request
        tournament_name = request.data.get('tournament_name')
        tournament_desc = request.data.get('tournament_desc')
        creator_id = request.data.get('creator_id')  # You may want to use login session token here
        tournament_game_id = request.data.get('tournament_game_id')
        reg_start_date = request.data.get('reg_start_date')
        reg_end_date = request.data.get('reg_end_date')
        tournament_start_date = request.data.get('tournament_start_date')
        tournament_end_date = request.data.get('tournament_end_date')
        tournament_format = request.data.get('tournament_format')
        tournament_status = request.data.get('tournament_status', 'upcoming')
        tournament_location = request.data.get('tournament_location')
        tournament_entry_fee = request.data.get('tournament_entry_fee')
        tournament_prize = request.data.get('tournament_prize')

        # Validate required fields
        if not all([tournament_name, tournament_desc, creator_id, tournament_game_id, reg_start_date, reg_end_date, tournament_start_date, tournament_end_date]):
            return Response({'status': 'error', 'message': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)

        # Fetch the creator (user) and game object
        creator = get_object_or_404(Users, user_id=creator_id)
        tournament_game = get_object_or_404(Games, id=tournament_game_id)

        # Create the Tournament instance
        tournament = Tournament.objects.create(
            tournament_name=tournament_name,
            tournament_desc=tournament_desc,
            tournament_creator=creator,
            tournament_game=tournament_game,
            tournament_registration_date=reg_start_date,
            tournament_registration_end_date=reg_end_date,
            tournament_start_date=tournament_start_date,
            tournament_end_date=tournament_end_date,
            tournament_format=tournament_format,
            tournament_status=tournament_status,
            tournament_location=tournament_location,
            tournament_entry_fee=tournament_entry_fee,
            tournament_prize=tournament_prize
        )

        return Response({'status': 'success', 'message': 'Tournament created successfully', 'tournament_id': tournament.tournament_id}, status=status.HTTP_201_CREATED)
    
    except Exception as e:
        return Response({'status': 'error', 'message': f'Error creating tournament: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['POST'])
def join_tournament(request):
    try:
        user_id = request.data.get('user_id')  # You might want to use login session token instead
        tournament_id = request.data.get('tournament_id')
        team_id = request.data.get('team_id')  # Optional if teams are involved

        # Validate required fields
        if not all([user_id, tournament_id]):
            return Response({'status': 'error', 'message': 'User ID and Tournament ID are required'}, status=status.HTTP_400_BAD_REQUEST)

        # Get user and tournament instances
        user = get_object_or_404(Users, user_id=user_id)
        tournament = get_object_or_404(Tournament, tournament_id=tournament_id)

        # If the tournament is team-based, ensure the user joins with a team
        if tournament.tournament_format == 'team' and not team_id:
            return Response({'status': 'error', 'message': 'Team ID is required for team-based tournaments'}, status=status.HTTP_400_BAD_REQUEST)

        # Check if team exists for team-based tournaments
        if team_id:
            team = get_object_or_404(Teams, team_id=team_id)
            # Add the team to the tournament (assuming a ManyToManyField relationship or similar exists)
            tournament.teams.add(team)
        else:
            # Individual tournaments, user can join directly
            tournament.participants.add(user)  # Assuming a ManyToManyField for participants in Tournament model

        return Response({'status': 'success', 'message': 'Successfully joined the tournament'}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'status': 'error', 'message': f'Error joining tournament: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['GET'])
def search_tournament(request):
    try:
        # Get search parameters from query string
        name = request.GET.get('name', None)
        game_id = request.GET.get('game_id', None)
        location = request.GET.get('location', None)
        status = request.GET.get('status', None)

        # Build query filters based on parameters
        query = Q()
        
        if name:
            query &= Q(tournament_name__icontains=name)  # Case-insensitive search for name
        
        if game_id:
            query &= Q(tournament_game__id=game_id)  # Filter by game ID
        
        if location:
            query &= Q(tournament_location__icontains=location)  # Case-insensitive search for location
        
        if status:
            query &= Q(tournament_status__iexact=status)  # Exact match for status

        # Query the database for matching tournaments
        tournaments = Tournament.objects.filter(query)

        # Check if any tournaments are found
        if tournaments.exists():
            # Return the list of tournaments
            tournament_list = [
                {
                    'tournament_id': tournament.tournament_id,
                    'tournament_name': tournament.tournament_name,
                    'tournament_desc': tournament.tournament_desc,
                    'game': tournament.tournament_game.name,  # Assuming the `Games` model has a 'name' field
                    'location': tournament.tournament_location,
                    'status': tournament.tournament_status,
                    'start_date': tournament.tournament_start_date,
                    'end_date': tournament.tournament_end_date
                } 
                for tournament in tournaments
            ]
            return Response({'status': 'success', 'tournaments': tournament_list}, status=status.HTTP_200_OK)
        
        return Response({'status': 'success', 'message': 'No tournaments found'}, status=status.HTTP_404_NOT_FOUND)

    except Exception as e:
        return Response({'status': 'error', 'message': f'Error searching for tournaments: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


def create_tournament_1(request):
    try:
        with transaction.atomic():
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
            event_id = request.data.get('event_id')
            tournament_visibility = request.data.get('tournament_visibility')  # public, private, protected
            reg_start_date_and_time = request.data.get('reg_start_date_and_time')
            reg_end_date_and_time = request.data.get('reg_end_date_and_time')
            entry_type = request.data.get('entry_type')  # Paid or Free
            entry_fee = request.data.get('entry_fee', 0.00 if entry_type == 'Free' else request.data.get('entry_fee'))
            tournament_logo = request.FILES.get('tournament_logo')
            tournament_banner = request.FILES.get('tournament_banner')
            tournament_format = request.data.get('tournament_format')
            tournament_access = request.data.get('tournament_access')  # team, individual, team and individual
            team_size = request.data.get('team_size')
            min_number_of_participants = request.data.get('min_number_of_participants')
            max_number_of_participants = request.data.get('max_number_of_participants')
            tournament_rules = request.data.get('tournament_rules')
            
            # Prize distribution logic
            prize_distribution_type = request.data.get('prize_distribution_type')  # distributed, winner_takes_all, no_prize
            prize_distribution = request.data.getlist('prize_distribution')  # If it's distributed
            prize = request.data.get('prize')  # If it's winner takes all
            
            # Sponsors
            sponsors = request.data.getlist('sponsor')  # List of sponsors
            
            # Social links
            facebook_link = request.data.get('facebook_link')
            twitter_link = request.data.get('twitter_link')
            instagram_link = request.data.get('instagram_link')
            youtube_link = request.data.get('youtube_link')
            twitch_link = request.data.get('twitch_link')
            kick_link = request.data.get('kick_link')

            # Create Tournament object
            tournament = Tournament.objects.create(
                tournament_title=tournament_title,
                tournament_logo=tournament_logo,
                tournament_banner=tournament_banner,
                tournament_description=tournament_description,
                tournament_rules=tournament_rules,
                start_date_and_time=start_date_and_time,
                end_date_and_time=end_date_and_time,
                tournament_visibility=tournament_visibility,
                tournament_type=tournament_type,
                tournament_location=tournament_location,
                player_size=max_number_of_participants,
                min_number_of_teams=min_number_of_participants,
                max_number_of_teams=max_number_of_participants,
                tournament_access=tournament_access,
                entry_fee=entry_type,
                entry_fee_price=entry_fee,
                facebook_link=facebook_link,
                twitter_link=twitter_link,
                instagram_link=instagram_link,
                youtube_link=youtube_link,
                twitch_link=twitch_link,
                kick_link=kick_link
            )

            # Save sponsors
            for sponsor in sponsors:
                tournament.sponsors.add(sponsor)

            # Prize distribution handling
            if prize_distribution_type == 'distributed':
                # Save distributed prizes
                for prize_info in prize_distribution:
                    position = prize_info.get('position')
                    prize_amount = prize_info.get('prize')
                    extras = prize_info.get('extras', '')

                    TournamentPrizeDistribution.objects.create(
                        tournament=tournament,
                        position=position,
                        prize=prize_amount,
                        extras=extras
                    )
            elif prize_distribution_type == 'winner_takes_all':
                # Save single winner prize
                TournamentPrizeDistribution.objects.create(
                    tournament=tournament,
                    position=1,
                    prize=prize,
                    extras='Winner takes all'
                )

            # No prize distribution needed if 'no_prize'

            return Response(
                {"status": "success", "message": "Tournament created successfully"}, 
                status=status.HTTP_201_CREATED
            )
        
    except Exception as e:
        return Response(
            {"status": "error", "message": str(e)}, 
            status=status.HTTP_400_BAD_REQUEST
        )